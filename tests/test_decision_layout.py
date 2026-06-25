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
        "console.log(JSON.stringify({pos: L.pos, laneCount: L.laneCount, rowOf: L.rowOf}));\n"
    )
    res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _spears(graph: dict, out: dict) -> int:
    """Count dots speared by an edge — a dot strictly inside an edge's (lane, row) bounding box."""
    pos = out["pos"]
    n = 0
    for e in graph["edges"]:
        a, b = pos.get(e["src"]), pos.get(e["dst"])
        if not a or not b:
            continue
        lo, hi = sorted((a["lane"], b["lane"]))
        r0, r1 = sorted((a["row"], b["row"]))
        for did, p in pos.items():
            if did in (e["src"], e["dst"]):
                continue
            if lo < p["lane"] < hi and r0 < p["row"] < r1:
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


def test_avoid_crossings_reorders_lanes_to_unspear_a_dependency():
    # P spans the whole height (rows 0..4); Q sits inside it (rows 1..3); R is a single dot at row 2.
    # P builds on R. Default packing assigns P=0, Q=1, R=2, so the P->R edge sweeps over Q's dot in
    # lane 1. avoidCrossings packs R adjacent to P (its dependency), clearing the span — same columns.
    g = {
        "decisions": [_dec("p@6", "P", 6), _dec("q@5", "Q", 5), _dec("r@4", "R", 4),
                      _dec("q@2", "Q", 2), _dec("p@1", "P", 1)],
        "edges": [_dep("p@6", "r@4")], "frontier": {}, "clash": [],
    }
    base = _run_layout(g)
    spread = _run_layout(g, {"avoidCrossings": True})
    assert _spears(g, base) == 1
    assert _spears(g, spread) == 0
    assert spread["laneCount"] == base["laneCount"]  # this shape costs no extra column
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


def test_same_landing_ties_are_stable_and_collision_free():
    g = {"decisions": [_dec("b@2", "fb", 2), _dec("a@2", "fa", 2), _dec("c@2", "fc", 2)],
         "edges": [], "frontier": {}, "clash": []}
    out = _run_layout(g)
    # distinct rows even at the same landing; tie-break by feature id.
    assert sorted(out["rowOf"].values()) == [0, 1, 2]
    _no_cell_collisions(out)
