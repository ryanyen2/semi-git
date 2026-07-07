"""Deterministic, offline corpus for the characterization golden master.

The op-log refactor (docs/plans/2026-07-01-001-refactor-oplog-fallback-ladder.md) re-homes the
semantic truth store phase by phase. Every phase must reproduce today's *observable* behavior —
the `sgt.api` projection that the CLI `--json`, MCP, the TUI, and the VS Code extension all read.
This module freezes that behavior: it builds representative projects with **explicit node ids and
typed effects only** (`Project.init` + `add_feature`/`add_plan`, the `tests/test_api.py` idiom),
so the projection is byte-stable across runs with no LLM, no network, and no git-SHA/timestamp
leakage. `test_golden.py` snapshots the views these builders produce and fails on drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, NamedTuple

from sgt import api
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus
from tests.laws import corpus as _kernel_git_corpus


def _linear_deps(tmp: str) -> Project:
    """base ← user (one file, inferred dependency) plus a PLANNED node with no effects — the
    stale-`provides`-on-PLANNED case the refactor targets (op-log-ontology §7)."""
    p = Project.init(tmp)
    p.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base capability"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    p.add_feature(
        Node(id="user", kind=NodeKind.CAPABILITY, intent="uses base"),
        [Effect.add_def("m.py", "user", "def user():\n    return base()")],
    )
    p.add_plan(
        [Node(id="planned", kind=NodeKind.CAPABILITY, intent="future work", status=NodeStatus.PLANNED)],
        [],
    )
    p.save()
    return p


def _fanout_multifile(tmp: str) -> Project:
    """One provider in core.py with two cross-file consumers — fan-out dependents + multi-file
    blame/status."""
    p = Project.init(tmp)
    p.add_feature(
        Node(id="core", kind=NodeKind.CAPABILITY, intent="shared core"),
        [Effect.add_def("core.py", "shared", "def shared():\n    return 42")],
    )
    p.add_feature(
        Node(id="reader", kind=NodeKind.CAPABILITY, intent="reads core"),
        [Effect.add_def("read.py", "read", "def read():\n    return shared()")],
    )
    p.add_feature(
        Node(id="writer", kind=NodeKind.CAPABILITY, intent="also reads core"),
        [Effect.add_def("write.py", "write", "def write():\n    return shared() + 1")],
    )
    p.save()
    return p


class Case(NamedTuple):
    build: Callable[[str], Project]
    files: tuple[str, ...]  # blame_view is captured per file
    node_id: str  # node_view is captured for this id
    ref: str  # show_view is captured for this ref


CORPUS: dict[str, Case] = {
    "linear_deps": Case(_linear_deps, ("m.py",), "user", "uses base"),
    "fanout_multifile": Case(_fanout_multifile, ("core.py", "read.py", "write.py"), "reader", "reads core"),
}


def capture_views(project: Project, case: Case) -> dict:
    """Every public `sgt.api` view for a built project, keyed by a stable name. This is the single
    source of truth for *what* the golden master covers; add a view here and every snapshot grows
    it additively."""
    views: dict = {
        "graph_view": api.graph_view(project),
        "status_view": api.status_view(project),
        "decision_graph_view": api.decision_graph_view(project),
        "frontier_view": api.frontier_view(project),
        "conflicts_view": api.conflicts_view(project),
        "entity_graph_view": api.entity_graph_view(project),
        "export_view": api.export_view(project),
        "timeframe_view@0": api.timeframe_view(project, 0),
        "node_view": api.node_view(project, project.graph.get(case.node_id)),
        "show_view": api.show_view(project, case.ref),
    }
    for f in case.files:
        views[f"blame_view::{f}"] = api.blame_view(project, f)
    return views


# -- kernel views (plan U7) -------------------------------------------------------------------
# The operation-ideal kernel's read surface (`oplog_view`/`state_view`/`ideal_diff_view`) reads a
# mined git repo, not an in-memory `Project`, so it needs git-repo fixtures. We reuse the
# deterministic, pinned-SHA fixtures the round-trip law harness already builds
# (`tests/laws/corpus.py`) -- same discipline (no LLM/network/wall-clock), applied to real git
# history -- so these kernel snapshots are byte-stable across runs too.


class KernelCase(NamedTuple):
    laws_name: str  # which tests/laws/corpus.py fixture to build and mine
    diff_refs: tuple[str, str] | None  # (ref_a, ref_b) to also snapshot ideal_diff_view, else None


KERNEL_CORPUS: dict[str, KernelCase] = {
    "mixed_coverage": KernelCase("mixed_coverage", None),
    "diverged_chain": KernelCase("diverged_chain", ("main", "release")),
}


def capture_kernel_views(name: str, root: str) -> dict:
    """Build a deterministic git-repo kernel fixture, mine it (`get`), and capture the U7 kernel
    views. Mirrors `capture_views` for the op-ideal kernel: the op DAG, the current ideal, and --
    for a diverged fixture -- the ideal-vs-ideal semantic diff between its two branches."""
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
