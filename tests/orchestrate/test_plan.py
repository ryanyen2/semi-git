"""Phase A: persist a decomposition as reviewable PLANNED nodes (no code authored)."""

from sgt.orchestrate.constraint import ConstraintGraph, SubTask
from sgt.orchestrate.loop import Orchestrator
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


def _orch(tmp_path, graph):
    proj = Project.init(tmp_path)
    orch = Orchestrator(proj, repo_path=str(tmp_path), decomposer=lambda *a, **k: graph)
    return orch, proj


# -- node model --------------------------------------------------------------
def test_planned_node_roundtrips_with_provides_needs():
    n = Node(id="n1", kind=NodeKind.CAPABILITY, intent="do x",
             status=NodeStatus.PLANNED, provides=["foo"], needs=["bar"])
    back = Node.from_dict(n.to_dict())
    assert back.status is NodeStatus.PLANNED
    assert back.provides == ["foo"] and back.needs == ["bar"]
    assert back.effect_bundle_id is None


# -- persistence -------------------------------------------------------------
def test_add_plan_persists_and_materialize_ignores(tmp_path):
    proj = Project.init(tmp_path)
    a = Node(id="a", kind=NodeKind.CAPABILITY, intent="make a", provides=["a"])
    b = Node(id="b", kind=NodeKind.CAPABILITY, intent="make b using a", needs=["a"])
    proj.add_plan([a, b], edges=[("b", "a")])  # b depends on a
    proj.save()

    reopened = Project.open(tmp_path)
    assert {n.id for n in reopened.graph.nodes()} == {"a", "b"}
    assert all(n.status is NodeStatus.PLANNED for n in reopened.graph.nodes())
    assert reopened.graph.successors("b") == ["a"]      # declared edge survived
    assert reopened.graph.get("a").provides == ["a"]
    assert reopened.materialize() == {}                  # planned nodes are inert
    assert reopened.valid()


# -- orchestrator.plan -------------------------------------------------------
def test_plan_creates_planned_nodes_with_edges(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("validate", "validate the email", provides=["validate"]))
    g.add(SubTask("normalize", "normalize the email", provides=["normalize"]))
    g.add(SubTask("register", "register using validate+normalize",
                  needs=["validate", "normalize"]))
    orch, proj = _orch(tmp_path, g)

    rep = orch.plan("validate, normalize, then register an email")
    assert rep.ok and rep.action == "plan" and len(rep.landed) == 3

    nodes = proj.graph.nodes()
    assert len(nodes) == 3 and all(n.status is NodeStatus.PLANNED for n in nodes)
    # register depends on both providers (inferred from needs<->provides)
    reg = next(n for n in nodes if "register" in n.intent)
    assert len(proj.graph.successors(reg.id)) == 2
    assert proj.materialize() == {}  # still no code authored
    assert proj.valid()


def test_plan_persists_planner_enrichment_to_decisions(tmp_path):
    # The planner now returns slug/context/consequence per sub-task; plan() must persist them so a
    # freshly-planned decision is rich (not decision-only) on every surface, with no extra LLM call.
    from sgt.decisions.store import build_decisions

    g = ConstraintGraph()
    g.add(SubTask("retr", "implement keyword retrieval", provides=["retrieve"],
                  slug="Keyword retrieval", context="No retrieval path exists yet.",
                  consequence="Callers can fetch ranked docs."))
    orch, proj = _orch(tmp_path, g)
    rep = orch.plan("add retrieval")
    assert rep.ok

    dec = {d.node_id: d for d in build_decisions(proj)}[rep.landed[0]]
    assert dec.intent.slug == "Keyword retrieval"
    assert dec.intent.context == "No retrieval path exists yet."
    assert dec.intent.consequence == "Callers can fetch ranked docs."
    # the long coding request stays the decision text
    assert dec.intent.decision == "implement keyword retrieval"


def test_plan_atomic_intent_persists_one_node(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("only", "add a 6-char url shortener", provides=["shorten"]))
    orch, proj = _orch(tmp_path, g)
    rep = orch.plan("add a url shortener")
    assert rep.ok and len(rep.landed) == 1
    assert proj.graph.get(rep.landed[0]).status is NodeStatus.PLANNED


def test_add_plan_reports_dropped_cyclic_edge(tmp_path):
    # A cyclic declared edge is dropped (would wedge layering), but surfaced — not silently lost.
    proj = Project.init(tmp_path)
    a = Node(id="a", kind=NodeKind.CAPABILITY, intent="a")
    b = Node(id="b", kind=NodeKind.CAPABILITY, intent="b")
    dropped = proj.add_plan([a, b], edges=[("a", "b"), ("b", "a")])
    assert dropped == [("b", "a")]
    assert proj.graph.successors("a") == ["b"]   # the non-cyclic edge survived


def test_revert_active_preserves_dependent_planned_draft(tmp_path):
    # Reverting realized code must not delete a PLANNED draft that declared a dependency on it.
    from sgt.effects.model import Effect

    proj = Project.init(tmp_path)
    proj.add_feature(Node("a", NodeKind.CAPABILITY, "a"),
                     [Effect.add_def("m.py", "a", "def a():\n    return 1")])
    proj.add_plan([Node(id="p", kind=NodeKind.CAPABILITY, intent="plan using a")], edges=[("p", "a")])
    proj.commit("feat a + plan p")

    rep = Orchestrator(proj, repo_path=str(tmp_path)).revert("a")
    assert rep.ok and not proj.graph.has("a")              # active feature gone
    assert proj.graph.has("p")                              # draft preserved
    assert proj.graph.get("p").status is NodeStatus.PLANNED


def test_revert_planned_node_removes_just_it(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("a", "make a", provides=["a"]))
    g.add(SubTask("b", "make b", provides=["b"]))  # independent of a
    orch, proj = _orch(tmp_path, g)
    orch.plan("two independent things")
    ids = [n.id for n in proj.graph.nodes()]

    rep = orch.revert(ids[0])
    assert rep.ok
    remaining = [n.id for n in proj.graph.nodes()]
    assert ids[0] not in remaining and ids[1] in remaining
