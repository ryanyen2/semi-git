"""Quarantine reconciliation: agent-free re-gate (graph-only sgt, R33).

A quarantine holds effects that did not commute with the active codebase when they were
landed. sgt never re-authors them — the coding agent owns code. Instead, ``reconcile``
periodically re-gates the quarantine's *existing* held effects against the current active
state: once the conflict clears (e.g. the rival node was reverted or suspended, or its
provider landed), the held effects commute as-is and the node can be resolved to ACTIVE.
"""

from __future__ import annotations

from sgt.effects.model import Effect
from sgt.engine.confluence import (
    INVARIANT_VIOLATED,
    can_land,
    max_coordination_free_batch_explained,
)
from sgt.project import Project


def attempt_recommute(project: Project, node_id: str) -> tuple[bool, list[Effect] | None, str]:
    """Re-gate a quarantine's held effects against the current active codebase.

    Returns ``(ok, effects, reason)``: on success ``effects`` is the held bundle, now
    confluent, ready to ``resolve_quarantine``; on failure ``reason`` is the refreshed hold
    reason for the witness. No re-authoring, no backend.
    """
    held = list(project.bundles.get(node_id, []))
    if not held:
        # An empty held bundle has nothing to conflict with — it is vacuously confluent, so
        # resolve it (flip ACTIVE with no effects) rather than leaving it pending forever.
        return True, [], ""
    cur = project.materialize()
    base = project.active_effects()
    if can_land(cur, held, base_effects=base):
        return True, held, ""
    _, expl = max_coordination_free_batch_explained(cur, held, base_effects=base)
    reason = expl[0][1] if expl else INVARIANT_VIOLATED
    return False, None, reason
