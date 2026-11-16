"""Sync stage 1 -- transport and preconditions (plan U19, D4).

Absorb any local reality first (R9), refuse a dirty tree (a clean tree is what an explicit tree
construction needs anyway), resolve the remote/branch to fetch, and pull `theirs`. `theirs`
already being an ancestor of `ours` (nothing new, or we're already ahead) is what makes a second
`sync` idempotent -- surfaced here as `up_to_date` so the composition can short-circuit before any
ingest touches disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ugh.core import lens
from ugh.store.gitbind import GitBinding, GitError

from . import state_ref as _state_ref


@dataclass(frozen=True)
class Fetched:
    remote: str
    branch: str
    theirs_sha: str
    ours_sha: str
    up_to_date: bool
    # Phase 1.2: the fetched `refs/ugh/state` tip -- theirs' committed ops/tables live here off the
    # branch tree. `None` when the remote has no such ref (a pre-1.2 remote / fresh clone), which
    # signals `ingest` to fall back to reading theirs' branch tree (still carrying that state during
    # the transition; the permanent bootstrap when the ref never existed).
    theirs_state_sha: str | None = None


def fetch(repo: Path, gb: GitBinding, remote: str | None, branch: str | None) -> Fetched:
    lens.get(repo)  # absorb local reality first (R9)
    if not gb.is_clean():
        raise lens.DirtyWorkingTreeError(
            "ugh sync requires a clean working tree -- `ugh save` or commit first"
        )

    remote = remote or gb.default_remote()
    branch = branch or gb.default_branch()
    if branch is None:
        raise ValueError("no branch to sync -- HEAD has no upstream and isn't on a named branch")

    ours_sha = gb.head()
    if ours_sha is None:
        raise ValueError("ugh sync requires at least one commit")

    gb.fetch(remote, branch)
    theirs_sha = gb.rev_parse("FETCH_HEAD")
    if theirs_sha is None:
        raise GitError(f"fetch of {remote}/{branch} produced no FETCH_HEAD")

    # Phase 1.2: fetch the repo-global `refs/ugh/state` into a *scratch* ref (force-updated each
    # fetch), leaving our authoritative local `refs/ugh/state` untouched -- so even the dry-run
    # `plan_sync` leaves no trace on it (R7), and our own publish history is never clobbered. A remote
    # without the ref (pre-1.2 / fresh) yields nothing, so `theirs_state_sha` stays `None` and
    # `ingest` falls back to theirs' branch tree (still carrying that state during the transition).
    # `ingest` reads theirs' ops and tables straight from the ref tree at `theirs_state_sha`;
    # theirs' ops land on disk through `materialize`'s `Store.add`.
    got_state = gb.fetch_ref(remote, f"+{_state_ref.STATE_REF}:{_state_ref.FETCH_STATE_REF}")
    theirs_state_sha = gb.rev_parse(_state_ref.FETCH_STATE_REF) if got_state else None

    return Fetched(
        remote=remote,
        branch=branch,
        theirs_sha=theirs_sha,
        ours_sha=ours_sha,
        up_to_date=theirs_sha in set(gb.commit_shas(ours_sha)),
        theirs_state_sha=theirs_state_sha,
    )
