"""Semantic blame: map each line of a materialized file to the node that owns it.

This is the in-situ ``git blame`` analogue, but per *feature* rather than per commit. The
working tree is the replay of the active effects (``project.materialize``); every effect
carries an ``eid`` that the log binds to a node, and every statement slot carries the
``(author, counter)`` of the edit that last set it. So a rendered line's owner is fully
recoverable from the log — we never *infer* authorship from a text diff.

Attribution is computed against the **same** materialized text the editor shows, and at the
finest granularity the data cleanly supports:

* module-level ``import`` / ``NAME = const`` lines -> the effect that authored them;
* a def/class unit -> the effect that last defined it (``add_def`` / ``replace_def``), with
  nested units (methods) overriding their enclosing class (innermost wins);
* inside a statement-managed function body -> each statement maps to its slot's owner
  (a seed statement to the function's definer; an inserted/replaced statement to the node
  of the op that set it), reusing the one ``build_statement_seq`` reconstruction path that
  materialization itself uses, so blame and the rendered tree can never disagree.

Lines no effect claims (blank separators) stay unattributed (``node_id=None``); a UI renders
nothing for them. Coarser attribution (whole-method inside a class, a reordered statement that
lost its PosId) is surfaced honestly as whole-unit blame rather than faked line precision.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass

from sgt.effects.model import (
    STMT_OPS,
    EffectOp,
    _FUNC_TYPES,
    build_statement_seq,
    materialize,
    units,
)
from sgt.store.graph import NodeStatus
from sgt.store.replica import ReplicaIdentity


@dataclass(frozen=True)
class Span:
    """A contiguous run of lines ``[start, end]`` (1-based, inclusive) owned by ``node_id``."""

    start: int
    end: int
    node_id: str | None

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "node_id": self.node_id}


def _active_entries(project):
    """Active, non-tombstoned log entries in the canonical materialization order.

    Mirrors ``Project.active_effects`` exactly (same admission + ``order_key`` sort) so the
    effect list we attribute over is byte-for-byte the one that produced ``materialize``.
    """
    active = {
        nid
        for nid in project.log.node_ids()
        if project.graph.has(nid) and project.graph.get(nid).status is NodeStatus.ACTIVE
    }
    return sorted(project.log.live_entries(active), key=lambda e: e.order_key)


def _normalize_import(src: str) -> str:
    """Canonical single-line form of an import statement, matching ``ast.unparse`` output."""
    try:
        return ast.unparse(ast.parse(src).body[0]).strip()
    except (SyntaxError, IndexError):
        return src.strip()


def _stmt_count(source: str) -> int:
    """How many top-level statements a slot's source expands to (materialize extends body by these)."""
    try:
        return max(1, len(ast.parse(source).body))
    except SyntaxError:
        return 1


def attribute(project) -> dict[str, list[Span]]:
    """Return, per materialized file, the line ranges and the node that owns each."""
    entries = _active_entries(project)
    effects = [e.effect for e in entries]
    eid_node = {e.eid: e.node_id for e in entries}
    # (author, counter) -> node, for resolving a statement slot's LWW edit identity.
    ac_node: dict[tuple[str, int], str] = {}
    for e in entries:
        ac_node[ReplicaIdentity.parse(e.effect.eid)] = e.node_id

    cb = materialize(effects)

    core = [e for e in effects if e.op not in STMT_OPS]
    stmt_ops = [e for e in effects if e.op in STMT_OPS]

    # Last definer (add_def/replace_def) per (file, target) — the node that owns a unit.
    definer: dict[tuple[str, str], str | None] = {}
    for e in core:
        if e.op in (EffectOp.ADD_DEF, EffectOp.REPLACE_DEF):
            definer[(e.file, e.target)] = eid_node.get(e.eid)

    # Module-level import / const authorship.
    import_owner: dict[tuple[str, str], str | None] = {}
    const_owner: dict[tuple[str, str], str | None] = {}
    for e in core:
        if e.op is EffectOp.ADD_IMPORT:
            import_owner[(e.file, _normalize_import(e.payload.get("source", "")))] = eid_node.get(e.eid)
        elif e.op in (EffectOp.SET_CONST, EffectOp.ADD_ASSIGN, EffectOp.REPLACE_ASSIGN):
            const_owner[(e.file, e.target)] = eid_node.get(e.eid)  # module-level name -> owner

    # Slot owners per statement-managed function: a (seq, owners) pair, owners aligned with
    # seq.ordered().
    defining_effect: dict[tuple[str, str], object] = {}
    for e in core:
        if e.op in (EffectOp.ADD_DEF, EffectOp.REPLACE_DEF):
            defining_effect[(e.file, e.target)] = e
    ops_by_func: dict[tuple[str, str], list] = defaultdict(list)
    for e in stmt_ops:
        ops_by_func[(e.file, e.target)].append(e)

    slot_owners: dict[tuple[str, str], tuple[object, list[str | None]]] = {}
    for key, ops in ops_by_func.items():
        d = defining_effect.get(key)
        if d is None:
            continue
        seq = build_statement_seq(d, ops)
        owners: list[str | None] = []
        for slot in seq.ordered():
            if slot.edit_author or slot.edit_counter != -1:
                owners.append(ac_node.get((slot.edit_author, slot.edit_counter)))
            else:  # seed statement -> the function's definer
                owners.append(definer.get(key))
        slot_owners[key] = (seq, owners)

    out: dict[str, list[Span]] = {}
    for file, src in cb.items():
        owner = _attribute_file(file, src, definer, import_owner, const_owner, slot_owners)
        out[file] = _coalesce(owner)
    return out


def _attribute_file(file, src, definer, import_owner, const_owner, slot_owners) -> list[str | None]:
    """Return a 1-based per-line owner array (index 0 unused) for one file."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [None] * (len(src.splitlines()) + 1)
    n_lines = len(src.splitlines())
    owner: list[str | None] = [None] * (n_lines + 1)

    # Units (defs/classes), outermost first so nested defs override their container's lines.
    unit_map = units(tree)
    for path in sorted(unit_map, key=lambda p: (unit_map[p].lineno, -_end(unit_map[p]))):
        node = unit_map[path]
        who = definer.get((file, path))
        for ln in range(node.lineno, _end(node) + 1):
            if 1 <= ln <= n_lines:
                owner[ln] = who

    # Inside statement-managed functions, override body statements with their slot owners.
    for path, node in unit_map.items():
        key = (file, path)
        if not isinstance(node, _FUNC_TYPES) or key not in slot_owners:
            continue
        seq, owners = slot_owners[key]
        slots = seq.ordered()
        body_idx = 0
        body = list(node.body)
        for slot, who in zip(slots, owners):
            k = _stmt_count(slot.source)
            for _ in range(k):
                if body_idx >= len(body):
                    break
                stmt = body[body_idx]
                for ln in range(stmt.lineno, _end(stmt) + 1):
                    if 1 <= ln <= n_lines:
                        owner[ln] = who
                body_idx += 1

    # Module-level imports and constants.
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            who = import_owner.get((file, _normalize_import(ast.unparse(stmt))))
            if who is not None:
                for ln in range(stmt.lineno, _end(stmt) + 1):
                    if 1 <= ln <= n_lines:
                        owner[ln] = who
        elif isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and (file, t.id) in const_owner:
                    who = const_owner[(file, t.id)]
                    for ln in range(stmt.lineno, _end(stmt) + 1):
                        if 1 <= ln <= n_lines:
                            owner[ln] = who
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if (file, stmt.target.id) in const_owner:
                who = const_owner[(file, stmt.target.id)]
                for ln in range(stmt.lineno, _end(stmt) + 1):
                    if 1 <= ln <= n_lines:
                        owner[ln] = who
    return owner


def _end(node: ast.AST) -> int:
    """``end_lineno`` (per docs it is optional and may be None) with a safe fallback."""
    return getattr(node, "end_lineno", None) or getattr(node, "lineno", 1)


def _coalesce(owner: list[str | None]) -> list[Span]:
    """Collapse a per-line owner array into contiguous ``Span`` runs (including None runs)."""
    spans: list[Span] = []
    n = len(owner) - 1
    ln = 1
    while ln <= n:
        who = owner[ln]
        start = ln
        while ln + 1 <= n and owner[ln + 1] == who:
            ln += 1
        spans.append(Span(start, ln, who))
        ln += 1
    return spans
