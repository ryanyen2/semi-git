"""Typed effects over real Python source, and the invariants that gate them.

Ported and adapted from the EICO research engine (`env/pyast.py`): typed AST-level
effects with preconditions, deterministic application, and a static invariant
predicate. A file's content is the replay of its active effect-bundles, so reverting
a feature is "drop its effects and re-materialize" — sound by construction.
"""

from sgt.effects.model import (
    Codebase,
    Effect,
    EffectError,
    EffectOp,
    apply_effect,
    apply_sequence,
    materialize,
    precondition_holds,
)
from sgt.effects.invariants import codebase_valid, invariant_valid, normalize

__all__ = [
    "Codebase",
    "Effect",
    "EffectError",
    "EffectOp",
    "apply_effect",
    "apply_sequence",
    "materialize",
    "precondition_holds",
    "codebase_valid",
    "invariant_valid",
    "normalize",
]
