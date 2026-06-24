"""U3 — the `decisions` surface: CLI `--json` and the MCP read tool, both via sgt.api."""

from __future__ import annotations

import json
import os

from sgt.api import decision_graph_view
from sgt.cli import main
from sgt.effects.model import Effect
from sgt.mcp.server import tool_decisions
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _seed(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base capability"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    proj.log.stamp_committed()
    proj.save()
    return proj


def _run_json(tmp_path, capsys, *argv) -> dict:
    capsys.readouterr()
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert main([*argv]) == 0
    finally:
        os.chdir(cwd)
    return json.loads(capsys.readouterr().out)


def test_decisions_json_shape(tmp_path, capsys):
    _seed(tmp_path)
    data = _run_json(tmp_path, capsys, "decisions", "--json")
    assert set(data) == {"decisions", "edges", "frontier", "clash", "count"}
    assert data["count"] == 1
    assert data["frontier"] == {"base": "base@1"}


def test_decisions_frontier_subcommand(tmp_path, capsys):
    _seed(tmp_path)
    data = _run_json(tmp_path, capsys, "decisions", "frontier", "--json")
    assert data["selection"] == {"base": "base@1"}
    assert data["lanes"] == ["base"]


def test_mcp_decisions_matches_cli_projection(tmp_path):
    _seed(tmp_path)
    assert tool_decisions(str(tmp_path), {}) == decision_graph_view(Project.open(tmp_path))
