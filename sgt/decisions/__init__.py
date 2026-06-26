"""The decision layer: version control whose unit is a *decision*.

A ``Decision`` is the group of effect-log entries that landed at one checkpoint for one
feature (``(node_id, landing)``), plus its intent (``Context / Decision / Consequence``),
the alternatives weighed, its code footprint, and its versioning lineage. Decisions are a
*projection over the append-only log* — never a second source of truth (see
``docs/plans/2026-06-24-001-feat-decision-dag.md`` KTD1).

Dependency between decisions is **derived** from the entity graph, not stored here; only
lineage (``revise`` / ``fork``) is intrinsic. The working tree is materialized from a
``Frontier`` — one in-force decision per feature lane.
"""

from sgt.decisions.model import (
    Alternative,
    Decision,
    Frontier,
    Intent,
    LifecycleKind,
)

__all__ = ["Alternative", "Decision", "Frontier", "Intent", "LifecycleKind"]
