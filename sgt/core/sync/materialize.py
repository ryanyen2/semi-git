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
from sgt.core.store import Store, _write_atomic, locked_section
from sgt.lens import authored, reconcile, tree
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
        _write_atomic(local, raw)  # torn copy would be skipped forever by the exists() guard (R5)


def _union_proposals(repo: Path, gb: GitBinding, theirs_sha: str) -> None:
    """G-Set union of theirs' committed proposals (C10): copy any `.sgt/proposals/` file we don't
    already have, byte-for-byte. Proposals are immutable review objects content-addressed by base+Δ,
    so -- exactly like claims (`_union_claims`) -- a file-level presence check is the whole merge: no
    field union, no conflict. A teammate's proposal therefore arrives verbatim on the next sync."""
    for path in gb.list_tree(theirs_sha, ".sgt/proposals/"):
        raw = gb.blob_bytes(theirs_sha, path)
        if raw is None:
            continue
        local = repo / path
        if local.exists():
            continue
        _write_atomic(local, raw)  # torn copy would be skipped forever by the exists() guard (R5)


def _union_reviews(repo: Path, gb: GitBinding, theirs_sha: str) -> None:
    """G-Set union of theirs' committed review records (plan U31, S7): copy any `.sgt/reviews/`
    file we don't already have, byte-for-byte. Review records are immutable and content-addressed
    by their reviewed op-set, exactly like claims/proposals -- a file-level presence check is the
    whole merge, so a teammate's ack arrives verbatim on the next sync."""
    for path in gb.list_tree(theirs_sha, ".sgt/reviews/"):
        raw = gb.blob_bytes(theirs_sha, path)
        if raw is None:
            continue
        local = repo / path
        if local.exists():
            continue
        _write_atomic(local, raw)  # torn copy would be skipped forever by the exists() guard (R5)


def _fork_records(forks: tuple[tuple[str, str, str], ...]) -> list[dict]:
    """The committed `.sgt/forks.json` body (C4): one record per open same-symbol fork, each with
    its two tips and the `sgt merge-op` remedy that closes it. Sorted for a deterministic blob. The
    excluded tips live only here -- never in any verb-visible ideal -- so a fork is shared state a
    teammate's next sync (and `sgt status`/`sgt forks`) reads, not a lost edit."""
    return [
        {"symbol": sym, "tips": [tip_a, tip_b], "remedy": f"sgt merge-op {tip_a[:8]} {tip_b[:8]}"}
        for sym, tip_a, tip_b in sorted(forks)
    ]


def stage_candidate(
    repo: Path, gb: GitBinding, ing: Ingested, res: Resolution
) -> dict[str, bytes]:
    """Persist the union's ops and write the reconciled *source* into the working tree -- but not
    one byte of the reconciled `.sgt` metadata. This is the half that must run *before* `land`'s
    oracle gate so the oracle sees the real candidate tree, yet leaves nothing that a non-landing
    exit must clean up beyond a worktree restore: op adds are monotone (content-addressed, append-
    only, R8) and the source is git-tracked, so `restore_worktree_to` rolls both back. Returns the
    materialized `{path: bytes}` for the caller. Shared by sync (which flushes metadata straight
    after) and land (which gates in between)."""
    store = Store(repo)
    store.init()
    for op in [*ing.theirs_ops, *ing.mined_ops]:
        store.add(op)  # re-unions provenance a same-id collision would otherwise drop (R8); the
        # mined foreign commits (C3) have no op file anywhere else, so they land for real here
    materialized = code(res.merged_ideal, ing.all_ops)
    lens._write_working_tree(repo, materialized, ing.all_ops)
    return materialized


def flush_reconciled_metadata(
    repo: Path, gb: GitBinding, theirs_sha: str, ing: Ingested, res: Resolution
) -> None:
    """Write the reconciled metadata -- the six artifacts the review found a red `land` leaking
    (pins, declared OR-Set, aliases, tree, durable fork record, in-tree ideal recovery) plus the
    claim/proposal/review G-Set unions -- under one locked section so no reader sees a half-union
    (R5/R6). `land` calls this only once the oracle is green and just before it builds the landing
    commit, so a refused land never persists it; sync calls it unconditionally right after
    `stage_candidate`. Op adds happened in `stage_candidate` (before this section) -- `Store.add`'s
    own lock must not nest inside `locked_section` (self-deadlock; see its contract)."""
    with locked_section(repo):
        save_pins(repo, res.unioned_pins)
        lens.save_declared_orset(repo, res.declared_orset)  # unioned declared-edge OR-Set (C1/D6)
        reconcile.save_aliases(repo, res.aliases)  # unioned feature-id alias G-Set (C1/D6)
        if res.unioned_authored:  # merged authored-feature collection (U6/R3/KTD3) -- written only
            authored.save_authored(repo, res.unioned_authored)  # when one exists, keeping the merge
            # commit byte-identical to pre-U6 for the common case (no authored features)
        tree.save(repo, res.tree_result)
        state.save_json(repo, "intent_prompts", res.prompts)  # union-by-key sidecar (U5/KTD5)
        state.save_json(repo, "forks", _fork_records(res.forks))  # durable, shared fork state (C4)
        _union_claims(repo, gb, theirs_sha)  # published-verdict G-Set travels with the merge (D8)
        _union_proposals(repo, gb, theirs_sha)  # committed review objects travel too (C10)
        _union_reviews(repo, gb, theirs_sha)  # trust-queue acks travel too (U31/S7)
        state.save_json(repo, "ideal", sorted(res.merged_ideal.op_ids))  # in-tree recovery (C5)


def persist_reconciled(
    repo: Path, gb: GitBinding, theirs_sha: str, ing: Ingested, res: Resolution
) -> None:
    """Persist the whole reconciled union -- ops, folded source, and all reconciled metadata --
    without staging or committing. Sync's `materialize` uses this verbatim (then runs a 2-parent
    `complete_merge`); `land` (U5) instead composes the two halves with its oracle gate between
    them, so a refused land rolls back with nothing persisted. Factored so the branch-record CAS
    reuses the exact reconciled-tree construction sync already tests, with no behavior change to
    sync itself."""
    stage_candidate(repo, gb, ing, res)
    flush_reconciled_metadata(repo, gb, theirs_sha, ing, res)


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
