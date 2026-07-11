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

from sgt import state
from sgt.core.fold import code
from sgt.core.ideal import Ideal
from sgt.core.mine import mine
from sgt.core.store import Store
from sgt.store.gitbind import GitBinding, format_op_trailers


class DirtyWorkingTreeError(Exception):
    """`put()` would overwrite uncommitted working-tree changes with different bytes (R9). Raised
    instead of silently clobbering; the caller absorbs the edit first (`get()` folds a dirty tree
    into the ideal) so the materialization reproduces it rather than reverting it."""


_DECLARED_FILE = "declared.json"


def _load_witnesses(repo: Path) -> dict[str, str]:
    return state.load_json(repo, "witness", default={})


def _save_witnesses(repo: Path, table: dict[str, str]) -> None:
    state.save_json(repo, "witness", table)


def _load_ideal_table(repo: Path) -> dict[str, list[str]]:
    """The persisted per-ref ideal: `{ref_key: [sorted op_ids]}`. This is the durable committed
    ideal (never the dirty overlay) that lets an explicit ideal edit (revert/pin, U8) survive a
    re-`get()` -- provenance alone can't represent "excluded though still in git history"."""
    return state.load_json(repo, "ideal_table", default={})


def _save_ideal_table(repo: Path, table: dict[str, list[str]]) -> None:
    state.save_json(repo, "ideal_table", table)


def _load_declared(repo: Path) -> frozenset[tuple[str, str]]:
    """The persisted declared order edges (`sgt after a b` -> `(a, b)` meaning `a <= b`, U8's
    escape hatch for ordering the analyzer can't infer). Repo-global, not per-ref: an edge is a
    fact about two ops' content, independent of which ref is checked out. `order`'s validity and
    up/down-set functions take these as their `declared` argument.

    One-shot migration: a repo whose declared edges still sit at the pre-U15 gitignored
    `.sgt/local/declared.json` gets them re-saved to the committed path and the old file removed,
    the first time anything reads declared edges."""
    body = state.load_json(repo, "declared")
    if body is None:
        old_path = repo / ".sgt" / "local" / _DECLARED_FILE
        if not old_path.is_file():
            return frozenset()
        edges = frozenset(tuple(pair) for pair in json.loads(old_path.read_text(encoding="utf-8")))
        _save_declared(repo, edges)
        old_path.unlink()
        return edges
    return frozenset(tuple(pair) for pair in body)


def _save_declared(repo: Path, edges: frozenset[tuple[str, str]]) -> None:
    payload = sorted([a, b] for a, b in edges)
    state.save_json(repo, "declared", payload)


def _ref_key(gb: GitBinding) -> str | None:
    """This ref's stable key in the witness table: its symbolic name, or the raw HEAD sha in
    detached-HEAD state (each detached position tracked independently)."""
    return gb.symbolic_ref() or gb.head()


def _committed_ids_by_provenance(gb: GitBinding, store: Store) -> set[str]:
    """Every stored op whose provenance intersects this ref's own commit ancestry -- the ref's
    ideal derived fresh from content-addressed history. Used only to *seed* the persisted
    `.sgt/local/ideal.json` entry on a ref's first tracked `get()`; once that entry exists it is
    authoritative, so an explicit ideal edit (revert/pin, U8) is never silently re-derived away
    by a later provenance scan that has no way to represent "excluded though still in history"."""
    ref_commits = set(gb.commit_shas())
    return {op.id for op in store.all_ops() if set(op.provenance) & ref_commits}


def current_ideal(repo: str | Path) -> Ideal:
    """The current ref's committed ideal as last persisted -- a *pure read* (no mining, no
    writes), reflecting any prior explicit edit (revert/pin, U8) that a provenance scan alone
    would miss. Falls back to the provenance-scan seed when the ref has no persisted entry yet.
    This is the plan-time view an ideal-edit verb previews against (`--emit` must be
    side-effect-free); `get()` is the mine-on-contact version `apply` runs to absorb reality."""
    repo = Path(repo)
    gb = GitBinding(repo)
    store = Store(repo)
    key = _ref_key(gb)
    table = _load_ideal_table(repo)
    ids = frozenset(table[key]) if key is not None and key in table else _committed_ids_by_provenance(gb, store)
    return Ideal.from_ops(ids, store.all_ops())


def ideal_for_ref(repo: str | Path, ref: str = "HEAD", store: Store | None = None) -> Ideal:
    """The ideal a given ref's committed history implies -- a *pure read*: no mining, no
    checkout, no side effects. It projects the ops already in the store onto `ref`'s own commit
    ancestry, exactly as `_committed_ids_by_provenance` does for the current ref, but for any
    ref, and never consults the persisted `.sgt/local/ideal.json` table (see `_sync`). A ref
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

    # (1,2) Mine committed history plus the current uncommitted working tree (R9), and persist
    # each op. Partition by the *returned* (post-merge) op's provenance, not the mined op's: a
    # dirty edit whose content is byte-identical to something already committed comes back from
    # `store.add` as the existing op with its real provenance intact, so it rightly counts as
    # committed, not pending.
    new_committed_ids: set[str] = set()
    pending_ids: set[str] = set()
    for op in mine(repo, since=since, treat_as_root=treat_as_root, include_dirty=True):
        stored = store.add(op)
        (new_committed_ids if stored.provenance else pending_ids).add(stored.id)

    # (3) Seed the persisted ideal from a provenance scan the first time this ref is tracked;
    # thereafter the stored set is authoritative (it can encode explicit exclusions -- U8's
    # revert/pin -- that a scan of git ancestry can't).
    key = _ref_key(gb) or head
    ideal_table = _load_ideal_table(repo)
    base_ids = set(ideal_table[key]) if key in ideal_table else _committed_ids_by_provenance(gb, store)

    # (4) The durable ideal gains only newly-committed ops; the dirty overlay is never persisted,
    # so a discarded working-tree edit (e.g. `git checkout -- .`) simply stops appearing on the
    # next `get()` rather than lingering in the table.
    committed_ids = base_ids | new_committed_ids
    ideal_table[key] = sorted(committed_ids)
    _save_ideal_table(repo, ideal_table)

    table = _load_witnesses(repo)
    table[key] = head
    _save_witnesses(repo, table)

    # (5) The in-memory ideal carries the dirty overlay on top of the durable committed set.
    return Ideal.from_ops(committed_ids | pending_ids, store.all_ops())


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
    new tree embodies. Mine-before-materialize (R9): `get()` runs first so a dirty tree or a
    foreign commit is absorbed into the store, then the fold is refused (rather than silently
    clobbering) if it would overwrite an uncommitted change with different bytes."""
    repo = Path(repo)
    gb = GitBinding(repo)
    get(repo)  # absorb any dirty tree / foreign commit first (R9)
    store = Store(repo)
    materialized = code(ideal, store.all_ops())
    conflicts = _dirty_conflicts(repo, gb, materialized)
    if conflicts:
        raise DirtyWorkingTreeError(
            f"put() would overwrite uncommitted changes: {sorted(conflicts)}"
        )
    _write_working_tree(repo, materialized)
    # Committed in-tree recovery record of *this* commit's ideal (C5): written before the commit
    # so the blob at the witness SHA describes that SHA's own ideal, recoverable after a
    # squash/rebase strips the trailers below. The local per-ref table stays authoritative for the
    # current ref; this is the historical record `sync` reads from a teammate's arbitrary SHA.
    state.save_json(repo, "ideal", sorted(ideal.op_ids))
    return gb.commit_all(message, trailers=format_op_trailers(sorted(ideal.op_ids)))


def record_ideal(repo: str | Path, ideal: Ideal, witness_sha: str) -> None:
    """Persist an explicitly-edited `ideal` as the current ref's authoritative committed set and
    advance the ref's witness to `witness_sha` -- the durability an ideal-edit verb (U8's
    revert/pin/restore/cherry-pick) needs after `put()` commits. Without it, the next `get()`
    would re-mine the materializing commit's diff as a fresh op and union it back onto a stale
    base, undoing a reducing edit. Called *after* `put()` so `witness_sha` is the post-commit
    HEAD: the next `get()` then mines nothing new (`since == witness_sha`) and trusts this set."""
    repo = Path(repo)
    key = _ref_key(GitBinding(repo)) or witness_sha
    itable = _load_ideal_table(repo)
    itable[key] = sorted(ideal.op_ids)
    _save_ideal_table(repo, itable)
    wtable = _load_witnesses(repo)
    wtable[key] = witness_sha
    _save_witnesses(repo, wtable)


def _dirty_conflicts(repo: Path, gb: GitBinding, materialized: dict[str, bytes]) -> set[str]:
    """Paths where `put()` would clobber an uncommitted change. A path is *dirty* when its
    on-disk bytes differ from HEAD's; it *conflicts* only when the ideal would additionally
    materialize different bytes there (or delete it). A dirty path the ideal already reproduces
    exactly -- the normal case once `get()` has folded the edit into the ideal -- is not a
    conflict, so the intended get()->put() flow never trips this guard. Only paths `put()` would
    actually write or delete are considered (`materialized` keys plus git-tracked paths); an
    untracked file the ideal doesn't cover is left untouched by `_write_working_tree`, so it can't
    conflict. `.sgt/` is skipped -- it's sgt's own state, not codebase content `put()` owns."""
    head = gb.head()
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    )
    tracked = {line for line in proc.stdout.splitlines() if line}

    conflicts: set[str] = set()
    for path in set(materialized) | tracked:
        if path.startswith(".sgt/"):
            continue
        full = repo / path
        on_disk = full.read_bytes() if full.is_file() else None
        committed = gb.blob_bytes(head, path) if head is not None else None
        if on_disk != committed and materialized.get(path) != on_disk:
            conflicts.add(path)
    return conflicts


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
