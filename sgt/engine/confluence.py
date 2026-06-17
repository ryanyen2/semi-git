"""The confluence gate (adapted from eico/env/pyast.py).

Two effects commute iff applying them in either order yields the same normalized
source. A batch is invariant-confluent iff all preconditions hold, every pair
commutes, and the applied result is invariant-valid. The orchestrator co-applies
the maximal coordination-free batch and quarantines the rest.
"""

from __future__ import annotations

from collections import defaultdict

from sgt.effects.invariants import codebase_valid, invariant_valid, normalize
from sgt.effects.model import (
    Codebase,
    Effect,
    EffectError,
    apply_effect,
    apply_sequence,
    precondition_holds,
)


def commute(source: str, e1: Effect, e2: Effect) -> bool:
    """Do e1 and e2 commute on `source`? (Different files trivially commute.)"""
    if e1.file != e2.file:
        return True
    # CALM: monotone additions to disjoint names touch independent regions and
    # commute by construction, even though appending them in different orders
    # produces different *text*. Same-target adds fall through to the apply path,
    # where the second precondition fails and they correctly do not commute.
    if e1.op.is_monotone and e2.op.is_monotone and e1.target != e2.target:
        return True
    try:
        a = normalize(apply_sequence(source, [e1, e2]))
    except EffectError:
        a = None
    try:
        b = normalize(apply_sequence(source, [e2, e1]))
    except EffectError:
        b = None
    return a is not None and a == b


def is_invariant_confluent(cb: Codebase, batch: list[Effect]) -> bool:
    """Can the whole batch co-apply to `cb` order-independently and stay valid?"""
    batch = list(batch)
    by_file: dict[str, list[Effect]] = defaultdict(list)
    for e in batch:
        by_file[e.file].append(e)

    for file, effects in by_file.items():
        src = cb.get(file, "")
        if not all(precondition_holds(src, e) for e in effects):
            return False
        for i in range(len(effects)):
            for j in range(i + 1, len(effects)):
                if not commute(src, effects[i], effects[j]):
                    return False
    # Apply the full batch and check codebase invariants on the result.
    try:
        result = dict(cb)
        for file, effects in by_file.items():
            result[file] = apply_sequence(result.get(file, ""), effects)
    except EffectError:
        return False
    return codebase_valid(result)


def can_land(cb: Codebase, batch: list[Effect]) -> bool:
    """Alias used by the orchestrator: is this batch safe to land on `cb`?"""
    return is_invariant_confluent(cb, batch)


def max_coordination_free_batch(cb: Codebase, candidates: list[Effect]) -> tuple[list[Effect], list[Effect]]:
    """Greedily select the largest prefix-stable confluent subset.

    Returns (admitted, held_back). Greedy is the scalable approximation to the exact
    max-coordination-free batch; the held-back effects are quarantined for serial
    resolution. (The exact/RL planner is deferred — see plan KTD7.)
    """
    admitted: list[Effect] = []
    held: list[Effect] = []
    for e in candidates:
        if is_invariant_confluent(cb, admitted + [e]):
            admitted.append(e)
        else:
            held.append(e)
    return admitted, held
