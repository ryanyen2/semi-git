"""Tests for sgt.lens.cluster -- the fused coupling graph (plan U12, R15/R16)."""

from __future__ import annotations

from itertools import product
from math import comb

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


# --- Phase B temporal prior (plan §3.1 / §5 pinned tests) ---------------------------------------

def _set_partitions(items):
    """Every set partition of `items` (Bell(n) of them) -- the brute-force universe the augmented-
    CPM lemma is checked against. n<=8 keeps Bell(n) tractable (Bell(6)=203)."""
    if not items:
        yield []
        return
    first, rest = items[0], items[1:]
    for parts in _set_partitions(rest):
        for i in range(len(parts)):
            yield parts[:i] + [[first] + parts[i]] + parts[i + 1:]
        yield [[first]] + parts


def _cpm_objective(partition, edges, size_of, gamma):
    """CPM objective from first principles (NOT leidenalg's normalized `quality()`): within-community
    edge weight minus gamma times the size-penalty sum(binom(sum node_sizes, 2)). Computed by hand so
    the lemma is a pure arithmetic identity about the graph `_augment_with_prior` builds -- an anchor
    of node_size 0 contributes to the edge term but never the penalty, which is the whole point."""
    comm_of = {n: i for i, c in enumerate(partition) for n in c}
    within = sum(w for e, w in edges.items() if len({comm_of[x] for x in e}) == 1)
    penalty = gamma * sum(comb(sum(size_of[n] for n in c), 2) for c in partition)
    return within - penalty


def test_augment_with_prior_places_one_size_zero_anchor_per_reused_leaf():
    """Structural guard (plan §3.1, Risks row "leidenalg changes node_sizes=0 behavior"): one anchor
    per previous leaf with >= 2 survivors, edge weight ω = alpha x mean positive induced weight to
    each survivor, node_size 0 (no CPM size penalty), a lone survivor gets no anchor, and alpha<=0
    returns the graph unaugmented."""
    members = ["m0", "m1", "m2", "m3", "m4"]
    induced = {frozenset(("m0", "m1")): 2.0, frozenset(("m1", "m2")): 4.0}
    prior = {"m0": "L1", "m1": "L1", "m2": "L1", "m3": "L2", "m4": "L3"}  # L2/L3 lone -> no anchor
    omega = 0.5 * (2.0 + 4.0) / 2  # alpha x mean positive induced weight

    aug_nodes, aug_edges, node_sizes, anchor_ids = cluster._augment_with_prior(members, induced, prior, 0.5)
    size_of = dict(zip(aug_nodes, node_sizes))

    assert anchor_ids == ["__prioranchor__::L1"]  # only L1 has >= 2 survivors; sorted, deterministic
    anchor = anchor_ids[0]
    assert size_of[anchor] == 0  # zero-size => no CPM size penalty (the size-neutrality construction)
    assert all(size_of[m] == 1 for m in members)
    assert {m: aug_edges[frozenset((anchor, m))] for m in ("m0", "m1", "m2")} == {
        "m0": omega, "m1": omega, "m2": omega}
    assert frozenset((anchor, "m3")) not in aug_edges  # anchor only bridges its own leaf's survivors

    # alpha <= 0 => omega 0 => the graph is returned exactly as given, no anchors.
    n2, e2, s2, a2 = cluster._augment_with_prior(members, induced, prior, 0.0)
    assert n2 == members and e2 == induced and s2 == [1] * len(members) and a2 == []


def test_augmented_cpm_optimum_equals_cpm_plus_omega_plurality():
    """The §5 lemma, brute-forced over every partition of a 6-node graph: the augmented-CPM optimum
    (best placement of each zero-size anchor given a fixed real partition P) equals
    CPM(P) + ω x Σ_L max_c |L ∩ c|. The identity holds *at a nonzero resolution* only because anchors
    carry no size penalty -- so this simultaneously pins the plurality lemma and anchor size-neutrality.
    Anchor placement is itself brute-forced (every community or its own singleton), confirming the
    plurality community is the optimum rather than assuming it."""
    members = ["m0", "m1", "m2", "m3", "m4", "m5"]
    induced = {
        frozenset(("m0", "m1")): 3.0, frozenset(("m1", "m2")): 2.0,
        frozenset(("m3", "m4")): 4.0, frozenset(("m4", "m5")): 1.0,
        frozenset(("m0", "m3")): 0.5,
    }
    prior = {"m0": "L1", "m1": "L1", "m2": "L1", "m3": "L2", "m4": "L2", "m5": "L3"}
    alpha, gamma = 0.5, 0.3
    positives = [w for w in induced.values() if w > 0]
    omega = alpha * sum(positives) / len(positives)
    reused = [L for L in set(prior.values()) if sum(v == L for v in prior.values()) >= 2]

    aug_nodes, aug_edges, node_sizes, anchor_ids = cluster._augment_with_prior(members, induced, prior, alpha)
    aug_size_of = dict(zip(aug_nodes, node_sizes))

    checked = 0
    for P in _set_partitions(members):
        cpm_p = _cpm_objective(P, induced, {m: 1 for m in members}, gamma)
        plurality = sum(omega * max(sum(prior.get(m) == L for m in c) for c in P) for L in reused)
        # brute-force every anchor placement: into one of P's communities, or its own singleton.
        best_aug = max(
            _augmented_value(P, anchor_ids, aug_edges, aug_size_of, gamma, placement)
            for placement in product(range(len(P) + 1), repeat=len(anchor_ids))
        )
        assert abs(best_aug - (cpm_p + plurality)) < 1e-9, (P, best_aug, cpm_p, plurality)
        checked += 1
    assert checked == 203  # Bell(6) -- every partition exercised


def _augmented_value(P, anchor_ids, aug_edges, size_of, gamma, placement):
    """CPM objective of the augmented graph for real partition `P` with each anchor placed per
    `placement` (community index in range(len(P)), or len(P) meaning its own singleton)."""
    aug_part = [list(c) for c in P]
    for anchor, choice in zip(anchor_ids, placement):
        if choice == len(P):
            aug_part.append([anchor])
        else:
            aug_part[choice].append(anchor)
    return _cpm_objective(aug_part, aug_edges, size_of, gamma)
