"""Reverse distillation: turn an out-of-band source change into typed effects.

This is the ``tree -> effects`` direction that makes the working tree and the effect
log a two-way relationship instead of a one-way projection. Given what `sgt` *expected*
a file to contain (the replay of its effects) and what is *actually* on disk (a human
edit, another agent, a direct ``git`` commit), it produces the `add_def` / `replace_def`
/ `remove_def` / `add_import` effects that transform expected into actual. A def removed
under one name and re-added with a near-identical body is recognized as a `rename_def`
(git-style rename detection) rather than a delete+add, so a refactor evolves a feature in
place instead of orphaning its node.

Pure and deterministic (no LLM): it answers *what* changed at top-level-unit granularity.
Clustering those changes into coherent features and labelling them is the LLM's job
(`sgt/agents/distill.py`). Changes it cannot express as typed effects (unparseable
source, module-level executable code) are returned as human-readable notes, never
silently dropped (origin R24).
"""

from __future__ import annotations

import ast
from difflib import SequenceMatcher

from sgt.effects.invariants import normalize
from sgt.effects.model import Codebase, Effect

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# Two functions whose name-blind bodies are this similar are treated as the same unit
# renamed (git-style rename detection). Below it, they are unrelated add/remove.
_RENAME_SIMILARITY = 0.8


def _toplevel_defs(tree: ast.Module) -> dict[str, ast.AST]:
    return {n.name: n for n in tree.body if isinstance(n, _DEF_TYPES)}


def _nameblind(node: ast.AST) -> str:
    """Normalized source of a def with its own name blanked, so a pure rename compares equal.

    Only the unit's own name is masked — call sites inside the body keep their identifiers —
    so a rename that also rewrites recursion still scores high without being mistaken for an
    unrelated function."""
    clone = ast.parse(ast.unparse(node)).body[0]
    clone.name = "_"
    return normalize(ast.unparse(clone))


def _detect_renames(
    exp_defs: dict[str, ast.AST], act_defs: dict[str, ast.AST],
) -> list[tuple[str, str, bool]]:
    """Pair removed↔added top-level *functions* by body similarity.

    Returns ``(old, new, body_changed)`` per confident one-to-one match. Greedy by descending
    similarity so the best pairing wins and each side is consumed once. Restricted to top-level
    sync ``def``s — the ``rename_def`` op renames those (not classes or ``async def``)."""
    removed = {n: v for n, v in exp_defs.items()
               if n not in act_defs and type(v) is ast.FunctionDef}
    added = {n: v for n, v in act_defs.items()
             if n not in exp_defs and type(v) is ast.FunctionDef}
    if not removed or not added:
        return []
    blind = {n: _nameblind(v) for n, v in {**removed, **added}.items()}
    scored = []
    for old in removed:
        for new in added:
            ratio = SequenceMatcher(None, blind[old], blind[new]).ratio()
            if ratio >= _RENAME_SIMILARITY:
                scored.append((ratio, old, new))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))  # deterministic: best first, then by name
    used_old: set[str] = set()
    used_new: set[str] = set()
    matches: list[tuple[str, str, bool]] = []
    for ratio, old, new in scored:
        if old in used_old or new in used_new:
            continue
        used_old.add(old)
        used_new.add(new)
        matches.append((old, new, ratio < 1.0))
    return matches


def _import_set(tree: ast.Module) -> set[str]:
    return {ast.unparse(n).strip() for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))}


def _other_toplevel(tree: ast.Module) -> str:
    """Normalized dump of top-level statements that are neither defs nor imports."""
    others = [n for n in tree.body
              if not isinstance(n, (*_DEF_TYPES, ast.Import, ast.ImportFrom))]
    return "\n".join(normalize(ast.unparse(n)) for n in others)


def distill_file(file: str, expected_src: str, actual_src: str) -> tuple[list[Effect], list[str]]:
    """Effects that turn ``expected_src`` into ``actual_src`` for one file, plus notes."""
    effects: list[Effect] = []
    notes: list[str] = []
    try:
        act = ast.parse(actual_src)
    except SyntaxError as ex:
        return [], [f"{file}: does not parse ({ex.msg}) — cannot distill, left as-is"]
    exp = ast.parse(expected_src or "")

    exp_defs, act_defs = _toplevel_defs(exp), _toplevel_defs(act)

    # A def removed under one name and re-added under another with a near-identical body is a
    # rename, not a delete+add — model it as one `rename_def` so the feature evolves in place
    # (its node keeps identity and reverting it restores the original name) instead of leaving a
    # zombie node + a spurious new one.
    renames = _detect_renames(exp_defs, act_defs)
    renamed_old = {old for old, _, _ in renames}
    renamed_new = {new for _, new, _ in renames}
    for old, new, body_changed in renames:
        effects.append(Effect.rename_def(file, old, new))
        if body_changed:
            effects.append(Effect.replace_def(file, new, ast.unparse(act_defs[new])))

    for name, node in act_defs.items():
        if name in renamed_new:
            continue  # handled as the target of a rename above
        src = ast.unparse(node)
        if name not in exp_defs:
            effects.append(Effect.add_def(file, name, src))
        elif normalize(src) != normalize(ast.unparse(exp_defs[name])):
            effects.append(Effect.replace_def(file, name, src))
    for name in exp_defs:
        if name not in act_defs and name not in renamed_old:
            effects.append(Effect.remove_def(file, name))

    for line in _import_set(act) - _import_set(exp):
        effects.append(Effect.add_import(file, line))
    removed_imports = _import_set(exp) - _import_set(act)
    if removed_imports:
        notes.append(f"{file}: removed imports not auto-distilled: {', '.join(sorted(removed_imports))}")

    if _other_toplevel(exp) != _other_toplevel(act):
        notes.append(f"{file}: module-level statements changed — not auto-distilled (review manually)")

    return effects, notes


def distill_codebase(expected: Codebase, actual: Codebase) -> tuple[list[Effect], list[str]]:
    """Distill every drifted/new/removed file across the codebase."""
    effects: list[Effect] = []
    notes: list[str] = []
    for file in sorted(set(expected) | set(actual)):
        exp_src = expected.get(file, "")
        if file not in actual:
            # the file was deleted on disk; remove every unit the graph put there
            for name in _toplevel_defs(ast.parse(exp_src or "")):
                effects.append(Effect.remove_def(file, name))
            continue
        fx, ns = distill_file(file, exp_src, actual[file])
        effects.extend(fx)
        notes.extend(ns)
    return effects, notes


def files_differ(expected_src: str, actual_src: str) -> bool:
    """Cheap drift predicate that ignores formatting (AST round-trip equality)."""
    return normalize(expected_src or "") != normalize(actual_src or "")
