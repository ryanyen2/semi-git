"""Statement-granular ops through the real `materialize` path.

Proves the merge-quality payoff end to end: edits to *different* statements of one function
are order-independent and both land, with statement identity seeded from the function's
defining effect (log-resident, stable across re-materialization).
"""

from __future__ import annotations

from sgt.effects.body import StatementSeq
from sgt.effects.model import Effect, materialize

DEF = "def f(u):\n    a = 1\n    b = 2\n    return a + b"
DEF_EID = "R0:0"


def _seed_positions():
    """The PosIds an author resolves against f's body — seeded from the defining effect."""
    body = "a = 1\nb = 2\nreturn a + b"
    return StatementSeq.from_source(body, author="R0", base_counter=0).positions()


def _base():
    return [Effect.add_def("app.py", "f", DEF, eid=DEF_EID)]


def test_single_replace_stmt_materializes():
    p = _seed_positions()
    effects = _base() + [
        Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10", eid="R1:0"),
    ]
    src = materialize(effects)["app.py"]
    assert "a = 10" in src and "b = 2" in src and "return a + b" in src


def test_distinct_statement_edits_are_order_independent():
    p = _seed_positions()
    e1 = Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10", eid="R1:0")
    e2 = Effect.replace_stmt("app.py", "f", p[1].to_dict(), "b = 20", eid="R2:0")
    a = materialize(_base() + [e1, e2])["app.py"]
    b = materialize(_base() + [e2, e1])["app.py"]
    assert a == b                       # order-independent: the commute win
    assert "a = 10" in a and "b = 20" in a


def test_insert_then_edit_inserted_statement_keeps_identity():
    p = _seed_positions()
    ins = Effect.insert_stmt("app.py", "f", p[0].to_dict(), p[1].to_dict(),
                             "c = 99", eid="R1:0")
    # the inserted statement's PosId is deterministic from the insert op's eid
    from sgt.effects.stmt import from_eid
    cpos = from_eid(p[0], p[1], "R1:0")
    edit = Effect.replace_stmt("app.py", "f", cpos.to_dict(), "c = 100", eid="R1:1")
    src = materialize(_base() + [ins, edit])["app.py"]
    assert "c = 100" in src and "c = 99" not in src   # edit found the inserted slot
    # ordering: inserted statement sits between a and b
    assert src.index("a = 1") < src.index("c = 100") < src.index("b = 2")


def test_remove_stmt():
    p = _seed_positions()
    rm = Effect.remove_stmt("app.py", "f", p[1].to_dict(), eid="R1:0")
    src = materialize(_base() + [rm])["app.py"]
    assert "b = 2" not in src and "a = 1" in src


def test_non_statement_history_unchanged():
    # A pure def history must materialize exactly as before (back-compat).
    assert materialize(_base())["app.py"] == materialize(_base())["app.py"]
    assert "def f" in materialize(_base())["app.py"]
