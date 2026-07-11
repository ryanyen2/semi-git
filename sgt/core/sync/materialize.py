"""Sync stage 4 -- persist the reconciled union and land the merge commit (plan U19, D4).

Only reached fork-free. This is the one stage that writes: persist theirs' op files for real
(unioning provenance a same-id collision would otherwise drop, R8), write the reconciled
pins/declared/tree, fold the source from the union, then commit a real 2-parent merge whose tree is
*exactly* what sgt wrote -- no textual 3-way merge runs on any path. The second parent is joined by
writing `.git/MERGE_HEAD` ourselves (`gb.complete_merge`), replacing the old `git merge -X ours`;
the two-clone tests assert the resulting tree is independently `code(merged_ideal, all_ops)`.

`land` (U23) reuses this stage over a locally-sourced union, so it takes the resolved state
explicitly rather than recomputing anything from disk.
"""

from __future__ import annotations

from pathlib import Path

from sgt import state
from sgt.core import lens
from sgt.core.fold import code
from sgt.core.store import Store
from sgt.lens import reconcile, tree
from sgt.lens.pins import save_pins
from sgt.store.gitbind import GitBinding, format_op_trailers

from .ingest import Ingested
from .resolve import Resolution


def _union_claims(repo: Path, gb: GitBinding, theirs_sha: str) -> None:
    """G-Set union of theirs' committed claims (D8): copy any `.sgt/claims/` file we don't already
    have, byte-for-byte. Claim files are immutable and keyed by `(ideal_key, runner_fp)`, so a
    file-level presence check is the whole merge -- no re-encode, no field union, no conflict."""
    for path in gb.list_tree(theirs_sha, ".sgt/claims/"):
        raw = gb.blob_bytes(theirs_sha, path)
        if raw is None:
            continue
        local = repo / path
        if local.exists():
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(raw)


def _fork_records(forks: tuple[tuple[str, str, str], ...]) -> list[dict]:
    """The committed `.sgt/forks.json` body (C4): one record per open same-symbol fork, each with
    its two tips and the `sgt merge-op` remedy that closes it. Sorted for a deterministic blob. The
    excluded tips live only here -- never in any verb-visible ideal -- so a fork is shared state a
    teammate's next sync (and `sgt status`/`sgt forks`) reads, not a lost edit."""
    return [
        {"symbol": sym, "tips": [tip_a, tip_b], "remedy": f"sgt merge-op {tip_a[:8]} {tip_b[:8]}"}
        for sym, tip_a, tip_b in sorted(forks)
    ]


def persist_reconciled(
    repo: Path, gb: GitBinding, theirs_sha: str, ing: Ingested, res: Resolution
) -> None:
    """Persist the reconciled union to the working tree -- everything `materialize` writes *before*
    it commits: theirs' op files for real (unioning provenance a same-id collision would otherwise
    drop, R8), the reconciled pins/declared/aliases/tree, the durable fork record, theirs' claim
    G-Set, the folded source, and the in-tree ideal-recovery record. Does not stage or commit.

    Shared verbatim by sync's `materialize` (which then runs a 2-parent `complete_merge`) and by
    `land` (U23, which stages + builds the commit off-ref then CAS-advances the branch). Factored so
    the branch-record CAS reuses the exact reconciled-tree construction sync already tests, with no
    behavior change to sync itself."""
    store = Store(repo)
    store.init()
    for op in [*ing.theirs_ops, *ing.mined_ops]:
        store.add(op)  # re-unions provenance a same-id collision would otherwise drop (R8); the
        # mined foreign commits (C3) have no op file anywhere else, so they land for real here

    save_pins(repo, res.unioned_pins)
    lens.save_declared_orset(repo, res.declared_orset)  # unioned declared-edge OR-Set (C1/D6)
    reconcile.save_aliases(repo, res.aliases)  # unioned feature-id alias G-Set (C1/D6)
    tree.save(repo, res.tree_result)
    state.save_json(repo, "forks", _fork_records(res.forks))  # durable, shared fork state (C4)
    _union_claims(repo, gb, theirs_sha)  # published-verdict G-Set travels with the merge (D8)

    materialized = code(res.merged_ideal, ing.all_ops)
    lens._write_working_tree(repo, materialized)
    state.save_json(repo, "ideal", sorted(res.merged_ideal.op_ids))  # in-tree recovery record (C5)


def materialize(
    repo: Path,
    gb: GitBinding,
    remote: str,
    branch: str,
    theirs_sha: str,
    ing: Ingested,
    res: Resolution,
) -> str:
    persist_reconciled(repo, gb, theirs_sha, ing, res)
    trailers = format_op_trailers(sorted(res.merged_ideal.op_ids))
    merge_sha = gb.complete_merge(f"sgt sync: merge {remote}/{branch}", theirs_sha, trailers=trailers)
    lens.record_ideal(repo, res.merged_ideal, merge_sha)
    return merge_sha
