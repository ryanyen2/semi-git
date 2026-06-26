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


def _planned_proj(tmp_path):
    """A planned-only workspace: 2 PLANNED nodes, b depends_on a, no checkpoints."""
    proj = Project.init(tmp_path)
    proj.add_plan(
        [
            Node(id="a", kind=NodeKind.CAPABILITY, intent="capability a"),
            Node(id="b", kind=NodeKind.CAPABILITY, intent="capability b"),
        ],
        edges=[("b", "a")],  # b depends on a
    )
    proj.save()
    return proj


def test_planned_workspace_renders_a_graph_with_empty_frontier(tmp_path):
    v = decision_graph_view(_planned_proj(tmp_path))
    assert v["count"] == 2
    assert v["frontier"] == {}  # planned-only -> nothing materialized
    assert v["clash"] == []
    assert all(d["status"] == "planned" for d in v["decisions"])


def test_planned_depends_on_surfaces_as_builds_on(tmp_path):
    v = decision_graph_view(_planned_proj(tmp_path))
    bo = [e for e in v["edges"] if e["type"] == "builds-on"]
    # dependent (b) builds-on dependency (a), derived, using bare planned ids
    assert {"src": "b", "dst": "a", "type": "builds-on", "derived": True} in bo


def test_status_field_present_on_landed_and_in_force(tmp_path):
    # base@1, user@2 landed; both are the tip of their lane -> in_force in the default frontier
    v = decision_graph_view(_proj(tmp_path))
    by_id = {d["id"]: d for d in v["decisions"]}
    assert by_id["base@1"]["status"] == "in_force"
    assert by_id["user@2"]["status"] == "in_force"


def test_landed_decision_not_in_force_is_status_landed(tmp_path):
    proj = _proj(tmp_path)
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()  # base lane tip is now base@3
    v = decision_graph_view(proj)
    by_id = {d["id"]: d for d in v["decisions"]}
    assert by_id["base@1"]["status"] == "landed"   # superseded on its lane
    assert by_id["base@3"]["status"] == "in_force"  # the tip
    assert by_id["user@2"]["status"] == "in_force"


def test_planned_revision_folds_into_the_lane_it_enhances(tmp_path):
    """A planned node that *redefines* an existing entity folds into that lane as a revise; one
    that only *needs* a name stays its own lane, linked by the cross-lane builds-on bridge.

    This is the "enhance preprocess" case: without folding the planned node floats as an orphan
    column with an invisible same-lane connector instead of stacking on the lane it enhances.
    """
    proj = _proj(tmp_path)  # base@1 provides base(), user@2 provides user()
    proj.add_plan(
        [
            Node(id="impl-helper", kind=NodeKind.CAPABILITY, intent="add a helper",
                 provides=["helper"]),
            Node(id="rewire-base", kind=NodeKind.CAPABILITY, intent="rewire base onto helper",
                 provides=["base"], needs=["helper"]),
        ],
        edges=[],  # no explicit depends_on — connectivity comes from folding + the name bridge
    )
    proj.save()
    v = decision_graph_view(proj)
    by_id = {d["id"]: d for d in v["decisions"]}

    # rewire-base provides `base` (owned by the landed base lane) -> folds in as a revise of base@1
    assert by_id["rewire-base"]["feature"] == by_id["base@1"]["feature"]
    assert by_id["rewire-base"]["lifecycle"] == {"kind": "revise", "of": "base@1"}
    revises = {(e["src"], e["dst"]) for e in v["edges"] if e["type"] == "revises"}
    assert ("rewire-base", "base@1") in revises

    # impl-helper provides a NEW name -> stays its own lane; rewire-base needs it -> builds-on bridge
    assert by_id["impl-helper"]["feature"] == "impl-helper"
    bo = {(e["src"], e["dst"]) for e in v["edges"] if e["type"] == "builds-on"}
    assert ("rewire-base", "impl-helper") in bo
    # neither planned node is left without an incident edge
    incident = {e["src"] for e in v["edges"]} | {e["dst"] for e in v["edges"]}
    assert {"impl-helper", "rewire-base"} <= incident


def test_planned_landings_float_above_the_landed_cohort(tmp_path):
    """Planned landings offset above the max landed landing, so they read as newest (top row)
    and never collide with landed landing integers (the duplicate `@2` confusion)."""
    proj = _proj(tmp_path)  # landed landings 1, 2
    proj.add_plan([Node(id="future", kind=NodeKind.CAPABILITY, intent="not built yet")], edges=[])
    proj.save()
    by_id = {d["id"]: d for d in decision_graph_view(proj)["decisions"]}
    assert by_id["future"]["landing"] > by_id["user@2"]["landing"]


def test_structure_is_surfaced_per_decision(tmp_path):
    """Each decision carries a deterministic defines/uses/used_by read from the entity graph."""
    proj = _proj(tmp_path)  # m.py: user() calls base()
    by_id = {d["id"]: d for d in decision_graph_view(proj)["decisions"]}
    assert by_id["base@1"]["structure"]["defines"] == ["base"]
    assert by_id["base@1"]["structure"]["used_by"] == ["user"]   # user() calls base()
    assert by_id["user@2"]["structure"]["uses"] == ["base"]


def test_planned_structure_uses_provides_needs(tmp_path):
    """A planned node has no footprint, so its structure falls back to declared provides/needs."""
    proj = _proj(tmp_path)
    proj.add_plan(
        [Node(id="kg", kind=NodeKind.CAPABILITY, intent="graph retrieval",
              provides=["retrieve_from_graph"], needs=["base"])],
        edges=[],
    )
    proj.save()
    by_id = {d["id"]: d for d in decision_graph_view(proj)["decisions"]}
    assert by_id["kg"]["structure"]["defines"] == ["retrieve_from_graph"]
    assert by_id["kg"]["structure"]["uses"] == ["base"]


def test_primary_head_is_the_integrator_not_every_tip(tmp_path):
    """HEAD is the one integrator a human reads as 'current', not the frontier's per-lane tips."""
    proj = _proj(tmp_path)  # user() calls base() -> user@2 builds-on base@1
    v = decision_graph_view(proj)
    assert len(v["frontier"]) == 2          # two in-force tips...
    assert v["head"] == "user@2"            # ...but the integrator (nothing builds on it) is HEAD


def test_frontier_diff_classifies_changes():
    a = {"base": "base@1", "retr": "retr@2", "kg": "kg@3"}
    b = {"base": "base@1", "retr": "retr@5", "embed": "embed@4"}
    d = frontier_diff(a, b)
    assert d["added"] == [{"feature": "embed", "decision": "embed@4"}]
    assert d["revoked"] == [{"feature": "kg", "decision": "kg@3"}]
    assert d["revised"] == [{"feature": "retr", "from": "retr@2", "to": "retr@5"}]
