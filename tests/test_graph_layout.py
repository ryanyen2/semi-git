"""The Composition Workbench's feature-timeline (Gantt) layout contract.

`computeGraphLayout` in `editor/vscode/media/workbench.js` is a pure function (no DOM/color), so
we slice it out and exercise it under node. It lays features out as a Gantt: one lane per feature
with `firstCommit`/`lastCommit`/`commits` (the renderer bins those into a density strip), lanes
grouped into subsystem swimlane `headers` and ordered by first appearance into `row`s, plus
co-change `edges` (top-K, hover-only) and a `frontier` that filters ops so scrubbing accretes.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/workbench.js"


def _run(map_view: dict, grid: dict | None = None, opts: dict | None = None) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    text = _JS.read_text(encoding="utf-8")
    start = text.index("function computeGraphLayout")
    end = text.index("// ---- end-graph-layout")
    snippet = text[start:end]
    harness = snippet + (
        f"const m = {json.dumps(map_view)};\n"
        f"const g = {json.dumps(grid or {'commits': [], 'cells': []})};\n"
        f"const L = computeGraphLayout(m, g, {json.dumps(opts or {})});\n"
        "console.log(JSON.stringify(L));\n"
    )
    res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _node(id_, parent, children, kind="feature"):
    return {"id": id_, "parent": parent, "children": children, "label": id_, "kind": kind,
            "size": 1, "op_count": 0, "dir": f"src/{id_}/"}


def _grid(*specs):
    """A `grid_view`-shaped cell table from `(feature_id, commit_index)` op specs (op id = `o<i>`),
    the canonical join `computeGraphLayout` now consumes (plan U3) in place of the raw op stream."""
    cells: dict[tuple, dict] = {}
    for i, (f, c) in enumerate(specs):
        cell = cells.setdefault((f, c), {"op_ids": [], "kinds": {}})
        cell["op_ids"].append(f"o{i}")
        cell["kinds"]["add"] = cell["kinds"].get("add", 0) + 1
    return {"commits": [{"index": i} for i in range(200)], "commit_count": 200,
            "cells": [{"feature_id": f, "commit_index": c, "op_ids": sorted(v["op_ids"]),
                       "op_count": len(v["op_ids"]), "kinds": v["kinds"], "fidelity": "full"}
                      for (f, c), v in sorted(cells.items())]}


def test_only_features_with_ops_are_placed():
    m = {"roots": ["A", "B", "C"],
         "nodes": [_node("A", None, []), _node("B", None, []), _node("C", None, [])],
         "edges": []}
    # C has no ops -> it does not exist on the timeline.
    out = _run(m, _grid(("A", 0), ("A", 1), ("B", 5)))
    assert {n["id"] for n in out["lanes"]} == {"A", "B"}


def test_feature_shared_by_two_subsystems_is_a_single_lane():
    # The map is a DAG: feature F is a child of both subsystems S1 and S2. The visibility walk
    # reaches it via both paths, but it must resolve to a single lane -- else a duplicate lane
    # shares an id and the id-keyed lane table drops all but the last copy.
    m = {"roots": ["S1", "S2"],
         "nodes": [_node("S1", None, ["F"], kind="subsystem"),
                   _node("S2", None, ["F"], kind="subsystem"),
                   _node("F", "S1", [])],
         "edges": []}
    out = _run(m, _grid(("F", 0), ("F", 3)))
    assert [l["id"] for l in out["lanes"]] == ["F"]


def test_op_count_is_lane_magnitude_and_span_is_first_last():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    out = _run(m, _grid(("A", 2), ("A", 0), ("A", 3)))
    lane = out["laneById"]["A"]
    assert lane["opCount"] == 3
    assert lane["firstCommit"] == 0 and lane["lastCommit"] == 3
    assert lane["commits"] == [0, 2, 3]  # sorted, for the renderer's density binning


def test_lanes_ordered_by_first_appearance():
    # Five solo features, each born at a different commit -> rows increase with firstCommit.
    m = {"roots": list("ABCDE"), "nodes": [_node(c, None, []) for c in "ABCDE"], "edges": []}
    out = _run(m, _grid(("E", 40), ("A", 0), ("C", 20), ("B", 10), ("D", 30)))
    by_row = sorted(out["lanes"], key=lambda n: n["row"])
    firsts = [n["firstCommit"] for n in by_row]
    assert firsts == sorted(firsts)
    assert [n["id"] for n in by_row] == ["A", "B", "C", "D", "E"]


def test_expanded_subsystem_becomes_a_swimlane_header_over_its_lanes():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])],
         "edges": []}
    out = _run(m, _grid(("F1", 0), ("F1", 1), ("F2", 2)))  # N0 expanded (not collapsed)
    assert {n["id"] for n in out["lanes"]} == {"F1", "F2"}
    assert len(out["headers"]) == 1
    hd = out["headers"][0]
    assert hd["collapsedId"] == "N0" and hd["laneCount"] == 2 and hd["opCount"] == 3
    # The header sits on its own row directly above its member lanes.
    assert hd["row"] < min(out["laneById"][f]["row"] for f in ("F1", "F2"))


def test_nested_subsystems_indent_by_depth_in_tree_order():
    # Root subsystem R holds a nested subsystem S (with F1, F2) and a direct feature F0. The nested
    # subsystem must render one level deeper than R -- not flattened onto R's level -- and rows must
    # follow tree order (R, then its children by first appearance), each carrying its nesting depth.
    m = {"roots": ["R"],
         "nodes": [_node("R", None, ["S", "F0"], kind="subsystem"),
                   _node("S", "R", ["F1", "F2"], kind="subsystem"),
                   _node("F1", "S", []), _node("F2", "S", []), _node("F0", "R", [])],
         "edges": []}
    # F1 is born first (0) and F0 later (5), so a level ordered by time alone would put S's whole
    # block ahead of F0 -- see the grouping test below for why it must not.
    out = _run(m, _grid(("F1", 0), ("F2", 10), ("F0", 5)))
    headers = {h["collapsedId"]: h for h in out["headers"]}
    assert headers["R"]["depth"] == 0 and headers["S"]["depth"] == 1  # S nests under R
    lanes = out["laneById"]
    assert lanes["F1"]["depth"] == 2 and lanes["F2"]["depth"] == 2  # features under the nested S
    assert lanes["F0"]["depth"] == 1  # a direct feature of R
    # Tree order: a header opens its own block, and the block stays contiguous under it.
    assert headers["R"]["row"] < headers["S"]["row"] < lanes["F1"]["row"] < lanes["F2"]["row"]
    assert headers["R"]["opCount"] == 3 and headers["R"]["laneCount"] == 3  # rolls up all 3 features


def test_a_parents_own_lanes_come_before_its_subsystem_blocks():
    """The webview half of `test_a_features_own_rows_come_before_its_subsystem_blocks` in
    tests/tui/test_graph.py -- the two layouts are behaviour-parallel by construction, and this is
    exactly the kind of ordering rule that silently drifts apart between them.

    A parent's own feature lanes are emitted before its sub-groups, so the swimlane indent means
    containment. Ordering a level by first appearance alone put F0 -- a direct feature of R, born
    after F1 -- below S's entire subtree, where it renders inside a swimlane it is not a member of,
    under a header whose "N feat" count then disagrees with the rows beneath it."""
    m = {"roots": ["R"],
         "nodes": [_node("R", None, ["S", "F0"], kind="subsystem"),
                   _node("S", "R", ["F1", "F2"], kind="subsystem"),
                   _node("F1", "S", []), _node("F2", "S", []), _node("F0", "R", [])],
         "edges": []}
    out = _run(m, _grid(("F1", 0), ("F2", 10), ("F0", 5)))
    headers = {h["collapsedId"]: h for h in out["headers"]}
    lanes = out["laneById"]
    assert lanes["F0"]["row"] < headers["S"]["row"], "R's own feature sits above R's sub-group"
    assert headers["S"]["row"] < lanes["F1"]["row"] < lanes["F2"]["row"], "S's block stays contiguous"


def test_feature_with_no_own_symbols_is_dropped_like_the_terminal_drops_it():
    """One drop rule, honoured on every surface. A feature whose ops touch only the residue/anchor
    sentinels has nothing to act on: `sgt show` reports "0 symbols in 0 files" and reverting it
    removes nothing. The terminal has dropped those rows for a while and this layout did not, so the
    workbench listed features the map didn't (and vice versa) for one and the same repo."""
    husk = _node("HUSK", None, [])
    husk["own_symbols"] = []
    real = _node("REAL", None, [])
    real["own_symbols"] = ["a.py::f"]
    m = {"roots": ["HUSK", "REAL"], "nodes": [husk, real], "edges": []}
    out = _run(m, _grid(("HUSK", 0), ("REAL", 1)))
    assert [l["id"] for l in out["lanes"]] == ["REAL"]


def test_a_husk_is_not_counted_in_the_group_it_was_dropped_from():
    """The count has to obey the drop rule too -- the same contract `tests/tui/test_graph.py` holds
    `graph_layout` to. `(N)` on a folded row and a header's `N feat` are both leaf counts, so a husk
    left in the leaf set makes the fold promise rows that opening it does not deliver."""
    husk, real = _node("HUSK", "N0", []), _node("REAL_IN", "N0", [])
    husk["own_symbols"], real["own_symbols"] = [], ["a.py::f"]
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["HUSK", "REAL_IN"], kind="subsystem"), husk, real],
         "edges": []}
    grid = _grid(("HUSK", 0), ("REAL_IN", 1))
    folded = _run(m, grid, opts={"collapsed": ["N0"]})
    assert [l["id"] for l in folded["lanes"]] == ["N0"]
    assert folded["lanes"][0]["leaves"] == ["REAL_IN"]
    opened = _run(m, grid)
    assert [l["id"] for l in opened["lanes"]] == ["REAL_IN"]
    assert opened["headers"][0]["laneCount"] == 1


def test_collapsed_subsystem_is_one_meta_lane_rolling_up_descendant_grid():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])],
         "edges": []}
    out = _run(m, _grid(("F1", 0), ("F1", 1), ("F2", 2)), opts={"collapsed": ["N0"]})
    assert len(out["lanes"]) == 1 and out["headers"] == []
    meta = out["lanes"][0]
    assert meta["id"] == "N0" and meta["isMeta"] is True and meta["opCount"] == 3


def test_frontier_filters_ops_so_scrubbing_accretes():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 2), ("B", 50))
    early = _run(m, hist, opts={"frontier": 10})
    assert {n["id"] for n in early["lanes"]} == {"A"}  # B's op is past the frontier
    later = _run(m, hist, opts={"frontier": 100})
    assert {n["id"] for n in later["lanes"]} == {"A", "B"}


def test_edge_into_collapsed_subsystem_reroutes_and_self_loops_drop():
    m = {"roots": ["N0", "F3"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", []), _node("F3", None, [])],
         "edges": [{"a": "F1", "b": "F3", "weight": 2.0},   # reroutes to N0<->F3
                   {"a": "F1", "b": "F2", "weight": 9.0}]}   # both under N0 -> self-loop, dropped
    out = _run(m, _grid(("F1", 0), ("F2", 1), ("F3", 2)), opts={"collapsed": ["N0"]})
    assert out["edges"] == [{"a": "F3", "b": "N0", "weight": 2.0}]


def test_topk_edges_kept_rest_in_overflow():
    nodes = [_node("hub", None, [])] + [_node(f"l{i}", None, []) for i in range(5)]
    edges = [{"a": "hub", "b": f"l{i}", "weight": float(5 - i)} for i in range(5)]
    m = {"roots": ["hub"] + [f"l{i}" for i in range(5)], "nodes": nodes, "edges": edges}
    hist = _grid(*[("hub", 0)] + [(f"l{i}", i + 1) for i in range(5)])
    out = _run(m, hist, opts={"topK": 3})
    assert out["overflow"]["hub"] == 2  # the two lightest of hub's 5 edges are counted, not lost


def test_deterministic_across_runs():
    m = {"roots": list("ABCDEF"),
         "nodes": [_node(c, None, []) for c in "ABCDEF"],
         "edges": [{"a": "A", "b": "C", "weight": 3.0}, {"a": "B", "b": "D", "weight": 2.0}]}
    hist = _grid(("A", 0), ("B", 1), ("C", 10), ("D", 11), ("E", 20), ("F", 21))
    key = lambda o: [(n["id"], n["row"], n["firstCommit"]) for n in o["lanes"]]
    assert key(_run(m, hist)) == key(_run(m, hist))


def test_empty_map_is_empty_timeline():
    out = _run({"roots": [], "nodes": [], "edges": []})
    assert out["lanes"] == [] and out["edges"] == [] and out["headers"] == []


# --- which subsystems the CLI map folds by default (finding 81) --------------------------------


def _mv(features_per_sub: list[int], husks: int = 0) -> dict:
    """A map view: a root, one leaf subsystem per entry, that many features under each."""
    nodes = [{"id": "N0", "parent": None, "children": [], "kind": "subsystem", "label": "root"}]
    f = 0
    for s, k in enumerate(features_per_sub):
        sid = f"S{s}"
        kids = []
        for _ in range(k):
            fid = f"F{f}"
            f += 1
            own = [] if len(kids) < husks else [f"{fid}.py::x"]
            nodes.append({"id": fid, "parent": sid, "children": [], "kind": "feature",
                          "label": fid, "own_symbols": own})
            kids.append(fid)
        nodes.append({"id": sid, "parent": "N0", "children": kids, "kind": "subsystem", "label": sid})
        nodes[0]["children"].append(sid)
    return {"nodes": nodes}


def test_a_map_inside_the_row_budget_folds_nothing():
    from sgt.cli.inspect import _default_collapsed

    assert _default_collapsed(_mv([5, 4, 3])) == ()  # 12 features + 4 headers


def test_an_oversized_map_folds_its_largest_leaf_subsystems_until_it_fits():
    from sgt.cli.inspect import MAP_ROW_BUDGET, _default_collapsed

    mv = _mv([12, 9, 6, 3])  # 30 features + 5 headers = 35 rows
    assert _default_collapsed(mv) == ("S0",)  # one fold of 12 already fits; it stops there

    mv = _mv([10, 10, 10, 10])  # 40 features + 5 headers = 45 rows, three folds to fit
    collapsed = _default_collapsed(mv)
    assert collapsed == ("S0", "S1", "S2")  # largest first, ties by id
    kind = {n["id"]: n["kind"] for n in mv["nodes"]}
    parent = {n["id"]: n["parent"] for n in mv["nodes"]}
    rows = sum(1 for n in mv["nodes"]
               if kind[n["id"]] == "subsystem" or parent[n["id"]] not in collapsed)
    assert rows <= MAP_ROW_BUDGET


def test_the_root_is_never_folded():
    from sgt.cli.inspect import _default_collapsed

    root_only = {"nodes": [{"id": "N0", "parent": None, "kind": "subsystem",
                            "children": [f"F{i}" for i in range(40)], "label": "root"}]
                 + [{"id": f"F{i}", "parent": "N0", "children": [], "kind": "feature",
                     "label": f"F{i}", "own_symbols": ["a.py::x"]} for i in range(40)]}
    assert _default_collapsed(root_only) == ()


def test_husk_features_do_not_push_the_map_over_its_budget():
    from sgt.cli.inspect import _default_collapsed

    # 30 features, but 18 of them own nothing and never render, so the map fits and nothing folds
    assert _default_collapsed(_mv([10, 10, 10], husks=6)) == ()
