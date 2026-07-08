"""Tests for sgt.lens.tree -- the hierarchical feature tree (plan U12, R15/R16/R17)."""

from __future__ import annotations

from itertools import combinations

from sgt.core.lens import get
from sgt.core.op import make_op
from sgt.core.store import Store
from sgt.lens import tree
from tests.laws import corpus


def _clique_graph(num_cliques: int, clique_size: int, weight: float = 50.0):
    """`num_cliques` fully-connected, mutually disconnected cliques -- a fixture whose optimal
    CPM partition is exactly `num_cliques` groups at (almost) any resolution, since merging two
    disconnected cliques only adds size penalty with zero edge-weight benefit."""
    members: list[str] = []
    fused: dict = {}
    for c in range(num_cliques):
        clique = [f"g{c}_n{i}" for i in range(clique_size)]
        members.extend(clique)
        for a, b in combinations(clique, 2):
            fused[frozenset((a, b))] = weight
    return members, fused


def test_split_once_finds_target_arity_on_seven_disjoint_cliques():
    members, fused = _clique_graph(num_cliques=7, clique_size=5)
    adj = tree._adjacency(fused)

    result = tree._split_once(members, fused, adj)

    assert result.reason is None
    assert len(result.groups) == 7
    assert all(len(g) == 5 for g in result.groups)
    assert sorted(m for g in result.groups for m in g) == sorted(members)


def test_split_once_reports_closest_arity_when_target_unreachable():
    members, fused = _clique_graph(num_cliques=3, clique_size=5)
    adj = tree._adjacency(fused)

    result = tree._split_once(members, fused, adj)

    assert result.groups is not None
    assert len(result.groups) == 3
    assert result.reason == "closest_arity"


def test_split_once_refuses_a_single_cohesive_clique():
    members, fused = _clique_graph(num_cliques=1, clique_size=10)
    adj = tree._adjacency(fused)

    result = tree._split_once(members, fused, adj)

    assert result.groups is None
    assert result.reason == "stop_split"


def test_attach_orphans_folds_sub_min_cluster_into_most_coupled_sibling():
    big = [["a", "b", "c", "d"], ["e", "f", "g", "h"]]
    orphan = ["x"]
    adj = {"x": [("a", 5.0), ("e", 1.0)]}

    groups = tree._attach_orphans(big, [orphan], adj)

    assert "x" in groups[0]
    assert "x" not in groups[1]


def test_subdivide_stops_at_max_leaf_without_attempting_a_split():
    node = tree._subdivide(["a", "b"], fused={}, adj={}, depth=0, max_depth=4, max_leaf=24)
    assert node["children"] == []
    assert node["split_reason"] == "max_leaf"


def test_subdivide_stops_at_max_depth_regardless_of_size():
    members, fused = _clique_graph(num_cliques=7, clique_size=5)
    adj = tree._adjacency(fused)
    node = tree._subdivide(members, fused, adj, depth=3, max_depth=4, max_leaf=1)
    assert node["children"] == []
    assert node["split_reason"] == "max_depth"


def test_subdivide_recurses_into_children_below_max_depth():
    members, fused = _clique_graph(num_cliques=7, clique_size=5)
    adj = tree._adjacency(fused)
    node = tree._subdivide(members, fused, adj, depth=0, max_depth=4, max_leaf=1, min_lane=1)
    assert len(node["children"]) == 7
    assert node["split_reason"] is None
    for child in node["children"]:
        # a single 5-node clique has no internal substructure to find at any searched gamma --
        # it recurses one level, refuses to split further (stop_split), and stays a leaf.
        assert child["children"] == []
        assert child["split_reason"] == "stop_split"


def test_assign_ops_to_leaves_plurality_vote_with_smallest_id_tiebreak():
    nodes = {
        "N0": {"children": ["N1", "N2"], "members": []},
        "N1": {"children": [], "members": ["a.py::foo", "a.py::bar"]},
        "N2": {"children": [], "members": ["b.py::baz"]},
    }
    two_to_one = make_op(
        {"a.py::foo": (None, "v1"), "a.py::bar": (None, "v2"), "b.py::baz": (None, "v3")},
        {"a.py::foo": b"1", "a.py::bar": b"2", "b.py::baz": b"3"},
    )
    tie = make_op(
        {"a.py::foo": (None, "v4"), "b.py::baz": (None, "v5")},
        {"a.py::foo": b"4", "b.py::baz": b"5"},
    )
    dead_only = make_op({"c.py::gone": (None, "v6")}, {"c.py::gone": b"6"})

    op_leaf = tree.assign_ops_to_leaves(nodes, [two_to_one, tie, dead_only])

    assert op_leaf[two_to_one.id] == "N1"  # 2 votes vs 1
    assert op_leaf[tie.id] == "N1"  # tie N1/N2 -> smallest id wins
    assert dead_only.id not in op_leaf  # touches no leaf-assigned symbol


def test_build_partitions_every_alive_symbol_into_exactly_one_leaf(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()

    result = tree.build(repo, ops, ideal)
    nodes = result["nodes"]

    from sgt.lens.cluster import alive_nodes
    expected = alive_nodes(ideal, ops)

    leaf_members = [m for nid, nd in nodes.items() if not nd["children"] for m in nd["members"]]
    assert sorted(leaf_members) == sorted(expected)
    assert len(leaf_members) == len(set(leaf_members))  # partition, not overlapping cover

    entity_ops = [op for op in ops if any("::" in s and "__residue__" not in s and "__anchor__" not in s for s in op.footprint)]
    for op in entity_ops:
        if any(sym in expected for sym in op.footprint):
            assert op.id in result["op_leaf"]


# --- Greene feature identity (plan D5) --------------------------------------------------------


def test_match_identities_continuation_keeps_feature_id():
    id_map, events = tree.match_identities({"F3": frozenset("abc")}, {"N1": frozenset("abc")})
    assert id_map["N1"] == "F3"
    assert events == [{"event": "continuation", "feature_id": "F3"}]


def test_match_identities_birth_mints_id_past_the_largest_existing():
    # old has F0 and F5 -> a birth must not reuse either; fresh ids start at F6
    id_map, events = tree.match_identities(
        {"F0": frozenset("ab"), "F5": frozenset("cd")}, {"N1": frozenset("xyz")}
    )
    assert id_map["N1"] == "F6"
    assert {"event": "birth", "feature_id": "F6"} in events
    assert sorted(e["event"] for e in events) == ["birth", "death", "death"]


def test_match_identities_split_one_old_into_two_new():
    # F0's members divide evenly; both halves overlap F0 at exactly theta=0.5
    id_map, events = tree.match_identities(
        {"F0": frozenset("abcd")}, {"N1": frozenset("ab"), "N2": frozenset("cd")}
    )
    # tie-break gives the continuation to the smaller new id; the other is a split off F0
    assert id_map["N1"] == "F0"
    assert id_map["N2"] == "F1"
    by_event = {e["event"]: e for e in events}
    assert by_event["continuation"]["feature_id"] == "F0"
    assert by_event["split"] == {"event": "split", "feature_id": "F1", "parent": "F0"}


def test_match_identities_merge_two_old_into_one_new():
    id_map, events = tree.match_identities(
        {"F0": frozenset("ab"), "F1": frozenset("cd")}, {"N1": frozenset("abcd")}
    )
    assert id_map["N1"] == "F0"  # survivor keeps the best (tie -> smallest) old id
    merge = next(e for e in events if e["event"] == "merge")
    assert merge == {"event": "merge", "feature_id": "F0", "merged_from": ["F0", "F1"]}
    assert not any(e["event"] == "death" for e in events)  # F1 is merged, not dead


def test_match_identities_unmatched_old_is_a_death():
    id_map, events = tree.match_identities(
        {"F0": frozenset("abc"), "F1": frozenset("xyz")}, {"N1": frozenset("abc")}
    )
    assert id_map["N1"] == "F0"
    assert {"event": "death", "feature_id": "F1"} in events


def test_load_save_round_trip(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    built = tree.build(repo, ops, ideal)
    tree.save(repo, built)
    loaded = tree.load(repo)

    assert loaded["roots"] == built["roots"]
    assert set(loaded["nodes"]) == set(built["nodes"])
    assert loaded["op_leaf"] == built["op_leaf"]


def test_rebuild_on_unchanged_history_renames_nothing(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    first = tree.build(repo, ops, ideal)
    tree.save(repo, first)
    second = tree.build(repo, ops, ideal)  # `previous` auto-loaded from .sgt/tree/tree.json

    first_leaves = {nid: sorted(nd["members"]) for nid, nd in first["nodes"].items() if not nd["children"]}
    second_leaves = {nid: sorted(nd["members"]) for nid, nd in second["nodes"].items() if not nd["children"]}
    assert first_leaves == second_leaves  # identical ids AND members -> pure continuation
    assert all(e["event"] == "continuation" for e in second["identity_events"])


def test_assign_pin_overrides_greene_and_survives_reruns(tmp_path):
    from sgt.lens.pins import Pins, save_pins

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    first = tree.build(repo, ops, ideal)
    tree.save(repo, first)
    member = next(m for nd in first["nodes"].values() if not nd["children"] for m in nd["members"])

    save_pins(repo, Pins(assign={member: "auth"}))  # pin overrides whatever id Greene would keep

    def leaf_of(result, m):
        return next(nid for nid, nd in result["nodes"].items() if not nd["children"] and m in nd["members"])

    for _ in range(10):  # ten re-clusters: the pinned member never leaves feature "auth"
        result = tree.build(repo, ops, ideal)
        assert leaf_of(result, member) == "auth"
        tree.save(repo, result)


# --- labeling + DEDUP (plan R15/R17) ----------------------------------------------------------


class _StubLabeler:
    """A labeler that returns caller-controlled labels -- lets DEDUP be tested without an LLM."""

    def __init__(self, labels: dict[frozenset, str]):
        self._labels = labels

    def _fl(self, label):
        from sgt.lens.label import FeatureLabel
        return FeatureLabel(label=label, rationale=f"stub for {label}")

    def label(self, members, subjects=None):
        return self._fl(self._labels.get(frozenset(members), "Unnamed"))

    def label_super(self, child_labels, files):
        return self._fl(" / ".join(sorted(set(child_labels))))


def test_dedup_merges_same_label_siblings_and_remaps_op_leaf():
    # two sibling leaves the stub labels identically -> DEDUP collapses them into one
    result = {
        "roots": ["N0"],
        "op_leaf": {"opA": "N1", "opB": "N2"},
        "nodes": {
            "N0": {"id": "N0", "parent": None, "depth": 0, "members": ["a", "b", "c", "d"],
                   "size": 4, "dir": "pkg", "children": ["N1", "N2"], "split_reason": None},
            "N1": {"id": "N1", "parent": "N0", "depth": 1, "members": ["a", "b"], "size": 2,
                   "dir": "pkg", "children": [], "split_reason": "stop_split"},
            "N2": {"id": "N2", "parent": "N0", "depth": 1, "members": ["c", "d"], "size": 2,
                   "dir": "pkg", "children": [], "split_reason": "stop_split"},
        },
    }
    stub = _StubLabeler({frozenset(["a", "b"]): "Auth", frozenset(["c", "d"]): "Auth"})

    tree.label_tree(result, labeler=stub)

    leaves = [nid for nid, nd in result["nodes"].items() if not nd["children"]]
    assert len(leaves) == 1  # the two same-label leaves merged
    survivor = leaves[0]
    assert sorted(result["nodes"][survivor]["members"]) == ["a", "b", "c", "d"]
    assert result["op_leaf"] == {"opA": survivor, "opB": survivor}  # remapped to the survivor


def _leaf(nid, parent, members, dir_):
    return {"id": nid, "parent": parent, "depth": 2, "members": members, "size": len(members),
            "dir": dir_, "children": [], "split_reason": "stop_split"}


def test_dedup_disambiguates_cross_subsystem_label_collision():
    # two "Store" leaves under *different* subsystems (N1, N2) -- not siblings, so they are
    # disambiguated by folder rather than merged.
    result = {
        "roots": ["N0"],
        "op_leaf": {},
        "nodes": {
            "N0": {"id": "N0", "parent": None, "depth": 0, "members": ["a", "x", "b", "y"],
                   "size": 4, "dir": "top", "children": ["N1", "N2"], "split_reason": None},
            "N1": {"id": "N1", "parent": "N0", "depth": 1, "members": ["a", "x"], "size": 2,
                   "dir": "core", "children": ["N3", "N4"], "split_reason": None},
            "N2": {"id": "N2", "parent": "N0", "depth": 1, "members": ["b", "y"], "size": 2,
                   "dir": "cli", "children": ["N5", "N6"], "split_reason": None},
            "N3": _leaf("N3", "N1", ["a"], "core"),
            "N4": _leaf("N4", "N1", ["x"], "core"),
            "N5": _leaf("N5", "N2", ["b"], "cli"),
            "N6": _leaf("N6", "N2", ["y"], "cli"),
        },
    }
    stub = _StubLabeler({
        frozenset(["a"]): "Store", frozenset(["x"]): "Order",
        frozenset(["b"]): "Store", frozenset(["y"]): "Cart",
    })

    tree.label_tree(result, labeler=stub)

    store_leaves = sorted(
        nd["label"] for nd in result["nodes"].values() if not nd["children"] and nd["label"].startswith("Store")
    )
    assert store_leaves == ["Store · cli", "Store · core"]


def test_label_tree_offline_fallback_is_deterministic(tmp_path, monkeypatch):
    import sgt.config

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    def _no_client(*_a, **_k):
        raise RuntimeError("no API key")

    monkeypatch.setattr(sgt.config, "get_client", _no_client)

    def labels_of(result):
        return {nid: nd["label"] for nid, nd in result["nodes"].items()}

    a = tree.build(repo, ops, ideal)
    tree.label_tree(a, repo)
    b = tree.build(repo, ops, ideal)
    tree.label_tree(b, repo)

    assert all(lbl for lbl in labels_of(a).values())  # every node named, no crash offline
    assert labels_of(a) == labels_of(b)  # deterministic fallback
