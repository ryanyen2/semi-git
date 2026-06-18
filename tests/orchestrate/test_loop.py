"""U6 tests: the fan-out orchestration loop (stub agent + stub decomposer)."""

from sgt.adapter.base import AgentResult, AgentStatus
from sgt.agents.planner import PlannerError
from sgt.effects.model import Effect
from sgt.orchestrate.constraint import ConstraintGraph, SubTask
from sgt.orchestrate.loop import Orchestrator
from sgt.project import Project
from sgt.store.graph import NodeStatus


class StubAgent:
    """Returns pre-scripted effects keyed by task intent."""
    name = "stub"

    def __init__(self, by_intent: dict[str, list]):
        self.by_intent = by_intent

    def execute_task(self, intent, codebase, allowed_files=None):
        effects = self.by_intent.get(intent)
        if effects is None:
            return AgentResult(status=AgentStatus.FAILED, error=f"no script for {intent!r}")
        return AgentResult(status=AgentStatus.OK, summary=intent, effects=list(effects))


def _orch(tmp_path, agent, graph, confirm=None):
    proj = Project.init(tmp_path)
    return Orchestrator(proj, agent, repo_path=str(tmp_path),
                        confirm=confirm, decomposer=lambda *a, **k: graph), proj


def test_two_independent_tasks_both_land(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("a", "make a", provides=["a"]))
    g.add(SubTask("b", "make b", provides=["b"]))
    agent = StubAgent({
        "make a": [Effect.add_def("m.py", "a", "def a():\n    return 1")],
        "make b": [Effect.add_def("m.py", "b", "def b():\n    return 2")],
    })
    orch, proj = _orch(tmp_path, agent, g)
    rep = orch._fanout_or_add("build a and b", "capability", "ab")
    assert rep.ok and len(rep.landed) == 2
    cb = proj.materialize()
    assert "def a" in cb["m.py"] and "def b" in cb["m.py"]
    assert proj.valid()


def test_dependent_task_sees_provider_and_gets_dep_edge(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("provider", "make foo", provides=["foo"]))
    g.add(SubTask("consumer", "make bar using foo", needs=["foo"]))
    agent = StubAgent({
        "make foo": [Effect.add_def("m.py", "foo", "def foo():\n    return 1")],
        "make bar using foo": [Effect.add_def("m.py", "bar", "def bar():\n    return foo()")],
    })
    orch, proj = _orch(tmp_path, agent, g)
    rep = orch._fanout_or_add("foo and bar", "capability", "fb")
    assert rep.ok and len(rep.landed) == 2
    # bar landed in the second layer, saw foo, and an inferred dep edge exists
    foo_node, bar_node = rep.landed[0], rep.landed[1]
    assert foo_node in proj.graph.successors(bar_node)
    assert proj.valid()


def test_atomic_plan_runs_inline_without_checkpoint(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("only", "atomic", provides=["x"]))
    confirm_calls = []
    # _add path dispatches the raw prompt, so script that intent too
    agent = StubAgent({"build x": [Effect.add_def("m.py", "x", "def x():\n    return 1")]})
    orch, proj = _orch(tmp_path, agent, g, confirm=lambda gr: confirm_calls.append(gr) or True)
    rep = orch._fanout_or_add("build x", "capability", "x")
    assert rep.ok
    assert confirm_calls == []  # ≤1 sub-task never hits the checkpoint (R29)


def test_rejected_checkpoint_aborts_with_no_nodes(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("a", "make a", provides=["a"]))
    g.add(SubTask("b", "make b", provides=["b"]))
    agent = StubAgent({"make a": [], "make b": []})
    orch, proj = _orch(tmp_path, agent, g, confirm=lambda gr: False)
    rep = orch._fanout_or_add("build", "capability", "ab")
    assert rep.ok is False
    assert proj.graph.nodes() == []


def test_conflicting_task_is_quarantined_with_witness(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("a", "define f one way", provides=["f"]))
    g.add(SubTask("b", "define f another way", provides=["f"]))
    agent = StubAgent({
        "define f one way": [Effect.add_def("m.py", "f", "def f():\n    return 1")],
        "define f another way": [Effect.add_def("m.py", "f", "def f():\n    return 2")],
    })
    orch, proj = _orch(tmp_path, agent, g)
    rep = orch._fanout_or_add("build f twice", "capability", "f")
    assert len(rep.landed) == 1 and len(rep.quarantined) == 1
    qid = rep.quarantined[0]
    assert proj.graph.get(qid).status is NodeStatus.QUARANTINED
    assert qid in proj.witnesses
    # quarantined effects are not materialized
    assert proj.materialize()["m.py"].count("def f") == 1
    assert proj.valid()


def test_all_failed_run_reports_not_ok_without_crashing(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("a", "boom one", provides=["a"]))
    g.add(SubTask("b", "boom two", provides=["b"]))
    agent = StubAgent({})  # nothing scripted -> both FAILED
    orch, proj = _orch(tmp_path, agent, g)
    rep = orch._fanout_or_add("build", "capability", "ab")
    assert rep.ok is False          # total backend outage is not success
    assert len(rep.failures) == 2   # surfaced structurally, not just in the message
    assert rep.landed == [] and rep.quarantined == []
    assert proj.graph.nodes() == []  # no empty commit, no phantom nodes


def test_empty_effects_result_lands_no_node(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("real", "make real", provides=["real"]))
    g.add(SubTask("noop", "do nothing", provides=[]))
    agent = StubAgent({
        "make real": [Effect.add_def("m.py", "real", "def real():\n    return 1")],
        "do nothing": [],  # OK status but no effects
    })
    orch, proj = _orch(tmp_path, agent, g)
    rep = orch._fanout_or_add("build", "capability", "x")
    assert len(rep.landed) == 1  # only the real task became a node
    assert proj.valid()


def test_planner_failure_degrades_to_single_agent(tmp_path):
    def boom(*a, **k):
        raise PlannerError("decomposition is not a DAG")
    proj = Project.init(tmp_path)
    agent = StubAgent({"build x": [Effect.add_def("m.py", "x", "def x():\n    return 1")]})
    orch = Orchestrator(proj, agent, repo_path=str(tmp_path), decomposer=boom)
    rep = orch._fanout_or_add("build x", "capability", "x")
    assert rep.ok  # fell back to the single-agent path
    assert "def x" in proj.materialize()["m.py"]


def test_failed_backend_task_leaves_rest_intact(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("a", "make a", provides=["a"]))
    g.add(SubTask("b", "boom", provides=["b"]))  # no script -> FAILED
    agent = StubAgent({"make a": [Effect.add_def("m.py", "a", "def a():\n    return 1")]})
    orch, proj = _orch(tmp_path, agent, g)
    rep = orch._fanout_or_add("build", "capability", "ab")
    assert len(rep.landed) == 1
    assert "def a" in proj.materialize()["m.py"]
    assert proj.valid()
