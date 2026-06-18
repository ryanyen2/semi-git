"""Quarantine reconciliation: bounded, non-blocking rewrite-to-commute (R33).

When a sub-task's effects do not commute with the landed codebase, the orchestrator
re-dispatches the task against the *post-land* tree — which now contains the winning
siblings — and re-gates the fresh result. A few attempts are tried; on success the
work lands, on exhaustion it is quarantined (the run never hard-fails).
"""

from __future__ import annotations

from sgt.adapter.base import AgentStatus, CodingAgentAdapter
from sgt.effects.model import Effect
from sgt.engine.confluence import (
    INVARIANT_VIOLATED,
    can_land,
    max_coordination_free_batch_explained,
)
from sgt.orchestrate.constraint import SubTask
from sgt.project import Project


def attempt_rewrite_to_commute(
    project: Project, agent: CodingAgentAdapter, task: SubTask, max_attempts: int
) -> tuple[bool, list[Effect] | None, str]:
    """Try to regenerate ``task`` so it commutes with the current codebase.

    Returns ``(landed, effects, reason)``: on success ``effects`` is the confluent
    rewrite to land; on failure ``reason`` is the latest hold reason for the witness.
    """
    last_reason = INVARIANT_VIOLATED
    for _ in range(max(0, max_attempts)):
        cur = project.materialize()
        result = agent.execute_task(task.intent, cur)
        if result.status is AgentStatus.FAILED or not result.effects:
            continue  # a failed attempt is just a failed attempt, not a crash
        if can_land(cur, result.effects):
            return True, list(result.effects), ""
        _, held = max_coordination_free_batch_explained(cur, result.effects)
        last_reason = held[0][1] if held else INVARIANT_VIOLATED
    return False, None, last_reason
