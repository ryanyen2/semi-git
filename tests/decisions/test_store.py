"""Recovering decisions from the log: grouping, footprint, lifecycle, and frontier."""

from sgt.decisions.model import Frontier, LifecycleKind
from sgt.decisions.store import (
    build_decisions,
    load_frontier,
    save_frontier,
    save_meta,
)
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import EdgeType, Node, NodeKind


def _proj(tmp_path):
    """base (checkpoint 1) then user (checkpoint 2) — two lanes, two landings."""
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base capability"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    proj.log.stamp_committed()  # base lands at frame 1
    proj.add_feature(
        Node(id="user", kind=NodeKind.CAPABILITY, intent="uses base"),
        [Effect.add_def("m.py", "user", "def user():\n    return base()")],
    )
    proj.log.stamp_committed()  # user lands at frame 2
    proj.save()
    return proj


def test_one_decision_per_node_per_checkpoint(tmp_path):
    decisions = build_decisions(_proj(tmp_path))
    by_id = {d.id: d for d in decisions}
    assert set(by_id) == {"base@1", "user@2"}
    assert by_id["base@1"].footprint == ["m.py::base"]
    assert by_id["user@2"].intent.decision == "uses base"
    # both are lane-introducing; depends-on (user->base) is NOT a lifecycle edge
    assert by_id["base@1"].lifecycle_kind is LifecycleKind.INTRODUCE
    assert by_id["user@2"].lifecycle_kind is LifecycleKind.INTRODUCE
    assert by_id["user@2"].feature == "user"


def test_accretion_is_a_revise_on_the_same_lane(tmp_path):
    proj = _proj(tmp_path)
    # extend `base` at a later checkpoint -> a second decision on the base lane
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()  # frame 3
    decisions = {d.id: d for d in build_decisions(proj)}
    assert "base@3" in decisions
    rev = decisions["base@3"]
    assert rev.lifecycle_kind is LifecycleKind.REVISE
    assert rev.lifecycle_of == "base@1"
    assert rev.feature == "base"  # same lane


def test_fork_starts_a_new_lane(tmp_path):
    proj = _proj(tmp_path)
    proj.add_feature(
        Node(id="base2", kind=NodeKind.CAPABILITY, intent="alt base"),
        [Effect.add_def("n.py", "base2", "def base2():\n    return 9")],
    )
    proj.graph.add_edge("base2", "base", EdgeType.DERIVES_FROM)  # base2 forks base
    proj.log.stamp_committed()
    decisions = {d.id: d for d in build_decisions(proj)}
    fork = decisions["base2@3"]
    assert fork.lifecycle_kind is LifecycleKind.FORK
    assert fork.lifecycle_of == "base@1"
    assert fork.feature == "base2"  # a fork is its own lane


def test_default_frontier_is_the_tip_per_lane(tmp_path):
    proj = _proj(tmp_path)
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()
    decisions = build_decisions(proj)
    f = load_frontier(proj, decisions)
    assert f.selection == {"base": "base@3", "user": "user@2"}


def test_pinned_frontier_persists_and_stale_pins_fall_back(tmp_path):
    proj = _proj(tmp_path)
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()
    decisions = build_decisions(proj)
    # pin base back to its first decision (compose-feature-versions)
    save_frontier(proj, Frontier({"base": "base@1", "user": "user@2"}))
    assert load_frontier(proj, decisions).selection["base"] == "base@1"
    # a pin to a decision that doesn't exist falls back to the lane tip
    save_frontier(proj, Frontier({"base": "base@999"}))
    assert load_frontier(proj, decisions).selection["base"] == "base@3"


def test_fix_node_sharing_an_entity_folds_into_the_same_lane(tmp_path):
    # The distiller can split a def-rewrite into a separate fix node that re-owns the same entity.
    # Footprint-grounded lane assignment (R13) must fold it into the original lane as a revise,
    # not spawn a phantom lane + a duplicate-owner clash.
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="embedding", kind=NodeKind.CAPABILITY, intent="embedding"),
        [Effect.add_def("e.py", "embed", "def embed(t):\n    return [t]")],
    )
    proj.log.stamp_committed()  # embedding@1 owns e.py::embed
    # a separate node re-owns the SAME entity (what a fix-node split looks like)
    proj.add_feature(
        Node(id="fix0001", kind=NodeKind.FIX, intent="rewrite embed"),
        [Effect.add_def("e.py", "embed", "def embed(t):\n    return [t, t]")],
    )
    proj.log.stamp_committed()  # fix0001@2 also owns e.py::embed
    decisions = {d.id: d for d in build_decisions(proj)}
    # both decisions live on the embedding lane (earliest node id), the later one a revise
    assert decisions["embedding@1"].feature == "embedding"
    assert decisions["fix0001@2"].feature == "embedding"
    assert decisions["fix0001@2"].lifecycle_kind is LifecycleKind.REVISE


def test_import_only_targets_do_not_create_a_lane(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    proj.log.stamp_committed()
    # an import-only node (what the distiller emits for `from x import y`) shares no entity, so it
    # stays its own (degenerate) lane but never steals/merges a real feature's entity ownership.
    from sgt.decisions.store import _is_entity_key
    assert _is_entity_key("m.py::base") is True
    assert _is_entity_key("m.py::from x import y") is False
    assert _is_entity_key("m.py::import os") is False


def test_authored_metadata_merges_from_sidecar(tmp_path):
    proj = _proj(tmp_path)
    save_meta(
        proj.sgt_dir,
        {"base@1": {"context": "needed a base", "consequence": "base() exists",
                    "alternatives": [{"option": "inline it", "why_rejected": "duplication"}]}},
    )
    base = {d.id: d for d in build_decisions(proj)}["base@1"]
    assert base.intent.context == "needed a base"
    assert base.intent.decision == "base capability"  # node intent retained as the decision
    assert base.alternatives[0].option == "inline it"
