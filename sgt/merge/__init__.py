"""Two-replica semantic merge (T0).

Merge is `union the logs (by eid) → re-gate → re-derive edges`. Node active/quarantined
status is treated as a *projection* of the unioned log: a node lands iff its effects are
both (1) free of a concurrent non-commuting conflict with an already-active node and (2)
sequentially applicable on the active set (materialize succeeds + stays invariant-valid).
See docs/design/2026-06-18-merge-edge-cases.md for the edge-case analysis this implements.
"""

from sgt.merge.engine import Delta, MergeReport, export_delta, merge
from sgt.merge.conflict import Conflict, Side, conflicts

__all__ = ["Delta", "MergeReport", "export_delta", "merge", "Conflict", "Side", "conflicts"]
