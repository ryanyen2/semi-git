"""Tests for experiments.patch_clustering.cohesion_harness -- Phase 5's cohesion/stability gate
(feature-timeline redesign plan, 2026-07-21). Pure-math helpers get small synthetic fixtures (same
idiom as tests/lens/test_tree.py's test_feature_edges_* fixtures); `greene_stability`/`run` get one
end-to-end pass against a real mined repo, since they call `tree.build` for real."""

from __future__ import annotations

from experiments.patch_clustering import cohesion_harness as harness
from sgt.core.lens import get
from sgt.core.store import Store
from sgt.lens import tree
from tests.laws import corpus


def _op(footprint_syms: list[str], sha: str):
    from sgt.core.op import make_op

    footprint = {sym: (None, sha) for sym in footprint_syms}
    images = {sym: sha.encode() for sym in footprint_syms}
    return make_op(footprint, images, provenance=(sha,))


def test_cohesion_scores_one_for_a_feature_whose_episodes_never_leave_it():
    nodes = {"F1": {"children": [], "members": ["a.py::foo", "a.py::bar"]}}
    ops = [_op(["a.py::foo", "a.py::bar"], "c1")]
    node_set, hubs = {"a.py::foo", "a.py::bar"}, set()

    scores = harness.cohesion(nodes, ops, node_set, hubs)
    assert scores == {"F1": 1.0}


def test_cohesion_scores_near_zero_for_a_feature_glued_only_by_cross_episodes():
    nodes = {
        "F1": {"children": [], "members": ["a.py::foo"]},
        "F2": {"children": [], "members": ["b.py::baz"]},
    }
    ops = [_op(["a.py::foo", "b.py::baz"], "c1")]
    node_set, hubs = {"a.py::foo", "b.py::baz"}, set()

    scores = harness.cohesion(nodes, ops, node_set, hubs)
    assert scores == {"F1": 0.0, "F2": 0.0}


def test_cohesion_omits_leaves_with_no_scored_commit_weight():
    nodes = {"F1": {"children": [], "members": ["a.py::lonely"]}}
    scores = harness.cohesion(nodes, ops=[], node_set={"a.py::lonely"}, hubs=set())
    assert scores == {}


def test_cross_feature_mass_zero_when_every_edge_stays_within_one_leaf():
    nodes = {"F1": {"children": [], "members": ["a.py::foo", "a.py::bar"]}}
    fused = {frozenset(("a.py::foo", "a.py::bar")): 5.0}
    assert harness.cross_feature_mass(nodes, fused) == 0.0


def test_cross_feature_mass_one_when_every_edge_crosses():
    nodes = {
        "F1": {"children": [], "members": ["a.py::foo"]},
        "F2": {"children": [], "members": ["b.py::baz"]},
    }
    fused = {frozenset(("a.py::foo", "b.py::baz")): 3.0}
    assert harness.cross_feature_mass(nodes, fused) == 1.0


def test_cross_feature_mass_none_on_an_empty_fused_graph():
    assert harness.cross_feature_mass({"F1": {"children": [], "members": []}}, {}) is None


def test_continuation_rate_one_when_every_old_leaf_survives():
    previous_nodes = {"F1": {"children": [], "members": ["a"]}, "F2": {"children": [], "members": ["b"]}}
    events = [{"event": "continuation", "feature_id": "F1"}, {"event": "continuation", "feature_id": "F2"}]
    result = harness.continuation_rate(previous_nodes, events)
    assert result == {
        "old_leaf_count": 2, "continuation_rate": 1.0,
        "events_by_type": {"continuation": 2, "merge": 0, "split": 0, "birth": 0, "death": 0},
    }


def test_continuation_rate_drops_when_an_old_leaf_dies():
    previous_nodes = {"F1": {"children": [], "members": ["a"]}, "F2": {"children": [], "members": ["b"]}}
    events = [{"event": "continuation", "feature_id": "F1"}, {"event": "death", "feature_id": "F2"}]
    result = harness.continuation_rate(previous_nodes, events)
    assert result["continuation_rate"] == 0.5


def test_continuation_rate_none_with_no_previous_leaves():
    assert harness.continuation_rate({}, [])["continuation_rate"] is None


def test_greene_stability_reports_none_with_no_persisted_tree(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()
    result = harness.greene_stability(repo, ops, ideal, previous=None)
    assert result == {"old_leaf_count": 0, "continuation_rate": None, "events_by_type": {}}


def test_greene_stability_full_continuation_on_an_unchanged_repo(tmp_path):
    """Rebuilding from scratch against a repo whose history hasn't moved must be a pure
    continuation for every leaf -- the harness's own sanity check on its rebuild path."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal, ops = get(repo), Store(repo).all_ops()
    first = tree.build(repo, ops, ideal)
    tree.save(repo, first)

    result = harness.greene_stability(repo, ops, ideal, previous=tree.load(repo))
    assert result["continuation_rate"] == 1.0
    assert result["events_by_type"]["death"] == 0


def test_run_end_to_end_against_a_real_repo_reports_all_sections(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    from sgt.lens.map import build_map

    get(repo)  # mine-on-contact -- build_map assumes the op store is already current
    build_map(repo)  # persists a real tree so run() has a `previous` to score

    report = harness.run(repo)
    assert report["n_ops"] > 0
    assert report["cohesion"]["n_leaves_scored"] >= 0
    assert 0.0 <= report["cross_feature_edge_mass"] <= 1.0
    assert report["greene_stability"]["continuation_rate"] == 1.0  # no history moved since build_map
