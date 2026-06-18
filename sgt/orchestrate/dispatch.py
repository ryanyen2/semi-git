"""Concurrent dispatch of one constraint-graph layer to coding-agent backends.

Backend calls are blocking I/O (HTTP to an LLM), so a thread pool parallelizes a
layer without rewriting the synchronous `CodingAgentAdapter` contract (plan KTD2).
Results come back in task order regardless of completion order; a worker that raises
is captured as a FAILED `AgentResult` (mirroring the adapter's own failure posture)
so one bad sub-task never kills the layer.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sgt.adapter.base import AgentResult, AgentStatus, CodingAgentAdapter
from sgt.effects.model import Codebase
from sgt.orchestrate.constraint import SubTask

DEFAULT_WORKERS = 4


def dispatch_layer(
    adapter: CodingAgentAdapter,
    tasks: list[SubTask],
    codebase: Codebase,
    allowed_files: set[str] | None = None,
    max_workers: int = DEFAULT_WORKERS,
) -> list[tuple[SubTask, AgentResult]]:
    """Run every sub-task in ``tasks`` concurrently against ``codebase``.

    Returns ``(task, result)`` pairs in the original ``tasks`` order. All tasks in a
    layer see the same ``codebase`` snapshot — dependents are handled by running a
    later layer against the post-land tree, not by intra-layer ordering.
    """
    if not tasks:
        return []

    def run(task: SubTask) -> AgentResult:
        try:
            return adapter.execute_task(task.intent, codebase, allowed_files)
        except Exception as ex:  # noqa: BLE001 - a worker crash is a failed task, not a layer crash
            return AgentResult(status=AgentStatus.FAILED, error=f"{type(ex).__name__}: {ex}")

    workers = max(1, min(max_workers, len(tasks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, tasks))  # pool.map preserves input order
    return list(zip(tasks, results))
