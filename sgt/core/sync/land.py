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

from sgt.config import load_oracle_config
from sgt.core import lens, oracle
from sgt.core.ideal import Ideal
from sgt.lens.pins import Contradiction
from sgt.store.gitbind import GitBinding, format_op_trailers

from . import ingest as _ingest
from . import materialize as _materialize
from . import resolve as _resolve

__all__ = ["LandReport", "land"]


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


def _oracle_gate(repo: Path, ideal: Ideal) -> str | None:
    """LAW-G: `None` iff the resulting op-set is oracle-green (safe to advance the shared tip);
    else a human reason the land is refused. A shared tip only ever points at a *verified* op-set,
    so *no oracle configured* also refuses (a green verdict cannot exist, so the tip must not move).
    The oracle runs its tiers against the already-materialized candidate tree (the caller persists
    the union before gating), keyed to `ideal_key(ideal)` exactly as U9 intends."""
    if load_oracle_config(repo) is None:
        return ("no oracle configured -- `sgt land` refuses to advance a shared branch to an "
                "unverified op-set (LAW-G); add `.sgt/oracle.json` and re-run")
    verdict = oracle.run(repo, ideal=ideal)  # runs tiers against the materialized candidate
    if oracle.overall_status(verdict) != "pass":
        return "the resulting op-set is not oracle-green (LAW-G); fix the failure, then `sgt land`"
    return None


def land(repo: str | Path, branch: str | None = None, retries: int = 5) -> LandReport:
    repo = Path(repo)
    gb = GitBinding(repo)

    lens.get(repo)  # mine-on-contact: absorb local reality first (R9)
    if not gb.is_clean():
        raise lens.DirtyWorkingTreeError(
            "sgt land requires a clean working tree -- `sgt put` or commit first"
        )

    if branch is None:
        ref_name = gb.symbolic_ref()
        if ref_name is None:
            raise ValueError("no branch to land -- HEAD is detached; pass a branch name")
        branch = ref_name.rsplit("/", 1)[-1]
    ref = f"refs/heads/{branch}"

    ours = gb.head()
    if ours is None:
        raise ValueError("sgt land requires at least one commit")

    for attempt in range(1, retries + 1):
        old = gb.rev_parse(ref)  # the shared tip we race against (None if the branch is new)
        theirs_sha = old if old is not None else ours

        ing = _ingest.ingest(repo, gb, theirs_sha, ours)
        res = _resolve.resolve(repo, ing)

        # A genuine fork blocks the land (unlike sync, which advances the fork-free part): the shared
        # tip is a gated, single-lineage record, so a same-symbol fork must be reconciled with
        # `sgt merge-op` before it can advance. Pure -- nothing is persisted yet, nothing to roll back.
        if res.forks:
            return LandReport(
                branch=branch, landed=False, blocked_reason="open fork(s) -- run `sgt merge-op`",
                forks=res.forks, attempts=attempt, ops_added=0,
                pin_contradictions=res.pin_contradictions, declared_cycles=res.declared_cycles,
                identity_events=tuple(res.tree_result.get("identity_events", [])),
            )

        # Persist the reconciled union into this session's own working tree/index (the same
        # construction sync's `materialize` uses), so the LAW-G gate runs the oracle against the
        # actual candidate tree. No ref has moved yet, so a red gate leaves nothing to undo.
        _materialize.persist_reconciled(repo, gb, theirs_sha, ing, res)

        gate = _oracle_gate(repo, res.merged_ideal)
        if gate is not None:
            return LandReport(
                branch=branch, landed=False, blocked_reason=gate, attempts=attempt, ops_added=0,
                pin_contradictions=res.pin_contradictions, declared_cycles=res.declared_cycles,
                identity_events=tuple(res.tree_result.get("identity_events", [])),
            )

        # Build the landing commit off-ref, then CAS the branch onto it. Parents: the branch tip we
        # advance from, plus our own HEAD when it diverges (a real 2-parent merge); deduped and
        # None-dropped so a fresh branch (old is None) roots at our HEAD alone.
        gb.stage_all()
        tree = gb.write_tree()
        parents = [p for p in dict.fromkeys([old, ours]) if p is not None]
        trailers = format_op_trailers(sorted(res.merged_ideal.op_ids))
        new = gb.commit_tree(tree, parents, f"sgt land: {branch}", trailers=trailers)

        if gb.update_ref_cas(ref, new, old):
            lens.record_ideal(repo, res.merged_ideal, new)
            ops_added = len(res.merged_ideal.op_ids - ing.theirs_ideal_ids)
            return LandReport(
                branch=branch, landed=True, land_sha=new, ops_added=ops_added, attempts=attempt,
                pin_contradictions=res.pin_contradictions, declared_cycles=res.declared_cycles,
                identity_events=tuple(res.tree_result.get("identity_events", [])),
            )
        # CAS lost: another session advanced `ref` off `old`. Re-loop and re-ingest against the
        # now-moved tip -- the re-union retry.

    return LandReport(
        branch=branch, landed=False,
        blocked_reason=f"persistent contention -- branch moved on every one of {retries} attempts",
        attempts=retries,
    )
