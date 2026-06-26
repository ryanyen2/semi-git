"""Decision-frontier verbs: restore (pin a lane to a decision), tag, diff, blast_radius."""

from sgt.api import decision_graph_view
from sgt.effects.model import Effect
from sgt.orchestrate.loop import Orchestrator
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
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()  # base@3 (adds helper)
    proj.save()
    (tmp_path / "m.py").write_text(
        "def base():\n    return 1\ndef helper():\n    return 2\n", encoding="utf-8"
    )
    (tmp_path / "u.py").write_text("def user():\n    return 1\n", encoding="utf-8")
    return proj


def _orch(proj, tmp_path):
    return Orchestrator(proj, repo_path=str(tmp_path), force=True)


def test_restore_pins_a_lane_and_rematerializes(tmp_path):
    proj = _proj(tmp_path)
    # restore to an earlier decision id pins the lane there (compose-feature-versions)
    rep = _orch(proj, tmp_path).restore("base@1")
    assert rep.ok, rep.message
    # working tree now reflects base@1 (no helper); user lane untouched
    reopened = Project.open(tmp_path)
    cb = reopened.materialize()
    assert "def base" in cb["m.py"] and "def helper" not in cb["m.py"]
    assert "u.py" in cb
    # the projection's frontier reflects the pin, and there is no perpetual drift
    assert decision_graph_view(reopened)["frontier"]["base"] == "base@1"
    assert reopened.check_drift().any is False


def test_restore_unknown_ref_errors(tmp_path):
    proj = _proj(tmp_path)
    rep = _orch(proj, tmp_path).restore("no-such-feature")
    assert not rep.ok and "matches" in rep.message


def test_tag_and_diff_report_decision_level_delta(tmp_path):
    proj = _proj(tmp_path)
    orch = _orch(proj, tmp_path)
    orch.tag("v1")                 # v1 = tip {base@3, user@2}
    orch.restore("base@1")          # HEAD now {base@1, user@2}
    d = orch.diff("v1", "HEAD")
    assert d["revised"] == [{"feature": "base", "from": "base@3", "to": "base@1"}]
    assert d["added"] == [] and d["revoked"] == []


def test_diff_unknown_ref_errors(tmp_path):
    proj = _proj(tmp_path)
    assert "error" in _orch(proj, tmp_path).diff("nope", "HEAD")


def test_blast_radius_follows_builds_on(tmp_path):
    # user() calls base() in the same file -> user builds-on base; reverting base disturbs user
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base capability"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    proj.log.stamp_committed()
    proj.add_feature(
        Node(id="user", kind=NodeKind.CAPABILITY, intent="uses base"),
        [Effect.add_def("m.py", "user", "def user():\n    return base()")],
    )
    proj.log.stamp_committed()
    proj.save()
    (tmp_path / "m.py").write_text(
        "def base():\n    return 1\ndef user():\n    return base()\n", encoding="utf-8"
    )
    out = _orch(proj, tmp_path).blast_radius("base@1")
    assert out["lane"] == "base"
    assert "user@2" in out["blast_radius"]
