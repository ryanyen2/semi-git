"""U2 tests: typed effects apply deterministically and replay into a codebase."""

import pytest

from sgt.effects.invariants import invariant_valid, normalize
from sgt.effects.model import (
    Effect,
    EffectError,
    EffectOp,
    apply_effect,
    apply_sequence,
    materialize,
    precondition_holds,
)


def test_add_def_appends_function():
    src = ""
    e = Effect.add_def("app.py", "shorten", "def shorten(url):\n    return url[:6]")
    out = apply_effect(src, e)
    assert "def shorten(url):" in out
    assert invariant_valid(out)


def test_add_def_precondition_rejects_duplicate():
    src = "def shorten(url):\n    return url"
    e = Effect.add_def("app.py", "shorten", "def shorten(u):\n    return u")
    assert precondition_holds(src, e) is False
    with pytest.raises(EffectError):
        apply_effect(src, e)


def test_set_const_changes_value():
    src = "CODE_LEN = 4\n"
    out = apply_effect(src, Effect.set_const("app.py", "CODE_LEN", 6))
    assert "CODE_LEN = 6" in normalize(out)


def test_rename_def_updates_call_sites():
    src = "def f():\n    return 1\n\ndef g():\n    return f()\n"
    out = apply_effect(src, Effect.rename_def("app.py", "f", "f2"))
    assert "def f2():" in out
    assert "return f2()" in out  # the refupdate
    assert invariant_valid(out)


def test_add_import_dedups():
    src = ""
    e = Effect.add_import("app.py", "import hashlib")
    out = apply_effect(src, e)
    assert "import hashlib" in out
    assert precondition_holds(out, e) is False  # already imported


def test_add_call_inserts_into_target_body():
    src = "def log():\n    pass\n\ndef handle():\n    return 1\n"
    out = apply_effect(src, Effect.add_call("app.py", "handle", "log"))
    assert "log()" in out


def test_materialize_replays_into_multiple_files():
    effects = [
        Effect.add_import("app.py", "import hashlib"),
        Effect.add_def("app.py", "shorten", "def shorten(u):\n    return hashlib.md5(u.encode()).hexdigest()[:6]"),
        Effect.add_def("store.py", "save", "def save(k, v):\n    return {k: v}"),
    ]
    cb = materialize(effects)
    assert set(cb) == {"app.py", "store.py"}
    assert "def shorten" in cb["app.py"]
    assert "def save" in cb["store.py"]
    assert invariant_valid(cb["app.py"])


def test_materialize_without_a_dropped_feature_is_clean():
    # Feature A (shorten) + Feature B (a redirect that calls shorten).
    feature_a = [Effect.add_def("app.py", "shorten", "def shorten(u):\n    return u[:6]")]
    feature_b = [Effect.add_def("app.py", "redirect", "def redirect(c):\n    return c")]
    full = materialize(feature_a + feature_b, check=True)
    assert "def shorten" in full["app.py"] and "def redirect" in full["app.py"]
    # Plug out feature B: replay only A.
    without_b = materialize(feature_a, check=True)
    assert "def shorten" in without_b["app.py"]
    assert "def redirect" not in without_b["app.py"]
