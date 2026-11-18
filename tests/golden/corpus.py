"""Deterministic, offline corpus for the characterization golden master.

The read surface (`log_view`/`state_view`/`status_view`/`show_view`) reads a mined git repo, so it
needs git-repo fixtures. We reuse the deterministic, pinned-SHA fixtures the round-trip law harness
already builds (`tests/laws/corpus.py`) -- same discipline (no network/wall-clock) -- so these
snapshots are byte-stable across runs. `test_golden.py` snapshots the views these builders produce
and fails on drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from ugh import api
from tests.laws import corpus as _kernel_git_corpus


class KernelCase(NamedTuple):
    laws_name: str  # which tests/laws/corpus.py fixture to build and mine
    diff_refs: tuple[str, str] | None  # (ref_a, ref_b) to mine both sides of a divergence, else None


KERNEL_CORPUS: dict[str, KernelCase] = {
    "mixed_coverage": KernelCase("mixed_coverage", None),
    "diverged_chain": KernelCase("diverged_chain", ("main", "release")),
}


def capture_kernel_views(name: str, root: str) -> dict:
    """Build a deterministic git-repo kernel fixture, mine it (`get`), and capture the views: the
    saves, the current ideal, the status, and `show` of the newest save."""
    from ugh.core.lens import get
    from ugh.store.gitbind import GitBinding

    case = KERNEL_CORPUS[name]
    repo = _kernel_git_corpus.CORPUS[case.laws_name].build(Path(root))
    if case.diff_refs:
        for ref in case.diff_refs:  # mine both branches so the store holds both sides' ops
            _kernel_git_corpus.checkout(repo, ref)
            get(repo)
    else:
        get(repo)

    views: dict = {
        "log_view": api.log_view(repo),
        "state_view": api.state_view(repo),
        "status_view": api.status_view(repo),
        "show_view": api.show_view(repo, GitBinding(repo).head()[:7]),
    }
    return views
