"""Typed effects over Python source.

Each effect targets one file and a **scope-qualified path** within it (``shorten``,
``UrlService``, ``UrlService.shorten``), so the unit of versioning is any addressable
syntax-tree node at any depth — not just a top-level def. A bare name (no dots) is the
top-level case, so effects written before paths existed keep working unchanged.

Application is deterministic; a file is the replay of its effects from an empty base,
which is what makes feature plug-out sound (drop the effects, re-materialize).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum

Codebase = dict[str, str]  # repo-relative path -> source text


class EffectOp(str, Enum):
    ADD_DEF = "add_def"          # add a function/class/method at a path's parent scope
    ADD_IMPORT = "add_import"    # add an import statement
    SET_CONST = "set_const"      # set a top-level NAME = constant
    RENAME_DEF = "rename_def"    # rename a top-level def + update call sites
    ADD_CALL = "add_call"        # append a call to `callee` inside the unit at `target`
    REPLACE_DEF = "replace_def"  # replace an existing unit's body/signature (any depth)
    REMOVE_DEF = "remove_def"    # remove the unit at `target` (the deletion primitive)
    # Statement-granular ops (target = the function path; payload carries the PosId, so two
    # edits to *different* statements of one function commute). Materialized structurally by
    # replaying a function's statement ops into a PosId-keyed sequence (see body.py).
    INSERT_STMT = "insert_stmt"   # insert a statement between two PosIds in a function body
    REPLACE_STMT = "replace_stmt" # replace the statement at a PosId
    REMOVE_STMT = "remove_stmt"   # remove (tombstone) the statement at a PosId

    @property
    def is_monotone(self) -> bool:
        # Pure additions to a fresh region; never invalidate existing references.
        return self in (EffectOp.ADD_DEF, EffectOp.ADD_IMPORT)


# Statement-granular ops are materialized structurally, not by the per-effect text apply.
STMT_OPS = (EffectOp.INSERT_STMT, EffectOp.REPLACE_STMT, EffectOp.REMOVE_STMT)


class EffectError(Exception):
    """Raised when an effect's precondition fails or its payload is malformed."""


@dataclass(frozen=True)
class Effect:
    file: str
    op: EffectOp
    target: str           # scope-qualified path: def/class/method path, const, in_func, old name
    payload: dict = field(default_factory=dict)
    eid: str = ""

    # -- constructors ------------------------------------------------------
    @staticmethod
    def add_def(file: str, path: str, source: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.ADD_DEF, path, {"source": source}, eid)

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
    def replace_def(file: str, path: str, source: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.REPLACE_DEF, path, {"source": source}, eid)

    @staticmethod
    def remove_def(file: str, path: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.REMOVE_DEF, path, {}, eid)

    @staticmethod
    def insert_stmt(file: str, func: str, after: dict | None, before: dict | None,
                    source: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.INSERT_STMT, func,
                      {"after": after, "before": before, "source": source}, eid)

    @staticmethod
    def replace_stmt(file: str, func: str, pos: dict, source: str, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.REPLACE_STMT, func, {"pos": pos, "source": source}, eid)

    @staticmethod
    def remove_stmt(file: str, func: str, pos: dict, eid: str = "") -> "Effect":
        return Effect(file, EffectOp.REMOVE_STMT, func, {"pos": pos}, eid)

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
_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def units(tree: ast.Module) -> dict[str, ast.AST]:
    """Map every scope-qualified path to its def/class node, recursively at any depth.

    ``def shorten`` -> ``{"shorten": <FunctionDef>}``; a method ``bar`` in ``class Foo``
    -> ``{"Foo": <ClassDef>, "Foo.bar": <FunctionDef>}``. This is the address space the
    effect ops operate over.
    """
    out: dict[str, ast.AST] = {}

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, _DEF_TYPES):
                path = f"{prefix}{node.name}"
                out[path] = node
                walk(node.body, path + ".")

    walk(tree.body, "")
    return out


def _parent_body(tree: ast.Module, path: str):
    """Return ``(body_list, leaf_name)`` for where ``path``'s leaf lives.

    ``body_list`` is None when the parent scope does not exist (so an add into a missing
    class fails its precondition rather than crashing).
    """
    parts = path.split(".")
    parent_parts, leaf = parts[:-1], parts[-1]
    if not parent_parts:
        return tree.body, leaf
    parent = units(tree).get(".".join(parent_parts))
    if parent is None or not hasattr(parent, "body"):
        return None, leaf
    return parent.body, leaf


def _names_in_body(body: list[ast.stmt]) -> set[str]:
    """Def/class names plus simple assignment targets declared directly in ``body``."""
    names: set[str] = set()
    for node in body:
        if isinstance(node, _DEF_TYPES):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _toplevel_names(tree: ast.Module) -> set[str]:
    """Names bound at module scope (back-compat helper used by invariants)."""
    return _names_in_body(tree.body)


def _function_units(tree: ast.Module) -> set[str]:
    """Leaf names of every function/method unit anywhere in the module."""
    return {p.split(".")[-1] for p, n in units(tree).items() if isinstance(n, _FUNC_TYPES)}


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


# ---------------------------------------------------------------------------
def precondition_holds(source: str, e: Effect) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    us = units(tree)
    names = _toplevel_names(tree)
    if e.op is EffectOp.ADD_DEF:
        body, leaf = _parent_body(tree, e.target)
        if body is None:
            return False  # parent scope (e.g. the enclosing class) does not exist
        return leaf not in _names_in_body(body)
    if e.op is EffectOp.ADD_IMPORT:
        return e.payload["source"].strip() not in _import_lines(source)
    if e.op is EffectOp.SET_CONST:
        return e.target in names
    if e.op is EffectOp.RENAME_DEF:
        new = e.payload["new"]
        defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        return e.target in defs and new not in names
    if e.op is EffectOp.ADD_CALL:
        target = us.get(e.target)
        return isinstance(target, _FUNC_TYPES) and e.payload.get("callee") in _function_units(tree)
    if e.op is EffectOp.REPLACE_DEF:
        return isinstance(us.get(e.target), _DEF_TYPES)  # can only replace a unit that exists
    if e.op is EffectOp.REMOVE_DEF:
        return e.target in us  # can only remove a unit that exists
    if e.op in STMT_OPS:
        return isinstance(us.get(e.target), _FUNC_TYPES)  # statement ops target a function
    return False


def _ensure_nonempty(node: ast.AST) -> None:
    """A class/function whose body we emptied needs a `pass` to stay parseable."""
    if isinstance(node, (*_DEF_TYPES,)) and not node.body:
        node.body = [ast.Pass()]


def apply_effect(source: str, e: Effect, check: bool = False) -> str:
    from sgt.effects.invariants import invariant_valid  # avoid import cycle

    if not precondition_holds(source, e):
        raise EffectError(f"precondition failed for {e.op.value} {e.target!r} in {e.file}")
    tree = ast.parse(source)
    if e.op is EffectOp.ADD_DEF:
        parsed = ast.parse(e.payload["source"]).body
        if not parsed:
            raise EffectError(f"add_def {e.target!r}: empty source")
        body, leaf = _parent_body(tree, e.target)
        top = parsed[0]
        if isinstance(top, _DEF_TYPES):
            top.name = leaf
        body.extend(parsed)
    elif e.op is EffectOp.ADD_IMPORT:
        imp = ast.parse(e.payload["source"]).body
        tree.body[0:0] = imp  # imports go to the top
    elif e.op is EffectOp.SET_CONST:
        tree = _ConstSetter(e.target, e.payload["value"]).visit(tree)
    elif e.op is EffectOp.RENAME_DEF:
        tree = _Renamer(e.target, e.payload["new"]).visit(tree)
    elif e.op is EffectOp.ADD_CALL:
        target = units(tree)[e.target]
        call = ast.Expr(value=ast.Call(
            func=ast.Name(id=e.payload["callee"], ctx=ast.Load()), args=[], keywords=[]))
        target.body.append(call)
    elif e.op is EffectOp.REPLACE_DEF:
        parsed = ast.parse(e.payload["source"]).body
        if not parsed:
            raise EffectError(f"replace_def {e.target!r}: empty source")
        old = units(tree)[e.target]
        parent_body, leaf = _parent_body(tree, e.target)
        top = parsed[0]
        if isinstance(top, _DEF_TYPES):
            top.name = leaf  # keep the unit's identity; only body/signature change
        parent_body[:] = [top if n is old else n for n in parent_body]
    elif e.op is EffectOp.REMOVE_DEF:
        old = units(tree)[e.target]
        parent_body, _ = _parent_body(tree, e.target)
        parent_body[:] = [n for n in parent_body if n is not old]
        parent_parts = e.target.split(".")[:-1]
        if parent_parts:
            _ensure_nonempty(units(tree).get(".".join(parent_parts)))
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


def _func_body_src(def_source: str) -> str:
    """The body of a ``def`` source as joined statement text (the seed for a StatementSeq)."""
    node = ast.parse(def_source).body[0]
    return "\n".join(ast.unparse(s) for s in getattr(node, "body", []))


def build_statement_seq(defining: "Effect", ops: list) -> "StatementSeq":
    """Reconstruct a function's live ``StatementSeq``: seed from the defining effect, replay ops.

    The single reconstruction path shared by materialization (``_apply_stmt_ops``) and
    statement-aware distillation (``sgt/effects/stmt_distill.py``). Both **must** agree on a
    function's PosIds, or a distilled ``replace_stmt`` would address a slot materialize never
    created. ``ops`` must be in the canonical replay order (oplog ``order_key``).
    """
    from sgt.effects.body import StatementSeq
    from sgt.effects.stmt import PosId
    from sgt.store.replica import ReplicaIdentity

    rid, ctr = ReplicaIdentity.parse(defining.eid)
    seq = StatementSeq.from_source(_func_body_src(defining.payload["source"]), rid, ctr)
    for e in ops:
        erid, ectr = ReplicaIdentity.parse(e.eid)
        if e.op is EffectOp.REPLACE_STMT:
            seq.replace(PosId.from_dict(e.payload["pos"]), e.payload["source"], erid, ectr)
        elif e.op is EffectOp.REMOVE_STMT:
            seq.remove(PosId.from_dict(e.payload["pos"]))
        else:  # INSERT_STMT
            after = PosId.from_dict(e.payload["after"]) if e.payload.get("after") else None
            before = PosId.from_dict(e.payload["before"]) if e.payload.get("before") else None
            seq.insert(after, before, e.payload["source"], erid, ectr)
    return seq


def _apply_stmt_ops(cb: Codebase, core: list, stmt_ops: list) -> Codebase:
    """Rebuild statement-managed function bodies by replaying their statement ops.

    Identity is **log-resident**: a function's original statements are seeded with PosIds
    derived from its *defining effect's* eid + index (both live in the log), never recomputed
    from the current text — so an inserted statement keeps its identity across commits and two
    replicas converge. Each statement op carries its target PosId, so edits to distinct
    statements of one function are independent.
    """
    from collections import defaultdict

    defining: dict[tuple[str, str], "Effect"] = {}
    for e in core:
        if e.op in (EffectOp.ADD_DEF, EffectOp.REPLACE_DEF):
            defining[(e.file, e.target)] = e  # last writer wins → current body

    by_func: dict[tuple[str, str], list] = defaultdict(list)
    for e in stmt_ops:
        by_func[(e.file, e.target)].append(e)

    for (file, func), ops in by_func.items():
        d = defining.get((file, func))
        if d is None:
            raise EffectError(f"statement op targets undefined function {func!r} in {file}")
        seq = build_statement_seq(d, ops)

        tree = ast.parse(cb[file])
        node = units(tree).get(func)
        if not isinstance(node, _FUNC_TYPES):
            raise EffectError(f"statement op target {func!r} is not a function in {file}")
        new_body: list[ast.stmt] = []
        for slot in seq.ordered():
            new_body.extend(ast.parse(slot.source).body)
        node.body = new_body or [ast.Pass()]
        ast.fix_missing_locations(tree)
        cb[file] = ast.unparse(tree)
    return cb


def materialize(effects, base: Codebase | None = None, check: bool = False) -> Codebase:
    """Replay an ordered list of effects into a codebase, grouped by file.

    `base` is the starting content per file (default empty). This is the canonical
    state function: the working tree is exactly the replay of the active effects.

    Statement-granular ops are applied structurally *after* the text ops, by replaying each
    managed function's statement ops into a PosId-keyed sequence (``_apply_stmt_ops``), so
    edits to distinct statements of one function are order-independent.
    """
    cb: Codebase = dict(base or {})
    core = [e for e in effects if e.op not in STMT_OPS]
    stmt_ops = [e for e in effects if e.op in STMT_OPS]
    for e in core:
        cb.setdefault(e.file, "")
        cb[e.file] = apply_effect(cb[e.file], e, check=check)
    if stmt_ops:
        cb = _apply_stmt_ops(cb, core, stmt_ops)
    return cb
