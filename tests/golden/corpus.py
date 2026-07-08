"""Deterministic, offline corpus for the operation-ideal kernel's characterization golden master.

The kernel's read surface (`oplog_view`/`state_view`/`ideal_diff_view`) reads a mined git repo,
not an in-memory object, so it needs git-repo fixtures. We reuse the deterministic, pinned-SHA
fixtures the round-trip law harness already builds (`tests/laws/corpus.py`) -- same discipline (no
LLM/network/wall-clock) -- so these snapshots are byte-stable across runs. `test_golden.py`
snapshots the views these builders produce and fails on drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from sgt import api
from tests.laws import corpus as _kernel_git_corpus


class KernelCase(NamedTuple):
    laws_name: str  # which tests/laws/corpus.py fixture to build and mine
    diff_refs: tuple[str, str] | None  # (ref_a, ref_b) to also snapshot ideal_diff_view, else None


KERNEL_CORPUS: dict[str, KernelCase] = {
    "mixed_coverage": KernelCase("mixed_coverage", None),
    "diverged_chain": KernelCase("diverged_chain", ("main", "release")),
}


def capture_kernel_views(name: str, root: str) -> dict:
    """Build a deterministic git-repo kernel fixture, mine it (`get`), and capture the U7 kernel
    views: the op DAG, the current ideal, and -- for a diverged fixture -- the ideal-vs-ideal
    semantic diff between its two branches."""
    from sgt.core.lens import get

    case = KERNEL_CORPUS[name]
    repo = _kernel_git_corpus.CORPUS[case.laws_name].build(Path(root))
    if case.diff_refs:
        for ref in case.diff_refs:  # mine both branches so the diff sees both sides' ops
            _kernel_git_corpus.checkout(repo, ref)
            get(repo)
    else:
        get(repo)

    views: dict = {
        "oplog_view": api.oplog_view(repo),
        "state_view": api.state_view(repo),
    }
    if case.diff_refs:
        a, b = case.diff_refs
        views["ideal_diff_view"] = api.ideal_diff_view(repo, a, b)
    return views
