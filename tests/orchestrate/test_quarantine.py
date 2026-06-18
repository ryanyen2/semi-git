"""U7 tests: bounded, non-blocking rewrite-to-commute."""

from sgt.adapter.base import AgentResult, AgentStatus
from sgt.effects.model import Effect
from sgt.orchestrate.constraint import ConstraintGraph, SubTask
from sgt.orchestrate.loop import Orchestrator
from sgt.orchestrate.quarantine import attempt_rewrite_to_commute
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


class StubAgent:
    name = "stub"

    def __init__(self, by_intent):
        self.by_intent = by_intent
        self.calls = []

    def execute_task(self, intent, codebase, allowed_files=None):
        self.calls.append(intent)
        effects = self.by_intent.get(intent)
        if effects is None:
            return AgentResult(status=AgentStatus.FAILED, error="no script")
        return AgentResult(status=AgentStatus.OK, summary=intent, effects=list(effects))


def test_rewrite_succeeds_when_state_now_supports_the_effects(tmp_path):
    # `extra` calls `base`; once base is landed, re-gating the same effects commutes.
    proj = Project.init(tmp_path)
    proj.add_feature(Node(id="base", kind=NodeKind.CAPABILITY, intent="base"),
                     [Effect.add_def("m.py", "base", "def base():\n    return 1")])
    agent = StubAgent({"use base": [Effect.add_def("m.py", "extra", "def extra():\n    return base()")]})
    task = SubTask("c", "use base")
    ok, effects, reason = attempt_rewrite_to_commute(proj, agent, task, max_attempts=2)
    assert ok and effects and reason == ""


def test_rewrite_fails_when_always_conflicting(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(Node(id="f", kind=NodeKind.CAPABILITY, intent="f"),
                     [Effect.add_def("m.py", "f", "def f():\n    return 1")])
    # re-defining f always fails its precondition, no matter the attempt
    agent = StubAgent({"redef f": [Effect.add_def("m.py", "f", "def f():\n    return 2")]})
    ok, effects, reason = attempt_rewrite_to_commute(proj, agent, SubTask("x", "redef f"), max_attempts=3)
    assert ok is False and effects is None and reason


def test_zero_attempts_never_dispatches(tmp_path):
    proj = Project.init(tmp_path)
    agent = StubAgent({"x": [Effect.add_def("m.py", "x", "def x():\n    return 1")]})
    ok, _, _ = attempt_rewrite_to_commute(proj, agent, SubTask("x", "x"), max_attempts=0)
    assert ok is False and agent.calls == []


def test_failed_attempt_does_not_crash(tmp_path):
    proj = Project.init(tmp_path)
    agent = StubAgent({})  # every dispatch -> FAILED
    ok, _, _ = attempt_rewrite_to_commute(proj, agent, SubTask("x", "x"), max_attempts=2)
    assert ok is False
    assert agent.calls == ["x", "x"]  # tried twice, no exception


def _orch(tmp_path, agent, graph, attempts=2):
    proj = Project.init(tmp_path)
    return Orchestrator(proj, agent, repo_path=str(tmp_path),
                        decomposer=lambda *a, **k: graph, rewrite_attempts=attempts), proj


def test_held_task_is_rewritten_and_lands_in_fanout(tmp_path):
    # `consumer` sorts before `producer`, so it is gated first against an empty tree
    # and held; after producer lands, rewrite-to-commute re-gates it successfully.
    g = ConstraintGraph()
    g.add(SubTask("consumer", "build consumer", provides=["consumer"]))
    g.add(SubTask("producer", "build producer", provides=["producer_fn"]))
    agent = StubAgent({
        "build consumer": [Effect.add_def("m.py", "consumer", "def consumer():\n    return producer_fn()")],
        "build producer": [Effect.add_def("m.py", "producer_fn", "def producer_fn():\n    return 1")],
    })
    orch, proj = _orch(tmp_path, agent, g)
    rep = orch._fanout_or_add("build both", "capability", "both")
    assert rep.ok and rep.quarantined == []  # consumer was reconciled, not quarantined
    cb = proj.materialize()
    assert "def consumer" in cb["m.py"] and "def producer_fn" in cb["m.py"]
    assert proj.valid()


def test_unreconcilable_task_stays_pending_non_blocking(tmp_path):
    g = ConstraintGraph()
    g.add(SubTask("a_first", "def f v1", provides=["f"]))
    g.add(SubTask("b_second", "def f v2", provides=["f"]))
    agent = StubAgent({
        "def f v1": [Effect.add_def("m.py", "f", "def f():\n    return 1")],
        "def f v2": [Effect.add_def("m.py", "f", "def f():\n    return 2")],
    })
    orch, proj = _orch(tmp_path, agent, g)
    rep = orch._fanout_or_add("define f twice", "capability", "f")
    assert rep.ok  # run completes despite the conflict (non-blocking)
    assert len(rep.landed) == 1 and len(rep.quarantined) == 1
    qid = rep.quarantined[0]
    assert proj.graph.get(qid).status is NodeStatus.QUARANTINED
    assert proj.witnesses[qid]["reason"]
    assert proj.valid()
