"""Deterministic, offline corpus for the operation-ideal kernel's characterization golden master.

The kernel's read surface (`oplog_view`/`state_view`/`ideal_diff_view`/`map_view`/`blame_view`/
`status_view`) reads a mined git repo, not an in-memory object, so it needs git-repo fixtures. We
reuse the deterministic, pinned-SHA fixtures the round-trip law harness already builds
(`tests/laws/corpus.py`) -- same discipline (no LLM/network/wall-clock) -- so these snapshots are
byte-stable across runs. `test_golden.py` snapshots the views these builders produce and fails on
drift. The feature lens (`build_map`) falls back to deterministic offline labels with no API key,
so `map_view`/`blame_view`/`status_view` are golden-safe too.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from sgt import api
from tests.laws import corpus as _kernel_git_corpus


class KernelCase(NamedTuple):
    laws_name: str  # which tests/laws/corpus.py fixture to build and mine
    diff_refs: tuple[str, str] | None  # (ref_a, ref_b) to also snapshot ideal_diff_view, else None
    blame_file: str  # a parseable path in the fixture to snapshot `blame_view` on


KERNEL_CORPUS: dict[str, KernelCase] = {
    "mixed_coverage": KernelCase("mixed_coverage", None, "pkg.py"),
    "diverged_chain": KernelCase("diverged_chain", ("main", "release"), "slugify.py"),
}


def capture_kernel_views(name: str, root: str) -> dict:
    """Build a deterministic git-repo kernel fixture, mine it (`get`), build the feature tree
    (`build_map`), and capture the kernel views: the op DAG, the current ideal, -- for a diverged
    fixture -- the ideal-vs-ideal semantic diff between its two branches, and the U13 feature-lens
    projection (`map_view`/`blame_view`/`status_view`)."""
    from sgt.core.lens import get
    from sgt.lens.map import build_map

    case = KERNEL_CORPUS[name]
    repo = _kernel_git_corpus.CORPUS[case.laws_name].build(Path(root))
    if case.diff_refs:
        for ref in case.diff_refs:  # mine both branches so the diff sees both sides' ops
            _kernel_git_corpus.checkout(repo, ref)
            get(repo)
    else:
        get(repo)

    build_map(repo)  # cluster + label (deterministic fallback labels, offline) + persist tree.json

    views: dict = {
        "oplog_view": api.oplog_view(repo),
        "state_view": api.state_view(repo),
        "map_view": api.map_view(repo),
        "blame_view": api.blame_view(repo, case.blame_file),
        "status_view": api.status_view(repo),
    }
    if case.diff_refs:
        a, b = case.diff_refs
        views["ideal_diff_view"] = api.ideal_diff_view(repo, a, b)
    return views
