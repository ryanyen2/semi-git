"""`ugh sync`: bring a teammate's work in through git without a textual merge (plan U15, R19/AE4).

Source files are *derived* (`code(I)`), so sync never merges them textually -- it fetches the
remote branch, unions the op store (near-free: `Store.add` unions provenance on any
content-address collision, R8), reconciles the ideal, merges the names three-way per op, and
then re-folds the working tree from the union. Footprint-disjoint work merges with zero
interaction; a same-symbol chain fork is *surfaced* (with the `ugh resolve` remedy) rather than
silently resolved -- the ADR's "the only possible conflict is chain
divergence" holds at sync time exactly as it does for a single clone's ideal.

Decomposed (plan U19, D4) into four pure stages so the same boundaries can be reused:
adoption-on-contact is `ingest` alone. `sync()` is their composition; only `materialize` (reached fork-free) touches disk, so a fork leaves nothing to roll
back -- there is no real `git merge` to abort, unlike the pre-U19 pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ugh import names
from ugh.store.gitbind import GitBinding

from . import fetch as _fetch
from . import ingest as _ingest
from . import materialize as _materialize
from . import resolve as _resolve
from . import state_ref as _state_ref
from .ingest import MinerVersionMismatch  # re-exported: the CLI catches it distinctly (C6)
from .caches import _restore_local_caches, _snapshot_local_caches

__all__ = ["SyncReport", "SyncPlan", "sync", "plan_sync", "MinerVersionMismatch"]


@dataclass(frozen=True)
class SyncReport:
    remote: str
    branch: str
    merged: bool  # a *clean* merge landed (no open fork); False means up-to-date, or a fork was
    # surfaced (the fork-free part still merged and the fork is recorded -- see `forks`)
    message: str
    fetched_sha: str | None = None
    merge_sha: str | None = None
    ops_added: int = 0
    forks: tuple[tuple[str, str, str], ...] = ()
    # (op id, ours, theirs) for each op both sides relabelled differently; ours was kept.
    name_conflicts: tuple[tuple[str, str, str], ...] = ()
    # U7/R12: how the merge-base ideal was recovered for three-way resolve, and how theirs' tip was
    # -- `trailers` | `mined` | `none`. `base_recovery == "none"` means the
    # base degraded to ∅ (union semantics); `theirs_recovery == "none"` is the tip footgun (ops but
    # no witnessed provenance). Either warrants a loud warning -- an unwitnessed claim was refused.
    base_recovery: str = "none"
    theirs_recovery: str = "mined"


@dataclass(frozen=True)
class SyncPlan:
    """The dry-run consequence of a `sync` (plan U19/D4): what folding in the teammate's branch
    *would* bring -- computed with `fetch -> ingest -> resolve` (no `materialize`, so the working
    tree and local branch never move), then rolled back to leave no trace (R7). A fork does not
    block a sync -- the fork-free part still merges and the fork surfaces as state -- so `forks`
    here is *what would surface*, not a blocker. `up_to_date` short-circuits when the
    fetch finds nothing new."""

    remote: str
    branch: str
    up_to_date: bool = False
    ops_added: int = 0
    forks: tuple[tuple[str, str, str], ...] = ()
    name_conflicts: tuple[tuple[str, str, str], ...] = ()
    base_recovery: str = "none"
    theirs_recovery: str = "mined"


def plan_sync(repo: str | Path, remote: str | None = None, branch: str | None = None) -> SyncPlan:
    """Dry-run a `sync` (D4): fetch the teammate's branch and run `ingest -> resolve` to compute what
    the merge *would* bring in -- incoming op count, any same-symbol fork that would surface, name
    conflicts, and the recovery modes -- then roll the transactional local caches back so
    the preview leaves no trace (R7). Skips `materialize` entirely (the only stage that moves the
    working tree or the local branch), so a feedforward pane can show the consequence before the
    merge lands. Mirrors `sync`'s own `ops_added` so the preview predicts the report."""
    repo = Path(repo)
    gb = GitBinding(repo)

    fetched = _fetch.fetch(repo, gb, remote, branch)
    if fetched.up_to_date:
        return SyncPlan(remote=fetched.remote, branch=fetched.branch, up_to_date=True)

    local_before = _snapshot_local_caches(repo)
    try:
        # Phase 1.2: theirs' ops and tables are read from the fetched `refs/ugh/state` tip
        # (`theirs_state_sha`) when transport supplied one; `None` falls back to theirs' branch tree
        # (pre-1.2 / fresh remote). See `ingest` for why the trailer ideal recovery still keys off
        # `theirs_sha` (trailers live in the commit message, untouched by the move).
        ing = _ingest.ingest(
            repo, gb, fetched.theirs_sha, fetched.ours_sha,
            theirs_state_sha=fetched.theirs_state_sha,
        )
        res = _resolve.resolve(repo, ing)
        return SyncPlan(
            remote=fetched.remote, branch=fetched.branch, ops_added=ing.ops_added,
            forks=res.forks, name_conflicts=res.name_conflicts,
            base_recovery=ing.base_recovery, theirs_recovery=ing.theirs_recovery,
        )
    finally:
        _restore_local_caches(repo, local_before)


def _merge_state_only(repo: Path, gb: GitBinding, theirs_state: str | None) -> tuple:
    """The branch is up to date but theirs' state ref may have moved (a `name` or `rename` with no
    commit): fold it into ours -- names three-way, their op blobs -- and return the name
    conflicts."""
    ours_state = _state_ref.read_sha(gb)
    if theirs_state is None or (ours_state and gb.is_ancestor(theirs_state, ours_state)):
        return ()
    base = gb.merge_base(ours_state, theirs_state) if ours_state else None
    _merged, conflicts = names.merge(names.at(gb, base), names.load(repo), names.at(gb, theirs_state))
    _state_ref._reconcile_remote_into_ref(gb, repo, ours_state, theirs_state)
    return tuple(conflicts)


def sync(repo: str | Path, remote: str | None = None, branch: str | None = None) -> SyncReport:
    repo = Path(repo)
    gb = GitBinding(repo)

    fetched = _fetch.fetch(repo, gb, remote, branch)
    if fetched.up_to_date:
        return SyncReport(
            remote=fetched.remote, branch=fetched.branch, merged=False,
            fetched_sha=fetched.theirs_sha, message="already up to date",
            name_conflicts=_merge_state_only(repo, gb, fetched.theirs_state_sha),
        )

    # Phase 1.2: theirs' ops/tables come from the fetched `refs/ugh/state` tip (`theirs_state_sha`);
    # `None` falls back to theirs' branch tree (pre-1.2 / fresh remote). See `plan_sync`.
    ing = _ingest.ingest(
        repo, gb, fetched.theirs_sha, fetched.ours_sha,
        theirs_state_sha=fetched.theirs_state_sha,
    )
    res = _resolve.resolve(repo, ing)

    # Divergence-as-state (D5/C4): a fork no longer aborts. `materialize` always runs -- it lands
    # the fork-free part (advancing the branch by it) *and* records any open forks as durable,
    # committed state. `merged` means a *clean* merge with no open fork; a fork makes it False
    # (attention needed) though the fork-free work still landed and the fork is now shared.
    merge_sha = _materialize.materialize(
        repo, gb, fetched.remote, fetched.branch, fetched.theirs_sha, ing, res,
    )

    # Phase 1.2: publish the reconciled local mirror onto `refs/ugh/state` at sync's transaction
    # boundary. The merge above unioned theirs' ops/tables into our on-disk mirror; this rebuilds
    # the state-ref tree from it and advances the ref (off theirs' fetched tip -> a fast-forward
    # push later). Raises `StateRefError` on a lost CAS -- the ref is correctness-bearing.
    _state_ref.publish_from_local(gb, repo, merged=fetched.theirs_state_sha)

    if res.forks:
        remedies = "; ".join(f"ugh resolve {_sym}" for _sym, a, b in res.forks)
        message = (
            f"merged fork-free work; {len(res.forks)} open fork(s) -- the forked symbol(s) sit at "
            f"the common ancestor until resolved with: {remedies}"
        )
    else:
        message = "merged"

    # R12: an unwitnessed base or a lost-provenance tip degraded to a set we couldn't trust, so the
    # union fell back to weaker semantics. Name it loudly in the message -- a silent degrade reads
    # as a clean merge when it isn't.
    if ing.base_recovery == "none":
        message += (" -- base recovery: none (no witnessed merge-base; using union semantics, "
                    "which cannot delete work removed on one side)")
    if ing.theirs_recovery == "none":
        message += (" -- theirs' tip carries ugh ops but no witnessed trailers/record; re-mine on "
                    "their side (`ugh log`) or restore the `Sgt-Op:` trailers, then sync again")

    return SyncReport(
        remote=fetched.remote, branch=fetched.branch, merged=not res.forks,
        fetched_sha=fetched.theirs_sha, merge_sha=merge_sha, ops_added=ing.ops_added,
        forks=res.forks,
        name_conflicts=res.name_conflicts,
        base_recovery=ing.base_recovery, theirs_recovery=ing.theirs_recovery,
        message=message,
    )
