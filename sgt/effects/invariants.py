"""The invariant predicate I — the correctness gate.

Adapted from eico/env/pyast.py and generalized to scope-qualified units. A source is
invariant-valid iff it PARSES, has UNIQUE names within every scope, every referenced
name RESOLVES (bound somewhere in the module, imported, or a builtin), resolvable
method calls (``self.m()`` / ``Class.m()``) hit a real method, and call ARITY is
satisfiable for locally-defined functions. Across a codebase, ``from <local> import x``
must name something the local module actually defines — so reverting a concept another
module imports is caught here rather than breaking at runtime.

These are the static analogues of EICO's type-validity, uniqueness, and reference-
integrity invariants. The analysis is deliberately conservative: an attribute call it
cannot resolve (``obj.m()``) is NOT flagged, so valid code is never falsely held back.
"""

from __future__ import annotations

import ast
import builtins

from sgt.effects.model import Codebase, _DEF_TYPES, _FUNC_TYPES, units

_BUILTINS = set(dir(builtins))
# Module-level dunders are always bound at runtime (common in `if __name__ == ...`).
_MODULE_DUNDERS = {
    "__name__", "__file__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__dict__", "__path__",
}


# ---------------------------------------------------------------------------
# Binding collection: every name bound anywhere in the module, all binding forms.
# A flat union is intentionally permissive about lexical scope (it never raises a
# false "undefined" for a name bound in a sibling scope) while still catching names
# that are bound nowhere at all.
# ---------------------------------------------------------------------------
def _bind_targets(target: ast.AST, into: set[str]) -> None:
    if isinstance(target, ast.Name):
        into.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _bind_targets(elt, into)
    elif isinstance(target, ast.Starred):
        _bind_targets(target.value, into)


def _all_bindings(tree: ast.Module) -> set[str]:
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound.add(alias.asname or alias.name.split(".")[0])
        if isinstance(node, _DEF_TYPES):
            bound.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = node.args
            for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
                bound.add(arg.arg)
            if a.vararg:
                bound.add(a.vararg.arg)
            if a.kwarg:
                bound.add(a.kwarg.arg)
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                _bind_targets(t, bound)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _bind_targets(node.target, bound)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    _bind_targets(item.optional_vars, bound)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.comprehension):
            _bind_targets(node.target, bound)
        elif isinstance(node, ast.NamedExpr):
            _bind_targets(node.target, bound)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
    return bound


# ---------------------------------------------------------------------------
# Uniqueness, per scope.
# ---------------------------------------------------------------------------
def _scopes_unique(tree: ast.Module) -> bool:
    """Def/class names must be unique within each scope (module + every class/func body)."""

    def ok(body: list[ast.stmt]) -> bool:
        names = [n.name for n in body if isinstance(n, _DEF_TYPES)]
        if len(names) != len(set(names)):
            return False
        for n in body:
            if isinstance(n, _DEF_TYPES) and not ok(n.body):
                return False
        return True

    return ok(tree.body)


# ---------------------------------------------------------------------------
# Reference resolution + method-call resolution.
# ---------------------------------------------------------------------------
def _class_methods(cls: ast.ClassDef) -> set[str]:
    return {n.name for n in cls.body if isinstance(n, _FUNC_TYPES)}


def _has_bases(cls: ast.ClassDef) -> bool:
    # If a class has bases/keywords we can't see, be permissive about its attributes.
    return bool(cls.bases) or bool(cls.keywords)


def _method_calls_resolve(tree: ast.Module) -> bool:
    """`self.m()` inside a base-less class, and `Local.m()`, must hit a real method."""
    classes = {p: n for p, n in units(tree).items() if isinstance(n, ast.ClassDef)}
    local_classes = {p.split(".")[-1]: n for p, n in classes.items()}

    # self.m() within each base-less class
    for cls in classes.values():
        if _has_bases(cls):
            continue
        methods = _class_methods(cls)
        for node in ast.walk(cls):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"):
                if node.func.attr not in methods:
                    return False

    # Local.m() where Local is a base-less class defined here
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)):
            cls = local_classes.get(node.func.value.id)
            if cls is not None and not _has_bases(cls) and node.func.attr not in _class_methods(cls):
                return False
    return True


# ---------------------------------------------------------------------------
# Arity for calls to locally-defined top-level functions.
# ---------------------------------------------------------------------------
def _signature(fn: ast.FunctionDef) -> dict:
    a = fn.args
    positional = [*a.posonlyargs, *a.args]
    return {
        "required": len(positional) - len(a.defaults),
        "max_positional": None if a.vararg else len(positional),
        "param_names": {arg.arg for arg in (*a.args, *a.kwonlyargs)},
        "has_kwarg": a.kwarg is not None,
    }


def _arity_ok(tree: ast.Module) -> bool:
    sigs = {n.name: _signature(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        sig = sigs.get(node.func.id)
        if sig is None:
            continue
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            continue  # *args spread — positional count is unknowable
        n_pos = len(node.args)
        kw_names = [kw.arg for kw in node.keywords]
        has_dstar = any(name is None for name in kw_names)  # **kwargs spread in the call
        if sig["max_positional"] is not None and n_pos > sig["max_positional"]:
            return False
        n_supplied = n_pos + sum(1 for name in kw_names if name is not None)
        if not has_dstar and n_supplied < sig["required"]:
            return False
        if not sig["has_kwarg"]:
            for name in kw_names:
                if name is not None and name not in sig["param_names"]:
                    return False
    return True


def _loaded_names(tree: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def invariant_valid(source: str) -> bool:
    """I for a single module: PARSES & UNIQUE & NAME_RESOLUTION & METHODS & ARITY."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    if not _scopes_unique(tree):
        return False

    bound = _all_bindings(tree) | _BUILTINS | _MODULE_DUNDERS
    if any(name not in bound for name in _loaded_names(tree)):
        return False

    if not _method_calls_resolve(tree):
        return False

    return _arity_ok(tree)


# ---------------------------------------------------------------------------
# Codebase-level: per-file validity + cross-module reference integrity.
# ---------------------------------------------------------------------------
def _module_candidates(modname: str) -> list[str]:
    """Repo-relative files a dotted module name could resolve to."""
    rel = modname.replace(".", "/")
    return [f"{rel}.py", f"{rel}/__init__.py"]


def _toplevel_exports(source: str) -> set[str]:
    """Top-level names a module provides (defs, classes, assignments)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, _DEF_TYPES):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


def _cross_module_ok(cb: Codebase) -> bool:
    """`from <local> import x` must name something the local module defines.

    Only checks imports of modules present in the codebase (local). Stdlib/third-party
    imports are assumed valid, so this never false-flags `from os import getpid`.
    """
    for src in cb.values():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return False
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level or node.module is None:
                continue
            local = next((c for c in _module_candidates(node.module) if c in cb), None)
            if local is None:
                continue  # third-party / stdlib — out of scope for this check
            exports = _toplevel_exports(cb[local])
            for alias in node.names:
                if alias.name != "*" and alias.name not in exports:
                    return False
    return True


def codebase_valid(cb: Codebase) -> bool:
    """I over a whole codebase: every file is invariant-valid AND imports resolve."""
    return all(invariant_valid(src) for src in cb.values()) and _cross_module_ok(cb)


def normalize(source: str) -> str:
    """Canonical form for state equality (AST round-trip)."""
    try:
        return ast.unparse(ast.parse(source))
    except SyntaxError:
        return source
