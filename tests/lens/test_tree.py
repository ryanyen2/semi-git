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


def test_feature_edges_rolls_symbol_pairs_up_to_leaf_pairs_and_sums_weight():
    nodes = {
        "N0": {"children": ["F1", "F2"], "members": []},
        "F1": {"children": [], "members": ["a.py::foo", "a.py::bar"]},
        "F2": {"children": [], "members": ["b.py::baz"]},
    }
    fused = {
        frozenset(("a.py::foo", "a.py::bar")): 5.0,  # intra-F1 -- must not appear as an edge
        frozenset(("a.py::foo", "b.py::baz")): 3.0,  # cross F1<->F2
        frozenset(("a.py::bar", "b.py::baz")): 2.0,  # cross F1<->F2, same pair, sums with above
    }
    edges = tree.feature_edges(nodes, fused)
    assert edges == [{"a": "F1", "b": "F2", "weight": 5.0}]


def test_feature_edges_ignores_pairs_touching_a_symbol_outside_any_leaf():
    nodes = {"F1": {"children": [], "members": ["a.py::foo"]}}
    fused = {frozenset(("a.py::foo", "dead.py::gone")): 9.0}
    assert tree.feature_edges(nodes, fused) == []


def test_feature_edges_sorted_descending_by_weight_then_by_id():
    nodes = {
        "F1": {"children": [], "members": ["a.py::x"]},
        "F2": {"children": [], "members": ["b.py::y"]},
        "F3": {"children": [], "members": ["c.py::z"]},
    }
    fused = {
        frozenset(("a.py::x", "b.py::y")): 1.0,
        frozenset(("a.py::x", "c.py::z")): 4.0,
        frozenset(("b.py::y", "c.py::z")): 4.0,
    }
    edges = tree.feature_edges(nodes, fused)
    assert edges == [
        {"a": "F1", "b": "F3", "weight": 4.0},
        {"a": "F2", "b": "F3", "weight": 4.0},
        {"a": "F1", "b": "F2", "weight": 1.0},
    ]


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


def test_residue_op_follows_its_anchor_entitys_leaf(tmp_path):
    """U4/R3: a residue op assigns to its anchor ENTITY's lane, not the residue symbol's own
    cluster -- so a feature owns the whitespace after its own entities, keeping a feature-scoped
    revert/materialization coherent (the U32 fix). A file-head residue (HEAD sentinel, no anchor
    entity) and an anchor whose entity is dead both fall back to the symbol's own cluster."""
    nodes = {
        "F1": {"children": [], "members": ["a.py::foo"]},
        # the residue after foo happens to cluster into F2 (with bar) -- its OP must still go to F1.
        "F2": {"children": [], "members": ["a.py::bar", "a.py::__residue__::foo",
                                            "a.py::__residue__::\x00HEAD\x00"]},
    }
    residue = make_op({"a.py::__residue__::foo": (None, "v1")}, {"a.py::__residue__::foo": b"1"})
    head_residue = make_op({"a.py::__residue__::\x00HEAD\x00": (None, "v2")},
                           {"a.py::__residue__::\x00HEAD\x00": b"2"})

    op_leaf = tree.assign_ops_to_leaves(nodes, [residue, head_residue])
    assert op_leaf[residue.id] == "F1"        # follows a.py::foo's lane, not its own cluster (F2)
    assert op_leaf[head_residue.id] == "F2"   # HEAD residue has no anchor entity -> own cluster


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


def test_match_identities_birth_mints_a_content_addressed_id():
    # a birth mints `f-<founding op>` from the founding op, never reusing an existing id
    id_map, events = tree.match_identities(
        {"F0": frozenset("ab"), "F5": frozenset("cd")}, {"N1": frozenset("xyz")},
        founding={"N1": "op-xyz"},
    )
    assert id_map["N1"] == "f-op-xyz"
    assert {"event": "birth", "feature_id": "f-op-xyz"} in events
    assert sorted(e["event"] for e in events) == ["birth", "death", "death"]


def test_match_identities_split_one_old_into_two_new():
    # F0's members divide evenly; both halves overlap F0 at exactly theta=0.5
    id_map, events = tree.match_identities(
        {"F0": frozenset("abcd")}, {"N1": frozenset("ab"), "N2": frozenset("cd")},
        founding={"N2": "op-cd"},
    )
    # tie-break gives the continuation to the smaller new id; the other is a split off F0,
    # minting a content-addressed `f-<founding op>` id
    assert id_map["N1"] == "F0"
    assert id_map["N2"] == "f-op-cd"
    by_event = {e["event"]: e for e in events}
    assert by_event["continuation"]["feature_id"] == "F0"
    assert by_event["split"] == {"event": "split", "feature_id": "f-op-cd", "parent": "F0"}


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


def test_save_skips_the_write_when_byte_identical_to_disk(tmp_path):
    """A rebuild with no new ops must not touch `tree.json`'s mtime -- it's a `.sgt/**/*.json`
    path a client's file watcher invalidates its cache on, so an unconditional rewrite on every
    no-op read makes a client's own refresh retrigger another refresh, forever."""
    import sgt.state as state

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    # The first build has no `previous` tree to Greene-match against (birth events); the second
    # build's `previous` is auto-loaded from what the first just saved, so it settles into pure
    # continuations -- only from the *third* build on is the result byte-identical to the prior
    # save, which is the steady-state a repeated no-op read actually hits.
    tree.save(repo, tree.build(repo, ops, ideal))
    tree.save(repo, tree.build(repo, ops, ideal))
    mtime_before = state.path(repo, "tree").stat().st_mtime_ns

    rebuilt = tree.build(repo, ops, ideal)  # same ops/ideal, already-settled previous
    tree.save(repo, rebuilt)

    mtime_after = state.path(repo, "tree").stat().st_mtime_ns
    assert mtime_after == mtime_before, "save() rewrote tree.json with no new state"


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


def test_dirty_subtree_rebuild_is_deterministic(tmp_path):
    """Phase 2 (dirty-subtree reclustering): a real edit -- not just a no-op refresh -- must still
    resplice/resplit deterministically. Two independent incremental builds from the same
    `previous` + new commit must agree, member-for-member."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()
    before = tree.build(repo, ops, ideal)
    tree.save(repo, before)

    corpus._write(repo, "d.py", "def quux():\n    return 4\n")
    corpus._commit(repo, "add quux", 7)

    ideal2, ops2 = get(repo), Store(repo).all_ops()
    incremental_a = tree.build(repo, ops2, ideal2, previous=before)
    incremental_b = tree.build(repo, ops2, ideal2, previous=before)

    def leaves(result):
        return {nid: sorted(nd["members"]) for nid, nd in result["nodes"].items() if not nd["children"]}

    assert leaves(incremental_a) == leaves(incremental_b)
    assert any("d.py::quux" in members for members in leaves(incremental_a).values())


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


def test_pinned_label_on_a_carried_id_survives_an_ordinary_rebuild(tmp_path):
    """A continuation always carries its old feature id, so a feature the user renamed (a `labels`
    pin keyed to that id) rebuilds with the id -- and therefore the pinned label -- intact. The id
    is never re-minted out from under the pin by an ordinary build."""
    from sgt.lens.pins import Pins

    repo = corpus.CORPUS["class_with_methods"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    natural = tree.build(repo, ops, ideal, pins=Pins(), previous=None)
    members = sorted({m for nd in natural["nodes"].values() if not nd["children"] for m in nd["members"]})

    # the feature lives under a carried id and carries a user-pinned label keyed to it.
    carried_id = next(nid for nid, nd in natural["nodes"].items() if not nd["children"])
    prev = {"nodes": {carried_id: {"members": members, "children": [], "parent": None, "depth": 0}}, "roots": [carried_id]}
    pins = Pins(labels={carried_id: "My Custom Label"})
    result = tree.build(repo, ops, ideal, pins=pins, previous=prev)
    tree.label_tree(result, repo, pins=pins)

    leaf = next(nid for nid, nd in result["nodes"].items() if not nd["children"])
    assert leaf == carried_id  # carried across the rebuild
    assert result["nodes"][leaf]["label"] == "My Custom Label"  # the pinned label survived


def test_authored_feature_label_overrides_the_cluster_leaf_and_survives_a_rebuild(tmp_path):
    """U7/R3: an authored feature (U6) is the authority over the clustered leaf. `label_tree` shows
    the authored label where a leaf's symbols are claimed, and a re-cluster does not scatter the
    authored members or drop the override."""
    from sgt.lens import authored

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    first = tree.build(repo, ops, ideal)
    tree.save(repo, first)
    fid, node = next((nid, nd) for nid, nd in first["nodes"].items() if not nd["children"])
    members = node["members"]

    feat = authored.create(members, "Authored Authority")
    authored.save_authored(repo, {feat.id: feat})

    result = tree.build(repo, ops, ideal)  # a plain re-cluster; the authored `af-` id is carried
    tree.label_tree(result, repo)

    member_leaf = tree.leaf_member_index(result["nodes"])
    claimed_leaves = {member_leaf[m] for m in members if m in member_leaf}
    assert len(claimed_leaves) == 1  # authored members were not scattered by the recluster
    claimed = next(iter(claimed_leaves))
    assert result["nodes"][claimed]["label"] == "Authored Authority"  # authored label wins


def test_authored_feature_with_empty_label_defers_to_the_clustered_label(tmp_path):
    """A claim with an *empty* label register (what `ledger.assign_at_save`'s new-lane cascade seeds)
    is "claimed but unnamed": it must NOT override the clustered/LLM label -- otherwise a save-time
    lane permanently shadows the real name a rebuild computes. Only a deliberate `rename` (non-empty)
    overrides (the test above)."""
    from sgt.lens import authored

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    first = tree.build(repo, ops, ideal)
    tree.label_tree(first, repo)
    tree.save(repo, first)
    fid, node = next((nid, nd) for nid, nd in first["nodes"].items() if not nd["children"])
    members, clustered_label = node["members"], node["label"]

    feat = authored.create(members, "")  # empty register -- claimed, not deliberately named
    authored.save_authored(repo, {feat.id: feat})

    result = tree.build(repo, ops, ideal)
    tree.label_tree(result, repo)

    member_leaf = tree.leaf_member_index(result["nodes"])
    claimed = member_leaf[members[0]]
    assert result["nodes"][claimed]["label"] == clustered_label  # clustered label stands, not blank


def test_build_is_shape_stable_whether_or_not_authored_features_exist(tmp_path):
    """Authored `af-` ids are additive: a build with an authored collection present produces the
    same tree shape as one without (authored features overlay the tree, they do not restructure
    it)."""
    from sgt.lens import authored
    from sgt.lens.pins import Pins

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()

    baseline = tree.build(repo, ops, ideal, pins=Pins())
    authored.save_authored(repo, {"af-x": authored.AuthoredFeature(id="af-x", label="X")})
    with_authored = tree.build(repo, ops, ideal, pins=Pins())
    assert sorted(with_authored["nodes"]) == sorted(baseline["nodes"])


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

    def leaf_request(self, members, subjects=None, kinds=None):
        return ("leaf", members, members)

    def super_request(self, child_labels, files):
        return ("super", (child_labels, files), [*child_labels, *files])

    def label_many(self, entries):
        out = []
        for kind, payload, _members in entries:
            if kind == "leaf":
                out.append(self.label(payload))
            else:
                child_labels, files = payload
                out.append(self.label_super(child_labels, files))
        return out


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


def test_assign_pin_with_scattered_members_resolves_to_one_leaf(tmp_path):
    # Regression: `_apply_assign_pins` assumed must-link keeps every member of one assign target in a
    # single leaf. But a target orphaned in the *previous* tree is spliced verbatim (never reclustered),
    # so its pinned members can scatter across several current leaves. Renaming *every* such leaf to the
    # pinned id aliased two leaves onto one node -> duplicate children -> `_dedup` deleted the survivor
    # and crashed with KeyError. The pin must resolve to the single plurality leaf instead.
    from sgt.lens.pins import Pins

    result = {
        "roots": ["N0"],
        "op_leaf": {"opA": "N1", "opB": "N2"},
        "nodes": {
            "N0": {"id": "N0", "parent": None, "depth": 0, "members": ["c1", "c2", "t1"],
                   "size": 3, "dir": "pkg", "children": ["N1", "N2"], "split_reason": None},
            "N1": _leaf("N1", "N0", ["c1", "c2"], "pkg"),   # plurality: 2 pinned members
            "N2": _leaf("N2", "N0", ["t1"], "tests"),        # 1 pinned member
        },
    }
    # all three members pinned to one orphan feature id, scattered across N1 (2) and N2 (1).
    pins = Pins(assign={"c1": "af-x", "c2": "af-x", "t1": "af-x"})

    tree._apply_assign_pins(result, pins)

    children = result["nodes"]["N0"]["children"]
    assert len(children) == len(set(children))                 # no aliased/duplicate child id
    assert children.count("af-x") == 1                          # pin attaches to exactly one leaf
    assert set(result["nodes"]) - {"N0"} == set(children)       # nodes and children stay consistent
    # the plurality leaf (N1, 2 members) becomes af-x; the minority leaf keeps its own id.
    assert "af-x" in result["nodes"] and result["nodes"]["af-x"]["members"] == ["c1", "c2"]
    assert "N2" in result["nodes"]
    assert result["op_leaf"] == {"opA": "af-x", "opB": "N2"}    # op_leaf remapped for the renamed leaf


def test_regroup_flat_root_groups_by_package_leaves_singletons_flat_and_is_idempotent():
    def leaf(dir_, m):
        return {"members": [m], "size": 1, "dir": dir_, "depth": 1, "children": [],
                "split_reason": "stop_split"}

    # 13 children (> max_arity): 4 in a/x, 4 in b/y, 4 in c/z, and one lone d/w.
    kids = ([leaf("a/x", f"a{i}") for i in range(4)]
            + [leaf("b/y", f"b{i}") for i in range(4)]
            + [leaf("c/z", f"c{i}") for i in range(4)]
            + [leaf("d/w", "d0")])
    root = {"members": [k["members"][0] for k in kids], "size": 13, "dir": "top", "depth": 0,
            "children": kids, "split_reason": None}

    tree._regroup_flat_root(root, max_arity=3)

    kinds = [(nd["dir"], bool(nd["children"])) for nd in root["children"]]
    assert sorted(kinds) == [("a/x", True), ("b/y", True), ("c/z", True), ("d/w", False)]
    assert all(nd["split_reason"] == "regrouped" for nd in root["children"] if nd["children"])
    subsystem = next(nd for nd in root["children"] if nd["dir"] == "a/x")
    assert subsystem["depth"] == 1 and all(c["depth"] == 2 for c in subsystem["children"])  # re-stamped
    leaves = [m for nd in root["children"] for c in ([nd] if not nd["children"] else nd["children"])
              for m in c["members"]]
    assert sorted(leaves) == sorted(f"{p}{i}" for p in "abc" for i in range(4)) + ["d0"]  # none lost

    before = [nd["dir"] for nd in root["children"]]
    tree._regroup_flat_root(root, max_arity=3)  # idempotent: distinct package dirs -> no re-nesting
    assert [nd["dir"] for nd in root["children"]] == before


def test_dedup_never_merges_internal_siblings_that_share_a_label():
    # Two subsystem (internal) siblings whose single children the stub labels identically -> both
    # inherit the same label. They must NOT merge: flattening an internal node to a leaf would
    # orphan its subtree and leak an internal id into op_leaf (the KeyError-N60 regression). Only
    # leaf siblings collapse; a colliding-label subsystem is left for the folder pass on its leaves.
    result = {
        "roots": ["N0"],
        "op_leaf": {"opA": "N3", "opB": "N4"},
        "nodes": {
            "N0": {"id": "N0", "parent": None, "depth": 0, "members": ["a", "b"], "size": 2,
                   "dir": "top", "children": ["N1", "N2"], "split_reason": None},
            "N1": {"id": "N1", "parent": "N0", "depth": 1, "members": ["a"], "size": 1,
                   "dir": "core", "children": ["N3"], "split_reason": None},
            "N2": {"id": "N2", "parent": "N0", "depth": 1, "members": ["b"], "size": 1,
                   "dir": "cli", "children": ["N4"], "split_reason": None},
            "N3": _leaf("N3", "N1", ["a"], "core"),
            "N4": _leaf("N4", "N2", ["b"], "cli"),
        },
    }
    stub = _StubLabeler({frozenset(["a"]): "Auth", frozenset(["b"]): "Auth"})

    tree.label_tree(result, labeler=stub)

    assert set(result["nodes"]) >= {"N0", "N1", "N2", "N3", "N4"}  # nothing orphaned
    assert result["nodes"]["N1"]["children"] == ["N3"]
    assert result["nodes"]["N2"]["children"] == ["N4"]
    leaves = {nid for nid, nd in result["nodes"].items() if not nd["children"]}
    assert set(result["op_leaf"].values()) <= leaves  # no internal-id leak into op_leaf


def test_stale_signals_version_forces_recluster_but_matching_version_splices(tmp_path, monkeypatch):
    from sgt.lens import cluster

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()
    prev = tree.build(repo, ops, ideal)
    assert prev["signals_version"] == cluster.SIGNALS_VERSION

    spliced = {"hit": False}
    orig = tree._build_root

    def _spy(*a, **k):
        spliced["hit"] = True
        return orig(*a, **k)

    monkeypatch.setattr(tree, "_build_root", _spy)

    tree.build(repo, ops, ideal, previous={**prev, "signals_version": "stale-old"})
    assert spliced["hit"] is False  # a signal-recipe change forces a full resplit, never the splice

    tree.build(repo, ops, ideal, previous=prev)
    assert spliced["hit"] is True  # same version -> ordinary incremental splice


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
