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
from sgt.lens import authored, tree
from sgt.lens.pins import save_pins
from sgt.store.gitbind import GitBinding, format_op_trailers

from .ingest import Ingested
from .resolve import Resolution


def _surface_dual_claims(repo: Path, ing: Ingested, res: Resolution) -> None:
    """After the authored-feature merge, surface (never silently resolve) any symbol left a live
    member of more than one authored feature (U6, the "Cross-clone dual-lane membership" risk): two
    clones each ran the save-time local move on the same new symbol against a locally-different
    owned-neighbour view, landing it live in two different `af-` ids that `merge_feature`'s
    union-within-one-id logic never reconciles. Each is recorded as a `conflict` in U7's suggestion
    queue for the user to resolve with `sgt feature move`. Best-effort: a hiccup here must never fail
    an otherwise-clean sync -- the merge itself is already persisted."""
    try:
        from sgt.core import suggest
        from sgt.lens import ledger

        frontier = res.merged_ideal.frontier(ing.all_ops)
        for sym, fids in ledger.dual_claims(res.unioned_authored):
            op_id = frontier.get(sym)
            if op_id is None:  # a dead symbol has no in-ideal op to content-address the record by
                continue
            suggest.add(
                repo, "conflict", fids, [op_id],
                rationale=f"{sym} is a live member of {len(fids)} lanes ({', '.join(fids)}) after "
                          "sync -- move it to one with `sgt feature move`",
            )
    except Exception:  # noqa: BLE001 -- a read-only advisory must never break a clean sync
        pass


def _union_claims(repo: Path, gb: GitBinding, state_sha: str) -> None:
    """G-Set union of theirs' committed claims (D8): copy any `.sgt/claims/` file we don't already
    have, byte-for-byte. Claim files are immutable and keyed by `(ideal_key, runner_fp)`, so a
    file-level presence check is the whole merge -- no re-encode, no field union, no conflict.
    Phase 1.2: read from the fetched `refs/sgt/state` tip (`state_sha`), which carries these files;
    it falls back to theirs' branch sha during the transition (see `ingest`)."""
    for path in gb.list_tree(state_sha, ".sgt/claims/"):
        raw = gb.blob_bytes(state_sha, path)
        if raw is None:
            continue
        local = repo / path
        if local.exists():
            continue
        _write_atomic(local, raw)  # torn copy would be skipped forever by the exists() guard (R5)


def _union_proposals(repo: Path, gb: GitBinding, state_sha: str) -> None:
    """G-Set union of theirs' committed proposals (C10): copy any `.sgt/proposals/` file we don't
    already have, byte-for-byte. Proposals are immutable review objects content-addressed by base+Δ,
    so -- exactly like claims (`_union_claims`) -- a file-level presence check is the whole merge: no
    field union, no conflict. A teammate's proposal therefore arrives verbatim on the next sync.
    Phase 1.2: read from the fetched `refs/sgt/state` tip (`state_sha`); see `_union_claims`."""
    for path in gb.list_tree(state_sha, ".sgt/proposals/"):
        raw = gb.blob_bytes(state_sha, path)
        if raw is None:
            continue
        local = repo / path
        if local.exists():
            continue
        _write_atomic(local, raw)  # torn copy would be skipped forever by the exists() guard (R5)


def _union_reviews(repo: Path, gb: GitBinding, state_sha: str) -> None:
    """G-Set union of theirs' committed review records (plan U31, S7): copy any `.sgt/reviews/`
    file we don't already have, byte-for-byte. Review records are immutable and content-addressed
    by their reviewed op-set, exactly like claims/proposals -- a file-level presence check is the
    whole merge, so a teammate's ack arrives verbatim on the next sync.
    Phase 1.2: read from the fetched `refs/sgt/state` tip (`state_sha`); see `_union_claims`."""
    for path in gb.list_tree(state_sha, ".sgt/reviews/"):
        raw = gb.blob_bytes(state_sha, path)
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


def save_fork_records(repo: Path, forks: tuple[tuple[str, str, str], ...]) -> None:
    """Persist the committed `.sgt/forks.json` body (C4) for `forks` -- the single writer shared by
    sync's flush (below) and `land`'s fork refusal (F23). `land` computes forks on the fly and used
    to refuse *without* persisting, so `sgt forks`/`resolve` (which read this file) saw nothing land
    was talking about; routing land's refusal through this same writer closes that dead end."""
    state.save_json(repo, "forks", _fork_records(forks))


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
    repo: Path, gb: GitBinding, theirs_sha: str, ing: Ingested, res: Resolution,
    *, theirs_state_sha: str | None = None,
) -> None:
    """Write the reconciled metadata -- the artifacts the review found a red `land` leaking
    (pins, declared OR-Set, tree, durable fork record, in-tree ideal recovery) plus the
    claim/proposal/review G-Set unions -- under one locked section so no reader sees a half-union
    (R5/R6). `land` calls this only once the oracle is green and just before it builds the landing
    commit, so a refused land never persists it; sync calls it unconditionally right after
    `stage_candidate`. Op adds happened in `stage_candidate` (before this section) -- `Store.add`'s
    own lock must not nest inside `locked_section` (self-deadlock; see its contract)."""
    # Phase 1.2: the content-addressed G-Set unions read from the fetched `refs/sgt/state` tip when
    # transport supplies one, falling back to theirs' branch sha during the transition (see `ingest`).
    state_sha = theirs_state_sha if theirs_state_sha is not None else theirs_sha
    with locked_section(repo):
        save_pins(repo, res.unioned_pins)
        lens.save_declared_orset(repo, res.declared_orset)  # unioned declared-edge OR-Set (C1/D6)
        if res.unioned_authored:  # merged authored-feature collection (U6/R3/KTD3) -- written only
            authored.save_authored(repo, res.unioned_authored)  # when one exists, keeping the merge
            # commit byte-identical to pre-U6 for the common case (no authored features)
            _surface_dual_claims(repo, ing, res)  # U6 overlap check: a cross-clone dual-claim
            # `merge_feature` can't reconcile surfaces as a conflict, never a silent resolve
        tree.save(repo, res.tree_result)
        if res.exclusions:  # merged exclusion OR-Set (§E) -- guarded so a no-exclusion merge stays
            lens.save_exclusions(repo, res.exclusions)  # byte-identical to pre-1.2 on that table
        state.save_json(repo, "intent_prompts", res.prompts)  # union-by-key sidecar (U5/KTD5)
        save_fork_records(repo, res.forks)  # durable, shared fork state (C4) -- shared writer w/ land
        _union_claims(repo, gb, state_sha)  # published-verdict G-Set travels with the merge (D8)
        _union_proposals(repo, gb, state_sha)  # committed review objects travel too (C10)
        _union_reviews(repo, gb, state_sha)  # trust-queue acks travel too (U31/S7)
        # Phase 1.2: the in-tree `.sgt/ideal.json` recovery write (C5) is gone -- the op store and its
        # tables travel on `refs/sgt/state`, off the branch tree, so a merge no longer records that
        # blob (recovery ladder is log -> trailers -> mine; see `ingest._theirs_ideal`).


def persist_reconciled(
    repo: Path, gb: GitBinding, theirs_sha: str, ing: Ingested, res: Resolution,
    *, theirs_state_sha: str | None = None,
) -> None:
    """Persist the whole reconciled union -- ops, folded source, and all reconciled metadata --
    without staging or committing. Sync's `materialize` uses this verbatim (then runs a 2-parent
    `complete_merge`); `land` (U5) instead composes the two halves with its oracle gate between
    them, so a refused land rolls back with nothing persisted. Factored so the branch-record CAS
    reuses the exact reconciled-tree construction sync already tests, with no behavior change to
    sync itself."""
    stage_candidate(repo, gb, ing, res)
    flush_reconciled_metadata(repo, gb, theirs_sha, ing, res, theirs_state_sha=theirs_state_sha)


def materialize(
    repo: Path,
    gb: GitBinding,
    remote: str,
    branch: str,
    theirs_sha: str,
    ing: Ingested,
    res: Resolution,
    *,
    theirs_state_sha: str | None = None,
) -> str:
    persist_reconciled(repo, gb, theirs_sha, ing, res, theirs_state_sha=theirs_state_sha)
    trailers = format_op_trailers(sorted(res.merged_ideal.op_ids))
    merge_sha = gb.complete_merge(f"sgt sync: merge {remote}/{branch}", theirs_sha, trailers=trailers)
    # `record_exclusions=False` (§E): the merged exclusion OR-Set persisted by `flush_reconciled_metadata`
    # is authoritative; re-deriving it from this merged ideal's delta would mint fresh tags and break
    # cross-clone OR-Set convergence.
    lens.record_ideal(repo, res.merged_ideal, merge_sha, record_exclusions=False)
    return merge_sha
