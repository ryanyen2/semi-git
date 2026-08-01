"""Shared git-plumbing fixtures for the two-clone sync/migrate suites.

These three helpers -- a bare remote, a working clone, a branch push -- were copy-pasted verbatim
into every two-clone test file. They are hoisted here so the `refs/sgt/state` transport added in
Phase 1.2 (a state-ref push alongside the branch push in `_push`, a state-ref fetch in `_clone`)
lands in exactly ONE place rather than being added to a dozen copies and silently forgotten in one.
`tests` is a package, so a test module imports these with `from tests.conftest import ...` and gets
the same module object pytest already loaded.

The higher-level fixtures (`_two_clones`, `_edit_and_commit`) stay local to each suite: their
signatures and return shapes genuinely differ (sync returns `(a, b)` and seeds arbitrary source;
migrate returns `(remote, a, b)` and synthesizes a v2 store), and they are not where state-ref
transport is added -- they call these shared primitives.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from sgt.core.sync import state_ref as _state_ref
from sgt.store.gitbind import GitBinding


def _init_bare(root: Path) -> Path:
    remote = root / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    return remote


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    GitBinding(dest).init()  # repo-scope identity, matches every two-clone fixture
    # Phase 1.2: `git clone` copies refs/heads + refs/tags but never refs/sgt/*, so pull the shared
    # state ref (if the remote has one) and materialize it into the fresh clone's local mirror --
    # the fresh-clone bootstrap. Best-effort: a pre-1.2 remote has no such ref and this no-ops.
    gb = GitBinding(dest)
    if gb.fetch_ref("origin", f"+{_state_ref.STATE_REF}:{_state_ref.STATE_REF}"):
        _state_ref.materialize_into_local(gb, dest)
    return dest


def _push(repo: Path, branch: str = "main") -> None:
    # Phase 1.2: publish this clone's local mirror onto `refs/sgt/state` and push it BEFORE the
    # branch, reconciling a non-fast-forward against a teammate's concurrent publish as a CRDT merge
    # -- exactly what the CLI's `sgt push` does via `publish_and_push`. This rebuilds the ref from
    # local first (so table mutations made through raw `GitBinding.commit_all` -- pins/declared edges
    # seeded directly in tests, never through a verb that publishes -- travel), and a branch commit's
    # `Sgt-Op:` trailers name ops that live only on the ref, so the ref must be durable first. A plain
    # best-effort push here silently drops a *merged* state tree on non-ff (each clone's state-ref
    # history is independent), which breaks cross-clone convergence (LAW-U); reconciling is the
    # faithful production behavior.
    _state_ref.publish_and_push(GitBinding(repo), repo, "origin")
    subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "origin", branch], check=True, capture_output=True
    )
