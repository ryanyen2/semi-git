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

from sgt.core import lens
from sgt.store.gitbind import GitBinding, GitError

from . import log as _log
from . import state_ref as _state_ref


@dataclass(frozen=True)
class Fetched:
    remote: str
    branch: str
    theirs_sha: str
    ours_sha: str
    up_to_date: bool
    # Phase 1.2: the fetched `refs/sgt/state` tip -- theirs' committed ops/tables live here off the
    # branch tree. `None` when the remote has no such ref (a pre-1.2 remote / fresh clone), which
    # signals `ingest` to fall back to reading theirs' branch tree (still carrying that state during
    # the transition; the permanent bootstrap when the ref never existed).
    theirs_state_sha: str | None = None


def fetch(repo: Path, gb: GitBinding, remote: str | None, branch: str | None) -> Fetched:
    lens.get(repo)  # absorb local reality first (R9)
    if not gb.is_clean():
        raise lens.DirtyWorkingTreeError(
            "sgt sync requires a clean working tree -- `sgt save` or commit first"
        )

    remote = remote or gb.default_remote()
    branch = branch or gb.default_branch()
    if branch is None:
        raise ValueError("no branch to sync -- HEAD has no upstream and isn't on a named branch")

    ours_sha = gb.head()
    if ours_sha is None:
        raise ValueError("sgt sync requires at least one commit")

    gb.fetch(remote, branch)
    theirs_sha = gb.rev_parse("FETCH_HEAD")
    if theirs_sha is None:
        raise GitError(f"fetch of {remote}/{branch} produced no FETCH_HEAD")

    # D1: best-effort secondary fetch of the land log ref, so base recovery can use it on this
    # clone too. An older remote that has never pushed the ref simply yields nothing to recover.
    log_ref = _log.log_ref(branch)
    gb.fetch_ref(remote, f"{log_ref}:{log_ref}")

    # Phase 1.2: fetch the repo-global `refs/sgt/state` into a *scratch* ref (force-updated each
    # fetch), leaving our authoritative local `refs/sgt/state` untouched -- so even the dry-run
    # `plan_sync` leaves no trace on it (R7), and our own publish history is never clobbered. A remote
    # without the ref (pre-1.2 / fresh) yields nothing, so `theirs_state_sha` stays `None` and
    # `ingest` falls back to theirs' branch tree (still carrying that state during the transition).
    #
    # We deliberately do NOT `materialize_into_local` here: `ingest` reads theirs' ops and tables
    # straight from the ref tree at `theirs_state_sha`, while reading *ours* from the on-disk mirror.
    # Materializing theirs' tables in would overwrite our live local tables (ideal/tree/pins/...) and
    # collapse the two sides `ingest` must reconcile. Theirs' ops land on disk the normal way, through
    # `materialize`'s `Store.add`. (The fresh-clone bootstrap in `_clone` *does* materialize -- there
    # is no live local state there to clobber.)
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
