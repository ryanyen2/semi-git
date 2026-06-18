"""U3 tests: commutativity, invariant-confluence, and max coordination-free batch."""

from sgt.effects.model import Effect
from sgt.engine.confluence import (
    can_land,
    commute,
    is_invariant_confluent,
    max_coordination_free_batch,
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
