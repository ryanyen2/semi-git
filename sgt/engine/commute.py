"""Static commutation rules for statement-granular ops.

The text-apply commutation check (``confluence.commute``) cannot evaluate statement ops:
their effect on a body depends on log-resident PosIds that are not in the source text. But
statement ops do not *need* an apply to decide commutation — it follows from their targets:

* two statement ops on **different** functions are independent;
* on the **same** function, edits to **distinct** statements (distinct PosIds, or an insert,
  which always allocates a fresh slot) commute; two replace/remove on the **same** PosId do
  not (that is the same-statement conflict the merge policy resolves);
* a statement op and a *def-level* op that **touches the same function** overlap and are kept
  apart (serialized), mirroring how ``add_def`` + ``add_call`` on one unit are order-sensitive.

``static_commute`` returns a definite verdict whenever a statement op is involved and ``None``
otherwise, so ``confluence.commute`` defers to its existing apply-and-compare path for the
pure def-level pairs it already handled — no behavior change there.
"""

from __future__ import annotations

from sgt.effects.model import STMT_OPS, Effect, EffectOp


def _pos_key(e: Effect):
    """The PosId a replace/remove targets, as a hashable key; ``None`` for an insert (fresh slot)."""
    if e.op in (EffectOp.REPLACE_STMT, EffectOp.REMOVE_STMT):
        pos = e.payload.get("pos") or {}
        return (tuple(pos.get("digits", ())), pos.get("author", ""), pos.get("counter", -1))
    return None  # INSERT_STMT allocates a brand-new slot — never collides with an existing one


def _touches_function(core: Effect, func: str) -> bool:
    """Does a def-level op affect ``func`` (same/ancestor/descendant path in the same file)?"""
    if core.op in (EffectOp.ADD_IMPORT, EffectOp.SET_CONST):
        return False  # module-level namespaces, not a function body
    from sgt.engine.confluence import _disjoint_paths

    return not _disjoint_paths(core.target, func)


def static_commute(e1: Effect, e2: Effect) -> bool | None:
    """Commutation verdict when a statement op is involved; ``None`` to defer to apply-compare."""
    if e1.op not in STMT_OPS and e2.op not in STMT_OPS:
        return None  # pure def-level pair — let confluence.commute decide as before
    if e1.file != e2.file:
        return True

    if e1.op in STMT_OPS and e2.op in STMT_OPS:
        if e1.target != e2.target:
            return True  # different functions
        p1, p2 = _pos_key(e1), _pos_key(e2)
        if p1 is None or p2 is None:
            return True  # an insert touches a fresh slot
        return p1 != p2  # same function: distinct statements commute, same one conflicts

    # one statement op, one def-level op
    stmt, core = (e1, e2) if e1.op in STMT_OPS else (e2, e1)
    return not _touches_function(core, stmt.target)
