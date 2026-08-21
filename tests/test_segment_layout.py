"""The Composition Workbench's chunk-car timeline layout contract.

`computeSegmentLayout` in `editor/vscode/media/workbench.js` is a pure function (no DOM/color) that
threads intent-segment "cars" onto `computeGraphLayout`'s gutter -- the visual atom becomes the
`<feature>@<n>` checkpoint, not the raw op. Mirrors `tests/tui/test_graph.py`'s Python coverage of
`segment_layout`, kept behaviour-parallel per project convention.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/workbench.js"


def _run(map_view: dict, grid: dict, segments: list, opts: dict | None = None) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    text = _JS.read_text(encoding="utf-8")
    # computeSegmentLayout calls computeGraphLayout, so the slice must span both functions.
    start = text.index("function computeGraphLayout")
    end = text.index("// ---- end-segment-layout")
    snippet = text[start:end]
    harness = snippet + (
        f"const m = {json.dumps(map_view)};\n"
        f"const g = {json.dumps(grid)};\n"
        f"const s = {json.dumps(segments)};\n"
        f"const L = computeSegmentLayout(m, g, s, {json.dumps(opts or {})});\n"
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
    the canonical join `computeSegmentLayout`/`computeGraphLayout` now consume (plan U3)."""
    cells: dict[tuple, dict] = {}
    for i, (f, c) in enumerate(specs):
        cell = cells.setdefault((f, c), {"op_ids": [], "kinds": {}})
        cell["op_ids"].append(f"o{i}")
        cell["kinds"]["add"] = cell["kinds"].get("add", 0) + 1
    return {"commits": [{"index": i} for i in range(200)], "commit_count": 200,
            "cells": [{"feature_id": f, "commit_index": c, "op_ids": sorted(v["op_ids"]),
                       "op_count": len(v["op_ids"]), "kinds": v["kinds"], "fidelity": "full"}
                      for (f, c), v in sorted(cells.items())]}


def _seg(feature_id, seg_index, op_ids, first_index, last_index,
         label=None, tier="co-changed", source="fallback", words=None):
    return {"feature_id": feature_id, "seg_index": seg_index,
            "checkpoint": f"{feature_id}@{seg_index}", "intent": label or f"seg {seg_index}",
            "rationale": "", "op_ids": list(op_ids), "op_count": len(op_ids),
            "commit_shas": [], "first_index": first_index, "last_index": last_index,
            "novelty": 0.0, "tier": tier, "source": source, "words": words or []}


def test_cars_carry_segment_metadata_and_are_ordered_by_seg_index():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1), ("A", 2))
    segs = [_seg("A", 1, ["o2"], 2, 2, label="second"), _seg("A", 0, ["o0", "o1"], 0, 1, label="first")]
    out = _run(m, hist, segs)
    cars = out["laneById"]["A"]["cars"]
    assert [c["segIndex"] for c in cars] == [0, 1]
    assert [c["label"] for c in cars] == ["first", "second"]
    assert cars[0]["checkpoint"] == "A@0" and cars[0]["opCount"] == 2
    assert cars[0]["tier"] == "co-changed" and cars[0]["source"] == "fallback"


def test_cars_carry_captured_words():
    """The VSCode chunk-car carries a chapter's captured words (intent-ledger P1), parallel to the
    terminal layout, so the extension can surface 'the history in my own words' on hover."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0))
    segs = [_seg("A", 0, ["o0"], 0, 0, words=["remove all completed tasks"])]
    car = _run(m, hist, segs)["laneById"]["A"]["cars"][0]
    assert car["words"] == ["remove all completed tasks"]


def test_cars_carry_whether_a_revert_took_them_out_of_head():
    """The JS twin of `test_a_reverted_checkpoint_is_drawn_as_removed_not_as_live`. A revert leaves a
    chapter in the store and takes it out of the ideal, so both timelines have to be able to draw a
    rewound chapter as rewound instead of redrawing it as live. `presentOpCount` of `None` -- an
    unreadable ideal, or a payload written before the field existed -- is no claim, and must not read
    as removed."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1), ("A", 2))
    live = _seg("A", 0, ["o0"], 0, 0)
    gone = _seg("A", 1, ["o1"], 1, 1)
    unknown = _seg("A", 2, ["o2"], 2, 2)  # no `present_op_count` key at all
    live["present_op_count"], gone["present_op_count"] = 1, 0

    cars = _run(m, hist, [live, gone, unknown])["laneById"]["A"]["cars"]
    assert [c["reverted"] for c in cars] == [False, True, False]
    assert [c["presentOpCount"] for c in cars] == [1, 0, None]


def test_sub_bins_group_a_cars_ops_by_commit_index():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 5), ("A", 5), ("A", 6))
    segs = [_seg("A", 0, ["o0", "o1", "o2"], 5, 6)]
    out = _run(m, hist, segs)
    car = out["laneById"]["A"]["cars"][0]
    assert car["subBins"] == [[5, 2], [6, 1]]


def test_lane_with_no_ops_has_no_cars_even_if_segments_exist():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    out = _run(m, {"commits": [], "cells": []}, [_seg("A", 0, ["o0"], 0, 0)])
    assert out["lanes"] == []


def test_collapsed_subsystem_aggregates_cars_from_all_its_features():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])], "edges": []}
    hist = _grid(("F1", 0), ("F2", 1))
    segs = [_seg("F1", 0, ["o0"], 0, 0, label="f1 chapter"),
            _seg("F2", 0, ["o1"], 1, 1, label="f2 chapter")]
    out = _run(m, hist, segs, opts={"collapsed": ["N0"]})
    assert len(out["lanes"]) == 1
    cars = out["lanes"][0]["cars"]
    assert {c["featureId"] for c in cars} == {"F1", "F2"}
    assert [c["label"] for c in cars] == ["f1 chapter", "f2 chapter"]


def test_car_past_frontier_is_flagged_future_not_dropped():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 50))
    segs = [_seg("A", 0, ["o0"], 0, 0), _seg("A", 1, ["o1"], 50, 50)]
    out = _run(m, hist, segs, opts={"frontier": 10})
    cars = out["laneById"]["A"]["cars"]
    assert len(cars) == 2
    assert cars[0]["isFuture"] is False
    assert cars[1]["isFuture"] is True


def test_deterministic_across_runs():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1), ("B", 2))
    segs = [_seg("A", 0, ["o0"], 0, 0), _seg("A", 1, ["o1"], 1, 1), _seg("B", 0, ["o2"], 2, 2)]
    key = lambda o: [(l["id"], [c["checkpoint"] for c in l["cars"]]) for l in o["lanes"]]
    assert key(_run(m, hist, segs)) == key(_run(m, hist, segs))
