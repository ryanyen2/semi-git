"""The atom of history (ADR docs/design/2026-07-06-operation-ideal-kernel.md S3.2): a mined,
content-addressed operation advancing one or more symbol chains.

Defined here in U2 (formally "owned" by U3 per the plan's file list) because mining cannot
produce an op stream without it -- content-addressing is what makes the identification law
(R8) hold: two independent mining runs that derive the same footprint+images+requires+kind
collapse to the same id even though their witness commit differs. U3 builds the persistent,
concurrency-safe *store* around this type (append-only files, locking, fsck); it does not
redefine the type itself.
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


MINER_VERSION = "12"  # Part of every op's content address: bump on any change to what mining
# produces, so ops minted under different rules never collide.

# symbol id -> (before_version, after_version); before_version is None for a fresh add.
# A version is `version(symbol, previous, content)`: never a commit SHA, so the same content
# sequence mined from different commits (a rebase) gives the same ops.
Footprint = dict[str, tuple[str | None, str]]

# symbol id -> verbatim after-bytes, or None for a removal (the ADR's "bottom" image).
Images = dict[str, "bytes | None"]

# (symbol id, version) pairs this op's images depend on -- the exact version the op saw when
# mined, not just the symbol name, so U4's reference edges point at the specific op that
# produced that version rather than every op that ever touched the symbol.
Requires = frozenset[tuple[str, str]]


def _symbol_kind(sym: str) -> str:
    """'whole_file' | 'residue' | 'anchor' | 'entity' (top-level) | 'nested' (skip for fold).

    The classifier for this kernel's symbol-id vocabulary, owned here rather than in `fold`
    because both `fold` (which splices each kind's image) and `ideal.covered_paths` (which
    decides which paths a given ideal materializes) must agree on it -- and `ideal` importing
    from `fold` would be a cycle (`fold` imports `Ideal`).

    `residue` is positional (kernel byte-fidelity fold, 2026-07-08): one pseudo-symbol per gap
    between top-level entities, named `__residue__::{anchor}` where `anchor` is the name of the
    preceding top-level entity (or a HEAD sentinel) -- not one blob per file.

    `import` is a top-level import statement (design 2026-08-27). It needs its own marker rather
    than riding as a plain entity because the entity/nested split is "does the name contain a
    dot", and a module specifier is full of them: `import:./App` would classify as *nested* and
    silently emit no bytes, while `import:react` classified as *entity* and folded fine. Relative
    imports would vanish and package imports would not."""
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


# The symbol kinds `fold._fold_file` splices bytes for on their own: a whole-file image, a
# top-level entity's image, or a file's residue. `anchor` (pure ordering metadata, never revised
# to BOTTOM) and `nested` (already subsumed by its containing top-level entity's image) emit
# nothing standalone.
CONTENT_BEARING_KINDS = frozenset({"whole_file", "residue", "entity", "import"})


def is_content_bearing(sym: str) -> bool:
    """True iff `sym` contributes bytes to `code(I)` on its own. A path is covered / materialized
    iff it has >=1 live content-bearing frontier symbol: an anchor left dangling after its entity
    and residue were pruned (a fully-removed file) can't keep the path alive as an empty `b''`,
    so `covered_paths` and `code` agree exactly on which paths exist (R7/R20)."""
    return _symbol_kind(sym) in CONTENT_BEARING_KINDS


@dataclass(frozen=True)
class Attribution:
    """Structured provenance for one witnessing commit (D7): who/what produced it, beyond the bare
    SHA already in ``Op.provenance``. Frozen and hashable. Sparse -- an op only carries an entry
    for a SHA that has at least one non-None field, so an op with no attribution has ``()``. Like
    ``intent`` and ``provenance``, excluded from ``compute_id`` (attribution is not identity)."""

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
    attribution: tuple[Attribution, ...] = ()  # sparse, sorted-by-sha structured provenance (D7);
    # one entry per SHA carrying >=1 non-None field; appendable; excluded from the id like provenance
    intent: str | None = None  # advisory label/rationale only; excluded from the id
    miner_version: str = MINER_VERSION
    derived: bool = False  # S4/U27: touches a generated/vendored file (e.g. a lockfile) --
    # advisory only, like intent/provenance/attribution, so review surfaces can collapse it;
    # excluded from the id since re-tagging a path derived/not-derived is not a content change.


def compute_id(
    footprint: Footprint,
    images: Images,
    requires: Requires,
    kind: str,
    miner_version: str = MINER_VERSION,
) -> str:
    """Content address over (payload, miner-version) only -- provenance and intent are
    deliberately excluded. Two mining runs that land the same symbols on the same bytes must
    produce the same id even if the witnessing commit differs (R8) or an intent label differs
    (advisory, never structural)."""
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
    """The ACI union of two structured-provenance sets (D7, LAW-U): group by SHA, and for each
    field combine field-by-field -- take the other side when one is None, keep it when both agree,
    and pick ``min`` when both are non-None and differ (deterministic, so any merge schedule
    converges). Returns a sorted-by-SHA tuple, dropping any entry left all-None."""
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
