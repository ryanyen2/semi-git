"""The fold: `code(ideal, ops)` turns an ideal into file bytes, for every ideal.

A file is the verbatim concatenation of its live segments with no byte synthesized: the head
residue, then each top-level entity or import in anchor order (which one follows which), each
followed by the residue gap after it. Nested entities add nothing, since their bytes lie inside
their top-level entity. A segment whose anchor is not live goes at the end, sorted. A whole-file
symbol's image is the file.
"""

from __future__ import annotations

from ugh.core.ideal import Ideal
from ugh.core.op import Op, _symbol_kind, is_bottom, is_content_bearing

_ANCHOR_FIRST = "\x00FIRST\x00"  # mirrors ugh.core.mine._ANCHOR_FIRST
_RESIDUE_HEAD = "\x00HEAD\x00"  # mirrors ugh.core.mine._RESIDUE_HEAD


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
        # nested entities add nothing: their bytes are inside their top-level entity

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
    # residue whose anchor entity is not live goes at the end, sorted, rather than vanishing
    for name in sorted(residue_of):
        if residue_of[name]:
            parts.append(residue_of[name])

    return b"".join(parts)


def code(ideal: Ideal, ops: list[Op]) -> dict[str, bytes]:
    """`{path: bytes}` for every path `ideal` covers. `ops` must carry the images of the ideal's
    frontier producers."""
    by_id = {op.id: op for op in ops}
    tip = ideal.frontier(ops)

    by_path: dict[str, dict[str, str]] = {}  # path -> {symbol: op_id}, live symbols only
    content_paths: set[str] = set()  # anchor-only paths do not materialize, as in covered_paths
    for sym, op_id in tip.items():
        after = by_id[op_id].footprint[sym][1]
        if is_bottom(after):
            continue
        path = sym.split("::", 1)[0]
        by_path.setdefault(path, {})[sym] = op_id  # anchors stay in a live path's set for ordering
        if is_content_bearing(sym):
            content_paths.add(path)

    return {
        path: _fold_file(path, symbols, by_id)
        for path, symbols in by_path.items()
        if path in content_paths
    }
