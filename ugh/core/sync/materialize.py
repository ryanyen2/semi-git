"""Sync stage 4 -- persist the reconciled union and land the merge commit (plan U19, D4).

This is the one stage that writes: persist theirs' op files for real (unioning provenance a
same-id collision would otherwise drop, R8), write the merged names and prompts, fold the source from
the union, then commit a real 2-parent merge whose tree is *exactly* what ugh wrote -- no textual
3-way merge runs on any path. The second parent is joined by writing `.git/MERGE_HEAD` ourselves
(`gb.complete_merge`), replacing the old `git merge -X ours`; the two-clone tests assert the
resulting tree is independently `code(merged_ideal, all_ops)`.
"""

from __future__ import annotations

from pathlib import Path

from ugh import names, state
from ugh.core import lens
from ugh.core.fold import code
from ugh.core.store import Store, locked_section
from ugh.store.gitbind import GitBinding, format_op_trailers

from .ingest import Ingested
from .resolve import Resolution


def _fork_records(forks: tuple[tuple[str, str, str], ...]) -> list[dict]:
    """The committed `.sgt/forks.json` body (C4): one record per open same-symbol fork, each with
    its two tips and the `ugh resolve <symbol>` remedy that closes it. Sorted for a deterministic
    blob. The excluded tips live only here -- never in any verb-visible ideal -- so a fork is shared
    state a teammate's next sync (and `ugh status`) reads, not a lost
    edit."""
    return [
        {"symbol": sym, "tips": [tip_a, tip_b], "remedy": f"ugh resolve {sym}"}
        for sym, tip_a, tip_b in sorted(forks)
    ]


def save_fork_records(repo: Path, forks: tuple[tuple[str, str, str], ...]) -> None:
    """Persist the committed `.sgt/forks.json` body (C4) for `forks` -- the single writer shared by
    `materialize` and `lens._record_parked_forks`."""
    state.save_json(repo, "forks", _fork_records(forks))


def materialize(
    repo: Path,
    gb: GitBinding,
    remote: str,
    branch: str,
    theirs_sha: str,
    ing: Ingested,
    res: Resolution,
) -> str:
    store = Store(repo)
    store.init()
    for op in [*ing.theirs_ops, *ing.mined_ops]:
        store.add(op)  # re-unions provenance a same-id collision would otherwise drop (R8); the
        # mined foreign commits (C3) have no op file anywhere else, so they land for real here
    lens._write_working_tree(repo, code(res.merged_ideal, ing.all_ops), ing.all_ops)
    # The reconciled metadata is written under one locked section so no reader sees a half-union
    # (R5/R6). `Store.add`'s own lock must not nest inside `locked_section` (self-deadlock).
    with locked_section(repo):
        names.save(repo, res.names)
        state.save_json(repo, "intent_prompts", res.prompts)  # union-by-key sidecar (U5/KTD5)
        save_fork_records(repo, res.forks)  # durable, shared fork state (C4)
    trailers = format_op_trailers(sorted(res.merged_ideal.op_ids))
    merge_sha = gb.complete_merge(f"ugh sync: merge {remote}/{branch}", theirs_sha, trailers=trailers)
    lens.get(repo, unbounded=True)  # the merge commit is mined like any other
    return merge_sha
