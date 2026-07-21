"""Tests for sgt.lens.cluster -- the fused coupling graph (plan U12, R15/R16)."""

from __future__ import annotations

from sgt.core.lens import get
from sgt.core.op import make_op
from sgt.core.store import Store
from sgt.lens import cluster
from sgt.store.gitbind import init_store
from tests.laws import corpus


def _hub_repo(tmp_path):
    """Two commits: commit 1 tangles ``foo`` (calls ``shared``) with ``shared`` itself, plus an
    unrelated ``bar``; commit 2 edits only ``shared``. ``shared`` is touched by 2 of the 3 mined
    ops, ``foo``/``bar`` by 1 each -- enough to cross the hub floor (``max(2, ...)``) for exactly
    one symbol on a tiny history."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "hub.py").write_text("def shared():\n    return 0\n", encoding="utf-8")
    (repo / "a.py").write_text("def foo():\n    return shared()\n", encoding="utf-8")
    (repo / "b.py").write_text("def bar():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feat(core): add shared, foo, bar")

    (repo / "hub.py").write_text("def shared():\n    return 2\n", encoding="utf-8")
    gb.commit_all("feat(core): tweak shared alone")
    return repo


def test_alive_nodes_excludes_removed_and_replaced_symbols(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()

    nodes = cluster.alive_nodes(ideal, ops)

    assert "b.py::baz" in nodes
    assert "c.py::qux" in nodes
    assert "a.py::foo" not in nodes  # renamed away to bar, then moved, then deleted
    assert "b.py::bar" not in nodes  # deleted in the "delete bar" commit
    assert "config.yaml" in nodes  # whole-file pseudo-symbol, id == path
    assert "logo.bin" in nodes


def test_hub_symbol_stripped_from_cochange_but_not_structural(tmp_path):
    repo = _hub_repo(tmp_path)
    ideal = get(repo)
    ops = Store(repo).all_ops()

    nodes, hubs, cochange, structural = cluster.signals(repo, ops, ideal)

    assert "hub.py::shared" in hubs
    assert "a.py::foo" not in hubs
    assert "b.py::bar" not in hubs

    # foo+shared were mined as one tangled op (foo calls shared) -- would be a cochange edge,
    # except shared is hub-stripped before pairs are formed.
    assert frozenset({"a.py::foo", "hub.py::shared"}) not in cochange
    assert not cochange  # no other pair ever shares an op's footprint in this fixture

    # Hub-stripping only affects the co-change signal; the structural (calls) edge survives.
    assert frozenset({"a.py::foo", "hub.py::shared"}) in structural


def test_scope_edges_group_symbols_by_conventional_commit_scope():
    """A pure-function test against hand-built ops -- scope grouping is a fact about
    (op.provenance -> commit subject -> declared scope), independent of mining internals."""
    op1 = make_op(
        {"a.py::foo": (None, "v1"), "b.py::bar": (None, "v2")},
        {"a.py::foo": b"1", "b.py::bar": b"2"},
        provenance=("sha1",),
    )
    op2 = make_op(
        {"c.py::baz": (None, "v3")}, {"c.py::baz": b"3"}, provenance=("sha2",),
    )
    subjects = {"sha1": "feat(core): add foo and bar", "sha2": "feat(other): add baz"}
    nodes = {"a.py::foo", "b.py::bar", "c.py::baz"}

    edges = cluster.scope_edges([op1, op2], subjects, nodes, hubs=set())

    assert edges == {frozenset({"a.py::foo", "b.py::bar"}): 10.0}


def test_commit_edges_bind_symbols_sharing_a_provenance_sha():
    """Co-commit recovers what U2's untangling strips: two single-symbol ops from the SAME commit
    changed together, so they get an edge; an op from another commit is disjoint."""
    op1 = make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"1"}, provenance=("sha1",))
    op2 = make_op({"b.py::bar": (None, "v2")}, {"b.py::bar": b"2"}, provenance=("sha1",))
    op3 = make_op({"c.py::baz": (None, "v3")}, {"c.py::baz": b"3"}, provenance=("sha2",))
    nodes = {"a.py::foo", "b.py::bar", "c.py::baz"}

    edges = cluster.commit_edges([op1, op2, op3], nodes, hubs=set())

    assert edges == {frozenset({"a.py::foo", "b.py::bar"}): 1.0}  # scale/(size-1) = 1/1


def test_commit_edges_exclude_hubs_and_mega_commits():
    a = make_op({"a.py::foo": (None, "v1")}, {"a.py::foo": b"1"}, provenance=("s",))
    hub = make_op({"hub.py::h": (None, "v2")}, {"hub.py::h": b"2"}, provenance=("s",))
    assert cluster.commit_edges([a, hub], {"a.py::foo", "hub.py::h"}, hubs={"hub.py::h"}) == {}

    ops = [make_op({f"f{i}.py::x": (None, "v")}, {f"f{i}.py::x": b"1"}, provenance=("s",)) for i in range(5)]
    nodes = {f"f{i}.py::x" for i in range(5)}
    assert cluster.commit_edges(ops, nodes, hubs=set(), max_commit=4) == {}  # 5-symbol commit > cap


def test_path_edges_bind_symbols_in_the_same_file_and_respect_hubs_and_cap():
    # an entity and its file's residue live in one file -> a cohesion edge; a different file is disjoint.
    nodes = {"a.py::foo", "a.py::__residue__::foo", "b.py::bar"}
    edges = cluster.path_edges(nodes, hubs=set(), scale=1.0)
    assert edges == {frozenset({"a.py::foo", "a.py::__residue__::foo"}): 1.0}

    three = {"a.py::x", "a.py::y", "a.py::z"}
    assert cluster.path_edges(three, hubs={"a.py::z"}, scale=1.0) == {frozenset({"a.py::x", "a.py::y"}): 1.0}
    assert cluster.path_edges(three, hubs=set(), max_file=2) == {}  # 3-symbol file > cap


def test_commit_scope_parses_conventional_prefix_and_falls_back_to_type():
    assert cluster.commit_scope("feat(store): add locking") == "store"
    assert cluster.commit_scope("fix: null check") == "fix"
    assert cluster.commit_scope("no scope or colon here") is None


def test_hub_normalize_preserves_total_weight_and_demotes_high_degree_pairs():
    # "hub" touches three others; "solo" touches one other -- hub_normalize should shrink the
    # hub's edges relative to solo's, while the total weight is preserved.
    structural = {
        frozenset({"hub", "a"}): 1.0,
        frozenset({"hub", "b"}): 1.0,
        frozenset({"hub", "c"}): 1.0,
        frozenset({"solo", "d"}): 1.0,
    }
    normalized = cluster.hub_normalize(structural)

    total_before = sum(structural.values())
    total_after = sum(normalized.values())
    assert abs(total_before - total_after) < 1e-9
    assert normalized[frozenset({"solo", "d"})] > normalized[frozenset({"hub", "a"})]


def test_fuse_sums_overlapping_and_disjoint_keys():
    a = {frozenset({"x", "y"}): 1.0}
    b = {frozenset({"x", "y"}): 2.0, frozenset({"y", "z"}): 3.0}
    fused = cluster._fuse(a, b)
    assert fused == {frozenset({"x", "y"}): 3.0, frozenset({"y", "z"}): 3.0}


def test_leiden_splits_two_disjoint_dense_pairs_into_two_communities():
    nodes = ["a", "b", "c", "d"]
    weights = {frozenset({"a", "b"}): 5.0, frozenset({"c", "d"}): 5.0}
    parts = cluster._leiden(nodes, weights, gamma=0.1)
    membership = {n: i for i, part in enumerate(parts) for n in part}
    assert membership["a"] == membership["b"]
    assert membership["c"] == membership["d"]
    assert membership["a"] != membership["c"]


def test_dominant_dir_picks_most_common_two_segment_prefix():
    members = ["sgt/core/op.py::Op", "sgt/core/ideal.py::Ideal", "sgt/entities/graph.py::EntityGraph"]
    assert cluster._dominant_dir(members) == "sgt/core"
