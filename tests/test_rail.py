"""The episode-rail layout contract, JS side: `episodeRailLayout` in workbench.js lays episodes
out as a vertical git-log (newest on top, feature lanes, interval-coloring reuse). Kept
behaviour-parallel with `sgt.tui.graph.episode_rail_layout` (tests/tui/test_episodes.py). Pure, so
we slice it and run it under node."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/workbench.js"


def _run(ep_view: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    text = _JS.read_text(encoding="utf-8")
    start = text.index("function episodeRailLayout")
    end = text.index("// ---- end-rail")
    snippet = text[start:end]
    harness = snippet + f"console.log(JSON.stringify(episodeRailLayout({json.dumps(ep_view)})));\n"
    res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _ep(*items):
    # items: (index, dominantFeature)
    return {"episodes": [{"index": i, "dominantFeature": f, "subject": f"c{i}", "opCount": 1, "sha": f"s{i}"}
                         for i, f in items]}


def test_newest_episode_is_row_zero():
    out = _run(_ep((0, "F1"), (1, "F1"), (2, "F1")))
    rows = {r["index"]: r["row"] for r in out["rows"]}
    assert rows == {2: 0, 1: 1, 0: 2}


def test_a_feature_s_episodes_share_one_lane():
    out = _run(_ep((0, "F1"), (1, "F1")))
    assert {r["lane"] for r in out["rows"]} == {0} and out["laneCount"] == 1


def test_non_overlapping_spans_reuse_a_lane_overlapping_do_not():
    reuse = _run(_ep((0, "F1"), (1, "F1"), (2, "F2"), (3, "F2")))
    assert reuse["laneCount"] == 1
    nested = _run(_ep((0, "F1"), (1, "F2"), (2, "F2"), (3, "F1")))
    assert nested["laneCount"] == 2
