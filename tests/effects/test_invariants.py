"""U2 tests: the invariant predicate catches semantic conflicts statically."""

from sgt.effects.invariants import codebase_valid, invariant_valid


def test_valid_module_passes():
    assert invariant_valid("def f():\n    return 1\n\ndef g():\n    return f()\n")


def test_syntax_error_fails():
    assert invariant_valid("def f(:\n  pass") is False


def test_duplicate_defs_fail():
    assert invariant_valid("def f():\n    pass\n\ndef f():\n    pass\n") is False


def test_unresolved_call_fails():
    # g calls f, but f is not defined anywhere -> reference-integrity violation
    assert invariant_valid("def g():\n    return f()\n") is False


def test_imported_name_resolves():
    assert invariant_valid("import hashlib\n\ndef h(x):\n    return hashlib.md5(x).hexdigest()\n")


def test_from_import_resolves():
    assert invariant_valid("from os import getpid\n\ndef p():\n    return getpid()\n")


def test_arity_violation_fails():
    # f needs one arg; the call supplies none
    assert invariant_valid("def f(x):\n    return x\n\ndef g():\n    return f()\n") is False


def test_rename_leaving_stale_caller_is_invalid():
    # the classic conflict: f renamed to f2, but a caller still references f
    bad = "def f2():\n    return 1\n\ndef g():\n    return f()\n"
    assert invariant_valid(bad) is False


def test_codebase_valid_requires_every_file():
    assert codebase_valid({"a.py": "x = 1\n", "b.py": "def f():\n    return 1\n"})
    assert codebase_valid({"a.py": "x = 1\n", "b.py": "def g():\n    return undefined_fn()\n"}) is False
