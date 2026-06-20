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


# --- scope-aware + codebase-aware (Phase 1 hardening) ---

def test_per_scope_uniqueness_allows_same_name_in_different_classes():
    src = ("class A:\n    def m(self):\n        return 1\n\n"
           "class B:\n    def m(self):\n        return 2\n")
    assert invariant_valid(src)


def test_duplicate_methods_in_one_class_fail():
    src = "class A:\n    def m(self):\n        return 1\n\n    def m(self):\n        return 2\n"
    assert invariant_valid(src) is False


def test_self_method_call_must_resolve():
    bad = "class S:\n    def a(self):\n        return self.gone()\n"
    assert invariant_valid(bad) is False
    good = "class S:\n    def a(self):\n        return self.b()\n\n    def b(self):\n        return 1\n"
    assert invariant_valid(good)


def test_attribute_call_on_unknown_object_is_not_flagged():
    # We cannot resolve obj.method(); being conservative avoids false positives.
    assert invariant_valid("def f(obj):\n    return obj.whatever()\n")


def test_too_many_positional_args_fail():
    assert invariant_valid("def f(x):\n    return x\n\ndef g():\n    return f(1, 2)\n") is False


def test_unknown_keyword_arg_fails():
    assert invariant_valid("def f(x):\n    return x\n\ndef g():\n    return f(z=1)\n") is False


def test_varargs_function_accepts_any_arity():
    assert invariant_valid("def f(*a, **k):\n    return a\n\ndef g():\n    return f(1, 2, z=3)\n")


def test_module_dunder_is_not_undefined():
    assert invariant_valid("if __name__ == '__main__':\n    print(1)\n")


def test_cross_module_import_must_resolve():
    # `from b import foo` is valid only while b actually defines foo.
    assert codebase_valid({
        "b.py": "def foo():\n    return 1\n",
        "a.py": "from b import foo\n\ndef use():\n    return foo()\n",
    })
    # Reverting foo out of b leaves a's import dangling -> caught here.
    assert codebase_valid({
        "b.py": "def other():\n    return 1\n",
        "a.py": "from b import foo\n",
    }) is False


def test_stdlib_import_is_not_cross_checked():
    assert codebase_valid({"a.py": "from os import getpid\n\ndef p():\n    return getpid()\n"})
