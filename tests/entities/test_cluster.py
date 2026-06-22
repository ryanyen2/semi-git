"""U7 — capability clustering: grouping, overlap-stable identity, offline determinism."""

from __future__ import annotations

from sgt.entities.cluster import (
    Cluster,
    cluster_features,
    load_cluster_store,
    save_cluster_store,
)


def test_grouping_by_adjacency():
    clusters = cluster_features(["A", "B", "C"], {frozenset(("A", "B"))})
    groups = sorted(sorted(c.members) for c in clusters)
    assert groups == [["A", "B"], ["C"]]


def test_offline_clustering_is_deterministic():
    args = (["A", "B", "C"], {frozenset(("A", "B"))})
    first = [c.to_dict() for c in cluster_features(*args)]
    second = [c.to_dict() for c in cluster_features(*args)]
    assert first == second


def test_new_group_mints_stable_id_and_default_label():
    c = cluster_features(["B", "A"], {frozenset(("A", "B"))})[0]
    # Stable id is a function of sorted membership; label is deterministic offline.
    assert c.cluster_id.startswith("c-") and len(c.cluster_id) == 10
    assert c.label == "capability:A"
    assert cluster_features(["A", "B"], {frozenset(("A", "B"))})[0].cluster_id == c.cluster_id


def test_identity_survives_member_revert():
    # Prior store: cluster c-keep owned {A,B,C}. Now C is reverted -> group {A,B}.
    prior = {"clusters": [{"cluster_id": "c-keep", "label": "RAG", "members": ["A", "B", "C"]}]}
    clusters = cluster_features(["A", "B"], {frozenset(("A", "B"))}, prior)
    assert len(clusters) == 1
    # Jaccard({A,B},{A,B,C}) = 2/3 >= 0.5 -> the cluster keeps its id and label, no flicker.
    assert clusters[0].cluster_id == "c-keep"
    assert clusters[0].label == "RAG"


def test_disjoint_membership_does_not_reuse_identity():
    prior = {"clusters": [{"cluster_id": "c-old", "label": "old", "members": ["X", "Y"]}]}
    clusters = cluster_features(["A", "B"], {frozenset(("A", "B"))}, prior)
    assert clusters[0].cluster_id != "c-old"  # no overlap -> fresh identity


def test_empty_members():
    assert cluster_features([], set()) == []


def test_label_fn_seam_overrides_default():
    clusters = cluster_features(["A", "B"], {frozenset(("A", "B"))}, label_fn=lambda m: "Knowledge Graph")
    assert clusters[0].label == "Knowledge Graph"


def test_store_roundtrip(tmp_path):
    clusters = [Cluster("c-1", "RAG", ["A", "B"])]
    save_cluster_store(tmp_path, clusters)
    loaded = load_cluster_store(tmp_path)
    assert loaded == {"clusters": [{"cluster_id": "c-1", "label": "RAG", "members": ["A", "B"]}]}
    # Missing store loads as empty, never raises.
    assert load_cluster_store(tmp_path / "nope") == {}
