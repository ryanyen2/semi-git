"""The Feature Map webview's layout contract.

`computeLayout` in `editor/vscode/media/map.js` is a pure function (no DOM/color dependency), so
we slice it out and exercise it under node: DFS row order + collapse, per-feature op lifebars from
the commit-index axis, and dependency-edge rerouting/top-K thresholding when an endpoint sits
inside a collapsed subsystem.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/map.js"


def _run_layout(map_view: dict, history: dict | None = None, opts: dict | None = None) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    text = _JS.read_text(encoding="utf-8")
    start = text.index("function computeLayout")
    end = text.index("// ---- end-layout")
    snippet = text[start:end]
    harness = snippet + (
        f"const m = {json.dumps(map_view)};\n"
        f"const h = {json.dumps(history or {'commits': [], 'ops': []})};\n"
        f"const L = computeLayout(m, h, {json.dumps(opts or {})});\n"
        "console.log(JSON.stringify(L));\n"
    )
    res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _node(id_, parent, children):
    return {"id": id_, "parent": parent, "children": children, "label": id_, "size": 1, "op_count": 0}


def test_dfs_rows_visit_roots_then_children_depth_first():
    m = {
        "roots": ["N0"],
        "nodes": [
            _node("N0", None, ["F1", "F2"]),
            _node("F1", "N0", []),
            _node("F2", "N0", []),
        ],
        "edges": [],
    }
    out = _run_layout(m)
    assert out["rows"] == ["N0", "F1", "F2"]
    assert out["depthOf"] == {"N0": 0, "F1": 1, "F2": 1}


def test_collapsing_a_subsystem_hides_its_children_from_rows():
    m = {
        "roots": ["N0"],
        "nodes": [
            _node("N0", None, ["F1", "F2"]),
            _node("F1", "N0", []),
            _node("F2", "N0", []),
        ],
        "edges": [],
    }
    out = _run_layout(m, opts={"collapsed": ["N0"]})
    assert out["rows"] == ["N0"]


def test_lifebar_spans_first_to_last_op_commit_index_for_a_feature():
    m = {"roots": ["F1"], "nodes": [_node("F1", None, [])], "edges": []}
    history = {
        "commits": [{"sha": "a", "subject": "c0", "index": 0}, {"sha": "b", "subject": "c1", "index": 1},
                    {"sha": "c", "subject": "c2", "index": 2}],
        "ops": [
            {"id": "op1", "kind": "add", "feature_id": "F1", "commit_index": 0},
            {"id": "op2", "kind": "rework", "feature_id": "F1", "commit_index": 2},
        ],
    }
    out = _run_layout(m, history)
    assert out["lifebars"]["F1"] == {"start": 0, "end": 2}
    assert [op["id"] for op in out["opsByFeature"]["F1"]] == ["op1", "op2"]
    assert out["commitCount"] == 3


def test_ops_with_no_feature_assignment_are_excluded_from_every_lifebar():
    m = {"roots": ["F1"], "nodes": [_node("F1", None, [])], "edges": []}
    history = {"commits": [], "ops": [{"id": "op1", "kind": "add", "feature_id": None, "commit_index": 0}]}
    out = _run_layout(m, history)
    assert out["lifebars"] == {}
    assert out["opsByFeature"] == {}


def test_edges_between_two_visible_leaves_pass_through_unchanged():
    m = {
        "roots": ["F1", "F2"],
        "nodes": [_node("F1", None, []), _node("F2", None, [])],
        "edges": [{"a": "F1", "b": "F2", "weight": 3.0}],
    }
    out = _run_layout(m)
    assert out["edges"] == [{"a": "F1", "b": "F2", "weight": 3.0}]
    assert out["overflow"] == {}


def test_edge_into_a_collapsed_subsystem_reroutes_to_the_subsystem_row_not_dropped():
    m = {
        "roots": ["N0", "F3"],
        "nodes": [
            _node("N0", None, ["F1", "F2"]),
            _node("F1", "N0", []),
            _node("F2", "N0", []),
            _node("F3", None, []),
        ],
        "edges": [{"a": "F1", "b": "F3", "weight": 2.0}],
    }
    out = _run_layout(m, opts={"collapsed": ["N0"]})
    assert out["edges"] == [{"a": "F3", "b": "N0", "weight": 2.0}]  # (a, b) normalized alphabetically


def test_edges_that_reroute_onto_the_same_collapsed_ancestor_are_dropped_as_self_loops():
    m = {
        "roots": ["N0"],
        "nodes": [
            _node("N0", None, ["F1", "F2"]),
            _node("F1", "N0", []),
            _node("F2", "N0", []),
        ],
        "edges": [{"a": "F1", "b": "F2", "weight": 5.0}],
    }
    out = _run_layout(m, opts={"collapsed": ["N0"]})
    assert out["edges"] == []


def test_edges_past_top_k_are_counted_in_overflow_never_silently_dropped():
    nodes = [_node("hub", None, [])]
    edges = []
    for i in range(5):
        leaf = f"leaf{i}"
        nodes.append(_node(leaf, None, []))
        edges.append({"a": "hub", "b": leaf, "weight": float(5 - i)})  # descending weight
    m = {"roots": ["hub"] + [f"leaf{i}" for i in range(5)], "nodes": nodes, "edges": edges}

    out = _run_layout(m, opts={"topK": 3})
    assert len(out["edges"]) == 3
    kept_weights = sorted((e["weight"] for e in out["edges"]), reverse=True)
    assert kept_weights == [5.0, 4.0, 3.0]  # the three heaviest survive
    assert out["overflow"]["hub"] == 2  # the two lightest are counted, not silently gone


def test_empty_map_has_no_rows_and_no_edges():
    out = _run_layout({"roots": [], "nodes": [], "edges": []})
    assert out["rows"] == []
    assert out["edges"] == []
    assert out["lifebars"] == {}
