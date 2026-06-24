"""U4 — materialize_frontier: compose an arbitrary frontier; tip-frontier == live materialize."""

from sgt.decisions.model import Frontier
from sgt.decisions.store import build_decisions
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _proj(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base capability"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    proj.log.stamp_committed()  # base@1
    proj.add_feature(
        Node(id="user", kind=NodeKind.CAPABILITY, intent="uses base"),
        [Effect.add_def("u.py", "user", "def user():\n    return 1")],
    )
    proj.log.stamp_committed()  # user@2
    proj.save()
    return proj


def test_tip_frontier_equals_live_materialize(tmp_path):
    proj = _proj(tmp_path)
    decisions = build_decisions(proj)
    tip = Frontier.tip_of(decisions).selection
    assert proj.materialize_frontier(tip) == proj.materialize()


def test_pinning_an_older_decision_drops_later_effects(tmp_path):
    proj = _proj(tmp_path)
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()  # base@3 adds helper()

    # tip: helper() is present
    tip = Frontier.tip_of(build_decisions(proj)).selection
    assert "def helper" in proj.materialize_frontier(tip)["m.py"]

    # pin base back to base@1: helper() drops, base() stays, user lane untouched
    pinned = proj.materialize_frontier({"base": "base@1", "user": "user@2"})
    assert "def base" in pinned["m.py"]
    assert "def helper" not in pinned["m.py"]
    assert "u.py" in pinned


def test_lane_absent_from_manifest_is_out_of_force(tmp_path):
    proj = _proj(tmp_path)
    # only base in the manifest -> user's file is not composed
    composed = proj.materialize_frontier({"base": "base@1"})
    assert "m.py" in composed
    assert "u.py" not in composed
