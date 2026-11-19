"""The op: one mined, content-addressed change advancing one or more symbol chains. Two mining
runs that derive the same footprint, images, requires and kind give the same id, whichever commit
they saw it in. Also the symbol-id vocabulary and how units and steps are addressed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

BOTTOM = "⊥"  # prefix of a removal version: the symbol no longer exists in code(I).


def version(symbol: str, previous: str | None, content: bytes | None) -> str:
    """A symbol's version: a hash of the symbol, the version it came from, and its content. Content
    that recurs is a new version, so the ops on one symbol form a simple path. `content=None` is a
    removal, marked with `BOTTOM`."""
    h = hashlib.sha1(f"{symbol}\0{previous or ''}\0".encode())
    if content is None:
        return BOTTOM + h.hexdigest()
    h.update(content)
    return h.hexdigest()


def is_bottom(version: str | None) -> bool:
    """True iff `version` is a removal."""
    return version is not None and version.startswith(BOTTOM)


MINER_VERSION = "12"  # part of every op's id: bump on any change to what mining produces

# symbol id -> (before_version, after_version); before_version is None for a fresh add.
# A version is `version(symbol, previous, content)`: never a commit SHA, so the same content
# sequence mined from different commits (a rebase) gives the same ops.
Footprint = dict[str, tuple[str | None, str]]

# symbol id -> verbatim after-bytes, or None for a removal.
Images = dict[str, "bytes | None"]

# (symbol id, version) pairs this op's images refer to: the exact version seen when mined.
Requires = frozenset[tuple[str, str]]


def _symbol_kind(sym: str) -> str:
    """'whole_file' | 'residue' | 'anchor' | 'import' | 'entity' (top-level) | 'nested'.

    A `residue` symbol is the gap after the top-level entity it names (or after the file's head).
    An `import` has its own marker because a module specifier contains dots, and a dotted name
    would otherwise read as nested."""
    if "::" not in sym:
        return "whole_file"
    _, _, rest = sym.partition("::")
    if rest.startswith("__residue__::"):
        return "residue"
    if rest.startswith("__anchor__::"):
        return "anchor"
    if rest.startswith("__import__::"):
        return "import"
    return "nested" if "." in rest else "entity"


def unit_of(sym: str) -> str:
    """The unit a symbol belongs to: its top-level definition (members, the trailing gap and the
    anchor fact belong to the definition), the file head, or the whole file."""
    path, sep, rest = sym.partition("::")
    if not sep:
        return sym
    for marker in ("__residue__::", "__anchor__::"):
        if rest.startswith(marker):
            name = rest[len(marker):]
            return sym if name == "\x00HEAD\x00" else unit_of(f"{path}::{name}")
    if rest.startswith("__import__::"):
        return sym
    return f"{path}::{rest.split('.', 1)[0]}"


def unit_address(unit: str) -> str:
    """How a unit is printed: `path::def`, or `path` for a whole file, a file's head or an import."""
    return unit if _symbol_kind(unit) == "entity" else unit.split("::", 1)[0]


def step_address(unit: str, sha: str) -> str:
    """How a step on `unit` in save `sha` is printed: its unit's address, `@<short sha>`."""
    return f"{unit_address(unit)}@{sha[:7]}"


# The kinds `fold._fold_file` emits bytes for. An anchor only orders; a nested entity's bytes lie
# inside its top-level entity.
CONTENT_BEARING_KINDS = frozenset({"whole_file", "residue", "entity", "import"})


def is_content_bearing(sym: str) -> bool:
    """True iff `sym` contributes bytes to `code(I)` on its own. A path materializes iff it has a
    live content-bearing frontier symbol, so a dangling anchor cannot keep a removed file alive."""
    return _symbol_kind(sym) in CONTENT_BEARING_KINDS


@dataclass(frozen=True)
class Attribution:
    """Who produced one witnessing commit, beyond its SHA in ``Op.provenance``. An op carries an
    entry only for a SHA with a non-None field. Excluded from ``compute_id``."""

    sha: str
    session: str | None = None
    agent: str | None = None
    plan: str | None = None


@dataclass(frozen=True)
class Op:
    """Frozen and hashable; nothing about a mined Op is mutable after mining. Correcting a wrong
    result happens by a new commit (a take-out is one), never by editing an op in place."""

    id: str
    footprint: Footprint
    images: Images
    requires: Requires = frozenset()
    kind: str = "touched"  # add | extend | rework | prune | move | merge -- derived, not authored
    provenance: tuple[str, ...] = ()  # witnessing commit SHAs; appendable; excluded from the id
    attribution: tuple[Attribution, ...] = ()  # sorted by sha; appendable; excluded from the id
    intent: str | None = None  # advisory label/rationale only; excluded from the id
    miner_version: str = MINER_VERSION
    derived: bool = False  # touches a generated or vendored file; advisory; excluded from the id


def compute_id(
    footprint: Footprint,
    images: Images,
    requires: Requires,
    kind: str,
    miner_version: str = MINER_VERSION,
) -> str:
    """The content address over the payload and miner version. Provenance, attribution, intent and
    `derived` are excluded, so the same change mined from another commit has the same id."""
    payload = {
        "footprint": {sym: list(ver) for sym, ver in sorted(footprint.items())},
        "images": {
            sym: (img.hex() if img is not None else None) for sym, img in sorted(images.items())
        },
        "requires": [list(r) for r in sorted(requires)],
        "kind": kind,
        "miner_version": miner_version,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def make_op(
    footprint: Footprint,
    images: Images,
    *,
    requires: Requires = frozenset(),
    kind: str = "touched",
    provenance: tuple[str, ...] = (),
    attribution: tuple[Attribution, ...] = (),
    intent: str | None = None,
    miner_version: str = MINER_VERSION,
    derived: bool = False,
) -> Op:
    """Construct an Op with its id computed from its content -- the only supported way to make
    one (never hand-assign ``.id``). ``attribution`` (like ``provenance``/``intent``/``derived``)
    rides along but does not enter the id."""
    op_id = compute_id(footprint, images, requires, kind, miner_version)
    return Op(
        id=op_id,
        footprint=footprint,
        images=images,
        requires=requires,
        kind=kind,
        provenance=provenance,
        attribution=attribution,
        intent=intent,
        miner_version=miner_version,
        derived=derived,
    )


def merge_attribution(
    a: tuple[Attribution, ...], b: tuple[Attribution, ...]
) -> tuple[Attribution, ...]:
    """The union of two attribution sets by SHA, field by field: a None takes the other side, and
    two different values keep the smaller, so any merge order converges. Sorted by SHA."""
    by_sha: dict[str, list[Attribution]] = {}
    for entry in (*a, *b):
        by_sha.setdefault(entry.sha, []).append(entry)
    merged: list[Attribution] = []
    for sha in sorted(by_sha):
        entries = by_sha[sha]
        fields: dict[str, str | None] = {}
        for field in ("session", "agent", "plan"):
            vals = [getattr(e, field) for e in entries if getattr(e, field) is not None]
            fields[field] = min(vals) if vals else None
        if any(v is not None for v in fields.values()):
            merged.append(Attribution(sha=sha, **fields))
    return tuple(merged)
