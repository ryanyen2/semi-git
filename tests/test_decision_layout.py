"""The Decision Graph layout contract.

The git-log layout in `editor/vscode/media/decision.js` is a pure function (`computeLayout`) with
no DOM/color dependency, so we slice it out and exercise it under node against the shapes the graph
must survive: a single feature stacking straight, concurrent features packing into separate lanes,
disjoint features reusing a lane, forks, and the empty graph. The invariant that matters most —
**no two decisions ever share the same (row, lane) cell** — is asserted on every shape.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/decision.js"


def _run_layout(graph: dict, opts: dict | None = None) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    text = _JS.read_text(encoding="utf-8")
    start = text.index("function computeLayout")
    end = text.index("// ---- end-layout")
    snippet = text[start:end]
    harness = snippet + (
        f"const g = {json.dumps(graph)};\n"
        f"const L = computeLayout(g, {json.dumps(opts or {})});\n"
        "console.log(JSON.stringify({pos: L.pos, laneCount: L.laneCount, rowOf: L.rowOf, "
        "head: L.head, unanchored: L.unanchored, edges: L.edges.map((e) => [e.src, e.dst, e.type])}));\n"
    )
    res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _spears(graph: dict, out: dict) -> int:
    """Count connectors hidden behind a dot, mirroring what edgePath() actually draws: a connector is
    a vertical run in its SOURCE's lane (`e.src`, the upper/child node) between the two endpoint rows.
    So it is hidden exactly when some other dot sits in the source's lane strictly between those rows.
    """
    pos = out["pos"]
    n = 0
    for e in graph["edges"]:
        a, b = pos.get(e["src"]), pos.get(e["dst"])
        if not a or not b:
            continue
        src_lane = a["lane"]                       # the vertical run lives in the source's lane
        r0, r1 = sorted((a["row"], b["row"]))
        for did, p in pos.items():
            if did in (e["src"], e["dst"]):
                continue
            if p["lane"] == src_lane and r0 < p["row"] < r1:
                n += 1
    return n


def _dec(id_, feature, landing):
    return {"id": id_, "feature": feature, "landing": landing, "commits": ["c"], "lifecycle": {"kind": "introduce", "of": None}}


def _no_cell_collisions(out):
    seen = set()
    for did, p in out["pos"].items():
        cell = (p["row"], p["lane"])
        assert cell not in seen, f"{did} collides at {cell}"
        seen.add(cell)


def test_single_feature_one_lane():
    g = {"decisions": [_dec("a@3", "f", 3), _dec("a@2", "f", 2), _dec("a@1", "f", 1)], "edges": [], "frontier": {}, "clash": []}
    out = _run_layout(g)
    assert out["laneCount"] == 1
    assert {p["lane"] for p in out["pos"].values()} == {0}
    # newest landing is the top row.
    assert out["rowOf"]["a@3"] == 0 and out["rowOf"]["a@1"] == 2
    _no_cell_collisions(out)


def test_concurrent_features_get_distinct_lanes():
    # two features interleaved in time -> their row-spans overlap -> two lanes.
    g = {"decisions": [_dec("a@4", "fa", 4), _dec("b@3", "fb", 3), _dec("a@2", "fa", 2), _dec("b@1", "fb", 1)],
         "edges": [], "frontier": {}, "clash": []}
    out = _run_layout(g)
    assert out["laneCount"] == 2
    assert out["pos"]["a@4"]["lane"] != out["pos"]["b@3"]["lane"]
    _no_cell_collisions(out)


def test_disjoint_features_reuse_a_lane():
    # fb lives entirely below fa (no time overlap) -> the lane is reused, width stays 1.
    g = {"decisions": [_dec("a@4", "fa", 4), _dec("a@3", "fa", 3), _dec("b@2", "fb", 2), _dec("b@1", "fb", 1)],
         "edges": [], "frontier": {}, "clash": []}
    out = _run_layout(g)
    assert out["laneCount"] == 1
    _no_cell_collisions(out)


def test_empty_graph():
    out = _run_layout({"decisions": [], "edges": [], "frontier": {}, "clash": []})
    assert out["laneCount"] == 1
    assert out["pos"] == {}


def _dep(src, dst):
    return {"src": src, "dst": dst, "type": "builds-on", "derived": True}


def test_a_connector_is_never_hidden_behind_a_dot_in_either_mode():
    # Integrator I (row 0) builds on dep D (row 2); unrelated X sits at row 1. Packed into one column
    # the I->D connector — a vertical run in I's lane — would draw straight through X's dot (hidden).
    # The overprint rule forbids that in BOTH modes: X (or D) is branched into another lane so the
    # connector stays visible. This is the core "no collapsed straight line" guarantee.
    g = {
        "decisions": [_dec("I@3", "I", 3), _dec("X@2", "X", 2), _dec("D@1", "D", 1)],
        "edges": [_dep("I@3", "D@1")], "frontier": {}, "clash": [],
    }
    base = _run_layout(g)
    spread = _run_layout(g, {"avoidCrossings": True})
    assert _spears(g, base) == 0       # no hidden connector even with the compact packer
    assert _spears(g, spread) == 0
    assert base["laneCount"] > 1       # the fan is spread, not collapsed onto the trunk
    assert spread["laneCount"] > 1
    _no_cell_collisions(base)
    _no_cell_collisions(spread)


def test_avoid_crossings_preserves_collision_free_invariant():
    # The crossing-reducing packer must still never put two decisions in one (row, lane) cell.
    g = {
        "decisions": [_dec("a@4", "fa", 4), _dec("b@3", "fb", 3), _dec("a@2", "fa", 2),
                      _dec("b@1", "fb", 1), _dec("c@2", "fc", 2)],
        "edges": [_dep("a@4", "b@1"), _dep("b@3", "c@2")], "frontier": {}, "clash": [],
    }
    _no_cell_collisions(_run_layout(g, {"avoidCrossings": True}))


def test_head_rooted_order_keeps_head_on_top_and_surfaces_fresh_orphans():
    # A fan: integrator I@8 is the head; its feeders b@4/a@2 nest beneath it. m@12 is a fresh,
    # unanchored decision that landed AFTER head (a just-planned/checkpointed node nothing connects
    # to). Head stays row 0; m@12 surfaces directly under head (not buried below the whole subtree),
    # then head's feeders, newest-first. m@12 has no incident edge, so it reports as `unanchored`.
    g = {
        "decisions": [_dec("m@12", "m", 12), _dec("I@8", "I", 8), _dec("b@4", "b", 4), _dec("a@2", "a", 2)],
        "edges": [_dep("I@8", "b@4"), _dep("I@8", "a@2")],
        "frontier": {}, "clash": [], "head": "I@8",
    }
    out = _run_layout(g)
    assert out["rowOf"]["I@8"] == 0                                   # head still on top
    assert out["rowOf"]["m@12"] == 1                                  # fresh orphan surfaced under head
    assert out["rowOf"]["b@4"] == 2 and out["rowOf"]["a@2"] == 3      # deps nested newest-first
    assert out["unanchored"] == ["m@12"]
    _no_cell_collisions(out)


def test_head_rooted_order_keeps_stale_orphans_below_the_tree():
    # An orphan that landed BEFORE head is an old disconnected lane, not fresh work — it stays below
    # head's subtree (newest-on-top preserved for it), unlike a fresh post-head orphan.
    g = {
        "decisions": [_dec("I@8", "I", 8), _dec("b@4", "b", 4), _dec("old@2", "old", 2)],
        "edges": [_dep("I@8", "b@4")],
        "frontier": {}, "clash": [], "head": "I@8",
    }
    out = _run_layout(g)
    assert out["rowOf"]["I@8"] == 0 and out["rowOf"]["b@4"] == 1      # head + its feeder
    assert out["rowOf"]["old@2"] == 2                                # stale orphan trails the tree
    assert out["unanchored"] == ["old@2"]
    _no_cell_collisions(out)


def test_a_dependent_co_locates_with_its_dependency_when_separating_would_hide_the_edge():
    # fa is a 2-decision spine (rows 0,2) in lane 0; fb sits inside it in lane 1. fc (row 3) builds on
    # fb. Putting fc in lane 0 would route the fc->fb connector (a vertical in fc's lane) straight
    # through fa's lower dot at row 2 — a hidden edge — so the overprint rule co-locates fc with fb in
    # lane 1 in BOTH modes. (Previously the compact packer separated them and hid the edge; not anymore.)
    g = {
        "decisions": [_dec("a1", "fa", 5), _dec("b1", "fb", 4), _dec("a2", "fa", 3), _dec("c1", "fc", 2)],
        "edges": [_dep("c1", "b1")], "frontier": {}, "clash": [],
    }
    base = _run_layout(g)
    adj = _run_layout(g, {"avoidCrossings": True})
    assert base["pos"]["c1"]["lane"] == base["pos"]["b1"]["lane"]   # co-located even in compact mode
    assert adj["pos"]["c1"]["lane"] == adj["pos"]["b1"]["lane"]
    assert _spears(g, base) == 0 and _spears(g, adj) == 0           # the connector is never hidden
    _no_cell_collisions(base)
    _no_cell_collisions(adj)


def test_fan_bus_collapse_brackets_leaf_feeders_into_one_lane():
    # HEAD builds on three pure leaves. They must share ONE adjacent bus lane (a visible bracket),
    # neither packed into HEAD's column (which hides the edges as verticals) nor fanned into 3 columns.
    g = {
        "decisions": [_dec("H@5", "H", 5), _dec("a@2", "a", 2), _dec("b@3", "b", 3), _dec("c@4", "c", 4)],
        "edges": [_dep("H@5", "a@2"), _dep("H@5", "b@3"), _dep("H@5", "c@4")],
        "frontier": {}, "clash": [], "head": "H@5",
    }
    out = _run_layout(g, {"avoidCrossings": True})
    hlane = out["pos"]["H@5"]["lane"]
    flanes = {out["pos"][x]["lane"] for x in ("a@2", "b@3", "c@4")}
    assert len(flanes) == 1            # feeders share one bus lane
    assert hlane not in flanes         # distinct from HEAD -> feeder→HEAD edges render as curves
    assert out["laneCount"] == 2
    _no_cell_collisions(out)


def test_integrator_fan_does_not_overprint_the_trunk():
    # The real rag-project shape: HEAD `run` builds on `ret` and `gen`; a second strand `prov` also
    # builds on `gen`; `emb` is an unanchored island. Interval-coloring packs everything into lane 0,
    # where HEAD's two builds-on edges draw as VERTICALS straight through every intervening dot — the
    # "straight line, can't see the fan" failure. The avoid-crossings packer must branch the feeders
    # out so no edge (vertical or diagonal) sweeps a foreign dot: zero spears, and more than one lane.
    g = {
        "decisions": [
            _dec("ret@1", "ret", 1), _dec("gen@2", "gen", 2), _dec("run@3", "run", 3),
            _dec("prov@4", "prov", 4),
            {"id": "prov@8", "feature": "prov", "landing": 8, "commits": ["c"],
             "lifecycle": {"kind": "revise", "of": "prov@4"}},
            _dec("emb@9", "emb", 9),
        ],
        "edges": [
            {"src": "prov@8", "dst": "prov@4", "type": "revises"},
            _dep("run@3", "gen@2"), _dep("run@3", "ret@1"), _dep("prov@4", "gen@2"),
        ],
        "frontier": {}, "clash": [], "head": "run@3",
    }
    for opts in ({}, {"avoidCrossings": True}):          # honest in BOTH compact and spread modes
        out = _run_layout(g, opts)
        assert _spears(g, out) == 0          # no connector is hidden behind a dot
        assert out["laneCount"] > 1          # the fan is spread across lanes, not collapsed to a line
        assert out["unanchored"] == ["emb@9"]
        # the lone island sits in the gutter — right of every connected (edge-bearing) lane.
        connected = max(p["lane"] for did, p in out["pos"].items() if did != "emb@9")
        assert out["pos"]["emb@9"]["lane"] > connected
        _no_cell_collisions(out)


def test_no_head_field_keeps_newest_on_top():
    # Backwards-compatible: with no `head`, ordering is the original newest-landing-first.
    g = {"decisions": [_dec("a@1", "f", 1), _dec("a@2", "f", 2)], "edges": [], "frontier": {}, "clash": []}
    out = _run_layout(g)
    assert out["rowOf"]["a@2"] == 0 and out["rowOf"]["a@1"] == 1
    _no_cell_collisions(out)


def test_equal_landing_floats_the_integrator_to_the_top():
    # An equal-landing cohort (e.g. the dev fixture, or a freshly-planned set with no `head`). The
    # integrator — the node that builds on the others and that nothing builds on — must anchor the
    # TOP. The depth tiebreak in byLanding does this; the old ascending tiebreak sank it to the
    # bottom, which read upside-down.
    g = {
        "decisions": [_dec("I@1", "I", 1), _dec("a@1", "a", 1), _dec("b@1", "b", 1)],
        "edges": [_dep("I@1", "a@1"), _dep("I@1", "b@1")], "frontier": {}, "clash": [],
    }
    out = _run_layout(g)
    assert out["rowOf"]["I@1"] == 0  # the integrator is on top, not the bottom
    _no_cell_collisions(out)


def test_transitive_reduction_drops_an_implied_builds_on_edge():
    # A builds on B, B builds on C, and A also builds on C directly. The A->C edge is implied by the
    # A->B->C path, so it must be dropped from the drawn/routed edge set (git-log clean), while the two
    # direct edges survive. revises/fork lineage is never reduced.
    g = {
        "decisions": [_dec("A", "fa", 3), _dec("B", "fb", 2), _dec("C", "fc", 1)],
        "edges": [_dep("A", "B"), _dep("B", "C"), _dep("A", "C")], "frontier": {}, "clash": [],
    }
    out = _run_layout(g)
    drawn = {(s, d) for s, d, _t in out["edges"]}
    assert ("A", "C") not in drawn          # implied shortcut removed
    assert ("A", "B") in drawn and ("B", "C") in drawn  # direct links kept


def test_transitive_reduction_keeps_independent_parallel_edges():
    # A builds on both B and C, but B and C are unrelated — neither edge is implied by the other, so
    # BOTH survive reduction (the reducer must not over-prune a genuine fan).
    g = {
        "decisions": [_dec("A", "fa", 3), _dec("B", "fb", 2), _dec("C", "fc", 1)],
        "edges": [_dep("A", "B"), _dep("A", "C")], "frontier": {}, "clash": [],
    }
    drawn = {(s, d) for s, d, _t in _run_layout(g)["edges"]}
    assert ("A", "B") in drawn and ("A", "C") in drawn


def test_same_landing_ties_are_stable_and_collision_free():
    g = {"decisions": [_dec("b@2", "fb", 2), _dec("a@2", "fa", 2), _dec("c@2", "fc", 2)],
         "edges": [], "frontier": {}, "clash": []}
    out = _run_layout(g)
    # distinct rows even at the same landing; tie-break by feature id.
    assert sorted(out["rowOf"].values()) == [0, 1, 2]
    _no_cell_collisions(out)
