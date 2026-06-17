"""Feature lifecycle operations, all gated by the confluence/invariant check.

`revert_feature` removes a node, its dependents, and orphaned dependencies, then
re-materializes the remaining codebase and verifies it is still invariant-valid
before the caller commits. `switch_feature` suspends or restores a node without
deletion (append-only graph), also gated by validity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sgt.engine.closure import revert_set
from sgt.project import Project
from sgt.store.graph import NodeStatus


@dataclass
class RevertOutcome:
    ok: bool
    removed: list[str] = field(default_factory=list)
    message: str = ""


def revert_feature(project: Project, node_id: str) -> RevertOutcome:
    if not project.graph.has(node_id):
        return RevertOutcome(False, message=f"unknown node {node_id!r}")
    to_remove = revert_set(project.graph, node_id)
    project.remove_nodes(to_remove)
    if not project.valid():
        # Re-materialized codebase is invalid — closure missed something. Refuse.
        return RevertOutcome(
            False,
            removed=sorted(to_remove),
            message="revert would leave the codebase invalid; aborted",
        )
    return RevertOutcome(True, removed=sorted(to_remove))


def switch_feature(project: Project, node_id: str, on: bool) -> RevertOutcome:
    if not project.graph.has(node_id):
        return RevertOutcome(False, message=f"unknown node {node_id!r}")
    node = project.graph.get(node_id)
    prev = node.status
    node.status = NodeStatus.ACTIVE if on else NodeStatus.SUSPENDED
    if not project.valid():
        node.status = prev  # roll back
        return RevertOutcome(
            False, message=f"switching {'on' if on else 'off'} would invalidate the codebase; aborted"
        )
    return RevertOutcome(True, removed=[node_id])
