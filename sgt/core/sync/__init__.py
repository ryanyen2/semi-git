"""`sgt sync`: bring a teammate's work in through git without a textual merge (plan U15, R19/AE4).

Source files are *derived* (`code(I)`), so sync never merges them textually -- it fetches the
remote branch, unions the op store (near-free: `Store.add` unions provenance on any
content-address collision, R8), reconciles the ideal, pins, declared edges, and feature tree, and
then re-folds the working tree from the union. Footprint-disjoint work merges with zero
interaction; a same-symbol chain fork is *surfaced* (with the exact `sgt merge-op`/`sgt pin`
remedy) rather than silently resolved -- the ADR's "the only possible conflict is chain
divergence" holds at sync time exactly as it does for a single clone's ideal.

Decomposed (plan U19, D4) into four pure stages so the same boundaries can be reused: `land` (U23)
is `ingest -> resolve -> materialize` over a local source, `propose` validation (U24) is
`ingest -> resolve` as a dry run, and adoption-on-contact is `ingest` alone. `sync()` is their
composition; only `materialize` (reached fork-free) touches disk, so a fork leaves nothing to roll
back -- there is no real `git merge` to abort, unlike the pre-U19 pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sgt.lens.pins import Contradiction
from sgt.store.gitbind import GitBinding

from . import fetch as _fetch
from . import ingest as _ingest
from . import materialize as _materialize
from . import resolve as _resolve
from .ingest import MinerVersionMismatch  # re-exported: the CLI catches it distinctly (C6)
from .land import LandReport, land  # SYNC-2: the CAS-gated shared-branch advance (U23)

__all__ = ["SyncReport", "sync", "MinerVersionMismatch", "LandReport", "land"]


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
    pin_contradictions: tuple[Contradiction, ...] = ()
    declared_cycles: tuple[tuple[str, str], ...] = ()
    identity_events: tuple[dict, ...] = field(default_factory=tuple)
    # U7/R12: how the merge-base ideal was recovered for three-way resolve, and how theirs' tip was
    # -- `trailers` | `ideal-record` | `mined` | `none`. `base_recovery == "none"` means the base
    # degraded to ∅ (union semantics); `theirs_recovery == "none"` is the tip footgun (ops but no
    # witnessed provenance). Either warrants a loud warning -- an unwitnessed claim was refused.
    base_recovery: str = "none"
    theirs_recovery: str = "mined"


def sync(repo: str | Path, remote: str | None = None, branch: str | None = None) -> SyncReport:
    repo = Path(repo)
    gb = GitBinding(repo)

    fetched = _fetch.fetch(repo, gb, remote, branch)
    if fetched.up_to_date:
        return SyncReport(
            remote=fetched.remote, branch=fetched.branch, merged=False,
            fetched_sha=fetched.theirs_sha, message="already up to date",
        )

    ing = _ingest.ingest(repo, gb, fetched.theirs_sha, fetched.ours_sha)
    res = _resolve.resolve(repo, ing)

    # Divergence-as-state (D5/C4): a fork no longer aborts. `materialize` always runs -- it lands
    # the fork-free part (advancing the branch by it) *and* records any open forks as durable,
    # committed state. `merged` means a *clean* merge with no open fork; a fork makes it False
    # (attention needed) though the fork-free work still landed and the fork is now shared.
    merge_sha = _materialize.materialize(
        repo, gb, fetched.remote, fetched.branch, fetched.theirs_sha, ing, res
    )

    if res.forks:
        remedies = "; ".join(f"sgt merge-op {a[:8]} {b[:8]}" for _sym, a, b in res.forks)
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
        message += (" -- theirs' tip carries sgt ops but no witnessed trailers/record; re-mine on "
                    "their side (`sgt log`) or restore the `Sgt-Op:` trailers, then sync again")

    return SyncReport(
        remote=fetched.remote, branch=fetched.branch, merged=not res.forks,
        fetched_sha=fetched.theirs_sha, merge_sha=merge_sha, ops_added=ing.ops_added,
        forks=res.forks,
        pin_contradictions=res.pin_contradictions, declared_cycles=res.declared_cycles,
        identity_events=tuple(res.tree_result.get("identity_events", [])),
        base_recovery=ing.base_recovery, theirs_recovery=ing.theirs_recovery,
        message=message,
    )
