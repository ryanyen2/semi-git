"""Statement-aware distillation: refine a whole-unit body rewrite into statement ops.

`sgt/effects/diff.py` answers *what file changed* at top-level-unit granularity — a changed
function body becomes a single ``replace_def``. That is too coarse for merge: two users (each
running their own file-editing agent — Gemini, Cursor, Claude Code, Codex, …) editing *different
statements* of one function would both produce ``replace_def`` and needlessly collide.

This module refines such a ``replace_def`` into ``insert_stmt``/``replace_stmt``/``remove_stmt``
ops addressed to the function's **log-resident** statement identities (PosIds), so the merge
engine sees statement granularity: distinct-statement edits commute and both land (EC5);
same-statement edits surface as a conflict (EC6). It is the agent-agnostic on-ramp — every
file-editing agent converges on the same reconcile path, and the merge guarantees ride on the
log, not on which agent produced the edit.

Two pieces:
* ``diff_statements`` — pure alignment of a reconstructed ``StatementSeq`` to the on-disk body.
* ``promote_body_rewrites`` — log-aware: looks up each function's defining effect + existing
  stmt ops, reconstructs its live sequence, and swaps a qualifying ``replace_def`` for stmt ops.

Scope (see docs/design/2026-06-18-statement-aware-distill.md): top-level functions only; a
signature change falls back to a whole-unit ``replace_def`` (and is noted).
"""

from __future__ import annotations

import ast
import copy

from sgt.effects.body import StatementSeq
from sgt.effects.invariants import normalize
from sgt.effects.model import (
    Codebase,
    Effect,
    EffectOp,
    _FUNC_TYPES,
    build_statement_seq,
    units,
)


# ---------------------------------------------------------------------------
# Pure alignment
# ---------------------------------------------------------------------------
def _lcs_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Indices of a longest common subsequence as increasing ``(i, j)`` pairs."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def diff_statements(live_seq: StatementSeq, actual_body_src: str, file: str, func: str) -> list[Effect]:
    """Stmt ops transforming ``live_seq`` (expected, with identity) into the actual body.

    Changed statements reuse the live slot's PosId (``replace_stmt``) so the merge engine can
    detect a same-statement conflict; insertions get a fresh PosId at materialize time, allocated
    between the surrounding kept positions.
    """
    live = live_seq.ordered()                       # [Slot] in body order
    live_norm = [normalize(s.source) for s in live]
    actual_stmts = [ast.unparse(n) for n in ast.parse(actual_body_src or "").body]
    actual_norm = [normalize(s) for s in actual_stmts]

    matches = _lcs_pairs(live_norm, actual_norm)
    effects: list[Effect] = []
    prev_i, prev_j = -1, -1
    # walk each gap between consecutive matched anchors, plus the trailing gap
    for i, j in [*matches, (len(live), len(actual_stmts))]:
        old_gap = live[prev_i + 1:i]                # unmatched live slots in this gap
        new_gap = actual_stmts[prev_j + 1:j]        # unmatched actual statements in this gap
        k = 0
        # pair changed statements positionally → replace at the existing PosId (identity reuse)
        while k < len(old_gap) and k < len(new_gap):
            effects.append(Effect.replace_stmt(file, func, old_gap[k].pos.to_dict(), new_gap[k]))
            k += 1
        for slot in old_gap[k:]:                    # surplus old → tombstone
            effects.append(Effect.remove_stmt(file, func, slot.pos.to_dict()))
        # surplus new → insert between the last kept/reused position and the next anchor
        after = old_gap[k - 1].pos if k > 0 else (live[prev_i].pos if prev_i >= 0 else None)
        before = live[i].pos if i < len(live) else None
        for src in new_gap[k:]:
            effects.append(Effect.insert_stmt(
                file, func,
                after.to_dict() if after is not None else None,
                before.to_dict() if before is not None else None,
                src,
            ))
        prev_i, prev_j = i, j
    return effects


# ---------------------------------------------------------------------------
# Log-aware promotion
# ---------------------------------------------------------------------------
def _func_node(source: str, name: str):
    """The top-level function node named ``name`` in ``source``, or None."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return None
    node = units(tree).get(name)
    return node if isinstance(node, _FUNC_TYPES) else None


def _sig_repr(node: ast.AST) -> str:
    """The function's header (name/args/decorators/returns) with its body blanked to ``pass``."""
    clone = copy.deepcopy(node)
    clone.body = [ast.Pass()]
    return ast.unparse(clone)


def _last_defining(active: list[Effect], file: str, func: str) -> Effect | None:
    """The effect materialize would treat as ``func``'s body source: the last add/replace_def."""
    found: Effect | None = None
    for e in active:
        if e.file == file and e.target == func and e.op in (EffectOp.ADD_DEF, EffectOp.REPLACE_DEF):
            found = e  # active is in canonical order → last writer wins, mirroring _apply_stmt_ops
    return found


def _stmt_ops_for(active: list[Effect], file: str, func: str) -> list[Effect]:
    """Existing statement ops for ``func`` in canonical order (as materialize replays them)."""
    return [e for e in active if e.file == file and e.target == func and e.op in
            (EffectOp.INSERT_STMT, EffectOp.REPLACE_STMT, EffectOp.REMOVE_STMT)]


def promote_body_rewrites(
    active: list[Effect], coarse: list[Effect], actual: Codebase,
) -> tuple[list[Effect], list[str]]:
    """Refine top-level-function ``replace_def``s in ``coarse`` into statement ops.

    ``active`` are the project's active effects (eid-carrying, canonical order) — the source of
    each function's defining effect and existing stmt ops. ``actual`` is the on-disk codebase.
    A ``replace_def`` is refined iff its target is a top-level function with a known defining
    effect and an **unchanged signature**; otherwise it passes through (and a signature change is
    noted). Non-``replace_def`` effects pass through untouched.
    """
    out: list[Effect] = []
    notes: list[str] = []
    for e in coarse:
        if e.op is not EffectOp.REPLACE_DEF or "." in e.target:
            out.append(e)
            continue
        defining = _last_defining(active, e.file, e.target)
        actual_node = _func_node(actual.get(e.file, ""), e.target)
        if defining is None or actual_node is None:
            out.append(e)                          # D1/no-identity or not a function → whole-unit
            continue
        try:
            def_node = ast.parse(defining.payload["source"]).body[0]
        except (SyntaxError, IndexError):
            out.append(e)
            continue
        if not isinstance(def_node, _FUNC_TYPES):
            out.append(e)
            continue
        if _sig_repr(def_node) != _sig_repr(actual_node):
            out.append(e)                          # D3: signature changed → whole-unit fallback
            notes.append(f"{e.file}:{e.target} signature changed — kept as whole-unit replace "
                         "(statement identity reset)")
            continue
        live = build_statement_seq(defining, _stmt_ops_for(active, e.file, e.target))
        actual_body = "\n".join(ast.unparse(s) for s in actual_node.body)
        stmt_ops = diff_statements(live, actual_body, e.file, e.target)
        out.extend(stmt_ops if stmt_ops else [e])  # empty alignment shouldn't happen; keep safe
    return out, notes
