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

BOTTOM = "⊥"  # the ADR's "removed" version/image sentinel: a symbol's after_version at the
# tip of its chain, meaning the symbol no longer exists in code(I).

MINER_VERSION = "1"  # R12: bump on any change to mining/untangling/identity logic. Part of
# every op's content address, so an algorithm upgrade opens a new identity space rather than
# silently colliding with -- or silently reusing -- ops minted under the old rules.

# symbol id -> (before_version, after_version); before_version is None for a fresh add.
# A "version" is a content-addressed string (the symbol's content hash, or a git blob OID for
# whole-file binary symbols) -- never a commit SHA, so the identification law (R8) can compare
# footprints across mining runs that saw different commits but identical bytes.
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
    from `fold` would be a cycle (`fold` imports `Ideal`)."""
    if "::" not in sym:
        return "whole_file"
    _, _, rest = sym.partition("::")
    if rest == "__residue__":
        return "residue"
    if rest.startswith("__anchor__::"):
        return "anchor"
    return "nested" if "." in rest else "entity"


# The symbol kinds `fold._fold_file` splices bytes for on their own: a whole-file image, a
# top-level entity's image, or a file's residue. `anchor` (pure ordering metadata, never revised
# to BOTTOM) and `nested` (already subsumed by its containing top-level entity's image) emit
# nothing standalone.
CONTENT_BEARING_KINDS = frozenset({"whole_file", "residue", "entity"})


def is_content_bearing(sym: str) -> bool:
    """True iff `sym` contributes bytes to `code(I)` on its own. A path is covered / materialized
    iff it has >=1 live content-bearing frontier symbol: an anchor left dangling after its entity
    and residue were pruned (a fully-removed file) can't keep the path alive as an empty `b''`,
    so `covered_paths` and `code` agree exactly on which paths exist (R7/R20)."""
    return _symbol_kind(sym) in CONTENT_BEARING_KINDS


@dataclass(frozen=True)
class Op:
    """Frozen and hashable; nothing about a mined Op is mutable after minting. Correcting a
    wrong result happens by minting a new op (revert/rework) or, for a wrong identity weld,
    ``identity split``/``identity join`` (U11) -- never by editing one in place."""

    id: str
    footprint: Footprint
    images: Images
    requires: Requires = frozenset()
    kind: str = "touched"  # add | extend | rework | prune | move | merge -- derived, not authored
    provenance: tuple[str, ...] = ()  # witnessing commit SHAs; appendable; excluded from the id
    intent: str | None = None  # advisory label/rationale only; excluded from the id
    miner_version: str = MINER_VERSION
    off_chain: bool = False  # R18: a hollow plan-intake op, not yet fulfilled. Lifecycle/storage
    # state (which store directory holds it), not content -- excluded from the id so fulfillment
    # (which flips this to False and splices real images) doesn't need to re-derive an identity
    # a human's concurrent edit could have already reserved.


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
    intent: str | None = None,
    miner_version: str = MINER_VERSION,
    off_chain: bool = False,
) -> Op:
    """Construct an Op with its id computed from its content -- the only supported way to make
    one (never hand-assign ``.id``)."""
    op_id = compute_id(footprint, images, requires, kind, miner_version)
    return Op(
        id=op_id,
        footprint=footprint,
        images=images,
        requires=requires,
        kind=kind,
        provenance=provenance,
        intent=intent,
        miner_version=miner_version,
        off_chain=off_chain,
    )
