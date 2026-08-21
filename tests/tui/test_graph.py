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
    _NOW_RULE,
    _chips,
    _forecast_band,
    _min_unique_prefixes,
    _reverted_gap_note,
    _state_banner,
    graph_layout,
    render_collab_preview_lines,
    render_graph_lines,
    render_rail_lines,
    render_save_list_lines,
    render_verb_preview_lines,
    resolve_focus_group,
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
         label=None, tier="co-changed", source="fallback", words=None):
    return {"feature_id": feature_id, "seg_index": seg_index,
            "checkpoint": f"{feature_id}@{seg_index}", "intent": label or f"seg {seg_index}",
            "rationale": "", "op_ids": list(op_ids), "op_count": len(op_ids),
            "commit_shas": [], "first_index": first_index, "last_index": last_index,
            "novelty": 0.0, "tier": tier, "source": source, "words": words or []}


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


def test_feature_with_no_own_symbols_is_dropped_and_a_husk_only_subsystem_with_it():
    """The drop rule, stated once here so the JS counterpart (`tests/test_graph_layout.py`) can be
    held to the same one: a feature whose ops touch only sentinels draws no lane, and a subsystem
    left with nothing but husks draws no header."""
    husk, real = _node("HUSK", "N0", []), _node("REAL", None, [])
    husk["own_symbols"], real["own_symbols"] = [], ["a.py::f"]
    m = {"roots": ["N0", "REAL"],
         "nodes": [_node("N0", None, ["HUSK"], kind="subsystem"), husk, real], "edges": []}
    out = graph_layout(m, _grid(("HUSK", 0), ("REAL", 1)))
    assert [l["id"] for l in out["lanes"]] == ["REAL"]
    assert out["headers"] == []


def test_the_subsystem_count_does_not_change_when_a_row_is_folded():
    """The twin of the feature count's own fix, one field over. A collapsed subsystem leaves
    `headers` and becomes a meta-LANE, so counting headers made the repo lose subsystems every time
    a row folded: the default map (which folds every leaf subsystem) printed `1 subsystem` where
    `--focus`, which folds nothing, printed `4` for the same repo at the same moment. Those are two
    headers a reader is explicitly told to move between, so the disagreement reads as one of the two
    views being wrong about what the codebase contains."""
    m = {"roots": ["R"],
         "nodes": [_node("R", None, ["S1", "S2"], kind="subsystem"),
                   _node("S1", "R", ["f1"], kind="subsystem"), _node("f1", "S1", []),
                   _node("S2", "R", ["f2"], kind="subsystem"), _node("f2", "S2", [])],
         "edges": []}
    grid = _grid(("f1", 0), ("f2", 1))
    opened = render_graph_lines(m, grid, color=False)[0]
    folded = render_graph_lines(m, grid, color=False, collapsed=("S1", "S2"))[0]
    # Two, not three: `R` is the root, which holds every feature in the repo, so it is the repo and
    # not one of the groupings inside it. Counting it told a reader of a repo with no subsystems at
    # all that it had one.
    assert "2 subsystems" in opened and "2 subsystems" in folded
    assert "2 features" in opened and "2 features" in folded  # the count already held


def test_a_husk_is_not_counted_in_the_group_it_was_dropped_from():
    """Dropping a husk from the listing has to drop it from the count too. `Name (N)` on a folded row
    and the header's `N feature(s)` are both `len(leaves)`, so a husk left in the leaf set made the
    map promise rows that opening the fold does not deliver -- and put the map's headline total four
    features above the same repo's `sgt log --tree` on the pilot fixture."""
    husk, real = _node("HUSK", "N0", []), _node("REAL_IN", "N0", [])
    husk["own_symbols"], real["own_symbols"] = [], ["a.py::f"]
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["HUSK", "REAL_IN"], kind="subsystem"), husk, real],
         "edges": []}
    folded = graph_layout(m, _grid(("HUSK", 0), ("REAL_IN", 1)), collapsed={"N0"})
    assert [l["id"] for l in folded["lanes"]] == ["N0"]
    assert folded["lanes"][0]["leaves"] == ["REAL_IN"]  # the count the `(N)` suffix prints
    # Open the same group: exactly the leaves the fold claimed, no more and no fewer.
    opened = graph_layout(m, _grid(("HUSK", 0), ("REAL_IN", 1)))
    assert [l["id"] for l in opened["lanes"]] == ["REAL_IN"]
    assert opened["headers"][0]["lane_count"] == 1


def test_nested_subsystems_indent_by_depth_in_tree_order():
    """Rows follow TREE order and carry their nesting depth -- the same contract
    `tests/test_graph_layout.py` holds `computeGraphLayout` to. A flat group list sorted globally by
    first appearance printed a nested subsystem wherever its first commit fell, routinely *above* the
    header of the parent containing it, which is why `sgt log --map` could not be matched against the
    workbench or the sidebar on the pilot's repo."""
    m = {"roots": ["R"],
         "nodes": [_node("R", None, ["S", "F0"], kind="subsystem"),
                   _node("S", "R", ["F1", "F2"], kind="subsystem"),
                   _node("F1", "S", []), _node("F2", "S", []), _node("F0", "R", [])],
         "edges": []}
    # F1 born first (0) so S sorts before the later-born F0 (5) within R.
    out = graph_layout(m, _grid(("F1", 0), ("F2", 10), ("F0", 5)))
    headers = {h["collapsed_id"]: h for h in out["headers"]}
    assert headers["R"]["depth"] == 0 and headers["S"]["depth"] == 1  # S nests under R
    lanes = out["node_by_id"]
    assert lanes["F1"]["depth"] == 2 and lanes["F2"]["depth"] == 2  # features under the nested S
    assert lanes["F0"]["depth"] == 1  # a direct feature of R
    assert headers["R"]["row"] < headers["S"]["row"] < lanes["F1"]["row"] < lanes["F0"]["row"]
    assert headers["R"]["op_count"] == 3 and headers["R"]["lane_count"] == 3  # rolls up all 3


def test_a_collapsed_child_subsystem_counts_its_features_in_the_parents_header():
    """A header's `lane_count` is a count of FEATURES, not of rows. It used to be `len(lane_objs)`, so
    a parent sitting above a collapsed child reported that whole child as one feature and disagreed
    with the workbench and the sidebar about the size of the same group."""
    m = {"roots": ["R"],
         "nodes": [_node("R", None, ["S"], kind="subsystem"),
                   _node("S", "R", ["F1", "F2"], kind="subsystem"),
                   _node("F1", "S", []), _node("F2", "S", [])],
         "edges": []}
    out = graph_layout(m, _grid(("F1", 0), ("F2", 10)), collapsed=["S"])
    assert [l["id"] for l in out["lanes"]] == ["S"]  # S folded to one meta-lane...
    hd = out["headers"][0]
    assert hd["collapsed_id"] == "R" and hd["lane_count"] == 2  # ...but R still says 2 features


def test_nesting_is_drawn_inside_the_title_column_so_the_commit_axis_stays_aligned():
    """The indent goes in the label field, never ahead of it: every row's density bar starts at the
    same column, so the shared commit axis is a straight line down the page."""
    m = {"roots": ["R"],
         "nodes": [_node("R", None, ["S", "F0"], kind="subsystem"),
                   _node("S", "R", ["F1"], kind="subsystem"),
                   _node("F1", "S", []), _node("F0", "R", [])],
         "edges": []}
    lines = render_graph_lines(m, _grid(("F1", 0), ("F0", 20)), color=False, bar_width=12)
    rows = [ln for ln in lines if "●" in ln]
    assert len(rows) == 2
    # Fixed geometry: the label field is padded back to the same total width whatever the indent, so
    # every row is the same length and the density bar occupies the same columns on all of them.
    assert len({len(ln) for ln in rows}) == 1
    assert len({ln.index("●") for ln in rows}) == 1
    nested = next(ln for ln in rows if "● F1" in ln)
    flat = next(ln for ln in rows if "● F0" in ln)
    assert nested.rindex("F1") == flat.rindex("F0") + 2  # the deeper lane's label steps in by 2
    hdrs = [ln for ln in lines if "▾" in ln]
    assert hdrs[0].index("▾") < hdrs[1].index("▾")   # and so does the nested header


def test_a_feature_in_no_subsystem_sits_left_of_one_that_is_in_a_group():
    """A save lands in no subsystem until the next regrouping, and this view drew it at the same
    column as the members of the band above it -- so a just-saved feature read as belonging to a
    subsystem it is not in, which is exactly the row a pilot participant could not find. `sgt log
    --tree` and the workbench both indent from the root; this row now does too."""
    m = {"roots": ["R", "LOOSE"],
         "nodes": [_node("R", None, ["F1"], kind="subsystem"),
                   _node("F1", "R", []), _node("LOOSE", None, [])],
         "edges": []}
    lines = render_graph_lines(m, _grid(("F1", 0), ("LOOSE", 20)), color=False, bar_width=12)
    rows = [ln for ln in lines if "●" in ln]
    member = next(ln for ln in rows if "F1" in ln)
    loose = next(ln for ln in rows if "LOOSE" in ln)
    assert member.rindex("F1") == loose.rindex("LOOSE") + 2  # the filed one steps in, the loose one doesn't
    assert len({len(ln) for ln in rows}) == 1  # and the commit axis stays aligned


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


def test_focus_view_renders_a_chapters_captured_words():
    """The zoom (`sgt log --focus <feature>`) shows each chapter in the user's own words -- the
    intent-ledger P1 payoff: 'the history answers in my own words'. Words the projection carried on
    the segment reach the render; a chapter with none simply omits them (never a guessed reason)."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1))
    segs = [
        _seg("A", 0, ["o0"], 0, 0, label="add clear cmd", words=["remove all completed tasks"]),
        _seg("A", 1, ["o1"], 1, 1, label="polish", words=[]),
    ]
    body = "\n".join(render_graph_lines(m, hist, segs, focus="A", color=False))
    assert "remove all completed tasks" in body  # chapter 0's words are on screen
    assert "add clear cmd" in body and "polish" in body  # both chapter labels still render


def test_focus_view_caps_the_words_shown_per_chapter():
    """A busy multi-commit chapter shows at most three captured words with a '+N more' tail, so the
    zoom stays scannable rather than becoming a wall of prose (Epicea's information-overload
    warning)."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0))
    segs = [_seg("A", 0, ["o0"], 0, 0, words=[f"turn {i}" for i in range(5)])]
    body = "\n".join(render_graph_lines(m, hist, segs, focus="A", color=False))
    assert "turn 0" in body and "turn 2" in body
    assert "turn 3" not in body and "turn 4" not in body
    assert "+2 more" in body


def test_reverted_work_no_lane_can_draw_is_named_on_screen():
    """`grid_view["reverted_unaccounted"]` is work a revert took out of the ideal that clustering left
    in no lane and no chapter (a reverted symbol is no member of any leaf). Every chapter then reads
    fully present and every lane draws solid while the code is missing from disk. The header has to
    say so, because nothing else on the screen can: the whole point is that these ops have no row."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0))
    hist["reverted_unaccounted"] = {"op_count": 4, "symbols": ["cart.py::apply_coupon"]}
    segs = [_seg("A", 0, ["o0"], 0, 0)]

    for body in ("\n".join(render_graph_lines(m, hist, segs, color=False)),
                 "\n".join(render_graph_lines(m, hist, segs, focus="A", color=False))):
        assert "4 reverted edits" in body
        assert "cart.py::apply_coupon" in body


def test_no_reverted_work_note_when_every_edit_has_a_lane():
    """Silent in the ordinary case -- and silent for a client whose payload predates the field, which
    is no claim either way rather than a report of zero."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0))
    segs = [_seg("A", 0, ["o0"], 0, 0)]
    body = "\n".join(render_graph_lines(m, hist, segs, color=False))
    assert "reverted edit(s)" not in body

    hist["reverted_unaccounted"] = {"op_count": 0, "symbols": []}
    assert "reverted edit(s)" not in "\n".join(render_graph_lines(m, hist, segs, color=False))


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


def test_a_reverted_checkpoint_is_drawn_as_removed_not_as_live():
    """A revert takes a chapter's ops out of the ideal and leaves them in the store -- the asymmetry
    `sgt restore` needs -- so the checkpoint detail must keep the chapter and say it is gone. It used
    to redraw it identically: same solid bar, same `3 checkpoints`, no marker, so the one screen
    that prints the `@n` handles told a user who had just reverted that nothing had happened. `░` is
    the glyph the revert preview already spends on removal, and `sgt restore` is the way back, so
    both belong on the row. A chapter with only *some* of its ops reverted says so as a count rather
    than picking one of the two extremes."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0), ("A", 1), ("A", 2))
    live = _seg("A", 0, ["o0"], 0, 0, label="kept")
    gone = _seg("A", 1, ["o1"], 1, 1, label="rewound")
    part = _seg("A", 2, ["o2", "o3"], 2, 2, label="half")
    live["present_op_count"], gone["present_op_count"], part["present_op_count"] = 1, 0, 1

    cars = segment_layout(m, hist, [live, gone, part])["node_by_id"]["A"]["cars"]
    assert [c["reverted"] for c in cars] == [False, True, False]

    body = "\n".join(render_graph_lines(m, hist, [live, gone, part], focus="A", color=False))
    assert "3 checkpoints" in body and "1 reverted" in body   # the count still counts them
    rewound = next(l for l in body.splitlines() if "rewound" in l)
    assert "░" in rewound and "█" not in rewound                # removed, in the preview's own glyph
    assert "reverted" in rewound and "sgt restore A@1" in rewound
    kept = next(l for l in body.splitlines() if "kept" in l)
    assert "█" in kept and "░" not in kept
    assert "1 of 2 edits reverted" in next(l for l in body.splitlines() if "half" in l)


def test_a_checkpoint_with_no_presence_claim_is_drawn_as_live():
    """`present_op_count` is `None` when the ideal could not be read -- an unborn or unmined ref --
    and every client that predates the field omits it. Absence of a claim is not a claim of removal:
    the row draws exactly as it always did, because guessing "reverted" from a missing key would
    turn an unmined repo into a screen full of phantom rewinds."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    hist = _grid(("A", 0))
    seg = _seg("A", 0, ["o0"], 0, 0, label="unknown")  # `_seg` omits `present_op_count` entirely
    assert segment_layout(m, hist, [seg])["node_by_id"]["A"]["cars"][0]["reverted"] is False
    body = "\n".join(render_graph_lines(m, hist, [seg], focus="A", color=False))
    assert "reverted" not in body and "sgt restore" not in body


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
    assert "2 features" in text
    assert "A" in text and "B" in text  # labels rendered


def test_render_map_lists_positioned_checkpoint_chips_and_drops_the_f_tag():
    """The map surface (formerly split into overview + `--timeline`): a bare-hex handle (no `f-`
    tag), a per-feature-packed edit-density bar, and the checkpoints spelled out as `@n slug` chips
    on their OWN indented sub-line below the bar (so a long label can't wrap the bar) -- the `@n` is
    the revert handle. NOT the opaque `✦N` count."""
    fid = "f-aaaaaaaaaa"
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid((fid, 0), (fid, 100), (fid, 199))
    segs = [_seg(fid, 0, ["o0"], 0, 0, label="scaffold"),
            _seg(fid, 1, ["o1", "o2"], 100, 199, label="refine")]
    lines = render_graph_lines(m, hist, segs, color=False)
    lane_idx = next(i for i, ln in enumerate(lines) if "aaaaaaaa" in ln)
    lane = lines[lane_idx]
    chips = lines[lane_idx + 1]                              # checkpoints ride on the sub-line below
    assert fid[:10] not in lane                              # the `f-` tag is gone from the handle
    assert "aaaaaaaa" in lane                                # bare-hex copy token
    assert "✦" not in lane                                   # no opaque ✦N count
    assert any(ch in lane for ch in "▁▂▃▄▅▆▇█·")              # a packed density bar on the lane line
    assert "scaffold" in chips and "refine" in chips         # checkpoints listed by name (own line)
    assert "@0" in chips and "@1" in chips                   # ...led by their @n revert handle
    assert any("edit density" in ln for ln in lines)         # legend explains the bar
    assert any("shared commit axis" in ln for ln in lines)   # legend states columns line up across rows
    # no segments -> no chips
    plain = render_graph_lines(m, _grid((fid, 0)), color=False)
    plain_lane = next(ln for ln in plain if "aaaaaaaa" in ln)
    assert "✦" not in plain_lane and "@0" not in plain_lane


def test_render_car_draws_tier_brackets():
    """`_render_car` (the shared checkpoint glyph, still used by the feedforward preview) brackets a
    car by its tier: `[ ]` co-changed/coupled, `( )` thematic."""
    from sgt.tui.graph import _render_car

    co = {"seg_index": 0, "tier": "co-changed", "sub_bins": [(0, 1)], "is_future": False}
    th = {"seg_index": 1, "tier": "thematic", "sub_bins": [(1, 5)], "is_future": False}
    assert _render_car(co, 6, "#abcdef", color=False).startswith("[")
    assert _render_car(th, 6, "#abcdef", color=False).startswith("(")


def test_time_bar_draws_a_lanes_gap_to_scale_on_the_shared_axis():
    """A lane that touched commit 0 and commit 199 marks both ends of the strip, with the quiet
    stretch between them left blank.

    This used to pack the two checkpoints back to back with a `┄` standing in for the skipped
    history, which reads one feature's life well but makes the map unreadable as a whole: every row
    became its own clock, so no two rows could be compared. Drawing to scale is what lets a column
    mean the same thing in every row.
    """
    fid = "f-cccccccccc"
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    # 200 commits; the lane touches only commit 0 and commit 199 -- a long quiet stretch between.
    grid = {"commits": [{"index": i} for i in range(200)], "commit_count": 200,
            "cells": [{"feature_id": fid, "commit_index": 0, "op_ids": ["o0"], "op_count": 1,
                       "kinds": {"add": 1}, "fidelity": "full"},
                      {"feature_id": fid, "commit_index": 199, "op_ids": ["o1"], "op_count": 1,
                       "kinds": {"add": 1}, "fidelity": "full"}]}
    segs = [_seg(fid, 0, ["o0"], 0, 0, tier="co-changed"),
            _seg(fid, 1, ["o1"], 199, 199, tier="co-changed")]
    lines = render_graph_lines(m, grid, segs, color=False)
    lane = next(ln for ln in lines if "cccccccc" in ln)
    bar = lane[lane.index("cccccccc") + 8:]
    marks = [i for i, ch in enumerate(bar) if ch in "▁▂▃▄▅▆▇█"]
    assert len(marks) == 2                       # one block per touched commit
    assert marks[1] - marks[0] > 8               # drawn to scale, not packed together
    assert set(bar[marks[0] + 1:marks[1]]) == {" "}  # the quiet stretch is blank


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


def test_render_collapsed_subsystems_are_one_meta_lane_each_no_expand_headers():
    """Map spatial LOD (Phase 3b): a collapsed subsystem renders as a single rolled-up meta-lane --
    no per-feature rows and no `▾` swimlane expand header. This is the counterpart to the expanded
    view above; with no `--focus` the CLI collapses every LEAF subsystem (here N0/N1 are leaves), so
    interior subsystems stay expanded as headers while leaf clusters fold to one row each."""
    m = {"roots": ["N0", "N1"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"), _node("F1", "N0", []),
                   _node("F2", "N0", []),
                   _node("N1", None, ["F3"], kind="subsystem"), _node("F3", "N1", [])],
         "edges": []}
    lines = render_graph_lines(m, _grid(("F1", 0), ("F2", 20), ("F3", 40)), color=False,
                               collapsed=("N0", "N1"))
    assert not any("▾" in ln for ln in lines)                 # nothing is expanded
    # both subsystems roll up to a meta-lane and no leaf feature (F1/F2/F3) gets its own row
    assert not any(f in ln for ln in lines for f in ("F1", "F2", "F3"))


def test_a_collapsed_group_does_not_print_a_build_local_id_in_the_handle_column():
    """Every `●` row's handle is a token a verb accepts. A `◈` row's was not.

    A collapsed subsystem's id is `N<k>` -- a DFS counter minted by `tree._register`, positional and
    therefore different after any reshape -- and no verb resolves it: `resolve_feature` matches leaves
    only, so on the study fixture `sgt show N2` answers "not a known feature, checkpoint, op, or
    symbol" for a token the map had just printed in its copy-paste column. The renderer knew the id
    was short and padded it; it never asked whether it was typeable.

    The group *is* reachable -- by its name, through `--focus` -- so the fix is to print the reachable
    thing and say the verb, not to invent a stable id for a row that is a fold rather than a feature.
    """
    m = {"roots": ["N0"],
         "nodes": [dict(_node("N0", None, ["F1", "F2"], kind="subsystem"), label="Scheduling"),
                   _node("F1", "N0", []), _node("F2", "N0", [])],
         "edges": []}
    lines = render_graph_lines(m, _grid(("F1", 0), ("F2", 20)), color=False, collapsed=("N0",))
    row = next(ln for ln in lines if "Scheduling" in ln)
    # The label may legitimately contain the group's name; the handle column must not carry the id.
    assert "N0" not in row, row
    text = "\n".join(lines)
    assert "--focus" in text, "a folded row needs the verb that opens it"


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


def test_map_density_bar_positions_checkpoints_by_commit_time():
    """The map density bar places each checkpoint at its own commit's column, so two checkpoints far
    apart in commit-time read as two density blocks with blank columns between them -- the gap is
    the boundary, not a `│` separator (which the merged view dropped)."""
    fid = "f-aaaaaaaaaa"
    m = {"roots": [fid], "nodes": [_node(fid, None, [])], "edges": []}
    hist = _grid((fid, 0), (fid, 100), (fid, 199))
    segs = [_seg(fid, 0, ["o0"], 0, 0), _seg(fid, 1, ["o1", "o2"], 100, 199)]
    lines = render_graph_lines(m, hist, segs, color=False)
    lane = next(ln for ln in lines if fid[2:10] in ln)  # handle is bare hex (no `f-`)
    pre = lane.split("  @")[0]                      # handle + label + density bar, before the @n chips
    assert "│" not in pre                           # no rewind-boundary separator anymore
    marks = [i for i, ch in enumerate(pre) if ch in "▁▂▃▄▅▆▇█"]
    assert len(marks) >= 2                          # a positioned density bar
    assert set(pre[marks[0] + 1:marks[-1]]) - set("▁▂▃▄▅▆▇█") == {" "}  # quiet columns, blank


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
    assert "3→0 edits" in text                                # the target lane's morph count
    assert "░" in text                                        # the magnitude bar ghosts the leaving ops
    assert "▸" in text and "✗" in text                        # first gone car ▸, subsequent ✗
    assert "· removed" in text                                # per-checkpoint removal note
    assert "also affected" in text and "prerequisite, kept" in text    # B, foundation, unchanged
    assert "2 other features unchanged" in text             # the dim context floor
    assert "removes 3 edits" in text and "src/a.py" in text          # summary names the files


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
    assert "✗" in after and "· removed" in after              # after ghosts the removed checkpoints
    assert "✗" not in before and "will be removed" in before  # before keeps them, flags the intent
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
    assert "restores 2 edits" in text


def test_render_collab_preview_clean_land_shows_ops_and_the_oracle_gate():
    """A clean land feedforward: the op count it advances the branch by, and the LAW-G oracle gate
    the confirm will run. No fork lines, and the summary says it advances + is one-way."""
    pv = {"verb": "land", "target": "main", "clean": True, "ops_added": 3, "forks": [],
          "oracle_configured": True, "pin_contradictions": [], "declared_cycles": []}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "land" in text and "main" in text
    assert "adds 3 ops to main" in text
    assert "oracle: green required" in text
    assert "advances main by 3 op" in text and "not auto-undoable" in text


def test_render_collab_preview_fork_blocks_and_names_the_resolve_remedy():
    """A fork blocks the land: it's listed with the high-level `sgt resolve <symbol>` remedy, the
    oracle is reported as not reached, and the summary says it won't advance."""
    pv = {"verb": "land", "target": "main", "clean": True, "ops_added": 0,
          "forks": [["api.py::route", "0ee9a65f11aa", "5e6eaf5822bb"]],
          "oracle_configured": True, "pin_contradictions": [], "declared_cycles": []}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "1 fork block the land" in text
    assert "api.py::route" in text and "sgt resolve api.py::route" in text
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
    assert "brings in 4 ops" in text
    assert "fork(s) surface" not in text
    assert "folds in 4 op · no forks · not auto-undoable" in text


def test_render_collab_preview_sync_fork_surfaces_without_blocking():
    """A sync fork *surfaces* (work waits at the common ancestor) rather than blocking: the fork is
    drawn with its `sgt resolve <symbol>` remedy and the tail counts it, but the fold still happens."""
    pv = {"verb": "sync", "remote": "origin", "target": "main", "ops_added": 2,
          "forks": [["api.py::route", "0ee9a65f11aa", "5e6eaf5822bb"]],
          "pin_contradictions": [], "declared_cycles": [], "base_recovery": "mined",
          "theirs_recovery": "mined"}
    text = "\n".join(render_collab_preview_lines(pv, color=False))
    assert "1 fork surface" in text and "nothing is lost" in text
    assert "api.py::route" in text and "sgt resolve api.py::route" in text
    assert "folds in 2 op · 1 fork surface to resolve · not auto-undoable" in text


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


# ── group focus + state markers (--focus <subsystem|theme>, fork/merge banner) ───────────────────


def _sub_map():
    """A map_view with two subsystems over feature leaves -- the shape resolve_focus_group walks."""
    return {"nodes": [
        {"id": "N1", "kind": "subsystem", "label": "Comms", "children": ["fa", "fb"]},
        {"id": "fa", "kind": "feature", "label": "Wire Codec", "members": ["s1"]},
        {"id": "fb", "kind": "feature", "label": "Remote Bus", "members": ["s2"]},
        {"id": "N2", "kind": "subsystem", "label": "State", "children": ["fc"]},
        {"id": "fc", "kind": "feature", "label": "RGA", "members": ["s3"]},
    ]}


def test_resolve_focus_group_subsystem_by_id_prefix_returns_its_feature_leaves():
    g = resolve_focus_group("N1", _sub_map(), {"commits": [], "cells": []})
    assert g == {"label": "Comms", "kind": "subsystem", "feature_ids": {"fa", "fb"}}


def test_resolve_focus_group_subsystem_by_label_is_case_insensitive():
    g = resolve_focus_group("comms", _sub_map(), {"commits": [], "cells": []})
    assert g["kind"] == "subsystem" and g["feature_ids"] == {"fa", "fb"}


def test_resolve_focus_group_theme_joins_its_commits_to_the_touched_features():
    grid = {"commits": [{"sha": "shaX", "index": 5}, {"sha": "shaY", "index": 9}],
            "cells": [{"feature_id": "fa", "commit_index": 5},
                      {"feature_id": "fc", "commit_index": 9}]}
    themes = {"t1": {"label": "Realtime", "atom_shas": ["shaX"]}}
    g = resolve_focus_group("realtime", _sub_map(), grid, themes)
    assert g == {"label": "Realtime", "kind": "theme", "feature_ids": {"fa"}}


def test_resolve_focus_group_returns_none_for_a_single_feature_so_caller_uses_the_map_path():
    # A feature id/unknown ref names no group -> None, so the CLI falls through to the single-lane
    # render_graph_lines(focus=...) detail path rather than the vertical group view.
    assert resolve_focus_group("fa", _sub_map(), {"commits": [], "cells": []}) is None
    assert resolve_focus_group("nope", _sub_map(), {"commits": [], "cells": []}) is None


def test_a_name_that_is_both_a_feature_and_a_theme_resolves_to_the_feature():
    """A theme is minted per save and labelled with the save message, so any feature still carrying
    its save-message label collides by construction -- on the pilot fixture, 11 of 17 feature names
    were also theme names. The theme used to win, so `sgt log --focus "agenda export"` answered with
    a save rail for whatever features that theme's commits touched, and the feature's own checkpoint
    detail -- the `@n` handles the map's chips tell you to rewind by, and the only screen that
    prints them -- was unreachable for two thirds of the map. `--focus`'s metavar is FEATURE and two
    footers advertise it as "its checkpoints", so the feature is what the name must resolve to."""
    m = _sub_map()
    grid = {"commits": [{"sha": "shaX", "index": 5}], "cells": [{"feature_id": "fa", "commit_index": 5}]}
    themes = {"t1": {"label": "Wire Codec", "atom_shas": ["shaX"]}}  # same name as feature `fa`
    assert resolve_focus_group("Wire Codec", m, grid, themes) is None
    assert resolve_focus_group("wire codec", m, grid, themes) is None  # and case-insensitively
    # A theme name that is NOT a feature name still resolves, so the group view is intact.
    themes["t2"] = {"label": "Realtime", "atom_shas": ["shaX"]}
    assert resolve_focus_group("realtime", m, grid, themes)["kind"] == "theme"


def test_a_name_that_is_both_a_feature_and_a_subsystem_resolves_to_the_feature():
    """Promoting a lone feature to its own subsystem gives the two the same label, and the fixture
    had one. The feature detail is the more specific of the two readings and the one `--focus`
    documents, so it wins; the subsystem stays reachable by its id, which the two never share."""
    m = {"nodes": [
        {"id": "N9", "kind": "subsystem", "label": "Slot Grid", "children": ["fz"]},
        {"id": "fz", "kind": "feature", "label": "Slot Grid", "members": ["s9"]},
    ]}
    assert resolve_focus_group("Slot Grid", m, {"commits": [], "cells": []}) is None
    assert resolve_focus_group("N9", m, {"commits": [], "cells": []})["kind"] == "subsystem"


def _rail_grid():
    def cell(f, c, oid):
        return {"feature_id": f, "commit_index": c, "op_ids": [oid], "op_count": 1,
                "kinds": {"add": 1}, "fidelity": "full"}
    return {"commits": [{"index": 0, "sha": "s0", "subject": "add wire"},
                        {"index": 1, "sha": "s1", "subject": "add bus"},
                        {"index": 2, "sha": "s2", "subject": "add rga"}],
            "cells": [cell("fa", 0, "o0"), cell("fb", 1, "o1"), cell("fc", 2, "o2")]}


def test_rail_names_reverted_work_no_lane_can_show():
    """The default `sgt log` is the screen a reader lands on, and it is a lane view like the map: a
    reverted symbol belongs to no leaf, so its ops lose their cell and every lane on the rail draws
    whole while the code is off disk. The map header already says so; saying it on only the second
    screen means the first one still reads as a completed codebase."""
    m = {"nodes": [{"id": "fa", "label": "Wire"}]}
    grid = _rail_grid()
    grid["reverted_unaccounted"] = {"op_count": 4, "symbols": ["cart.py::apply_coupon"]}
    text = "\n".join(render_rail_lines(m, grid, color=False))
    assert "4 reverted edits" in text
    assert "cart.py::apply_coupon" in text
    # and silent when there is nothing to disclose (or nothing claimed)
    assert "reverted edit(s)" not in "\n".join(render_rail_lines(m, _rail_grid(), color=False))


def test_render_rail_only_features_scopes_the_vertical_tree_to_the_group():
    m = {"nodes": [{"id": "fa", "label": "Wire"}, {"id": "fb", "label": "Bus"}, {"id": "fc", "label": "RGA"}]}
    text = "\n".join(render_rail_lines(m, _rail_grid(), color=False,
                                       only_features={"fa", "fb"}, group_label="Comms"))
    assert "focus: Comms" in text and "2 features" in text
    assert "add wire" in text and "add bus" in text
    assert "add rga" not in text  # fc's save is outside the group -> filtered out


def test_state_banner_renders_forks_with_symbol_and_resolve_remedy():
    # stored remedy is left stale on purpose: the banner derives `sgt resolve <symbol>` from the
    # symbol, so a forks.json committed before the remedy switch still shows the working command.
    states = {"forks": [{"symbol": "room.py::apply", "tips": ["a1b2c3d4e5", "f6a7b8c9d0"],
                         "remedy": "sgt merge-op a1b2c3d4 f6a7b8c9"}], "rewrites": {"drafts": []}}
    text = "\n".join(_state_banner(states, color=False))
    assert "1 open fork" in text and "room.py::apply" in text
    assert "sgt resolve room.py::apply" in text


def test_state_banner_separates_cross_version_forks_from_divergent_edits():
    """F82: a fork whose tips come from two MINER_VERSIONs is the same commit mined twice, not two
    people editing one symbol. `sgt resolve <symbol>` cannot close it -- only `migrate ops-v3` can --
    so the banner must not hand the user a hand-merge for each one. Observed on sgt's own repo, where
    `status` offered 612 hand-merges for 619 cross-version pairs."""
    states = {"forks": [
        {"symbol": "room.py::apply", "tips": ["a1", "a2"], "cross_version": False},
        {"symbol": "blame.ts::render", "tips": ["b1", "b2"], "cross_version": True},
    ], "rewrites": {"drafts": []}}
    text = "\n".join(_state_banner(states, color=False))
    assert "sgt resolve room.py::apply" in text          # the real divergence keeps its remedy
    assert "sgt resolve blame.ts::render" not in text    # the artifact must not get one
    assert "sgt advanced migrate ops-v3" in text
    assert "1 open fork" in text                      # counted honestly: one is a real fork


def test_state_banner_renders_merge_op_drafts_with_repair_remedy():
    states = {"forks": [], "rewrites": {"drafts": [
        {"verb": "merge-op", "target": "room.py::apply", "draft_id": "rw-abcdef123456"}]}}
    text = "\n".join(_state_banner(states, color=False))
    assert "1 pending merge-op draft" in text and "room.py::apply" in text
    assert "sgt advanced repair rw-abcdef123" in text  # draft_id capped at 12 chars


def test_state_banner_is_empty_for_no_state_and_ignores_non_merge_op_drafts():
    assert _state_banner(None, color=False) == []
    assert _state_banner({}, color=False) == []
    # a non-merge-op draft (a plain edit) is not a fork resolution -> not surfaced as one
    states = {"forks": [], "rewrites": {"drafts": [{"verb": "edit", "target": "x", "draft_id": "d1"}]}}
    assert _state_banner(states, color=False) == []


def test_render_rail_places_a_plan_ghost_row_on_its_feature_lane():
    m = {"nodes": [{"id": "fa", "label": "Wire"}, {"id": "fb", "label": "Bus"}, {"id": "fc", "label": "RGA"}]}
    grid = _rail_grid()
    grid["ghosts"] = [{"feature_id": "fa", "title": "wire up presence", "known_feature": True}]
    text = "\n".join(render_rail_lines(m, grid, color=False))
    assert "◇" in text and "plan" in text and "wire up presence" in text


def test_render_rail_drops_a_ghost_with_no_lane_to_the_unplaced_gutter():
    m = {"nodes": [{"id": "fa", "label": "Wire"}]}
    grid = _rail_grid()
    grid["ghosts"] = [{"feature_id": "fz", "title": "future work", "known_feature": False}]
    text = "\n".join(render_rail_lines(m, grid, color=False))
    assert "planned (no lane yet)" in text and "future work" in text


def test_render_graph_draws_a_plan_step_as_a_named_card_in_the_lane_forecast_band():
    """A pending plan step is a NAMED card on its lane's own row, past the `┊` now-rule -- the same
    place and grammar the lane's built work uses. It used to be a `◇ planned: …` chip on the
    checkpoint line below, which put "what is coming" in a different spot than everything else."""
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    grid = _grid(("A", 0), ("A", 1))
    grid["ghosts"] = [{"feature_id": "A", "title": "add caching", "known_feature": True}]
    lines = render_graph_lines(m, grid, color=False)
    text = "\n".join(lines)
    assert "◇ add caching" in text                     # named, not a bare count
    assert "planned: " not in text                     # the old chip encoding is gone
    assert f"past {_NOW_RULE} = planned" in text        # and the legend explains the new region
    # The card sits on the LANE row (right of the density bar), not on a line of its own.
    lane_row = next(l for l in lines if "add caching" in l)
    assert _NOW_RULE in lane_row and "A" in lane_row


def test_forecast_band_collapses_extra_steps_but_always_names_at_least_one():
    """Crowding must never reduce the band to a bare count. With more steps than cards fit, the last
    card becomes `◇+N` -- but only once another step is already named; a band with room for a single
    card names that card instead of showing a naked number."""
    wide = _forecast_band(["first step", "second step", "third step"], 2 + 17 * 2, "#888", color=False)
    assert "◇ first step" in wide and "◇+2" in wide

    # One slot, three steps: the terminal has no tooltip, so the count rides on the named card rather
    # than being deferred to hover -- the reader gets both "what is next" and "there is more".
    narrow = _forecast_band(["only room for one", "second", "third"], 2 + 17, "#888", color=False)
    assert "◇ only room… +2" in narrow
    assert "◇+" not in narrow  # never a naked count as the band's only content

    # An empty forecast occupies its reserved columns without drawing a rule (nothing is coming).
    assert _forecast_band([], 12, "#888", color=False) == " " * 12


def _wide_grid(n):
    """A single save touched by `n` features -- the many-chips case that overran the terminal."""
    return {"commits": [{"index": 0, "sha": "sha0000", "subject": "big save"}],
            "cells": [{"feature_id": f"f{i}", "commit_index": 0, "op_ids": [f"o{i}"],
                       "op_count": 1, "kinds": {"add": 1}, "fidelity": "full"} for i in range(n)]}


def _wide_map(n):
    return {"nodes": [{"id": f"f{i}", "label": f"feature-{i}-" + "z" * 40} for i in range(n)]}


def test_render_save_list_has_no_lane_column_and_lists_saves_newest_first():
    m = {"nodes": [{"id": "fa", "label": "Wire"}, {"id": "fb", "label": "Bus"},
                   {"id": "fc", "label": "RGA"}]}
    lines = render_save_list_lines(m, _rail_grid(), color=False)
    text = "\n".join(lines)
    assert "3 saves" in text and "newest on top" in text
    # the lane art the wall was made of is gone
    assert "●" not in text and "│" not in text
    # every save is listed with its commit position, sha, subject and feature chip
    assert "c2" in text and "add rga" in text and "RGA" in text
    assert "c0" in text and "add wire" in text and "Wire" in text
    # newest (c2) renders above oldest (c0)
    body = [l for l in lines if " sha" in l or "add " in l]
    assert body.index(next(l for l in body if "add rga" in l)) < \
           body.index(next(l for l in body if "add wire" in l))


def test_render_save_list_draws_a_topology_spine_when_topology_given():
    # With git topology, the default log gains a narrow git-log-style spine: a `●` for a save on the
    # first-parent trunk, a `◆` merge, and a `│●` for a save that landed on a side branch. s2 is a
    # merge, s1 is off-trunk, s0 is trunk.
    m = {"nodes": [{"id": "fa", "label": "Wire"}, {"id": "fb", "label": "Bus"},
                   {"id": "fc", "label": "RGA"}]}
    topology = {"mainline": {"s0", "s2"}, "merges": {"s2"}}
    lines = render_save_list_lines(m, _rail_grid(), topology=topology, color=False)
    text = "\n".join(lines)
    row_of = {tag: next(l for l in lines if tag in l)
              for tag in ("add rga", "add bus", "add wire")}
    assert row_of["add rga"].lstrip().startswith("◆")   # s2 is a merge
    assert row_of["add bus"].lstrip().startswith("│●")  # s1 landed on a side branch
    assert row_of["add wire"].lstrip().startswith("●")  # s0 landed on the trunk
    # the legend names exactly the glyphs that appear (merge + side branch here)
    assert "◆ merge" in text and "on a side branch" in text


def test_render_save_list_without_topology_has_no_spine():
    # No topology -> byte-for-byte the lane-less list (the golden-snapshot contract); no spine glyphs.
    m = {"nodes": [{"id": "fa", "label": "Wire"}, {"id": "fb", "label": "Bus"},
                   {"id": "fc", "label": "RGA"}]}
    text = "\n".join(render_save_list_lines(m, _rail_grid(), color=False))
    assert "●" not in text and "◆" not in text and "on trunk" not in text


def test_render_save_list_bounds_chips_so_a_wide_save_does_not_wrap():
    """The claim is the bound, not the ellipsis: a save touching six wide-labelled features must not
    produce a row that overruns the terminal. How the chips give way is `_chips`' business (it now
    prefers dropping a name into `+N` over half-naming several), so asserting `…` here would pin a
    mechanism this test does not exist to protect."""
    lines = render_save_list_lines(_wide_map(6), _wide_grid(6), color=False)
    row = next(l for l in lines if "big save" in l)
    assert "+" in row           # features past the width budget collapsed into a +N counter
    assert len(row) <= 130      # bounded -- no terminal-overrunning row


def test_render_rail_also_bounds_wide_chips():
    """The wrapping fix applies to the opt-in lane rail too (shared `_chips` budget)."""
    lines = render_rail_lines(_wide_map(6), _wide_grid(6), color=False)
    row = next(l for l in lines if "big save" in l)
    assert "+" in row and len(row) <= 130


def test_rail_rows_fit_the_terminal_instead_of_wrapping_the_lanes():
    """Measured on a real repo in an 80-column terminal: every row of the *default* view came out
    109-189 columns and wrapped, which folds the lane gutter onto a second line and destroys the one
    thing the rail is for -- a recurring feature reading as one unbroken vertical line. `--map` has
    fitted its bar to the terminal all along for exactly this reason, so the first screen was the only
    one that did not. Rows are what must fit: the subject keeps priority over the chips, because the
    subject is how a reader identifies the save at all."""
    m = {"nodes": [{"id": f, "label": f"Feature {f.upper()} With A Long Name"} for f in ("fa", "fb", "fc")]}
    grid = _rail_grid()
    grid["cells"] = [{"feature_id": f, "commit_index": i, "op_ids": [f"o{i}"], "op_count": 1,
                      "kinds": {"add": 1}, "fidelity": "full"} for i, f in enumerate(("fa", "fb", "fc"))]
    rows = [l for l in render_rail_lines(m, grid, color=False, width=80) if " c0 " in l or " c1 " in l]
    assert rows, "no save rows rendered"
    for row in rows:
        assert len(row) <= 80, f"{len(row)}: {row}"
    assert any("add wire" in r or "add wire"[:6] in r for r in rows), rows


def test_nothing_on_the_rail_overruns_the_terminal_width():
    """The live-repo shape the first fit pass missed. With a wide lane gutter the fixed columns leave
    ~12 for the chips, and `_chips` admitted its first name regardless of the budget (ellipsizing it
    to 22), so rows still came out 94 wide at 80 columns and still wrapped. A budget that is not a
    bound is decoration. The prose lines were worse -- the legend measured 181 columns and the `next:`
    footer 190 -- and a wrapped legend is where a reader learns this header is not worth reading.

    So the claim is the whole screen, not the rows: nothing the rail prints may exceed the width it
    was given, because one wrapped line folds the gutter and breaks the vertical line the view exists
    to draw."""
    n = 18
    m = {"nodes": [{"id": f"f{i}", "label": f"Semantic Versioning Architecture {i}"} for i in range(n)]}
    commits = [{"index": c, "sha": f"sha{c:04d}",
                "subject": "feat(intent): surface the plan intake ledger"} for c in range(n)]
    cells = [{"feature_id": f"f{i}", "commit_index": c, "op_ids": [f"o{c}-{i}"], "op_count": 1,
              "kinds": {"add": 1}, "fidelity": "full"}
             for c in range(n) for i in {c, (c + 1) % n}]  # every feature touched twice -> its own lane
    grid = {"commits": commits, "cells": cells}
    topology = {"mainline": {f"sha{c:04d}" for c in range(0, n, 2)}, "merges": {"sha0003"}}
    lines = render_rail_lines(m, grid, color=False, width=80, topology=topology)
    over = [(len(l), l) for l in lines if len(l) > 80]
    assert not over, over
    # and the subject still survives whole enough to tell one save from another
    assert any("feat(intent)" in l for l in lines), lines


def test_chips_spend_the_budget_on_whole_names_before_truncating_several():
    """A truncated feature name is often unidentifiable -- on a real repo this column read
    `Semantic Versioning… · Operation Match… · +13`, three half-names where the reader can identify
    none of them. The budget is the same either way, so spend it on names that survive: admit a label
    only if it fits whole, and let the rest roll into the `+N` that was already going to be there.
    This is the rule `_forecast_cards` already follows -- name a thing or count it, never half-name
    it -- so the two columns now read by one law."""
    m = {"nodes": [{"id": "fa", "label": "Semantic Versioning Architecture"},
                   {"id": "fb", "label": "Deterministic Operation Synthesis"},
                   {"id": "fc", "label": "Intent Clustering & Visualization"}]}
    grid = _rail_grid()
    grid["cells"] = [{"feature_id": f, "commit_index": 0, "op_ids": [f"o{i}"], "op_count": 3 - i,
                      "kinds": {"add": 1}, "fidelity": "full"} for i, f in enumerate(("fa", "fb", "fc"))]
    row = next(l for l in render_rail_lines(m, grid, color=False) if "add wire" in l)

    assert "Semantic Versioning Architecture" in row, row   # the main feature, named in full
    assert "…" not in row, row                              # nothing half-named
    assert "+2" in row, row                                 # and the rest counted, not mangled


def test_chips_half_name_the_main_feature_rather_than_showing_a_bare_count():
    """When the budget cannot fit one whole name, the cell still names the main feature and counts the
    rest. The alternative -- a bare `+15` -- is the same cell for fifteen different states, and it is
    what an 80-column `sgt log --saves` printed on every row of a real repo. One hint plus a count is
    one name, so the whole-name rule ("never half-name *several*") is intact."""
    m = {"nodes": [{"id": f"f{i}", "label": f"Semantic Versioning Architecture {i}"} for i in range(4)]}
    r = {"feature": "f0", "features": {f"f{i}": 3 - i for i in range(4)}}
    cell = _chips(r, {n["id"]: n["label"] for n in m["nodes"]}, color=False, budget=24)
    assert cell.startswith("Semantic Versioni"), cell   # the main feature, identifiable
    assert cell.endswith("+3"), cell                    # the other three counted
    assert len(cell) <= 24, cell

    # ...but below the width where a truncated name identifies anything, the count alone is honest.
    tight = _chips(r, {n["id"]: n["label"] for n in m["nodes"]}, color=False, budget=10)
    assert tight == "+4", tight


def test_gap_note_names_the_symbols_whole_and_wraps_them():
    """A qualified symbol name runs ~36 columns, so the flat `syms[:4]` this note started with measured
    110 and wrapped wherever the terminal chose. `sgt restore` takes a name the reader has to read back
    off this screen exactly -- a clipped one is a command they cannot type -- so the names are never
    truncated: they move to their own wrapped lines, and a long list is capped and counted."""
    syms = [f"services/checkout.py::apply_coupon_{i}" for i in range(9)]
    lines = _reverted_gap_note({"op_count": 12, "symbols": syms}, 80)
    assert not [l for l in lines if len(l) > 80], lines
    text = "\n".join(lines)
    assert "services/checkout.py::apply_coupon_0" in text   # whole, not clipped
    assert "+3 more" in text                                # past the cap, counted
    assert "apply_coupon_8" not in text
    # one short name still rides on the count line, where it costs no extra row
    one = _reverted_gap_note({"op_count": 4, "symbols": ["cart.py::apply_coupon"]}, 80)
    assert one[0].endswith("below: cart.py::apply_coupon"), one


def test_the_save_list_fits_the_terminal_too():
    """The lane-less list had the same defect as the rail, on the screen beside it: 39 of its 44 rows
    overran an 80-column terminal, because each renderer bounded its chips against a constant instead
    of against the terminal. Unlike the rail it keeps a chip floor -- it has no lane gutter, so the
    chips are the only thing on the row that says which feature a save belongs to."""
    n = 8
    m = {"nodes": [{"id": f"f{i}", "label": f"Semantic Versioning Architecture {i}"} for i in range(n)]}
    grid = {"commits": [{"index": c, "sha": f"sha{c:04d}",
                         "subject": "feat(intent): surface the aligned plan intake ledger"}
                        for c in range(n)],
            "cells": [{"feature_id": f"f{i}", "commit_index": c, "op_ids": [f"o{c}-{i}"],
                       "op_count": 1, "kinds": {"add": 1}, "fidelity": "full"}
                      for c in range(n) for i in {c, (c + 1) % n}]}
    lines = render_save_list_lines(m, grid, color=False, width=80,
                                   topology={"mainline": {f"sha{c:04d}" for c in range(0, n, 2)}})
    assert not [(len(l), l) for l in lines if len(l) > 80]
    rows = [l for l in lines if " c1 " in l or " c2 " in l]
    assert rows and all("Semantic Versioni" in r for r in rows), rows  # attribution survives the fit


def test_chips_still_ellipsize_when_even_the_main_name_cannot_fit():
    """The fallback has to stay: one name that overruns the budget is still better ellipsized than
    dropped, because a `+1` alone would tell the reader nothing about what they are looking at."""
    m = {"nodes": [{"id": "fa", "label": "x" * 80}]}
    grid = _rail_grid()
    grid["cells"] = [{"feature_id": "fa", "commit_index": 0, "op_ids": ["o0"], "op_count": 1,
                      "kinds": {"add": 1}, "fidelity": "full"}]
    row = next(l for l in render_rail_lines(m, grid, color=False) if "add wire" in l)
    assert "…" in row and "xxx" in row


def test_render_rail_group_focus_hides_out_of_group_plan_ghosts():
    # In a group focus the rail shows only that group's plan steps -- a ghost for a feature outside
    # `only_features` is not this group's concern and must not leak into its "no lane yet" gutter.
    m = {"nodes": [{"id": "fa", "label": "Wire"}, {"id": "fb", "label": "Bus"}, {"id": "fc", "label": "RGA"}]}
    grid = _rail_grid()
    grid["ghosts"] = [{"feature_id": "fa", "title": "in group step", "known_feature": True},
                      {"feature_id": "fc", "title": "out of group step", "known_feature": True}]
    text = "\n".join(render_rail_lines(m, grid, color=False, only_features={"fa", "fb"}, group_label="Comms"))
    assert "in group step" in text
    assert "out of group step" not in text


def _lane_rows(lines):
    """The lane rows of a rendered map, ANSI stripped -- the rows a reader scans down."""
    import re
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in lines]
    return [ln for ln in plain if re.match(r"^\s+[▸ ][◈●]", ln)]


def test_map_columns_line_up_across_rows_whatever_the_handle_width():
    """Every lane row occupies the same columns, so the map can be read downward.

    The handle is a short `N9` on a meta lane and an 8-char hex on a feature lane. Left unpadded,
    that difference shifted the label, the bar, and the chip line by several columns on every row,
    which is what made the map read as noise even though each row was individually correct.
    """
    meta, feat = "N9", "f-aaaaaaaaaa"
    m = {"roots": [meta, feat],
         "nodes": [_node(meta, None, [], kind="meta"), _node(feat, None, [])], "edges": []}
    hist = _grid((meta, 10), (feat, 10))
    segs = [_seg(meta, 0, ["o0"], 10, 10, label="one"), _seg(feat, 0, ["o1"], 10, 10, label="two")]

    rows = _lane_rows(render_graph_lines(m, hist, segs, color=False))
    assert len(rows) == 2
    assert len({len(r) for r in rows}) == 1, "lane rows must occupy identical column ranges"


def test_map_draws_every_lane_on_one_shared_commit_axis():
    """Two features that were edited at the same commit must mark the same column, and a feature
    edited much later must mark a later one. Before this, each lane packed its own checkpoints from
    the left, so column N meant a different point in time on every row."""
    a, b = "f-aaaaaaaaaa", "f-bbbbbbbbbb"
    m = {"roots": [a, b], "nodes": [_node(a, None, []), _node(b, None, [])], "edges": []}
    # a and b both edit at commit 10; b also edits at 190, far to the right on a 200-commit axis.
    hist = _grid((a, 10), (b, 10), (b, 190))
    segs = [_seg(a, 0, ["o0"], 10, 10, label="early"),
            _seg(b, 0, ["o1"], 10, 10, label="early"),
            _seg(b, 1, ["o2"], 190, 190, label="late")]

    rows = _lane_rows(render_graph_lines(m, hist, segs, color=False))
    marks = [[i for i, ch in enumerate(r) if ch in "▁▂▃▄▅▆▇█"]
             for r in rows]
    assert all(marks), "each lane drew at least one density block"
    assert marks[0][0] == marks[1][0], "the shared commit is the same column in both rows"
    assert marks[1][-1] > marks[0][-1], "a later commit sits further right"
