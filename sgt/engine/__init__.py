"""The EICO confluence gate and dependency-closure logic.

`confluence` decides which effects co-apply (commute + preserve invariants);
`closure` computes what must be removed when a feature is reverted (the node, its
dependents, and dependencies that become orphaned).
"""

from sgt.engine.confluence import (
    can_land,
    commute,
    is_invariant_confluent,
    max_coordination_free_batch,
)
from sgt.engine.closure import dependents_closure, revert_set

__all__ = [
    "can_land",
    "commute",
    "is_invariant_confluent",
    "max_coordination_free_batch",
    "dependents_closure",
    "revert_set",
]
