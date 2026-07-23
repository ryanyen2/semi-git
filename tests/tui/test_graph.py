"""The terminal feature-timeline (Gantt) layout + renderer (`sgt.tui.graph`), and the TUI overlay.

`graph_layout` is the Python counterpart of the VS Code `computeGraphLayout` and is held to the
same contract (see tests/test_graph_layout.py): one lane per feature with first/last/commits,
lanes grouped into subsystem swimlane headers and ordered by first appearance, collapsed
subsystems roll up, a frontier filters ops, deterministic. The pure-function tests need no repo;
the pilot test (Textual) boots the real app and opens the graph screen.
"""

from __future__ import annotations

import pytest

from sgt.tui.graph import graph_layout, render_graph_lines, segment_layout


def _node(id_, parent, children, kind="feature"):
    return {"id": id_, "parent": parent, "children": children, "label": id_.upper(),
            "kind": kind, "size": 1, "op_count": 0, "dir": f"src/{id_}/"}


def _grid(*specs):
    """A `grid_view`-shaped cell table from `(feature_id, commit_index)` op specs (op id = `o<i>`),
    the canonical input the layouts now consume (plan U3) in place of the raw op stream."""
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
         label=None, tier="co-changed", source="fallback"):
    return {"feature_id": feature_id, "seg_index": seg_index,
            "checkpoint": f"{feature_id}@{seg_index}", "intent": label or f"seg {seg_index}",
            "rationale": "", "op_ids": list(op_ids), "op_count": len(op_ids),
            "commit_shas": [], "first_index": first_index, "last_index": last_index,
            "novelty": 0.0, "tier": tier, "source": source}


def test_only_features_with_ops_are_placed():
    m = {"roots": ["A", "B", "C"],
         "nodes": [_node("A", None, []), _node("B", None, []), _node("C", None, [])], "edges": []}
    out = graph_layout(m, _grid(("A", 0), ("A", 1), ("B", 5)))
    assert {n["id"] for n in out["lanes"]} == {"A", "B"}


def test_op_count_magnitude_and_span():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    out = graph_layout(m, _grid(("A", 3), ("A", 0), ("A", 1)))
    lane = out["node_by_id"]["A"]
    assert lane["op_count"] == 3
    assert lane["first_commit"] == 0 and lane["last_commit"] == 3
    assert lane["commits"] == [0, 1, 3]


def test_lanes_ordered_by_first_appearance():
    m = {"roots": list("ABCDE"), "nodes": [_node(c, None, []) for c in "ABCDE"], "edges": []}
    out = graph_layout(m, _grid(("E", 40), ("A", 0), ("C", 20), ("B", 10), ("D", 30)))
    by_row = sorted(out["lanes"], key=lambda n: n["row"])
    assert [n["id"] for n in by_row] == ["A", "B", "C", "D", "E"]


def test_expanded_subsystem_makes_a_header_over_its_lanes():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])], "edges": []}
    out = graph_layout(m, _grid(("F1", 0), ("F1", 1), ("F2", 2)))
    assert {n["id"] for n in out["lanes"]} == {"F1", "F2"}
    assert len(out["headers"]) == 1
    hd = out["headers"][0]
    assert hd["collapsed_id"] == "N0" and hd["lane_count"] == 2 and hd["op_count"] == 3
    assert hd["row"] < min(out["node_by_id"][f]["row"] for f in ("F1", "F2"))


def test_collapsed_subsystem_rolls_up_descendant_ops():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])], "edges": []}
    out = graph_layout(m, _grid(("F1", 0), ("F1", 1), ("F2", 2)), collapsed=["N0"])
    assert len(out["lanes"]) == 1 and out["headers"] == []
    assert out["lanes"][0]["is_meta"] and out["lanes"][0]["op_count"] == 3


def test_frontier_filters_ops_so_scrubbing_accretes():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 2), ("B", 50))
    assert {n["id"] for n in graph_layout(m, hist, frontier=10)["lanes"]} == {"A"}
    assert {n["id"] for n in graph_layout(m, hist, frontier=100)["lanes"]} == {"A", "B"}


def test_edge_reroutes_into_collapsed_subsystem_and_self_loops_drop():
    m = {"roots": ["N0", "F3"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", []), _node("F3", None, [])],
         "edges": [{"a": "F1", "b": "F3", "weight": 2.0}, {"a": "F1", "b": "F2", "weight": 9.0}]}
    out = graph_layout(m, _grid(("F1", 0), ("F2", 1), ("F3", 2)), collapsed=["N0"])
    assert out["edges"] == [{"a": "F3", "b": "N0", "weight": 2.0}]


def test_deterministic():
    m = {"roots": list("ABCDEF"), "nodes": [_node(c, None, []) for c in "ABCDEF"],
         "edges": [{"a": "A", "b": "C", "weight": 3.0}, {"a": "B", "b": "D", "weight": 2.0}]}
    hist = _grid(("A", 0), ("B", 1), ("C", 10), ("D", 11), ("E", 20), ("F", 21))
    key = lambda o: [(n["id"], n["row"], n["first_commit"]) for n in graph_layout(m, hist)["lanes"]]
    assert key(m) == key(m)


# ── segment_layout (the chunk-car atom) ─────────────────────────────────────────────────────────


def test_cars_carry_segment_metadata_and_are_ordered_by_seg_index():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1), ("A", 2))
    segs = [_seg("A", 1, ["o2"], 2, 2, label="second"), _seg("A", 0, ["o0", "o1"], 0, 1, label="first")]
    out = segment_layout(m, hist, segs)
    cars = out["node_by_id"]["A"]["cars"]
    assert [c["seg_index"] for c in cars] == [0, 1]
    assert [c["label"] for c in cars] == ["first", "second"]
    assert cars[0]["checkpoint"] == "A@0" and cars[0]["op_count"] == 2
    assert cars[0]["tier"] == "co-changed" and cars[0]["source"] == "fallback"


def test_sub_bins_group_a_cars_ops_by_commit_index():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 5), ("A", 5), ("A", 6))
    segs = [_seg("A", 0, ["o0", "o1", "o2"], 5, 6)]
    out = segment_layout(m, hist, segs)
    car = out["node_by_id"]["A"]["cars"][0]
    assert car["sub_bins"] == [(5, 2), (6, 1)]


def test_lane_with_no_ops_has_no_cars_even_if_segments_exist():
    # a phantom/stale segment for a feature whose ops never landed in history -> lane doesn't exist
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    out = segment_layout(m, {"commits": [], "cells": []}, [_seg("A", 0, ["o0"], 0, 0)])
    assert out["lanes"] == []


def test_collapsed_subsystem_aggregates_cars_from_all_its_features():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])], "edges": []}
    hist = _grid(("F1", 0), ("F2", 1))
    segs = [_seg("F1", 0, ["o0"], 0, 0, label="f1 chapter"),
            _seg("F2", 0, ["o1"], 1, 1, label="f2 chapter")]
    out = segment_layout(m, hist, segs, collapsed=["N0"])
    assert len(out["lanes"]) == 1
    cars = out["lanes"][0]["cars"]
    assert {c["feature_id"] for c in cars} == {"F1", "F2"}
    assert [c["label"] for c in cars] == ["f1 chapter", "f2 chapter"]  # ordered by first_index


def test_car_past_frontier_is_flagged_future_not_dropped():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 50))
    segs = [_seg("A", 0, ["o0"], 0, 0), _seg("A", 1, ["o1"], 50, 50)]
    out = segment_layout(m, hist, segs, frontier=10)
    cars = out["node_by_id"]["A"]["cars"]
    assert len(cars) == 2  # the lane exists (op0 <= frontier) so both its cars stay in place
    assert cars[0]["is_future"] is False
    assert cars[1]["is_future"] is True


def test_segment_layout_deterministic():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1), ("B", 2))
    segs = [_seg("A", 0, ["o0"], 0, 0), _seg("A", 1, ["o1"], 1, 1), _seg("B", 0, ["o2"], 2, 2)]
    key = lambda: [(l["id"], [c["checkpoint"] for c in l["cars"]])
                   for l in segment_layout(m, hist, segs)["lanes"]]
    assert key() == key()


def test_render_lines_carry_header_and_labels():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    lines = render_graph_lines(m, _grid(("A", 0), ("B", 40)), color=False)
    text = "\n".join(lines)
    assert "2 feature(s)" in text
    assert "A" in text and "B" in text  # labels rendered


def test_render_lane_leads_with_handle_and_draws_checkpoint_cars():
    """Each lane surfaces the short `f-XXXX` handle (the copy-paste token for `sgt revert`), a `✦N`
    checkpoint count, and its checkpoints as bracketed cars in `seg_index` order -- the atom is the
    segment, not a raw commit-time column."""
    fid = "f-aaaaaaaaaa"  # digit-free id + label (label = id.upper()) so only cars carry digits
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid((fid, 0), (fid, 100), (fid, 199))
    segs = [_seg(fid, 0, ["o0"], 0, 0), _seg(fid, 1, ["o1", "o2"], 100, 199)]
    lines = render_graph_lines(m, hist, segs, color=False)
    lane = next(ln for ln in lines if fid[:10] in ln)
    assert fid[:10] in lane and "✦2" in lane          # handle + checkpoint count
    strip = lane.split("✦")[0]
    assert "0" in strip and "1" in strip              # both cars' @n digits drawn
    assert strip.index("0") < strip.index("1")        # @0 sits left of @1 (seg_index order)
    # no segments -> no cars, no ✦ count, plain dim lifetime track instead
    plain = render_graph_lines(m, _grid((fid, 0)), color=False)
    plain_lane = next(ln for ln in plain if fid[:10] in ln)
    assert "✦" not in plain_lane


def test_render_car_widths_reflect_op_count_and_tier_brackets():
    fid = "f-bbbbbbbbbb"
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid(*[(fid, i) for i in range(6)])
    segs = [_seg(fid, 0, ["o0"], 0, 0, tier="co-changed"),
            _seg(fid, 1, ["o1", "o2", "o3", "o4", "o5"], 1, 5, tier="thematic")]
    lines = render_graph_lines(m, hist, segs, color=False)
    lane = next(ln for ln in lines if fid[:10] in ln)
    assert "[" in lane and "]" in lane   # co-changed car
    assert "(" in lane and ")" in lane   # thematic car


def test_render_links_hidden_by_default_and_shown_with_show_links():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])],
         "edges": [{"a": "A", "b": "B", "weight": 5.0}]}
    hist = _grid(("A", 0), ("B", 1))
    plain = render_graph_lines(m, hist, color=False)
    assert not any("↔" in ln for ln in plain)
    linked = render_graph_lines(m, hist, color=False, show_links=True)
    assert any("↔" in ln for ln in linked)


def test_render_focus_mode_shows_one_lane_full_detail():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1), ("B", 5))
    segs = [_seg("A", 0, ["o0"], 0, 0, label="scaffold"), _seg("A", 1, ["o1"], 1, 1, label="refine")]
    lines = render_graph_lines(m, hist, segs, focus="A", color=False)
    text = "\n".join(lines)
    assert "scaffold" in text and "refine" in text
    assert "B" not in text  # only A's lane detail is drawn, not B's


def test_render_focus_mode_on_unknown_feature_reports_no_lane():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    lines = render_graph_lines(m, _grid(("A", 0)), focus="nope", color=False)
    assert any("nope" in ln for ln in lines)


def test_render_focus_mode_resolves_a_unique_id_prefix_or_label():
    """The graph prints a 10-char id prefix as each lane's handle -- `--focus` must accept that
    same prefix back, and a case-insensitive label, not just the full id."""
    fid = "f-0575f655extralongid"
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid((fid, 0))
    segs = [_seg(fid, 0, ["o0"], 0, 0, label="chapter one")]
    by_prefix = render_graph_lines(m, hist, segs, focus="f-0575f655", color=False)
    assert any("chapter one" in ln for ln in by_prefix)
    by_label = render_graph_lines(m, hist, segs, focus=fid.upper(), color=False)  # label = id.upper()
    assert any("chapter one" in ln for ln in by_label)


def test_render_swimlane_header_present_for_expanded_subsystem():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])], "edges": []}
    lines = render_graph_lines(m, _grid(("F1", 0), ("F2", 40)), color=False)
    assert any("▾" in ln and "feat" in ln for ln in lines)  # the subsystem swimlane header row


def test_render_frontier_note_present_when_folded():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    lines = render_graph_lines(m, _grid(("A", 0), ("A", 5)), frontier=3, color=False)
    assert any("frontier" in ln for ln in lines)


# ── TUI overlay (Textual) ──────────────────────────────────────────────────────────────────────

def test_graph_screen_opens_and_scrubs(tmp_path):
    pytest.importorskip("textual")
    import asyncio

    from sgt.core.lens import get
    from sgt.lens.map import build_map
    from sgt.tui.app import GraphScreen, SgtTui
    from tests.laws import corpus

    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    build_map(repo)

    async def drive():
        app = SgtTui(str(repo))
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            assert isinstance(app.screen, GraphScreen)
            # scrub the frontier older, then back to HEAD -- neither should raise
            await pilot.press("left")
            await pilot.pause()
            await pilot.press("home")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(drive())
