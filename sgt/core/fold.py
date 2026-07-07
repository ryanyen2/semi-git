"""The fold: total, deterministic materialization (ADR S3.4/S3.5; plan R3, R6, R7).

`code(ideal, ops)` splices, for each symbol, the verbatim after-image of the ideal's maximal
op -- byte-exact, never `ast.unparse` (the plan's byte-splicing KTD exists specifically to kill
the formatting-loss regression class that produces). Three folds compose per file:

    - entities: only *top-level* entities (dot-free names) are spliced directly. A class's own
      captured span already contains its current methods' bytes (containment means byte
      overlap: any change inside a method also changes its enclosing class's content_hash, so
      the class always gets its own fresh touch too), so nested (dotted-name) entities exist
      for identity/reference/blame, not for independent materialization -- splicing both would
      duplicate content.
    - layout: entities are ordered by their anchor facts (which entity precedes which, R6) via
      a DFS placement, so an anchor's "children" (things inserted after it) stay contiguous.
      An entity with no recorded anchor (or whose recorded predecessor isn't live) falls back
      to the end, sorted by name, so the fold stays total even for unusual ideals.
    - imports: derived, not versioned (R6) -- the union of `requires` of this file's live
      entities that name a symbol hosted by a *different*, still-live file, rendered as
      `from <module> import <name>` lines. Reverting the sole consumer of an import removes
      that line for free, since it's recomputed from what's actually live, never stored.

Residue is rendered verbatim, before the derived-imports block: it's opaque (never reparsed),
so whatever internal order its lines had -- including `from __future__ import ...` first -- is
preserved automatically. Known v1 limitation: residue is one blob per file, position-agnostic
relative to entities, so a file with both leading (imports/constants) and trailing (a
`__main__` guard) residue would render both together at the top rather than interleaved; the
corpus this ships against doesn't exercise that shape yet.

Whole-file pseudo-symbols (R7: non-parseable paths) bypass all of this -- their image *is* the
file's bytes.
"""

from __future__ import annotations

from sgt.core.ideal import Ideal
from sgt.core.op import BOTTOM, Op, _symbol_kind, is_content_bearing

_ANCHOR_FIRST = "\x00FIRST\x00"  # mirrors sgt.core.mine._ANCHOR_FIRST


def _anchor_target(sym: str) -> str:
    """The entity name an `__anchor__` symbol is *about*."""
    return sym.split("::__anchor__::", 1)[1]


def _module_of(path: str) -> str:
    """A best-effort dotted module name for a repo-relative path -- `a/b.py` -> `a.b`."""
    stem = path[:-3] if path.endswith(".py") else path
    return stem.replace("/", ".")


def _order_entities(names: set[str], anchor_of: dict[str, str | None]) -> list[str]:
    """DFS placement by anchor fact: an entity's "children" (things anchored after it) stay
    contiguous; an entity with no recorded anchor, or whose recorded predecessor isn't live,
    falls back to the end (sorted) so the fold always produces *some* deterministic order."""
    children: dict[str | None, list[str]] = {}
    for name in names:
        children.setdefault(anchor_of.get(name), []).append(name)
    for lst in children.values():
        lst.sort()

    order: list[str] = []
    visited: set[str] = set()

    def visit(pred: str | None) -> None:
        for name in children.get(pred, ()):
            if name in visited or name not in names:
                continue
            visited.add(name)
            order.append(name)
            visit(name)

    visit(None)
    for name in sorted(names):
        if name not in visited:
            order.append(name)
    return order


def _derived_imports(
    path: str, live_entity_syms: set[str], by_id: dict[str, Op], tip: dict[str, str]
) -> bytes:
    """R6: `requires` of this file's live entities that name a symbol hosted by a different,
    still-live file, rendered as sorted `from <module> import <name>` lines."""
    by_foreign_file: dict[str, set[str]] = {}
    for sym in live_entity_syms:
        op = by_id[tip[sym]]
        for req_sym, _req_version in op.requires:
            req_path, _, req_name = req_sym.partition("::")
            if req_path == path or _symbol_kind(req_sym) not in ("entity", "nested"):
                continue
            req_tip_op_id = tip.get(req_sym)
            if req_tip_op_id is None:
                continue  # the required symbol isn't part of this ideal at all
            if by_id[req_tip_op_id].footprint[req_sym][1] == BOTTOM:
                continue  # the required symbol has since been removed
            top_name = req_name.split(".", 1)[0]  # import the top-level owner, not a bare method
            by_foreign_file.setdefault(req_path, set()).add(top_name)

    lines = [
        f"from {_module_of(fp)} import {', '.join(sorted(names))}"
        for fp, names in sorted(by_foreign_file.items())
    ]
    return "\n".join(lines).encode("utf-8")


def _fold_file(path: str, symbols: dict[str, str], by_id: dict[str, Op], tip: dict[str, str]) -> bytes:
    whole_file_op = symbols.get(path)
    if whole_file_op is not None:
        return by_id[whole_file_op].images[path] or b""

    anchor_of: dict[str, str | None] = {}
    entity_names: set[str] = set()
    entity_images: dict[str, bytes] = {}
    residue_bytes = b""

    for sym, op_id in symbols.items():
        kind = _symbol_kind(sym)
        if kind == "anchor":
            name = _anchor_target(sym)
            marker = (by_id[op_id].images[sym] or b"").decode("utf-8")
            anchor_of[name] = None if marker == _ANCHOR_FIRST else marker
        elif kind == "entity":
            _, _, name = sym.partition("::")
            entity_names.add(name)
            entity_images[name] = by_id[op_id].images[sym] or b""
        elif kind == "residue":
            residue_bytes = by_id[op_id].images[sym] or b""
        # "nested" entities contribute nothing directly -- already subsumed by their
        # containing top-level entity's own image.

    live_entity_syms = {f"{path}::{name}" for name in entity_names}
    imports_bytes = _derived_imports(path, live_entity_syms, by_id, tip)

    order = _order_entities(entity_names, anchor_of)
    parts = [p for p in (residue_bytes, imports_bytes, *(entity_images[n] for n in order)) if p]
    return b"\n\n\n".join(parts) + (b"\n" if parts else b"")


def code(ideal: Ideal, ops: list[Op]) -> dict[str, bytes]:
    """Total, deterministic materialization at entity granularity (R3): every ideal
    materializes; there is no quarantine, no confluence gate, no gated rung."""
    by_id = {op.id: op for op in ops}
    tip = ideal.frontier(ops)

    by_path: dict[str, dict[str, str]] = {}  # path -> {symbol: op_id}, live symbols only
    content_paths: set[str] = set()  # paths with >=1 live content-bearing symbol -- the rest are
    # anchor-only leftovers of a fully-pruned file and don't materialize (R7); matches covered_paths
    for sym, op_id in tip.items():
        after = by_id[op_id].footprint[sym][1]
        if after == BOTTOM:
            continue
        path = sym.split("::", 1)[0]
        by_path.setdefault(path, {})[sym] = op_id  # anchors stay in a live path's set for ordering
        if is_content_bearing(sym):
            content_paths.add(path)

    return {
        path: _fold_file(path, symbols, by_id, tip)
        for path, symbols in by_path.items()
        if path in content_paths
    }
