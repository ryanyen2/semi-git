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

from typing import Callable, NamedTuple

from sgt import api
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


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
