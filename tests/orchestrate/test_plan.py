"""Phase A: persist a decomposition as reviewable PLANNED nodes (no code authored)."""

from sgt.orchestrate.constraint import ConstraintGraph, SubTask
from sgt.orchestrate.loop import Orchestrator
from sgt.orchestrate.sync import run_sync
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


def _orch(tmp_path, graph):
    proj = Project.init(tmp_path)
    orch = Orchestrator(proj, repo_path=str(tmp_path), decomposer=lambda *a, **k: graph)
    return orch, proj


# -- grounding: existing capabilities are surfaced for the normalizer to anchor against ----
def test_graph_grounding_lists_landed_capabilities_by_their_def_names(tmp_path):
    # A landed capability must appear in grounding so freeform normalize can EXTEND/USING a real
    # name instead of inventing an abstract token (the orphaned-plan root cause).
    proj = Project.init(tmp_path)
    proj.add_plan([Node(id="n1", kind=NodeKind.CAPABILITY,
                        intent="add generate that calls the LLM", provides=["generate"])], edges=[])
    (tmp_path / "rag.py").write_text("def generate(ctx):\n    return ctx\n", encoding="utf-8")
    run_sync(proj, repo_path=str(tmp_path), confirm=lambda c: True, fulfills="n1")

    orch = Orchestrator(proj, repo_path=str(tmp_path))
    lines = orch._graph_grounding()
    assert any("generate" in ln for ln in lines), lines
    assert all(isinstance(ln, str) for ln in lines)


def test_graph_grounding_is_empty_on_a_fresh_project(tmp_path):
    orch = Orchestrator(Project.init(tmp_path), repo_path=str(tmp_path))
    assert orch._graph_grounding() == []


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
    assert rep.ok
    assert "def a" not in proj.materialize().get("m.py", "")  # active feature out of force
    assert proj.graph.has("a")                                 # lossless: retained, restorable
    assert proj.graph.has("p")                                 # draft preserved (not cascaded)
    assert proj.graph.get("p").status is NodeStatus.PLANNED


# -- intent DSL on plan ------------------------------------------------------
def _no_planner(*a, **k):
    raise AssertionError("the LLM planner must not run for canonical DSL")


def test_canonical_dsl_plans_one_node_without_the_planner(tmp_path):
    # A canonical statement parses deterministically and offline — the decomposer is never called.
    proj = Project.init(tmp_path)
    orch = Orchestrator(proj, repo_path=str(tmp_path), decomposer=_no_planner)
    rep = orch.plan("ADD validate_email, normalize_email USING re")
    assert rep.ok and len(rep.landed) == 1 and "ADD validate_email" in rep.message
    n = proj.graph.get(rep.landed[0])
    assert n.status is NodeStatus.PLANNED
    assert n.provides == ["validate_email", "normalize_email"] and n.needs == ["re"]


def test_replace_dsl_captures_high_confidence_alternative(tmp_path):
    from sgt.decisions.store import build_decisions

    proj = Project.init(tmp_path)
    orch = Orchestrator(proj, repo_path=str(tmp_path), decomposer=_no_planner)
    rep = orch.plan("REPLACE bubble_sort WITH quicksort BECAUSE O(n^2) too slow")
    assert rep.ok
    dec = {d.node_id: d for d in build_decisions(proj)}[rep.landed[0]]
    assert len(dec.alternatives) == 1
    a = dec.alternatives[0]
    assert a.option == "bubble_sort" and a.source == "user" and a.confidence == "high"


def test_extend_dsl_folds_into_existing_lane_as_revise(tmp_path):
    from sgt.effects.model import Effect
    from sgt.decisions.store import build_decisions

    proj = Project.init(tmp_path)
    proj.add_feature(Node("auth", NodeKind.CAPABILITY, "auth", provides=["login"]),
                     [Effect.add_def("auth.py", "login", "def login():\n    return 1")])
    proj.commit("feat auth")
    orch = Orchestrator(proj, repo_path=str(tmp_path), decomposer=_no_planner)
    rep = orch.plan("EXTEND login TO add logout")   # 'login' resolves to the auth lane
    assert rep.ok
    planned = next(d for d in build_decisions(proj) if d.node_id == rep.landed[0])
    assert planned.feature == "auth" and planned.lifecycle_kind.value == "revise"


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
