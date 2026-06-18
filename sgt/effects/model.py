"""Typed effects over Python source.

Each effect targets one file and carries a named payload so an LLM can emit it as
structured JSON. Application is deterministic; a file is the replay of its effects
from an empty base, which is what makes feature plug-out sound (drop the effects,
re-materialize).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum

Codebase = dict[str, str]  # repo-relative path -> source text


class EffectOp(str, Enum):
    ADD_DEF = "add_def"          # add a top-level function/class from source
    ADD_IMPORT = "add_import"    # add an import statement
    SET_CONST = "set_const"      # set a top-level NAME = constant
    RENAME_DEF = "rename_def"    # rename a top-level def + update call sites
    ADD_CALL = "add_call"        # append a call to `callee` inside `in_func`
    REPLACE_DEF = "replace_def"  # replace an existing top-level def's body/signature

    @property
    def is_monotone(self) -> bool:
        # Pure additions to a fresh region; never invalidate existing references.
        return self in (EffectOp.ADD_DEF, EffectOp.ADD_IMPORT)


class EffectError(Exception):
    """Raised when an effect's precondition fails or its payload is malformed."""


@dataclass(frozen=True)
class Effect:
    file: str
    op: EffectOp
    target: str           # primary name: def name / const / in_func / old name
    payload: dict = field(default_factory=dict)
    eid: str = ""

    # -- constructors ------------------------------------------------------
    @staticmethod
    def add_def(file: str, name: str, source: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.ADD_DEF, name, {"source": source}, eid)

    @staticmethod
    def add_import(file: str, source: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.ADD_IMPORT, source.strip(), {"source": source.strip()}, eid)

    @staticmethod
    def set_const(file: str, name: str, value, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.SET_CONST, name, {"value": value}, eid)

    @staticmethod
    def rename_def(file: str, old: str, new: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.RENAME_DEF, old, {"new": new}, eid)

    @staticmethod
    def add_call(file: str, in_func: str, callee: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.ADD_CALL, in_func, {"callee": callee}, eid)

    @staticmethod
    def replace_def(file: str, name: str, source: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.REPLACE_DEF, name, {"source": source}, eid)

    # -- serialization (persistence + LLM structured output) ---------------
    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "op": self.op.value,
            "target": self.target,
            "payload": self.payload,
            "eid": self.eid,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Effect":
        return cls(
            file=d["file"],
            op=EffectOp(d["op"]),
            target=d["target"],
            payload=dict(d.get("payload", {})),
            eid=d.get("eid", ""),
        )


# ---------------------------------------------------------------------------
# AST helpers (adapted from eico/env/pyast.py)
# ---------------------------------------------------------------------------
def _toplevel_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _import_lines(source: str) -> set[str]:
    lines: set[str] = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.add(ast.unparse(node).strip())
    return lines


class _ConstSetter(ast.NodeTransformer):
    def __init__(self, name, value):
        self.name, self.value = name, value

    def visit_Assign(self, node):
        if any(isinstance(t, ast.Name) and t.id == self.name for t in node.targets):
            node.value = ast.Constant(value=self.value)
        return node


class _Renamer(ast.NodeTransformer):
    def __init__(self, old, new):
        self.old, self.new = old, new

    def visit_FunctionDef(self, node):
        if node.name == self.old:
            node.name = self.new
        self.generic_visit(node)
        return node

    def visit_Name(self, node):
        if node.id == self.old:
            node.id = self.new
        return node


class _CallAdder(ast.NodeTransformer):
    def __init__(self, in_func, callee):
        self.in_func, self.callee = in_func, callee

    def visit_FunctionDef(self, node):
        if node.name == self.in_func:
            call = ast.Expr(value=ast.Call(
                func=ast.Name(id=self.callee, ctx=ast.Load()), args=[], keywords=[]))
            node.body.append(call)
        return node


# ---------------------------------------------------------------------------
def precondition_holds(source: str, e: Effect) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    names = _toplevel_names(tree)
    if e.op is EffectOp.ADD_DEF:
        return e.target not in names
    if e.op is EffectOp.ADD_IMPORT:
        return e.payload["source"].strip() not in _import_lines(source)
    if e.op is EffectOp.SET_CONST:
        return e.target in names
    if e.op is EffectOp.RENAME_DEF:
        new = e.payload["new"]
        defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        return e.target in defs and new not in names
    if e.op is EffectOp.ADD_CALL:
        defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        return e.target in defs and e.payload["callee"] in defs
    if e.op is EffectOp.REPLACE_DEF:
        defs = {n.name for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        return e.target in defs  # can only replace a def that exists
    return False


def apply_effect(source: str, e: Effect, check: bool = False) -> str:
    from sgt.effects.invariants import invariant_valid  # avoid import cycle

    if not precondition_holds(source, e):
        raise EffectError(f"precondition failed for {e.op.value} {e.target!r} in {e.file}")
    tree = ast.parse(source)
    if e.op is EffectOp.ADD_DEF:
        parsed = ast.parse(e.payload["source"]).body
        if not parsed:
            raise EffectError(f"add_def {e.target!r}: empty source")
        top = parsed[0]
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top.name = e.target
        tree.body.extend(parsed)
    elif e.op is EffectOp.ADD_IMPORT:
        imp = ast.parse(e.payload["source"]).body
        tree.body[0:0] = imp  # imports go to the top
    elif e.op is EffectOp.SET_CONST:
        tree = _ConstSetter(e.target, e.payload["value"]).visit(tree)
    elif e.op is EffectOp.RENAME_DEF:
        tree = _Renamer(e.target, e.payload["new"]).visit(tree)
    elif e.op is EffectOp.ADD_CALL:
        tree = _CallAdder(e.target, e.payload["callee"]).visit(tree)
    elif e.op is EffectOp.REPLACE_DEF:
        parsed = ast.parse(e.payload["source"]).body
        if not parsed:
            raise EffectError(f"replace_def {e.target!r}: empty source")
        top = parsed[0]
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            top.name = e.target  # keep the node's identity; only body/signature change
        new_body, replaced = [], False
        for n in tree.body:
            if (not replaced and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == e.target):
                new_body.append(top)
                replaced = True
            else:
                new_body.append(n)
        tree.body = new_body
    else:
        raise EffectError(f"unknown op {e.op}")
    ast.fix_missing_locations(tree)
    out = ast.unparse(tree)
    if check and not invariant_valid(out):
        raise EffectError(f"invariant violated after {e.op.value} {e.target!r}")
    return out


def apply_sequence(source: str, effects, check: bool = False) -> str:
    cur = source
    for e in effects:
        cur = apply_effect(cur, e, check=check)
    return cur


def materialize(effects, base: Codebase | None = None, check: bool = False) -> Codebase:
    """Replay an ordered list of effects into a codebase, grouped by file.

    `base` is the starting content per file (default empty). This is the canonical
    state function: the working tree is exactly the replay of the active effects.
    """
    cb: Codebase = dict(base or {})
    for e in effects:
        cb.setdefault(e.file, "")
        cb[e.file] = apply_effect(cb[e.file], e, check=check)
    return cb
