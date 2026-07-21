"""The episodic projection contract, JS side: `rollupEpisodes` in editor/vscode/media/workbench.js
is a pure function (no DOM), so we slice it out and exercise it under node. Kept behaviour-parallel
with `sgt.tui.graph.episodes` (see tests/tui/test_episodes.py for the identical Python contract)."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/workbench.js"


def _run(map_view: dict, history: dict) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    text = _JS.read_text(encoding="utf-8")
    start = text.index("function rollupEpisodes")
    end = text.index("// ---- end-episodes")
    snippet = text[start:end]
    harness = snippet + (
        f"const m = {json.dumps(map_view)};\n"
        f"const h = {json.dumps(history)};\n"
        "console.log(JSON.stringify(rollupEpisodes(m, h)));\n"
    )
    res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _hist(commits, ops):
    return {"commits": [{"index": i, "sha": s, "subject": subj} for i, s, subj in commits],
            "ops": [{"id": oid, "kind": k, "feature_id": f, "commit_index": c} for oid, k, f, c in ops]}


def _map(*labels):
    return {"nodes": [{"id": fid, "label": lbl} for fid, lbl in labels]}


def test_ops_sharing_a_commit_index_roll_up_into_one_episode():
    m = _map(("F1", "Auth"))
    hist = _hist(
        [(0, "sha0", "add login"), (1, "sha1", "fix login")],
        [("o0", "add", "F1", 0), ("o1", "extend", "F1", 0), ("o2", "rework", "F1", 1)],
    )
    out = _run(m, hist)
    eps = out["episodes"]
    assert [e["index"] for e in eps] == [0, 1]
    assert eps[0]["opIds"] == ["o0", "o1"] and eps[0]["opCount"] == 2
    assert eps[0]["sha"] == "sha0" and eps[0]["subject"] == "add login"
    assert eps[0]["kinds"] == {"add": 1, "extend": 1}
    assert eps[1]["opIds"] == ["o2"] and eps[1]["kinds"] == {"rework": 1}


def test_dominant_feature_is_the_commit_s_most_touched_feature():
    m = _map(("F1", "Auth"), ("F2", "Billing"))
    hist = _hist([(0, "s0", "c0")],
                 [("a", "add", "F1", 0), ("b", "add", "F1", 0), ("c", "add", "F2", 0)])
    out = _run(m, hist)
    assert out["episodes"][0]["dominantFeature"] == "F1"
    assert out["episodes"][0]["features"] == {"F1": 2, "F2": 1}


def test_episodes_group_by_dominant_feature_ordered_by_first_appearance():
    m = _map(("F1", "Auth"), ("F2", "Billing"))
    hist = _hist(
        [(0, "s0", "c0"), (1, "s1", "c1"), (2, "s2", "c2")],
        [("a", "add", "F2", 0), ("b", "add", "F1", 1), ("c", "extend", "F2", 2)],
    )
    out = _run(m, hist)
    groups = out["groups"]
    assert [g["featureId"] for g in groups] == ["F2", "F1"]
    f2 = groups[0]
    assert f2["label"] == "Billing" and f2["episodeIndices"] == [0, 2]
    assert f2["opCount"] == 2 and f2["firstIndex"] == 0 and f2["lastIndex"] == 2
    assert f2["kinds"] == {"add": 1, "extend": 1}


def test_unattributed_ops_fall_under_a_null_group():
    m = _map()
    hist = _hist([(0, "s0", "c0")], [("a", "add", None, 0)])
    out = _run(m, hist)
    assert out["episodes"][0]["dominantFeature"] is None
    assert out["groups"][0]["featureId"] is None and out["groups"][0]["label"] == "(unattributed)"


def test_empty_history_is_empty():
    out = _run(_map(), {"commits": [], "ops": []})
    assert out == {"episodes": [], "groups": []}
