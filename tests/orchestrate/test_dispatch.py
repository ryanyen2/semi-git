"""U4 tests: concurrent layer dispatch with stub adapters."""

import threading
import time

from sgt.adapter.base import AgentResult, AgentStatus
from sgt.effects.model import Effect
from sgt.orchestrate.constraint import SubTask
from sgt.orchestrate.dispatch import dispatch_layer


class StubAdapter:
    name = "stub"

    def __init__(self):
        self.seen: list[str] = []
        self._lock = threading.Lock()

    def execute_task(self, intent, codebase, allowed_files=None):
        with self._lock:
            self.seen.append(intent)
        return AgentResult(status=AgentStatus.OK, summary=intent,
                           effects=[Effect.add_def("a.py", intent, f"def {intent}():\n    return 1")])


def test_layer_returns_results_in_task_order():
    adapter = StubAdapter()
    tasks = [SubTask(k, k) for k in ("a", "b", "c")]
    out = dispatch_layer(adapter, tasks, {})
    assert [t.key for t, _ in out] == ["a", "b", "c"]
    assert all(r.status is AgentStatus.OK for _, r in out)


def test_worker_exception_becomes_failed_result():
    class Boom:
        name = "boom"

        def execute_task(self, intent, codebase, allowed_files=None):
            if intent == "bad":
                raise RuntimeError("kaboom")
            return AgentResult(status=AgentStatus.OK, summary=intent)

    tasks = [SubTask("good", "good"), SubTask("bad", "bad"), SubTask("good2", "good2")]
    out = dispatch_layer(Boom(), tasks, {})
    by_key = {t.key: r for t, r in out}
    assert by_key["bad"].status is AgentStatus.FAILED
    assert "kaboom" in by_key["bad"].error
    # siblings still return ok
    assert by_key["good"].status is AgentStatus.OK
    assert by_key["good2"].status is AgentStatus.OK


def test_scope_violation_passes_through():
    class ScopeStub:
        name = "scope"

        def execute_task(self, intent, codebase, allowed_files=None):
            return AgentResult(status=AgentStatus.SCOPE_VIOLATION, summary=intent)

    out = dispatch_layer(ScopeStub(), [SubTask("a", "a")], {})
    assert out[0][1].status is AgentStatus.SCOPE_VIOLATION


def test_tasks_actually_run_concurrently():
    # If serialized, 3 x 50ms sleeps take >=150ms; concurrent they take ~50ms.
    class SlowStub:
        name = "slow"

        def execute_task(self, intent, codebase, allowed_files=None):
            time.sleep(0.05)
            return AgentResult(status=AgentStatus.OK, summary=intent)

    tasks = [SubTask(k, k) for k in ("a", "b", "c")]
    start = time.monotonic()
    dispatch_layer(SlowStub(), tasks, {}, max_workers=3)
    assert time.monotonic() - start < 0.12


def test_empty_layer_returns_empty():
    assert dispatch_layer(StubAdapter(), [], {}) == []
