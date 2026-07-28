"""The terminal feature-timeline (Gantt) layout + renderer (`sgt.tui.graph`), and the TUI overlay.

`graph_layout` is the Python counterpart of the VS Code `computeGraphLayout` and is held to the
same contract (see tests/test_graph_layout.py): one lane per feature with first/last/commits,
lanes grouped into subsystem swimlane headers and ordered by first appearance, collapsed
subsystems roll up, a frontier filters ops, deterministic. The pure-function tests need no repo;
the pilot test (Textual) boots the real app and opens the graph screen.
"""

from __future__ import annotations

import pytest

from sgt.tui.graph import (
    _min_unique_prefixes,
    graph_layout,
    render_collab_preview_lines,
    render_graph_lines,
    render_verb_preview_lines,
    segment_layout,
)


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


def test_feature_shared_by_two_subsystems_is_one_rowed_lane():
    # The map is a DAG: feature F is a child of both subsystems S1 and S2. The visibility walk
    # reaches it via both, but it must resolve to a single lane -- a duplicate lane shares an id, and
    # the id-keyed lane table would drop all but the last copy, leaving a stray lane with no `row`
    # (which crashed render_graph_lines with KeyError: 'row').
    m = {"roots": ["S1", "S2"],
         "nodes": [_node("S1", None, ["F"], kind="subsystem"),
                   _node("S2", None, ["F"], kind="subsystem"),
                   _node("F", "S1", [])],
         "edges": []}
    grid = _grid(("F", 0), ("F", 3))
    out = graph_layout(m, grid)
    assert [l["id"] for l in out["lanes"]] == ["F"]  # exactly one lane, not two
    assert all("row" in l for l in out["lanes"])  # every lane is placed
    # The renderer that crashed with KeyError: 'row' now completes.
    render_graph_lines(m, grid, color=False)


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


def test_render_overview_lists_checkpoint_chips_not_a_count_and_drops_the_f_tag():
    """The default (`timeline=False`) surface is a compact per-feature overview: a bare-hex handle
    (no `f-` tag), an op-density sparkline, and the checkpoints spelled out as chips -- NOT the
    opaque `✦N` count and NOT the positioned car rail (no `@n` digits, no wrapping chapter line)."""
    fid = "f-aaaaaaaaaa"  # digit-free id; labels below are digit-free so only a rail/count would add digits
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid((fid, 0), (fid, 100), (fid, 199))
    segs = [_seg(fid, 0, ["o0"], 0, 0, label="scaffold"),
            _seg(fid, 1, ["o1", "o2"], 100, 199, label="refine")]
    lines = render_graph_lines(m, hist, segs, color=False)
    lane = next(ln for ln in lines if "aaaaaaaa" in ln)
    assert fid[:10] not in lane                              # the `f-` tag is gone from the handle
    assert "aaaaaaaa" in lane                                # bare-hex copy token
    assert "scaffold" in lane and "refine" in lane           # checkpoints listed by name, not counted
    assert "✦" not in lane                                   # no opaque ✦N count in the overview
    assert any(ch in lane for ch in "▁▂▃▄▅▆▇█·")              # a density sparkline, not digit cars
    assert not any("@0" in ln or "@1" in ln for ln in lines)  # no wrapping chapter line
    assert any("op-density" in ln for ln in lines)           # legend explains the sparkline
    # no segments -> no chips, no count
    plain = render_graph_lines(m, _grid((fid, 0)), color=False)
    plain_lane = next(ln for ln in plain if "aaaaaaaa" in ln)
    assert "✦" not in plain_lane


def test_render_timeline_draws_the_car_rail_with_digits_and_chapter_line():
    """`timeline=True` preserves the shared-commit-time rail: each checkpoint is a bracketed car
    carrying its `@n` digit in `seg_index` order, with a spelled-out chapter line beneath the lane."""
    fid = "f-aaaaaaaaaa"
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid((fid, 0), (fid, 100), (fid, 199))
    segs = [_seg(fid, 0, ["o0"], 0, 0), _seg(fid, 1, ["o1", "o2"], 100, 199)]
    lines = render_graph_lines(m, hist, segs, color=False, timeline=True)
    lane = next(ln for ln in lines if fid[2:10] in ln and "✦2" in ln)  # handle is bare hex (no `f-`)
    strip = lane.split("✦")[0]
    assert "0" in strip and "1" in strip              # both cars' @n digits drawn
    assert strip.index("0") < strip.index("1")        # @0 sits left of @1 (seg_index order)
    assert any("@0" in ln for ln in lines)            # the chapter line spells the checkpoints out


def test_render_car_widths_reflect_op_count_and_tier_brackets():
    fid = "f-bbbbbbbbbb"
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid(*[(fid, i) for i in range(6)])
    segs = [_seg(fid, 0, ["o0"], 0, 0, tier="co-changed"),
            _seg(fid, 1, ["o1", "o2", "o3", "o4", "o5"], 1, 5, tier="thematic")]
    lines = render_graph_lines(m, hist, segs, color=False, timeline=True)
    lane = next(ln for ln in lines if fid[2:10] in ln)  # handle is bare hex (no `f-`)
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
    by_bare = render_graph_lines(m, hist, segs, focus="0575f655", color=False)  # bare hex, the token the gutter prints
    assert any("chapter one" in ln for ln in by_bare)
    by_label = render_graph_lines(m, hist, segs, focus=fid.upper(), color=False)  # label = id.upper()
    assert any("chapter one" in ln for ln in by_label)


def test_render_overview_blank_line_separates_subsystem_groups():
    """Whitespace carries the hierarchy: in the overview each subsystem group is preceded by a blank
    line (except when it opens the list) and its features are indented under the header."""
    m = {"roots": ["N0", "N1"],
         "nodes": [_node("N0", None, ["F1"], kind="subsystem"), _node("F1", "N0", []),
                   _node("N1", None, ["F2"], kind="subsystem"), _node("F2", "N1", [])],
         "edges": []}
    lines = render_graph_lines(m, _grid(("F1", 0), ("F2", 40)), color=False)
    headers = [i for i, ln in enumerate(lines) if "▾" in ln]
    assert len(headers) == 2
    assert lines[headers[1] - 1] == ""                          # blank line before the 2nd subsystem group
    feat_rows = [ln for ln in lines if "●" in ln]
    assert feat_rows and all(ln.startswith("   ") for ln in feat_rows)  # features indented under their header


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


# ── jj-style minimal-unique-prefix handles ──────────────────────────────────────────────────────


def test_min_unique_prefixes_grow_until_the_prefix_is_unique():
    """Each id's bright prefix is the shortest length (>= floor) that no other id shares -- jj's
    disambiguator. A globally-distinct id stays at the floor; a near-collision grows to the first
    differing char."""
    ids = ["f-abcdefaa", "f-abcdefbb", "f-zzz111111"]
    out = _min_unique_prefixes(ids, floor=5, cap=10)
    assert out["f-zzz111111"] == 5                 # unique by char 5 -> stays at the floor
    assert out["f-abcdefaa"] == 9                  # shares "f-abcdef" with its sibling -> grows to 9
    assert out["f-abcdefbb"] == 9
    # never exceeds the id length or the cap
    assert all(out[i] <= min(len(i), 10) for i in ids)


def test_overview_sparkline_is_segmented_by_checkpoint():
    """The default overview density bar draws one region per checkpoint (car), joined by a `│`
    rewind boundary -- so a feature with N checkpoints shows N-1 separators inside its bar."""
    fid = "f-aaaaaaaaaa"
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid((fid, 0), (fid, 100), (fid, 199))
    segs = [_seg(fid, 0, ["o0"], 0, 0), _seg(fid, 1, ["o1", "o2"], 100, 199)]
    lines = render_graph_lines(m, hist, segs, color=False)
    lane = next(ln for ln in lines if fid[2:10] in ln)  # handle is bare hex (no `f-`)
    assert "│" in lane                             # a rewind boundary between the two checkpoints
    # one checkpoint -> no separator inside the bar (only the two-car lane gets a `│`)
    one = render_graph_lines(m, _grid((fid, 0)), [_seg(fid, 0, ["o0"], 0, 0)], color=False)
    one_lane = next(ln for ln in one if fid[2:10] in ln)
    assert "│" not in one_lane


# ── feedforward verb-preview graph ───────────────────────────────────────────────────────────────


def _preview_fixture():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1), ("A", 2), ("B", 5))  # o0,o1,o2 -> A ; o3 -> B
    segs = [_seg("A", 0, ["o0"], 0, 0, label="scaffold"),
            _seg("A", 1, ["o1", "o2"], 1, 2, label="refine"),
            _seg("B", 0, ["o3"], 5, 5, label="b thing")]
    return m, hist, segs


def test_render_verb_preview_marks_removed_checkpoints_and_the_blast():
    """A revert feedforward: the target feature's fully-removed checkpoints are marked (the first
    with `▸`, the rest `✗`), the reached feature carries its `before → after` count + role from the
    focus subgraph, and the summary counts ops + affected features."""
    m, hist, segs = _preview_fixture()
    preview_view = {
        "verb": "revert", "target": "A", "removed": ["o0", "o1", "o2"], "added": [],
        "focus": {
            "so_what": "", "context_count": 2, "edges": [],
            "nodes": [{"feature_id": "A", "label": "A", "role": "target", "ops_before": 3, "ops_after": 0},
                      {"feature_id": "B", "label": "B", "role": "foundation", "ops_before": 1, "ops_after": 1}],
        },
        "files": {"src/a.py": "--- a\n+++ b\n"},
    }
    lines = render_verb_preview_lines(m, hist, segs, preview_view, focus_fid="A", color=False)
    text = "\n".join(lines)
    assert "rewind" in text                                   # the header verb
    assert "3→0 op" in text                                   # the target lane's morph count
    assert "░" in text                                        # the magnitude bar ghosts the leaving ops
    assert "▸" in text and "✗" in text                        # first gone car ▸, subsequent ✗
    assert "op removed" in text                               # per-checkpoint removal note
    assert "also affected" in text and "prerequisite, kept" in text    # B, foundation, unchanged
    assert "1→1" in text                                      # B's before → after
    assert "2 unchanged feature(s)" in text                   # the dim context floor
    assert "removes 3 op" in text and "1 other feature" in text        # summary


def test_render_verb_preview_before_frame_shows_checkpoints_still_present():
    """The `before` frame of the morph: the same removed checkpoints are drawn still present
    (flagged for what the edit *will* do), so the user can compare against the default `after` frame
    that ghosts them with `✗`."""
    m, hist, segs = _preview_fixture()
    preview_view = {
        "verb": "revert", "target": "A", "removed": ["o0", "o1", "o2"], "added": [],
        "focus": {"so_what": "", "context_count": 0, "edges": [],
                  "nodes": [{"feature_id": "A", "label": "A", "role": "target", "ops_before": 3, "ops_after": 0}]},
        "files": {},
    }
    after = "\n".join(render_verb_preview_lines(m, hist, segs, preview_view, focus_fid="A", color=False))
    before = "\n".join(render_verb_preview_lines(m, hist, segs, preview_view, focus_fid="A", color=False, frame="before"))
    assert "✗" in after and "op removed" in after             # after ghosts the removed checkpoints
    assert "✗" not in before and "will remov" in before       # before keeps them, flags the intent
    assert after.count("░") > before.count("░")               # the magnitude bar empties in the after frame
    assert "showing before" in before                          # the summary names the frame


def test_render_verb_preview_restore_shows_restored_and_partial():
    """A restore feedforward marks the re-added slice: a checkpoint whose ops are all restored
    reads as fully touched, one only partly restored as `◐`, and the summary says `restores`."""
    m, hist, segs = _preview_fixture()
    preview_view = {
        "verb": "restore", "target": "A", "removed": [], "added": ["o0", "o1"],
        "focus": {"so_what": "", "context_count": 1, "edges": [],
                  "nodes": [{"feature_id": "A", "label": "A", "role": "target", "ops_before": 1, "ops_after": 3}]},
        "files": {},
    }
    lines = render_verb_preview_lines(m, hist, segs, preview_view, focus_fid="A", color=False)
    text = "\n".join(lines)
    assert "restore" in text
    assert "restored" in text                                 # seg0 (o0) fully re-added
    assert "◐" in text                                        # seg1 (o1 of o1,o2) partly re-added
    assert "restores 2 op" in text


def test_render_collab_preview_clean_land_shows_ops_and_the_oracle_gate():
    """A clean land feedforward: the op count it advances the branch by, and the LAW-G oracle gate
    the confirm will run. No fork lines, and the summary says it advances + is one-way."""
    pv = {"verb": "land", "target": "main", "clean": True, "ops_added": 3, "forks": [],
          "oracle_configured": True, "pin_contradictions": [], "declared_cycles": []}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "land" in text and "main" in text
    assert "adds 3 op(s) to main" in text
    assert "oracle: green required" in text
    assert "advances main by 3 op" in text and "not auto-undoable" in text


def test_render_collab_preview_fork_blocks_and_names_the_merge_op_remedy():
    """A fork blocks the land: it's listed with the exact `sgt merge-op` remedy, the oracle is
    reported as not reached, and the summary says it won't advance."""
    pv = {"verb": "land", "target": "main", "clean": True, "ops_added": 0,
          "forks": [["api.py::route", "0ee9a65f11aa", "5e6eaf5822bb"]],
          "oracle_configured": True, "pin_contradictions": [], "declared_cycles": []}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "1 fork(s) block the land" in text
    assert "api.py::route" in text and "sgt merge-op 0ee9a65f 5e6eaf58" in text
    assert "oracle: not reached" in text
    assert "adds 0 op" not in text          # the noisy zero-op line is suppressed under a blocking fork
    assert "won't advance" in text


def test_render_collab_preview_no_oracle_reports_law_g_refusal():
    pv = {"verb": "land", "target": "main", "clean": True, "ops_added": 2, "forks": [],
          "oracle_configured": False, "pin_contradictions": [], "declared_cycles": []}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "oracle: none configured" in text and "LAW-G" in text
    assert "won't advance" in text


def test_render_collab_preview_unclean_plan_renders_the_error_alone():
    pv = {"verb": "land", "target": "main", "clean": False, "error": "working tree not clean"}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "working tree not clean" in text


def test_render_collab_preview_clean_sync_brings_in_ops_with_no_forks():
    """A fork-free sync: the op count it folds in, and a tail that says no forks + one-way. No
    fork-surface block, no R12 recovery warnings."""
    pv = {"verb": "sync", "remote": "origin", "target": "main", "ops_added": 4, "forks": [],
          "pin_contradictions": [], "declared_cycles": [], "base_recovery": "mined",
          "theirs_recovery": "mined"}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "sync" in text and "origin/main" in text
    assert "brings in 4 op(s)" in text
    assert "fork(s) surface" not in text
    assert "folds in 4 op · no forks · not auto-undoable" in text


def test_render_collab_preview_sync_fork_surfaces_without_blocking():
    """A sync fork *surfaces* (work waits at the common ancestor) rather than blocking: the fork is
    drawn with its `merge-op` remedy and the tail counts it, but the fold still happens."""
    pv = {"verb": "sync", "remote": "origin", "target": "main", "ops_added": 2,
          "forks": [["api.py::route", "0ee9a65f11aa", "5e6eaf5822bb"]],
          "pin_contradictions": [], "declared_cycles": [], "base_recovery": "mined",
          "theirs_recovery": "mined"}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "1 fork(s) surface" in text and "nothing is lost" in text
    assert "api.py::route" in text and "sgt merge-op 0ee9a65f 5e6eaf58" in text
    assert "folds in 2 op · 1 fork(s) surface to resolve · not auto-undoable" in text


def test_render_collab_preview_sync_surfaces_degraded_recovery_loudly():
    """R12: a sync that fell back to union semantics (no witnessed merge-base) or a lost-provenance
    tip says so loudly -- never silent."""
    pv = {"verb": "sync", "remote": "origin", "target": "main", "ops_added": 1, "forks": [],
          "pin_contradictions": [], "declared_cycles": [], "base_recovery": "none",
          "theirs_recovery": "none"}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "base recovery: none" in text
    assert "theirs' tip has sgt ops but no witnessed trailers" in text


def test_render_collab_preview_resolve_shows_the_three_step_remedy_and_oracle():
    """A clean resolve feedforward: the two fork tips, the numbered fulfill/oracle/land steps, and
    the green-required oracle gate."""
    pv = {"verb": "resolve", "target": "api.py::route", "clean": True,
          "tips": ["0ee9a65f11aa", "5e6eaf5822bb"], "oracle_configured": True}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "resolve" in text and "api.py::route" in text
    assert "0ee9a65f" in text and "5e6eaf58" in text
    assert "fulfill your merged edit" in text
    assert "run the oracle" in text
    assert "land it" in text
    assert "oracle: green required" in text
    assert "fulfill + oracle + land · closes the fork on api.py::route · not auto-undoable" in text


def test_render_collab_preview_resolve_without_a_draft_renders_the_error_alone():
    pv = {"verb": "resolve", "target": "api.py::route", "clean": False,
          "error": "no drafted reconciliation — run `sgt resolve api.py::route` first"}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "no drafted reconciliation" in text
    assert "fulfill" not in text          # the step list is suppressed when the plan is unclean


def test_render_collab_preview_resolve_with_no_oracle_warns_it_lands_unverified():
    pv = {"verb": "resolve", "target": "api.py::route", "clean": True,
          "tips": ["0ee9a65f11aa", "5e6eaf5822bb"], "oracle_configured": False}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "oracle: none configured" in text and "lands unverified" in text
