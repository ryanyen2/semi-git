"""The EICO confluence gate.

`confluence` decides which effects co-apply (commute + preserve invariants). Recompose closure
(which lanes go off together) now lives in `sgt.lifecycle.algebra`, lifted from the graph's
DEPENDS_ON edges to lane granularity — the destructive node-removal `closure` module is gone with
the lossless frontier model.
"""

from sgt.engine.confluence import (
    can_land,
    commute,
    is_invariant_confluent,
    max_coordination_free_batch,
)

__all__ = [
    "can_land",
    "commute",
    "is_invariant_confluent",
    "max_coordination_free_batch",
]
