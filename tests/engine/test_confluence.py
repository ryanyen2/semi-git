"""U3 tests: commutativity, invariant-confluence, and max coordination-free batch."""

from sgt.effects.model import Effect
from sgt.engine.confluence import (
    INVARIANT_VIOLATED,
    NON_COMMUTING_PREFIX,
    PRECONDITION_FAILED,
    can_land,
    commute,
    is_invariant_confluent,
    max_coordination_free_batch,
    max_coordination_free_batch_explained,
)


def test_disjoint_file_effects_commute():
    e1 = Effect.add_def("a.py", "f", "def f():\n    return 1")
    e2 = Effect.add_def("b.py", "g", "def g():\n    return 2")
    assert commute("", e1, e2)


def test_independent_adds_in_same_file_commute():
    src = ""
    e1 = Effect.add_def("a.py", "f", "def f():\n    return 1")
    e2 = Effect.add_def("a.py", "g", "def g():\n    return 2")
    assert commute(src, e1, e2)


def test_two_adds_of_same_name_do_not_commute():
    # both try to define `f` — order-sensitive / conflicting
    e1 = Effect.add_def("a.py", "f", "def f():\n    return 1")
    e2 = Effect.add_def("a.py", "f", "def f():\n    return 2")
    assert commute("", e1, e2) is False


def test_two_replace_defs_of_same_target_do_not_commute():
    # Two edits rewriting the same function are order-sensitive (last writer wins).
    src = "def f():\n    return 0\n"
    e1 = Effect.replace_def("a.py", "f", "def f():\n    return 1")
    e2 = Effect.replace_def("a.py", "f", "def f():\n    return 2")
    assert commute(src, e1, e2) is False


def test_methods_in_same_class_commute_when_disjoint():
    # Adding two different methods into a class touches independent regions.
    src = "class Svc:\n    def a(self):\n        return 1\n"
    e1 = Effect.add_def("app.py", "Svc.b", "def b(self):\n    return 2")
    e2 = Effect.add_def("app.py", "Svc.c", "def c(self):\n    return 3")
    assert commute(src, e1, e2)


def test_class_and_its_method_add_do_not_fastpath_commute():
    # Adding class A and adding A.m overlap (A.m needs A first) -> not order-free.
    e1 = Effect.add_def("app.py", "A", "class A:\n    pass")
    e2 = Effect.add_def("app.py", "A.m", "def m(self):\n    return 1")
    assert commute("", e1, e2) is False


def test_replace_def_commutes_with_add_on_disjoint_target():
    # Rewriting f and adding g touch independent regions -> order-independent.
    src = "def f():\n    return 0\n"
    e1 = Effect.replace_def("a.py", "f", "def f():\n    return 1")
    e2 = Effect.add_def("a.py", "g", "def g():\n    return 2")
    assert commute(src, e1, e2) is True


def test_confluent_batch_lands():
    cb = {"a.py": ""}
    batch = [
        Effect.add_import("a.py", "import hashlib"),
        Effect.add_def("a.py", "shorten", "def shorten(u):\n    return hashlib.md5(u.encode()).hexdigest()[:6]"),
    ]
    assert is_invariant_confluent(cb, batch)
    assert can_land(cb, batch)


def test_batch_producing_invalid_code_is_not_confluent():
    cb = {"a.py": ""}
    # a call to an undefined function -> name-resolution invariant fails
    batch = [Effect.add_def("a.py", "g", "def g():\n    return missing()")]
    assert is_invariant_confluent(cb, batch) is False


def test_max_batch_admits_safe_holds_conflicting():
    cb = {"a.py": ""}
    good = Effect.add_def("a.py", "f", "def f():\n    return 1")
    dup = Effect.add_def("a.py", "f", "def f():\n    return 2")  # conflicts with good
    other = Effect.add_def("a.py", "h", "def h():\n    return 3")
    admitted, held = max_coordination_free_batch(cb, [good, dup, other])
    names = {e.target for e in admitted}
    assert "f" in names and "h" in names
    assert dup in held


def test_explained_all_confluent_has_no_reasons():
    cb = {"a.py": ""}
    batch = [
        Effect.add_def("a.py", "f", "def f():\n    return 1"),
        Effect.add_def("a.py", "g", "def g():\n    return 2"),
    ]
    admitted, held = max_coordination_free_batch_explained(cb, batch)
    assert len(admitted) == 2 and held == []


def test_explained_duplicate_name_reason():
    cb = {"a.py": ""}
    good = Effect.add_def("a.py", "f", "def f():\n    return 1")
    dup = Effect.add_def("a.py", "f", "def f():\n    return 2")
    admitted, held = max_coordination_free_batch_explained(cb, [good, dup])
    assert [e.target for e in admitted] == ["f"]
    assert len(held) == 1
    held_effect, reason = held[0]
    assert held_effect is dup
    # second add of `f` fails its precondition once the first has landed
    assert reason in (PRECONDITION_FAILED, f"{NON_COMMUTING_PREFIX}f")


def test_explained_invariant_violation_reason():
    cb = {"a.py": ""}
    # a lone def calling an undefined name -> the combined result is invariant-invalid,
    # but its precondition holds and nothing pairwise-conflicts
    bad = Effect.add_def("a.py", "g", "def g():\n    return missing()")
    admitted, held = max_coordination_free_batch_explained(cb, [bad])
    assert admitted == []
    assert len(held) == 1 and held[0][1] == INVARIANT_VIOLATED


def test_explained_non_commuting_reason_names_counterpart():
    # two replace_defs on the same existing function: order-sensitive, non-commuting
    cb = {"a.py": "def f():\n    return 0\n"}
    e1 = Effect.replace_def("a.py", "f", "def f():\n    return 1")
    e2 = Effect.replace_def("a.py", "f", "def f():\n    return 2")
    admitted, held = max_coordination_free_batch_explained(cb, [e1, e2])
    assert [e.target for e in admitted] == ["f"]
    held_effect, reason = held[0]
    assert reason == f"{NON_COMMUTING_PREFIX}f"
