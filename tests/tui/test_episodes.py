"""The episodic projection contract (Stage C): `sgt.tui.graph.episodes` rolls the flat op stream
up into one episode per commit (ops sharing a `commit_index`) and groups episodes by their dominant
feature into collapsible episode-groups. Kept behaviour-parallel with `rollupEpisodes` in
editor/vscode/media/workbench.js (see tests/test_episodes.py for the JS side)."""

from __future__ import annotations

from sgt.tui.graph import episode_rail_layout, episodes


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
    out = episodes(m, hist)
    eps = out["episodes"]
    assert [e["index"] for e in eps] == [0, 1]  # one episode per commit, index-ordered
    assert eps[0]["op_ids"] == ["o0", "o1"] and eps[0]["op_count"] == 2
    assert eps[0]["sha"] == "sha0" and eps[0]["subject"] == "add login"
    assert eps[0]["kinds"] == {"add": 1, "extend": 1}
    assert eps[1]["op_ids"] == ["o2"] and eps[1]["kinds"] == {"rework": 1}


def test_dominant_feature_is_the_commit_s_most_touched_feature():
    m = _map(("F1", "Auth"), ("F2", "Billing"))
    # commit 0: F1 twice, F2 once -> F1 dominates.
    hist = _hist([(0, "s0", "c0")],
                 [("a", "add", "F1", 0), ("b", "add", "F1", 0), ("c", "add", "F2", 0)])
    out = episodes(m, hist)
    assert out["episodes"][0]["dominant_feature"] == "F1"
    assert out["episodes"][0]["features"] == {"F1": 2, "F2": 1}


def test_episodes_group_by_dominant_feature_ordered_by_first_appearance():
    m = _map(("F1", "Auth"), ("F2", "Billing"))
    # F2 appears first (commit 0), F1 at commit 1, F2 again at commit 2.
    hist = _hist(
        [(0, "s0", "c0"), (1, "s1", "c1"), (2, "s2", "c2")],
        [("a", "add", "F2", 0), ("b", "add", "F1", 1), ("c", "extend", "F2", 2)],
    )
    out = episodes(m, hist)
    groups = out["groups"]
    # F2 group first (born at commit 0); it holds both its episodes.
    assert [g["feature_id"] for g in groups] == ["F2", "F1"]
    f2 = groups[0]
    assert f2["label"] == "Billing" and f2["episode_indices"] == [0, 2]
    assert f2["op_count"] == 2 and f2["first_index"] == 0 and f2["last_index"] == 2
    assert f2["kinds"] == {"add": 1, "extend": 1}


def test_unattributed_ops_fall_under_a_none_group():
    m = _map()
    hist = _hist([(0, "s0", "c0")], [("a", "add", None, 0)])
    out = episodes(m, hist)
    assert out["episodes"][0]["dominant_feature"] is None
    assert out["groups"][0]["feature_id"] is None and out["groups"][0]["label"] == "(unattributed)"


def test_empty_history_is_empty():
    out = episodes(_map(), {"commits": [], "ops": []})
    assert out == {"episodes": [], "groups": []}


# ── episode_rail_layout (vertical git-log) ──────────────────────────────────────────────────────


def _rail(commits, ops):
    return episode_rail_layout(episodes(_map(("F1", "A"), ("F2", "B"), ("F3", "C")), _hist(commits, ops)))


def test_newest_episode_is_row_zero():
    out = _rail([(0, "s0", "c0"), (1, "s1", "c1"), (2, "s2", "c2")],
                [("a", "add", "F1", 0), ("b", "add", "F1", 1), ("c", "add", "F1", 2)])
    rows = {r["index"]: r["row"] for r in out["rows"]}
    assert rows == {2: 0, 1: 1, 0: 2}  # commit 2 (newest) on top


def test_a_feature_s_episodes_share_one_lane():
    out = _rail([(0, "s0", "c0"), (1, "s1", "c1")],
                [("a", "add", "F1", 0), ("b", "add", "F1", 1)])
    lanes = {r["lane"] for r in out["rows"]}
    assert lanes == {0} and out["lane_count"] == 1  # one feature -> one column, both episodes on it


def test_non_overlapping_feature_spans_reuse_a_lane_overlapping_ones_do_not():
    # F1 at commits 0-1, F2 at 2-3: spans don't overlap in rows -> share lane 0.
    out = _rail([(i, f"s{i}", f"c{i}") for i in range(4)],
                [("a", "add", "F1", 0), ("b", "add", "F1", 1), ("c", "add", "F2", 2), ("d", "add", "F2", 3)])
    assert out["lane_count"] == 1  # interval coloring reuses the single lane

    # F1 at 0 and 3, F2 at 1-2: F2's span sits INSIDE F1's -> they overlap -> two lanes.
    out2 = _rail([(i, f"s{i}", f"c{i}") for i in range(4)],
                 [("a", "add", "F1", 0), ("b", "add", "F2", 1), ("c", "add", "F2", 2), ("d", "add", "F1", 3)])
    assert out2["lane_count"] == 2
