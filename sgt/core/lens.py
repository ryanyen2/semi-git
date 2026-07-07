"""The lens: get/put integration with git (ADR S6; plan R8, R9, R10, R20).

`get` mines any commits new to the current ref since that ref's last witness, persists them
into the store (whose provenance-merge on a content-address collision *is* the identification
law, R8), then reconstructs the ref's current ideal as every stored op whose provenance
intersects the ref's own commit ancestry. A squash merge or rebase lands its result as a new
witness commit on ops that already exist (same content, same id), so it's absorbed into the
ref's ideal rather than forking (AE1) -- no special-casing, this falls out of content-addressing
plus ref-ancestry membership. A `git checkout` to a ref this lens has never tracked mines that
ref's own history cold; that's slower, never wrong, since re-mining already-known content just
re-derives the same op ids and merges witnesses.

`put` runs `code(I)` and writes the result to the working tree (deleting any git-tracked path
the ideal no longer covers), then commits with `Sgt-Op:` trailers naming every op the tree now
embodies. Mine-before-materialize (R9) is why every mutating verb should call `get` before
computing its edit: a dirty working tree or a foreign commit made outside sgt is absorbed first,
so the verb's own change lands on top of *current* reality, not stale state.

`init(repo, horizon=...)` is the genesis-horizon mechanism (R10): pre-horizon history is never
mined at all -- everything at the horizon commit becomes one add-op per symbol (via `mine`'s
`treat_as_root`), and mining continues normally from there to HEAD. Lazy background mining of
pre-horizon history is deliberately out of scope here (plan Scope Boundaries).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.mine import mine
from sgt.core.store import Store
from sgt.store.gitbind import GitBinding, format_op_trailers

_WITNESS_FILE = "witness.json"


def _witness_path(repo: Path) -> Path:
    return repo / ".sgt" / "local" / _WITNESS_FILE


def _load_witnesses(repo: Path) -> dict[str, str]:
    path = _witness_path(repo)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_witnesses(repo: Path, table: dict[str, str]) -> None:
    path = _witness_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _ref_key(gb: GitBinding) -> str | None:
    """This ref's stable key in the witness table: its symbolic name, or the raw HEAD sha in
    detached-HEAD state (each detached position tracked independently)."""
    return gb.symbolic_ref() or gb.head()


def _reconstruct_ideal(gb: GitBinding, store: Store) -> Ideal:
    """Every stored op whose provenance intersects this ref's own commit ancestry -- the ref's
    current ideal, derived fresh from content-addressed history rather than stored as its own
    explicit set."""
    ref_commits = set(gb.commit_shas())
    all_ops = store.all_ops()
    included = {op.id for op in all_ops if set(op.provenance) & ref_commits}
    return Ideal.from_ops(included, all_ops)


def ideal_for_ref(repo: str | Path, ref: str = "HEAD", store: Store | None = None) -> Ideal:
    """The ideal a given ref's committed history implies -- a *pure read*: no mining, no
    checkout, no side effects. It projects the ops already in the store onto `ref`'s own commit
    ancestry, exactly as `_reconstruct_ideal` does for the current ref, but for any ref. A ref
    whose history was never mined yields an under-approximated ideal, so contact it with `get()`
    first for completeness. The read views (U7's `state_view`/`ideal_diff_view`) use this to
    inspect and compare refs without disturbing the working tree."""
    repo = Path(repo)
    gb = GitBinding(repo)
    store = store or Store(repo)
    ref_commits = set(gb.commit_shas(ref))
    all_ops = store.all_ops()
    included = {op.id for op in all_ops if set(op.provenance) & ref_commits}
    return Ideal.from_ops(included, all_ops)


def _sync(repo: Path, since: str | None, treat_as_root: str | None = None) -> Ideal:
    gb = GitBinding(repo)
    store = Store(repo)
    store.init()

    head = gb.head()
    if head is None:
        return Ideal.from_ops(frozenset(), [])  # nothing committed yet

    for op in mine(repo, since=since, treat_as_root=treat_as_root):
        store.add(op)

    table = _load_witnesses(repo)
    table[_ref_key(gb) or head] = head
    _save_witnesses(repo, table)

    return _reconstruct_ideal(gb, store)


def get(repo: str | Path) -> Ideal:
    """Mine what's new to the current ref, persist it, and return the ref's current ideal."""
    repo = Path(repo)
    gb = GitBinding(repo)
    key = _ref_key(gb)
    since = _load_witnesses(repo).get(key) if key is not None else None
    return _sync(repo, since=since)


def init(repo: str | Path, horizon: str | None = None) -> Ideal:
    """`sgt init`: bind (or reuse) the repo and the kernel store, then mine -- from genesis, or
    from `horizon` onward if given (R10)."""
    repo = Path(repo)
    gb = GitBinding(repo)
    gb.init()
    store = Store(repo)
    store.init()

    if horizon is None:
        return get(repo)

    horizon_sha = gb.rev_parse(horizon)
    if horizon_sha is None:
        raise ValueError(f"cannot resolve horizon {horizon!r}")
    return _sync(repo, since=gb.parent_of(horizon_sha), treat_as_root=horizon_sha)


def put(repo: str | Path, ideal: Ideal, message: str = "sgt: materialize ideal") -> str:
    """`code(I)` -> working tree -> a witness commit carrying one `Sgt-Op:` trailer per op the
    new tree embodies."""
    repo = Path(repo)
    gb = GitBinding(repo)
    store = Store(repo)
    materialized = code(ideal, store.all_ops())
    _write_working_tree(repo, materialized)
    return gb.commit_all(message, trailers=format_op_trailers(sorted(ideal.op_ids)))


def _write_working_tree(repo: Path, materialized: dict[str, bytes]) -> None:
    """Write every materialized path; delete any git-tracked path the ideal no longer covers --
    the fold is total, so an absent path means the ideal genuinely doesn't include it, not that
    something was missed."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    )
    tracked = [line for line in proc.stdout.splitlines() if line]

    for path, data in materialized.items():
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    for path in tracked:
        if path in materialized or path.startswith(".sgt/"):
            continue
        full = repo / path
        if full.is_file():
            full.unlink()
