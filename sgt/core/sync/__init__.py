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


@dataclass(frozen=True)
class SyncReport:
    remote: str
    branch: str
    merged: bool  # a merge commit landed; False means nothing new, or a fork was surfaced
    message: str
    fetched_sha: str | None = None
    merge_sha: str | None = None
    ops_added: int = 0
    forks: tuple[tuple[str, str, str], ...] = ()
    pin_contradictions: tuple[Contradiction, ...] = ()
    declared_cycles: tuple[tuple[str, str], ...] = ()
    identity_events: tuple[dict, ...] = field(default_factory=tuple)


def sync(repo: str | Path, remote: str | None = None, branch: str | None = None) -> SyncReport:
    repo = Path(repo)
    gb = GitBinding(repo)

    fetched = _fetch.fetch(repo, gb, remote, branch)
    if fetched.up_to_date:
        return SyncReport(
            remote=fetched.remote, branch=fetched.branch, merged=False,
            fetched_sha=fetched.theirs_sha, message="already up to date",
        )

    ing = _ingest.ingest(repo, gb, fetched.theirs_sha)
    res = _resolve.resolve(repo, ing)

    if res.forks:
        remedies = "; ".join(f"sgt merge-op {a[:8]} {b[:8]}" for _sym, a, b in res.forks)
        return SyncReport(
            remote=fetched.remote, branch=fetched.branch, merged=False,
            fetched_sha=fetched.theirs_sha, forks=res.forks,
            message=f"fork(s) detected, not merged -- resolve with: {remedies}",
        )

    merge_sha = _materialize.materialize(
        repo, gb, fetched.remote, fetched.branch, fetched.theirs_sha, ing, res
    )
    return SyncReport(
        remote=fetched.remote, branch=fetched.branch, merged=True,
        fetched_sha=fetched.theirs_sha, merge_sha=merge_sha, ops_added=ing.ops_added,
        pin_contradictions=res.pin_contradictions, declared_cycles=res.declared_cycles,
        identity_events=tuple(res.tree_result.get("identity_events", [])),
        message="merged",
    )
