"""The fold: total, deterministic materialization (ADR S3.4/S3.5; plan R3, R6, R7).

`code(ideal, ops)` splices, for each symbol, the verbatim after-image of the ideal's maximal
op -- byte-exact, never `ast.unparse` (the plan's byte-splicing KTD exists specifically to kill
the formatting-loss regression class that produces). A file folds to a pure verbatim
concatenation of its live segments in document order, with **zero synthesized bytes** -- no
separator, no trailing newline, no derived content is ever inserted (kernel byte-fidelity
audit, 2026-07-08: any synthesized byte is a place a real file's exact bytes could stop
round-tripping):

    - entities: only *top-level* entities (dot-free names) are spliced directly. A class's own
      captured span already contains its current methods' bytes (containment means byte
      overlap: any change inside a method also changes its enclosing class's content_hash, so
      the class always gets its own fresh touch too), so nested (dotted-name) entities exist
      for identity/reference/blame, not for independent materialization -- splicing both would
      duplicate content.
    - imports: top-level import statements, spliced exactly like entities and ordered by the
      same anchor facts (design 2026-08-27). Promoting them out of residue is what makes a single
      import removable; it adds no synthesized bytes, because a module-level statement cannot
      overlap a definition's span, so the partition stays verbatim.
    - residue: **positional**, not one blob per file. One segment per gap between top-level
      entities, keyed by the name of the entity immediately preceding it (a HEAD sentinel for
      the gap before the first entity, or the whole file when it has none). Concatenating every
      top-level entity in anchor order together with its trailing residue segment reconstructs
      the file exactly -- a verbatim byte partition of the original, which is what makes
      imports, module docstrings, blank lines, comments, and a trailing `__main__` guard all
      round-trip regardless of where they sit.
    - layout: entities are ordered by their anchor facts (which entity precedes which, R6) via
      a DFS placement, so an anchor's "children" (things inserted after it) stay contiguous.
      An entity with no recorded anchor (or whose recorded predecessor isn't live) falls back
      to the end, sorted by name, so the fold stays total even for unusual ideals. A residue
      segment whose own anchor entity isn't live in this ideal (e.g. a partial revert) is never
      dropped silently either -- it renders at the end, sorted, rather than vanishing.

Imports are not derived or pruned here (a deliberate change from an earlier design, R6
deviation D3): once the fold is pure verbatim splicing, it cannot also rewrite an import block
without breaking exactly the byte-fidelity this module exists to guarantee -- and auto-deriving
never worked for calls inside methods anyway (their `requires` attach to the method symbol, not
the file-level entity list). An import is just residue now; reverting its only consumer leaves
it exactly where it was, byte for byte. Surfacing "this revert leaves an unused import" is a
verb-layer preview concern (reference edges already carry the information), not the fold's.

Whole-file pseudo-symbols (R7: non-parseable paths) bypass all of this -- their image *is* the
file's bytes.
"""

from __future__ import annotations

from sgt.core.ideal import Ideal
from sgt.core.op import Op, _symbol_kind, is_bottom, is_content_bearing

_ANCHOR_FIRST = "\x00FIRST\x00"  # mirrors sgt.core.mine._ANCHOR_FIRST
_RESIDUE_HEAD = "\x00HEAD\x00"  # mirrors sgt.core.mine._RESIDUE_HEAD


def _anchor_target(sym: str) -> str:
    """The entity name an `__anchor__` symbol is *about*."""
    return sym.split("::__anchor__::", 1)[1]


def _residue_anchor(sym: str) -> str:
    """The entity name (or `_RESIDUE_HEAD`) a `__residue__` symbol's trailing gap follows."""
    return sym.split("::__residue__::", 1)[1]


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


def _fold_file(path: str, symbols: dict[str, str], by_id: dict[str, Op]) -> bytes:
    whole_file_op = symbols.get(path)
    if whole_file_op is not None:
        return by_id[whole_file_op].images[path] or b""

    anchor_of: dict[str, str | None] = {}
    entity_names: set[str] = set()
    entity_images: dict[str, bytes] = {}
    residue_of: dict[str, bytes] = {}

    for sym, op_id in symbols.items():
        kind = _symbol_kind(sym)
        if kind == "anchor":
            name = _anchor_target(sym)
            marker = (by_id[op_id].images[sym] or b"").decode("utf-8")
            anchor_of[name] = None if marker == _ANCHOR_FIRST else marker
        elif kind in ("entity", "import"):
            _, _, name = sym.partition("::")
            entity_names.add(name)
            entity_images[name] = by_id[op_id].images[sym] or b""
        elif kind == "residue":
            residue_of[_residue_anchor(sym)] = by_id[op_id].images[sym] or b""
        # "nested" entities contribute nothing directly -- already subsumed by their
        # containing top-level entity's own image.

    order = _order_entities(entity_names, anchor_of)

    parts: list[bytes] = []
    head = residue_of.pop(_RESIDUE_HEAD, None)
    if head:
        parts.append(head)
    for name in order:
        parts.append(entity_images[name])
        gap = residue_of.pop(name, None)
        if gap:
            parts.append(gap)
    # Orphaned residue: its anchor entity isn't live in this ideal (e.g. a partial revert).
    # Never dropped silently -- appended at the end, sorted, so the fold stays deterministic.
    for name in sorted(residue_of):
        if residue_of[name]:
            parts.append(residue_of[name])

    return b"".join(parts)


def code(ideal: Ideal, ops: list[Op], only_paths: "set[str] | None" = None) -> dict[str, bytes]:
    """Total, deterministic materialization at entity granularity (R3): every ideal
    materializes; there is no quarantine, no confluence gate, no gated rung.

    `only_paths`, if given, restricts the fold to those paths -- for a caller that will only read
    a known handful of entries (a backstop check on the paths a delete would touch) and whose
    `ops` may carry images for exactly those paths' frontier producers
    (`lens.ops_with_frontier_images(for_paths=...)`). Folding an unfetched path there would
    silently produce zero-length content, so the restriction is correctness, not just speed."""
    by_id = {op.id: op for op in ops}
    tip = ideal.frontier(ops)

    by_path: dict[str, dict[str, str]] = {}  # path -> {symbol: op_id}, live symbols only
    content_paths: set[str] = set()  # paths with >=1 live content-bearing symbol -- the rest are
    # anchor-only leftovers of a fully-pruned file and don't materialize (R7); matches covered_paths
    for sym, op_id in tip.items():
        after = by_id[op_id].footprint[sym][1]
        if is_bottom(after):
            continue
        path = sym.split("::", 1)[0]
        if only_paths is not None and path not in only_paths:
            continue
        by_path.setdefault(path, {})[sym] = op_id  # anchors stay in a live path's set for ordering
        if is_content_bearing(sym):
            content_paths.add(path)

    return {
        path: _fold_file(path, symbols, by_id)
        for path, symbols in by_path.items()
        if path in content_paths
    }
