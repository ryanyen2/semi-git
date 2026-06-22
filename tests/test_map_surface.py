"""U4 — the `map` surface: CLI `--json` and the MCP read tool, both delegating to sgt.api."""

from __future__ import annotations

import json
import os

from sgt.api import entity_graph_view
from sgt.cli import main
from sgt.mcp.server import tool_map
from sgt.project import Project


def _run_map_json(tmp_path, capsys) -> dict:
    capsys.readouterr()  # drain prior output (e.g. the init banner) so stdout is pure JSON
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert main(["map", "--json"]) == 0
    finally:
        os.chdir(cwd)
    return json.loads(capsys.readouterr().out)


def test_map_json_lists_entities(tmp_path, capsys):
    main(["init", str(tmp_path)])
    (tmp_path / "m.py").write_text(
        "def callee():\n    return 1\ndef caller():\n    return callee()\n", encoding="utf-8"
    )
    data = _run_map_json(tmp_path, capsys)
    assert set(data) == {"entities", "edges", "reduced_edges", "components", "count"}
    assert data["count"] == 2
    assert {e["name"] for e in data["entities"]} == {"caller", "callee"}


def test_map_empty_repo_is_well_formed(tmp_path, capsys):
    main(["init", str(tmp_path)])
    data = _run_map_json(tmp_path, capsys)
    assert data["count"] == 0
    assert data["entities"] == [] and data["edges"] == [] and data["components"] == []


def test_mcp_map_tool_matches_cli_projection(tmp_path):
    main(["init", str(tmp_path)])
    (tmp_path / "m.py").write_text(
        "class C:\n    def m(self):\n        return 1\n", encoding="utf-8"
    )
    # MCP read tool and the api projection are the same dict — surfaces can't drift.
    assert tool_map(str(tmp_path), {}) == entity_graph_view(Project.open(tmp_path))
