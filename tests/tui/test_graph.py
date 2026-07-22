"""The terminal feature-timeline (Gantt) layout + renderer (`sgt.tui.graph`), and the TUI overlay.

`graph_layout` is the Python counterpart of the VS Code `computeGraphLayout` and is held to the
same contract (see tests/test_graph_layout.py): one lane per feature with first/last/commits,
lanes grouped into subsystem swimlane headers and ordered by first appearance, collapsed
subsystems roll up, a frontier filters ops, deterministic. The pure-function tests need no repo;
the pilot test (Textual) boots the real app and opens the graph screen.
"""

from __future__ import annotations

import pytest

from sgt.tui.graph import graph_layout, render_graph_lines


def _node(id_, parent, children, kind="feature"):
    return {"id": id_, "parent": parent, "children": children, "label": id_.upper(),
            "kind": kind, "size": 1, "op_count": 0, "dir": f"src/{id_}/"}


def _hist(*specs):
    return {"commits": [{"index": i} for i in range(200)],
            "ops": [{"id": f"o{i}", "kind": "add", "feature_id": f, "commit_index": c}
                    for i, (f, c) in enumerate(specs)]}


def test_only_features_with_ops_are_placed():
    m = {"roots": ["A", "B", "C"],
         "nodes": [_node("A", None, []), _node("B", None, []), _node("C", None, [])], "edges": []}
    out = graph_layout(m, _hist(("A", 0), ("A", 1), ("B", 5)))
    assert {n["id"] for n in out["lanes"]} == {"A", "B"}


def test_op_count_magnitude_and_span():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    out = graph_layout(m, _hist(("A", 3), ("A", 0), ("A", 1)))
    lane = out["node_by_id"]["A"]
    assert lane["op_count"] == 3
    assert lane["first_commit"] == 0 and lane["last_commit"] == 3
    assert lane["commits"] == [0, 1, 3]


def test_lanes_ordered_by_first_appearance():
    m = {"roots": list("ABCDE"), "nodes": [_node(c, None, []) for c in "ABCDE"], "edges": []}
    out = graph_layout(m, _hist(("E", 40), ("A", 0), ("C", 20), ("B", 10), ("D", 30)))
    by_row = sorted(out["lanes"], key=lambda n: n["row"])
    assert [n["id"] for n in by_row] == ["A", "B", "C", "D", "E"]


def test_expanded_subsystem_makes_a_header_over_its_lanes():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])], "edges": []}
    out = graph_layout(m, _hist(("F1", 0), ("F1", 1), ("F2", 2)))
    assert {n["id"] for n in out["lanes"]} == {"F1", "F2"}
    assert len(out["headers"]) == 1
    hd = out["headers"][0]
    assert hd["collapsed_id"] == "N0" and hd["lane_count"] == 2 and hd["op_count"] == 3
    assert hd["row"] < min(out["node_by_id"][f]["row"] for f in ("F1", "F2"))


def test_collapsed_subsystem_rolls_up_descendant_ops():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])], "edges": []}
    out = graph_layout(m, _hist(("F1", 0), ("F1", 1), ("F2", 2)), collapsed=["N0"])
    assert len(out["lanes"]) == 1 and out["headers"] == []
    assert out["lanes"][0]["is_meta"] and out["lanes"][0]["op_count"] == 3


def test_frontier_filters_ops_so_scrubbing_accretes():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    hist = _hist(("A", 0), ("A", 2), ("B", 50))
    assert {n["id"] for n in graph_layout(m, hist, frontier=10)["lanes"]} == {"A"}
    assert {n["id"] for n in graph_layout(m, hist, frontier=100)["lanes"]} == {"A", "B"}


def test_edge_reroutes_into_collapsed_subsystem_and_self_loops_drop():
    m = {"roots": ["N0", "F3"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", []), _node("F3", None, [])],
         "edges": [{"a": "F1", "b": "F3", "weight": 2.0}, {"a": "F1", "b": "F2", "weight": 9.0}]}
    out = graph_layout(m, _hist(("F1", 0), ("F2", 1), ("F3", 2)), collapsed=["N0"])
    assert out["edges"] == [{"a": "F3", "b": "N0", "weight": 2.0}]


def test_deterministic():
    m = {"roots": list("ABCDEF"), "nodes": [_node(c, None, []) for c in "ABCDEF"],
         "edges": [{"a": "A", "b": "C", "weight": 3.0}, {"a": "B", "b": "D", "weight": 2.0}]}
    hist = _hist(("A", 0), ("B", 1), ("C", 10), ("D", 11), ("E", 20), ("F", 21))
    key = lambda o: [(n["id"], n["row"], n["first_commit"]) for n in graph_layout(m, hist)["lanes"]]
    assert key(m) == key(m)


def test_render_lines_carry_header_axis_and_labels():
    m = {"roots": ["A", "B"], "nodes": [_node("A", None, []), _node("B", None, [])], "edges": []}
    lines = render_graph_lines(m, _hist(("A", 0), ("B", 40)), color=False)
    text = "\n".join(lines)
    assert "2 feature(s)" in text
    assert "time →" in text  # the shared time axis is labeled
    assert "A" in text and "B" in text  # labels rendered


def test_render_lane_leads_with_handle_and_shows_checkpoint_count():
    """Each lane must surface the short `f-XXXX` handle (the copy-paste token for `sgt revert`) and,
    when a checkpoint count is supplied, a `✦N` annotation -- the "what can I operate on" the user
    couldn't find in the old label-only render."""
    m = {"roots": ["f-00abcdef01"], "nodes": [_node("f-00abcdef01", None, [])], "edges": []}
    lines = render_graph_lines(m, _hist(("f-00abcdef01", 0), ("f-00abcdef01", 5)), color=False,
                               checkpoints={"f-00abcdef01": 3})
    lane = next(ln for ln in lines if "f-00abcdef" in ln)
    assert "f-00abcdef" in lane   # the 10-char handle prefix
    assert "✦3" in lane           # three rewind points on this lane
    # no checkpoints dict -> no ✦ on the lane (the legend footer may still explain ✦), handle stays
    plain = render_graph_lines(m, _hist(("f-00abcdef01", 0)), color=False)
    plain_lane = next(ln for ln in plain if "f-00abcdef" in ln)
    assert "✦" not in plain_lane


def test_render_swimlane_header_present_for_expanded_subsystem():
    m = {"roots": ["N0"],
         "nodes": [_node("N0", None, ["F1", "F2"], kind="subsystem"),
                   _node("F1", "N0", []), _node("F2", "N0", [])], "edges": []}
    lines = render_graph_lines(m, _hist(("F1", 0), ("F2", 40)), color=False)
    assert any("▾" in ln and "feat" in ln for ln in lines)  # the subsystem swimlane header row


def test_render_frontier_note_present_when_folded():
    m = {"roots": ["A"], "nodes": [_node("A", None, [])], "edges": []}
    lines = render_graph_lines(m, _hist(("A", 0), ("A", 5)), frontier=3, color=False)
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
