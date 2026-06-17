"""The invariant predicate I — the correctness gate.

Adapted from eico/env/pyast.py. A source is invariant-valid iff it PARSES, has
UNIQUE top-level defs, every CALLED name RESOLVES (defined locally, imported, or a
builtin), and call ARITY is satisfiable. These are the static analogues of EICO's
type-validity, uniqueness, and reference-integrity invariants — strong enough to
catch the "rename a function / leave a stale caller" class of semantic conflict
without executing code.
"""

from __future__ import annotations

import ast
import builtins

from sgt.effects.model import Codebase, _toplevel_names

_BUILTINS = set(dir(builtins))


def _called_names(tree: ast.AST) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.add(node.func.id)
    return calls


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _func_required_arity(tree: ast.Module) -> dict[str, int]:
    arities: dict[str, int] = {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef):
            arities[n.name] = len(n.args.args) - len(n.args.defaults)
    return arities


def invariant_valid(source: str) -> bool:
    """I for a single module: PARSES & UNIQUE_DEFS & NAME_RESOLUTION & ARITY."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    # UNIQUE top-level defs
    defs = [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    if len(defs) != len(set(defs)):
        return False

    # NAME RESOLUTION: every called bare name is defined locally, imported, a param,
    # a local binding, or a builtin.
    local_bindings = _toplevel_names(tree) | _imported_names(tree) | _BUILTINS
    for fn in tree.body:
        if isinstance(fn, ast.FunctionDef):
            local_bindings |= {a.arg for a in fn.args.args}
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            local_bindings.add(t.id)
    for called in _called_names(tree):
        if called not in local_bindings:
            return False

    # ARITY: a call to a known local function must supply its required positionals.
    arities = _func_required_arity(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn in arities and not any(isinstance(a, ast.Starred) for a in node.args):
                if len(node.args) + len(node.keywords) < arities[fn]:
                    return False
    return True


def codebase_valid(cb: Codebase) -> bool:
    """I over a whole codebase: every file is invariant-valid."""
    return all(invariant_valid(src) for src in cb.values())


def normalize(source: str) -> str:
    """Canonical form for state equality (AST round-trip)."""
    try:
        return ast.unparse(ast.parse(source))
    except SyntaxError:
        return source
