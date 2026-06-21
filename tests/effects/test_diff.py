"""Tests for the reverse distiller (tree -> typed effects), focused on rename detection."""

from __future__ import annotations

from sgt.effects.diff import distill_file
from sgt.effects.model import EffectOp, apply_sequence


def _ops(effects):
    return [(e.op, e.target, e.payload.get("new")) for e in effects]


def test_module_binding_is_distilled_not_noted():
    """Single-name module-level bindings now become assign effects (was: a manual-review note)."""
    actual = ("import re\n"
              "_WORD_RE = re.compile('\\\\w+')\n"
              "def tok(s):\n    return _WORD_RE.findall(s)\n")
    effects, notes = distill_file("p.py", "", actual)
    ops = {(e.op, e.target) for e in effects}
    assert (EffectOp.ADD_ASSIGN, "_WORD_RE") in ops
    assert notes == []  # nothing left for manual review
    # round-trips: replaying onto empty reproduces a valid module
    assert "_WORD_RE = re.compile" in apply_sequence("", effects)


def test_changed_and_removed_bindings():
    effects, _ = distill_file("m.py", "X = 1\nY = 2\n", "X = 99\n")  # X changed, Y removed
    assert {(e.op, e.target) for e in effects} == {
        (EffectOp.REPLACE_ASSIGN, "X"), (EffectOp.REMOVE_ASSIGN, "Y")}


def test_non_single_name_statements_still_noted():
    """Tuple-unpacking / bare expressions remain uncaptured — but with an explicit note."""
    effects, notes = distill_file("m.py", "", "a, b = 1, 2\nprint('hi')\n")
    assert effects == []
    assert notes and "NOT captured" in notes[0]


def test_pure_rename_emits_single_rename_def():
    expected = "def shorten(url):\n    return url[:6]\n"
    actual = "def make_code(url):\n    return url[:6]\n"
    effects, notes = distill_file("s.py", expected, actual)
    assert _ops(effects) == [(EffectOp.RENAME_DEF, "shorten", "make_code")]
    assert not notes
    # replaying the rename over the expected source reproduces the actual source
    assert apply_sequence(expected, effects).strip() == actual.strip()


def test_rename_with_body_change_emits_rename_then_replace():
    expected = "def shorten(url):\n    return url[:6]\n"
    actual = "def make_code(url, n=8):\n    return url[:n]\n"
    effects, _ = distill_file("s.py", expected, actual)
    ops = [e.op for e in effects]
    assert ops == [EffectOp.RENAME_DEF, EffectOp.REPLACE_DEF]
    assert effects[0].target == "shorten" and effects[0].payload["new"] == "make_code"
    assert effects[1].target == "make_code"
    assert apply_sequence(expected, effects).strip() == actual.strip()


def test_unrelated_add_and_remove_are_not_a_rename():
    expected = "def alpha():\n    return 1\n"
    actual = "def beta():\n    x = sum(range(100))\n    return x * 2 + 7\n"
    effects, _ = distill_file("s.py", expected, actual)
    ops = sorted(e.op for e in effects)
    assert ops == sorted([EffectOp.ADD_DEF, EffectOp.REMOVE_DEF])  # no false-positive rename


def test_class_rename_falls_back_to_add_remove():
    # rename_def renames functions only; a class rename must stay delete+add.
    expected = "class Foo:\n    def m(self):\n        return 1\n"
    actual = "class Bar:\n    def m(self):\n        return 1\n"
    effects, _ = distill_file("s.py", expected, actual)
    assert EffectOp.RENAME_DEF not in {e.op for e in effects}
    assert {e.op for e in effects} == {EffectOp.ADD_DEF, EffectOp.REMOVE_DEF}


def test_one_to_one_matching_picks_best_pair():
    # two removed, two added; each added matches exactly one removed body.
    expected = (
        "def a(x):\n    return x + 1\n\n"
        "def b(x):\n    return x * 100 - 5\n"
    )
    actual = (
        "def a2(x):\n    return x + 1\n\n"
        "def b2(x):\n    return x * 100 - 5\n"
    )
    effects, _ = distill_file("s.py", expected, actual)
    renames = {(e.target, e.payload["new"]) for e in effects if e.op is EffectOp.RENAME_DEF}
    assert renames == {("a", "a2"), ("b", "b2")}
    assert apply_sequence(expected, effects).strip() == actual.strip()


def test_async_rename_not_detected():
    # rename_def's precondition is sync FunctionDef only; async stays delete+add (op-safe).
    expected = "async def fetch(u):\n    return await get(u)\n"
    actual = "async def pull(u):\n    return await get(u)\n"
    effects, _ = distill_file("s.py", expected, actual)
    assert EffectOp.RENAME_DEF not in {e.op for e in effects}
