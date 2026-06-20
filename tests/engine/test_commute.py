"""Log-aware confluence for statement ops: static commute + history-seeded gating."""

from __future__ import annotations

from sgt.effects.body import StatementSeq
from sgt.effects.model import Effect, materialize
from sgt.engine.commute import static_commute
from sgt.engine.confluence import can_land, max_coordination_free_batch_explained

DEF = "def f(u):\n    a = 1\n    b = 2\n    return a + b"


def _positions():
    return StatementSeq.from_source("a = 1\nb = 2\nreturn a + b", "R0", 0).positions()


def _base():
    return [Effect.add_def("app.py", "f", DEF, eid="R0:0")]


# -- static_commute (pure, no apply) ----------------------------------------
def test_distinct_statement_replaces_commute():
    p = _positions()
    e1 = Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10", eid="R1:0")
    e2 = Effect.replace_stmt("app.py", "f", p[1].to_dict(), "b = 20", eid="R2:0")
    assert static_commute(e1, e2) is True


def test_same_statement_replaces_do_not_commute():
    p = _positions()
    e1 = Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10", eid="R1:0")
    e2 = Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 99", eid="R2:0")
    assert static_commute(e1, e2) is False


def test_statement_op_and_replace_def_same_func_conflict():
    p = _positions()
    stmt = Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10", eid="R1:0")
    rdef = Effect.replace_def("app.py", "f", "def f(u):\n    return 0", eid="R2:0")
    assert static_commute(stmt, rdef) is False        # def-rewrite overlaps the body
    assert static_commute(stmt, Effect.add_import("app.py", "import os")) is True
    assert static_commute(stmt, Effect.add_def("other.py", "g", "def g():\n    return 1")) is True


def test_pure_def_level_pair_defers_to_apply_path():
    a = Effect.add_def("a.py", "x", "def x():\n    return 1")
    b = Effect.add_def("a.py", "y", "def y():\n    return 2")
    assert static_commute(a, b) is None  # not our concern → confluence.commute decides


# -- the gate with history (base_effects) -----------------------------------
def test_distinct_statement_edits_both_admit_through_gate():
    p = _positions()
    base = _base()
    cb = materialize(base)
    cands = [
        Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10", eid="R1:0"),
        Effect.replace_stmt("app.py", "f", p[1].to_dict(), "b = 20", eid="R2:0"),
    ]
    admitted, held = max_coordination_free_batch_explained(cb, cands, base_effects=base)
    assert len(admitted) == 2 and not held          # the merge-quality win, through the gate


def test_same_statement_edits_hold_one():
    p = _positions()
    base = _base()
    cb = materialize(base)
    cands = [
        Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10", eid="R1:0"),
        Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 99", eid="R2:0"),
    ]
    admitted, held = max_coordination_free_batch_explained(cb, cands, base_effects=base)
    assert len(admitted) == 1 and len(held) == 1     # same statement → one quarantined


def test_can_land_seeds_from_history():
    p = _positions()
    base = _base()
    cb = materialize(base)
    good = [Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10", eid="R1:0")]
    assert can_land(cb, good, base_effects=base)
    # without history the gate refuses statement ops rather than mis-applying
    assert not can_land(cb, good)
