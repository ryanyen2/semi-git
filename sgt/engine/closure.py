"""Dependency-closure logic for reverting a feature.

Reverting a node removes the node and everything that transitively depends on it
(its dependents — you cannot keep a feature whose dependency is gone). Dependencies
of the removed set that become orphaned (no remaining dependents) are then
garbage-collected, the way `api-keys` disappears when the only feature using it is
reverted but survives when a dashboard still needs it (origin AE2).
"""

from __future__ import annotations

from sgt.store.graph import EdgeType, NodeKind, NodeStatus, SemanticGraph

# Only "support" kinds are cascade-GC'd when orphaned. A capability the user added
# on purpose is not collected just because the feature using it was reverted; a
# concept/infra node introduced to serve others is.
_GC_KINDS = {NodeKind.CONCEPT, NodeKind.INFRASTRUCTURE}


def dependents_closure(graph: SemanticGraph, node_id: str) -> set[str]:
    """`node_id` plus every node that transitively depends on it.

    A PLANNED dependent is *not* pulled in: a plan is a draft, not realized code, so it is
    not invalidated by removing the feature it declared a dependency on (its now-dangling
    edge is dropped with the removed node, and re-inferred when the plan is later fulfilled).
    Reverting real code must never silently delete the user's pending plan.
    """
    closure: set[str] = set()
    stack = [node_id]
    while stack:
        cur = stack.pop()
        if cur in closure:
            continue
        closure.add(cur)
        # predecessors are nodes pointing at `cur` (i.e. they depend on it); skip PLANNED
        # drafts so reverting an active feature does not garbage-collect a pending plan.
        stack.extend(p for p in graph.predecessors(cur)
                     if graph.get(p).status is not NodeStatus.PLANNED)
    return closure


def _depends_on(graph: SemanticGraph, node_id: str) -> list[str]:
    """Nodes that `node_id` depends on (its DEPENDS_ON successors)."""
    return [e.dst for e in graph.edges()
            if e.src == node_id and e.type is EdgeType.DEPENDS_ON]


def revert_set(graph: SemanticGraph, node_id: str) -> set[str]:
    """Full set of nodes to remove when reverting `node_id`.

    = dependents closure, then cascade-GC any dependency whose every dependent is
    inside the removal set.
    """
    to_remove = dependents_closure(graph, node_id)

    changed = True
    while changed:
        changed = False
        # dependencies of removed nodes that might now be orphaned
        deps: set[str] = set()
        for n in to_remove:
            deps.update(_depends_on(graph, n))
        for dep in deps - to_remove:
            if graph.get(dep).kind not in _GC_KINDS:
                continue  # capabilities/fixes are intentional — never auto-GC'd
            remaining_dependents = [p for p in graph.predecessors(dep) if p not in to_remove]
            if not remaining_dependents:
                to_remove.add(dep)
                changed = True
    return to_remove
