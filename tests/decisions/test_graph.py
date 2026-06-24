"""decision_graph_view: lifecycle edges stored, builds-on/clash derived from the entity graph."""

from sgt.api import decision_graph_view, frontier_diff, frontier_view
from sgt.decisions.store import save_frontier
from sgt.decisions.model import Frontier
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _proj(tmp_path):
    """Two lanes on disk: user() calls base(), so the entity graph has user -> base."""
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
    # entity_graph_view parses the working tree, so the file must be on disk
    (tmp_path / "m.py").write_text(
        "def base():\n    return 1\ndef user():\n    return base()\n", encoding="utf-8"
    )
    proj.save()
    return proj


def test_builds_on_is_derived_from_the_entity_graph(tmp_path):
    v = decision_graph_view(_proj(tmp_path))
    assert v["count"] == 2
    assert v["frontier"] == {"base": "base@1", "user": "user@2"}
    # user() calls base() -> a DERIVED builds-on edge, authored by nobody
    bo = [e for e in v["edges"] if e["type"] == "builds-on"]
    assert {"src": "user@2", "dst": "base@1", "type": "builds-on", "derived": True} in bo
    assert v["clash"] == []


def test_lifecycle_edge_is_stored_for_a_revise(tmp_path):
    proj = _proj(tmp_path)
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()
    v = decision_graph_view(proj)
    revises = [e for e in v["edges"] if e["type"] == "revises"]
    assert {"src": "base@3", "dst": "base@1", "type": "revises"} in revises


def test_frontier_view_lists_lanes_and_selection(tmp_path):
    v = frontier_view(_proj(tmp_path))
    assert v["selection"] == {"base": "base@1", "user": "user@2"}
    assert set(v["lanes"]) == {"base", "user"}


def test_pinning_an_older_decision_repoints_the_derived_edge(tmp_path):
    # compose-feature-versions: pin base back to its first decision while user stays at tip.
    proj = _proj(tmp_path)
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()  # base lane tip is now base@3

    # default (tip): user builds-on the latest base decision
    bo_default = [e for e in decision_graph_view(proj)["edges"] if e["type"] == "builds-on"]
    assert {"src": "user@2", "dst": "base@3", "type": "builds-on", "derived": True} in bo_default

    # pin base@1: base still cumulatively owns base(), so the edge re-points to base@1
    save_frontier(proj, Frontier({"base": "base@1", "user": "user@2"}))
    bo_pinned = [e for e in decision_graph_view(proj)["edges"] if e["type"] == "builds-on"]
    assert {"src": "user@2", "dst": "base@1", "type": "builds-on", "derived": True} in bo_pinned


def test_frontier_diff_classifies_changes():
    a = {"base": "base@1", "retr": "retr@2", "kg": "kg@3"}
    b = {"base": "base@1", "retr": "retr@5", "embed": "embed@4"}
    d = frontier_diff(a, b)
    assert d["added"] == [{"feature": "embed", "decision": "embed@4"}]
    assert d["revoked"] == [{"feature": "kg", "decision": "kg@3"}]
    assert d["revised"] == [{"feature": "retr", "from": "retr@2", "to": "retr@5"}]
