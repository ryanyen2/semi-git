"""`sgt land [branch]`: advance a shared branch record by compare-and-swap (plan U23, C9/LAW-G).

Where `sync` folds a *remote* teammate's work into the local branch, `land` advances the *shared
branch record itself* -- the git ref `refs/heads/<branch>` -- from a *local* source (this session's
HEAD), gated so the shared tip only ever points at a verified op-set (LAW-G). It is the enforcement
point for LAW-G that `sync` deliberately is not (the U20 contract note): `sync` advances a branch
ungated; `land` is the gated shared-branch advance.

The verb reuses the sync pipeline's stages verbatim over a local source -- `ingest -> resolve`
build the in-memory union of the branch tip (`theirs`) and this session's HEAD (`ours`), then the
reconciled tree is persisted with `materialize.persist_reconciled` (the same reconciled-tree
construction sync tests). It then departs from sync in *how it commits*: rather than moving HEAD
with an ordinary commit, it builds the landing commit *off-ref* (`git commit-tree`) and advances the
branch with an atomic compare-and-swap (`git update-ref <ref> <new> <old>`). That CAS is the entire
concurrency-safety mechanism for two sessions racing to advance one branch: exactly one wins, and
the loser re-loops -- re-ingesting against the now-moved tip (the "re-union retry") and either
landing on it or surfacing a genuine fork.

Concurrency model (see the U23 FINDINGS entry). The store-lock audit showed the single-writer
`.sgt/lock` is a *per-`add()`* lock -- correct for op appends, but it does not serialize whole
verbs, so it is not what makes a branch advance atomic. The branch-record CAS is. Two concurrent
landers must therefore materialize into *separate* working trees/indexes (each session its own
clone or `git worktree` of the shared ref store) so they don't clobber one another's index; the ref
CAS across the shared ref store is the arbiter. No second lock is added around the CAS.

Consistency: `land` targets `refs/heads/<branch>`. When that branch is the checked-out branch
(symbolic HEAD), the CAS moves the ref HEAD already points at, so HEAD follows automatically and the
staged index already equals the landed tree -- no separate HEAD move is needed. When HEAD is
detached (or on a different branch) and diverges from the branch tip, the landing commit is a real
2-parent merge of the branch tip and this session's HEAD, and HEAD is left where it is (the session
advanced the *shared* ref, which is the point).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sgt import state
from sgt.config import load_oracle_config
from sgt.core import lens, oracle
from sgt.core.ideal import Ideal
from sgt.lens.pins import Contradiction
from sgt.store.gitbind import GitBinding, format_op_trailers

from . import ingest as _ingest
from . import log as _log
from . import materialize as _materialize
from . import resolve as _resolve
from . import state_ref as _state_ref

__all__ = ["LandReport", "land", "LandPlan", "plan_land"]


@dataclass(frozen=True)
class LandPlan:
    """The dry-run consequence of a `land` (plan U19/D4): what the CAS *would* advance the shared
    branch by, computed with `ingest -> resolve` alone -- no oracle run, no ref move, and rolled
    back to leave zero trace (R7), so a feedforward pane can show it before the expensive/one-way
    part. `oracle_configured` is the LAW-G pre-check (no oracle -> the land will refuse); `clean`
    is False (with `error` set) when the tree isn't landable yet."""

    branch: str
    ops_added: int = 0
    forks: tuple[tuple[str, str, str], ...] = ()
    pin_contradictions: tuple[Contradiction, ...] = ()
    declared_cycles: tuple[tuple[str, str], ...] = ()
    oracle_configured: bool = True
    advisory: str | None = None
    clean: bool = True
    error: str | None = None


@dataclass(frozen=True)
class LandReport:
    branch: str
    landed: bool
    land_sha: str | None = None
    blocked_reason: str | None = None  # human reason a land was refused (red/no-oracle/fork/contention)
    forks: tuple[tuple[str, str, str], ...] = ()
    ops_added: int = 0  # ops this land adds to the shared branch (merged_ideal minus the old tip)
    attempts: int = 1  # CAS attempts made (>1 means it lost a race and re-unioned)
    pin_contradictions: tuple[Contradiction, ...] = ()
    declared_cycles: tuple[tuple[str, str], ...] = ()
    identity_events: tuple[dict, ...] = field(default_factory=tuple)
    advisory: str | None = None  # D6: non-blocking "someone landed since your last sync" notice


_NO_ORACLE = (
    "no oracle configured -- `sgt land` refuses to advance a shared branch to an "
    "unverified op-set (LAW-G); add `.sgt/oracle.json` and re-run"
)


def _oracle_gate(repo: Path, ideal: Ideal) -> str | None:
    """LAW-G: `None` iff the resulting op-set is oracle-green (safe to advance the shared tip);
    else a human reason the land is refused. A shared tip only ever points at a *verified* op-set,
    so *no oracle configured* also refuses (a green verdict cannot exist, so the tip must not move).
    The oracle runs its tiers against the already-materialized candidate tree (the caller stages the
    union source before gating), recording the verdict keyed to `ideal_key(ideal)` -- so a green
    land's merged ideal is left oracle-green for later verbs, exactly as U9 intends."""
    if load_oracle_config(repo) is None:
        return _NO_ORACLE
    verdict = oracle.run(repo, ideal=ideal)  # runs tiers against the staged candidate source
    if oracle.overall_status(verdict) != "pass":
        return "the resulting op-set is not oracle-green (LAW-G); fix the failure, then `sgt land`"
    return None


def _recover_pending_land(repo: Path, gb: GitBinding) -> None:
    """Roll back a land the previous process crashed out of (R7): if a `land_pending` journal
    survives, its owning `land` died between materializing the candidate tree and the CAS (or
    before clearing it on a clean exit), so the working tree may hold an un-landed candidate.
    Restore it to the journaled pre-land snapshot and clear the journal. Called at the very start
    of every `land`, before the clean-tree precondition, so a crashed land never wedges the next."""
    pending = state.load_json(repo, "land_pending", default=None)
    if pending and pending.get("snapshot"):
        gb.restore_worktree_to(pending["snapshot"])
    if pending:
        state.save_json(repo, "land_pending", {})


# `.sgt/local/` files a non-landing land legitimately keeps, so the rollback below must NOT rewind
# them -- exactly `tests/core/test_land.py::_worktree_state`'s exempt set: the oracle verdict is real
# work worth caching, `land_pending` is the crash journal `land` manages itself, and `lock` is the
# store mutex. Every other local artifact `lens.get`'s mine-on-contact touches (fidelity/sync_cache
# marks, the derived ideal/witness/backfill caches, the op-index sidecar) is derived and
# self-healing, so it must roll back for a land that does not land to leave no trace (R7).
_LAND_KEEPS_LOCAL = frozenset({"oracle.json", "land_pending.json", "lock"})


def _iter_local_caches(repo: Path):
    """The top-level `.sgt/local/` files that participate in a land's transaction. Top-level only,
    so the `.sgt/local/hollow/` op-store subtree (monotone, like `.sgt/ops/`) is left untouched; the
    exempt caches and in-flight temp files are skipped."""
    local = repo / state.SGT_DIR / "local"
    if not local.is_dir():
        return
    for p in local.iterdir():
        if p.is_file() and p.name not in _LAND_KEEPS_LOCAL and not p.name.startswith(".tmp-"):
            yield p


def _snapshot_local_caches(repo: Path) -> dict[str, bytes]:
    """Byte snapshot of the transactional local caches, captured before mine-on-contact touches
    them so every non-landing exit can restore the true pre-land baseline."""
    return {p.name: p.read_bytes() for p in _iter_local_caches(repo)}


def _restore_local_caches(repo: Path, before: dict[str, bytes]) -> None:
    """Roll the transactional local caches back to `before` (R7): rewrite any the land changed,
    delete any it newly created, and re-create any it removed. `restore_worktree_to` only rewinds
    git-*tracked* state, so these gitignored caches -- which a `restore_worktree_to` never sees --
    need their own restore for a land that does not land to leave the tree byte-identical."""
    local = repo / state.SGT_DIR / "local"
    now = {p.name: p for p in _iter_local_caches(repo)}
    for name, p in now.items():
        if name not in before:
            p.unlink()  # appeared during the land -> remove it
        elif p.read_bytes() != before[name]:
            state._atomic_write_text(p, before[name].decode("utf-8"))  # churned -> rewind
    for name, data in before.items():
        if name not in now:
            state._atomic_write_text(local / name, data.decode("utf-8"))  # land removed it -> restore


# Phase 1.2: the reconciled metadata tables (pins/tree/declared/authored/intent/forks) moved off the
# branch tree onto `refs/sgt/state` and are now gitignored, so `restore_worktree_to` -- which only
# rewinds git-*tracked* state -- no longer rolls back a non-landing land's `flush_reconciled_metadata`
# writes the way it used to when they were tracked. These two helpers give that mutable-table surface
# its own snapshot/restore (mirroring `_snapshot_local_caches`), so an R7 non-landing exit again
# leaves the whole `.sgt` surface byte-identical. The content-addressed stores (ops/claims/proposals/
# reviews) stay exempt: they are monotone and append-only, re-added identically on the retry.
def _snapshot_traveling_tables(repo: Path) -> dict[str, bytes]:
    """Byte snapshot of the gitignored traveling tables as they sit before the land touches them."""
    snap: dict[str, bytes] = {}
    for name in _state_ref._TRAVELING_TABLES:
        p = state.path(repo, name)
        if p.is_file():
            snap[name] = p.read_bytes()
    return snap


def _restore_traveling_tables(repo: Path, before: dict[str, bytes]) -> None:
    """Roll the traveling tables back to `before` (R7): rewrite any the land changed, delete any it
    newly created, re-create any it removed -- the git-untracked analogue of `restore_worktree_to`."""
    for name in _state_ref._TRAVELING_TABLES:
        p = state.path(repo, name)
        exists = p.is_file()
        if name in before:
            if not exists or p.read_bytes() != before[name]:
                state._atomic_write_text(p, before[name].decode("utf-8"))  # churned/removed -> rewind
        elif exists:
            p.unlink()  # appeared during the land -> remove it


def land(repo: str | Path, branch: str | None = None, retries: int = 5) -> LandReport:
    repo = Path(repo)
    gb = GitBinding(repo)

    _recover_pending_land(repo, gb)  # undo any crashed prior land before touching the tree (R7)
    # Baseline the gitignored local caches now -- `_recover_pending_land` already rewound any crashed
    # prior land, so this is the true pre-land state every non-landing exit rolls back to (R7).
    local_before = _snapshot_local_caches(repo)
    tables_before = _snapshot_traveling_tables(repo)  # the moved gitignored tables (Phase 1.2)
    lens.get(repo)  # mine-on-contact: absorb local reality first (R9)
    if not gb.is_clean():
        raise lens.DirtyWorkingTreeError(
            "sgt land requires a clean working tree -- commit or stash your changes first"
        )

    # LAW-G with zero mutation: no oracle -> a green verdict cannot exist, so refuse before staging
    # anything (not even the monotone op adds). Pre-checked here so this path leaves no trace at all.
    if load_oracle_config(repo) is None:
        _restore_local_caches(repo, local_before)  # refuse with zero trace -- even the get() caches
        _restore_traveling_tables(repo, tables_before)  # ...and the moved gitignored tables (Phase 1.2)
        return LandReport(branch=branch or "?", landed=False, blocked_reason=_NO_ORACLE)

    if branch is None:
        ref_name = gb.symbolic_ref()
        if ref_name is None:
            raise ValueError("no branch to land -- HEAD is detached; pass a branch name")
        branch = ref_name.rsplit("/", 1)[-1]
    ref = f"refs/heads/{branch}"

    ours = gb.head()
    if ours is None:
        raise ValueError("sgt land requires at least one commit")

    # The pre-land tree is clean (checked above), so `ours` (HEAD) is the exact snapshot to restore
    # to on any non-landing exit (R7). Whether `ref` is the checked-out branch decides two things:
    # a checked-out win leaves HEAD *on* the landed commit (keep the materialized tree, journal the
    # edit for `sgt undo`); a non-checked-out win advanced only the shared ref, so the session's own
    # tree is restored and undo stays scoped to the checked-out ref.
    checked_out = gb.symbolic_ref() == ref
    snapshot = ours
    # Journal the pre-land snapshot so a crash mid-land is recoverable (R7). Cleared on every
    # *normal* exit below; only an exception/crash leaves it, which is exactly when the next land's
    # `_recover_pending_land` should roll the tree back to `snapshot`.
    state.save_json(repo, "land_pending", {"ref": ref, "snapshot": snapshot})

    # D6: pre-flight staleness advisory -- purely informational, never blocks or alters the CAS/
    # retry logic below. If the log's latest landed sha for this branch is not an ancestor of our
    # HEAD, someone landed since our last sync/land here; name it so the session can `sgt sync`
    # before retrying, rather than only discovering it after losing a CAS race.
    advisory = None
    log_entries = _log.read(gb, branch)
    if log_entries:
        latest_landed = log_entries[0].landed_sha
        if not gb.is_ancestor(latest_landed, ours):
            advisory = (
                f"{branch} has landed work since your last sync ({latest_landed[:12]} is not an "
                f"ancestor of HEAD) -- `sgt sync` first to fold it in before landing"
            )

    def _blocked(reason: str, attempt: int, res=None, forks=(), ing=None) -> LandReport:
        gb.restore_worktree_to(snapshot)  # a land that does not land leaves no trace (R7)
        _restore_local_caches(repo, local_before)  # ...and rewind the gitignored local caches too
        _restore_traveling_tables(repo, tables_before)  # ...and the moved gitignored tables (Phase 1.2)
        state.save_json(repo, "land_pending", {})  # normal (non-crash) exit -- clear the journal
        if forks:  # F23: a fork refusal *does* leave one trace, after the rollback -- the committed
            # `.sgt/forks.json` sync's materialize writes, so `sgt forks`/`resolve` see the forks land
            # is refusing on (the "run merge-op / but forks says none" dead end). Written last so the
            # worktree restore above doesn't clobber it; the red-gate/contention paths stay trace-free.
            _materialize.save_fork_records(repo, forks)
            # ...and the ops those records name. `stage_candidate` (the usual op writer) runs only
            # after this check, so without this the record pointed at a tip the store never held --
            # which `_open_fork_records` treats as stale and silently drops, reopening the very dead
            # end F23 closed. Monotone/append-only, so it survives the rollback above by design (R8).
            if ing is not None:
                _materialize.save_fork_ops(repo, ing)
        extra = {} if res is None else dict(
            pin_contradictions=res.pin_contradictions, declared_cycles=res.declared_cycles,
            identity_events=tuple(res.tree_result.get("identity_events", [])),
        )
        return LandReport(
            branch=branch, landed=False, blocked_reason=reason, attempts=attempt, ops_added=0,
            forks=forks, advisory=advisory, **extra,
        )

    for attempt in range(1, retries + 1):
        old = gb.rev_parse(ref)  # the shared tip we race against (None if the branch is new)
        theirs_sha = old if old is not None else ours

        ing = _ingest.ingest(repo, gb, theirs_sha, ours, branch=branch)
        res = _resolve.resolve(repo, ing)

        # A genuine fork blocks the land (unlike sync, which advances the fork-free part): the shared
        # tip is a gated, single-lineage record, so a same-symbol fork must be reconciled with
        # `sgt resolve <symbol>` before it can advance.
        if res.forks:
            sym = res.forks[0][0]
            return _blocked(f"open fork(s) -- run `sgt resolve {sym}`", attempt, res,
                            forks=res.forks, ing=ing)

        # Stage the reconciled *source* only (ops are monotone; metadata waits), so the LAW-G gate
        # runs the oracle against the real candidate tree. No metadata, no ref move yet -- a red gate
        # rolls back with a worktree restore alone.
        _materialize.stage_candidate(repo, gb, ing, res)

        gate = _oracle_gate(repo, res.merged_ideal)
        if gate is not None:
            return _blocked(gate, attempt, res)

        # Green: now flush the reconciled metadata so the landing commit's tree carries it, build the
        # commit off-ref, and CAS the branch onto it. Parents: the branch tip we advance from, plus
        # our own HEAD when it diverges (a real 2-parent merge); deduped and None-dropped so a fresh
        # branch (old is None) roots at our HEAD alone.
        _materialize.flush_reconciled_metadata(repo, gb, theirs_sha, ing, res)
        gb.stage_all()
        tree = gb.write_tree()
        parents = [p for p in dict.fromkeys([old, ours]) if p is not None]
        trailers = format_op_trailers(sorted(res.merged_ideal.op_ids))
        new = gb.commit_tree(tree, parents, f"sgt land: {branch}", trailers=trailers)

        if gb.update_ref_cas(ref, new, old):
            # Won the race: the ref now durably points at `new`. Clear the crash journal *first* so
            # a crash in the tiny post-CAS window can't make the next land roll back a landed commit.
            state.save_json(repo, "land_pending", {})
            # Persist the durable ideal table/witness for the *target* branch only after the CAS,
            # journaling the edit (for `sgt undo`) only when landing the checked-out ref.
            # `record_exclusions=False` (§E): `flush_reconciled_metadata` above already persisted the
            # merged exclusion OR-Set; re-minting tags from this ideal's delta would break convergence.
            lens.record_ideal(repo, res.merged_ideal, new, ref_key=ref, journal=checked_out,
                              record_exclusions=False)
            _log.append(gb, branch, new, res.merged_ideal.op_ids)  # D1: best-effort, never raises
            # Phase 1.2 push ordering (§D): publish the landed state (ops + reconciled tables) onto
            # `refs/sgt/state` so the branch tip's `Sgt-Op:` trailers reference ops that are durable
            # off the branch tree. Runs on BOTH paths, before the shared-out worktree restore below --
            # the landed op-set is what a teammate reads back either way. `land` advances a *local*
            # shared ref (no remote push), so this is `publish_from_local`, not `publish_and_push`.
            _state_ref.publish_from_local(gb, repo)
            if not checked_out:
                gb.restore_worktree_to(snapshot)  # advanced only the shared ref; restore our tree
                # A shared-out land (mirrors the `journal=checked_out` guard above): record it in the
                # unified operation log (U8/KTD6) for provenance under the session's own ref, but its
                # inverse is refused -- it already left this clone, so `undo` never rewinds it. A
                # checked-out land instead journals an undoable `ideal_edit` via `record_ideal` above.
                from sgt.core import oplog
                try:
                    oplog.append(repo, {"kind": "land", "branch": branch, "ops": sorted(res.merged_ideal.op_ids)})
                except Exception:  # noqa: BLE001 -- provenance logging is never load-bearing for the land
                    pass
            ops_added = len(res.merged_ideal.op_ids - ing.theirs_ideal_ids)
            return LandReport(
                branch=branch, landed=True, land_sha=new, ops_added=ops_added, attempts=attempt,
                pin_contradictions=res.pin_contradictions, declared_cycles=res.declared_cycles,
                identity_events=tuple(res.tree_result.get("identity_events", [])),
                advisory=advisory,
            )
        # CAS lost: another session advanced `ref` off `old`. Roll back this attempt's staged tree
        # and metadata, then re-loop to re-ingest against the now-moved tip -- the re-union retry.
        gb.restore_worktree_to(snapshot)

    return _blocked(
        f"persistent contention -- branch moved on every one of {retries} attempts", retries,
    )


def plan_land(repo: str | Path, branch: str | None = None) -> LandPlan:
    """Dry-run a `land` (D4): run `ingest -> resolve` against the branch tip to compute what the CAS
    *would* advance the shared branch by, then roll the transactional local caches back so the
    preview leaves no trace (R7). Deliberately skips the two mutating/expensive steps `land` does --
    `stage_candidate` + the oracle gate, and the ref CAS -- so a feedforward can show the
    consequence before the user commits to running the tests and moving the shared tip. The oracle
    verdict is *not* computed here; `oracle_configured` only reports whether one exists (LAW-G will
    refuse a land with none). Mirrors `land`'s own `ops_added`/`advisory` computation so the preview
    predicts the report."""
    repo = Path(repo)
    gb = GitBinding(repo)

    local_before = _snapshot_local_caches(repo)
    try:
        lens.get(repo)  # mine-on-contact: preview against local reality, exactly as `land` does
        if not gb.is_clean():
            return LandPlan(branch=branch or "?", clean=False,
                            error="working tree not clean -- commit or stash your changes first")

        if branch is None:
            ref_name = gb.symbolic_ref()
            if ref_name is None:
                return LandPlan(branch="?", clean=False,
                                error="HEAD is detached -- pass a branch name to land onto")
            branch = ref_name.rsplit("/", 1)[-1]
        ref = f"refs/heads/{branch}"

        ours = gb.head()
        if ours is None:
            return LandPlan(branch=branch, clean=False, error="no commit to land")

        old = gb.rev_parse(ref)
        theirs_sha = old if old is not None else ours

        ing = _ingest.ingest(repo, gb, theirs_sha, ours, branch=branch)
        res = _resolve.resolve(repo, ing)
        ops_added = len(res.merged_ideal.op_ids - ing.theirs_ideal_ids)

        advisory = None
        log_entries = _log.read(gb, branch)
        if log_entries and not gb.is_ancestor(log_entries[0].landed_sha, ours):
            advisory = (
                f"{branch} has landed work since your last sync ({log_entries[0].landed_sha[:12]} "
                f"is not an ancestor of HEAD) -- `sgt sync` first to fold it in before landing"
            )

        return LandPlan(
            branch=branch, ops_added=ops_added, forks=res.forks,
            pin_contradictions=res.pin_contradictions, declared_cycles=res.declared_cycles,
            oracle_configured=load_oracle_config(repo) is not None, advisory=advisory,
        )
    finally:
        _restore_local_caches(repo, local_before)
