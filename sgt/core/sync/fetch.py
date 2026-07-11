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


@dataclass(frozen=True)
class Fetched:
    remote: str
    branch: str
    theirs_sha: str
    ours_sha: str
    up_to_date: bool


def fetch(repo: Path, gb: GitBinding, remote: str | None, branch: str | None) -> Fetched:
    lens.get(repo)  # absorb local reality first (R9)
    if not gb.is_clean():
        raise lens.DirtyWorkingTreeError(
            "sgt sync requires a clean working tree -- `sgt put` or commit first"
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

    return Fetched(
        remote=remote,
        branch=branch,
        theirs_sha=theirs_sha,
        ours_sha=ours_sha,
        up_to_date=theirs_sha in set(gb.commit_shas(ours_sha)),
    )
