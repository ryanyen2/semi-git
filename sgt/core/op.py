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


def salted_bottom(sha: str) -> str:
    """A removal sentinel salted by the *deleting/flipping commit* (U9): ``⊥@<sha>``. A prune's
    after_version is salted so a later re-add of the same symbol can chain onto its *specific*
    deletion (before_version == this salt) instead of both births claiming ``(symbol, None)`` and
    pseudo-forking. Each deletion mints a distinct salt, so an identical-content rebirth *cycle*
    (add->del->A->del->A) forms one long chain rather than collapsing two deletions into one."""
    return f"{BOTTOM}@{sha}"


def is_bottom(version: str | None) -> bool:
    """True iff ``version`` marks a removed chain tip -- the bare ``BOTTOM`` sentinel or any salted
    bottom ``⊥@<sha>`` (U9). Every liveness test (`fold`, `ideal.covered_paths`, mine's prune-kind
    detection, status/cluster surfaces) routes through this rather than an exact ``== BOTTOM`` so a
    salted tip still reads dead; `order.py` treats versions as opaque strings so salted bottoms
    chain and ground with no special-casing there."""
    return version is not None and (version == BOTTOM or version.startswith(BOTTOM + "@"))


MINER_VERSION = "6"  # R12: bump on any change to mining/untangling/identity logic. Part of
# every op's content address, so an algorithm upgrade opens a new identity space rather than
# silently colliding with -- or silently reusing -- ops minted under the old rules.
# v2 (2026-07-08, kernel byte-fidelity audit): byte-native entity/residue addressing (was
# line-based), decorator/export span-widening, duplicate-id coalescing, and positional
# per-gap residue segments (was one blob per file) -- see FINDINGS.md.
# v3 (2026-07-13, U9): rebirth chaining (add->delete->re-add is one chain via salted bottoms,
# detected purely from git history) and representation-flip bridging (parseable<->whole-file
# transitions close the losing side with BOTTOM ops and re-birth the winning side by chaining
# onto them) -- kills the ~20% closure loss the U22.5 pseudo-fork caused. See FINDINGS.md.
# v4 (2026-07-31, 1.3/F7): merge-aware mining -- a merge commit mines only the paths it resolved
# differently from *every* parent (the conflict/evil hunks), not the whole first-parent cumulative
# diff, so the second parent's own chain is no longer re-minted as one forking op. See the workflow
# hardening plan (docs/plans/2026-07-31-001).
# v5 (2026-08-05, launch): default tier exclusions -- any dot-path (`.claude/`, `.github/`,
# `.mcp.json`, sgt's own `.sgt/`) and anything the repo-root `.gitignore` matches (honored even
# for tracked files) resolve to `ignored`, so tooling/config no longer mints one-file features.
# `.sgt/tiers.json` overrides still force-include. See sgt/core/tiers.py.
# v6 (2026-08-07, launch): JavaScript is parsed. `.js`/`.mjs`/`.cjs` map to the TypeScript grammar
# and `.jsx` to the TSX one (TS is a syntactic superset of JS, so no new grammar dependency), where
# before every one of them fell through to a single whole-file symbol -- a JS/React repo mined no
# symbol-level ops at all, which silently removed features/blame/revert granularity for it. Bumped
# because those files now mine into many entity ops instead of one, so the two identity spaces must
# not be confused. See sgt/entities/extract.py `_EXT_LANG`.

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
    from `fold` would be a cycle (`fold` imports `Ideal`).

    `residue` is positional (kernel byte-fidelity fold, 2026-07-08): one pseudo-symbol per gap
    between top-level entities, named `__residue__::{anchor}` where `anchor` is the name of the
    preceding top-level entity (or a HEAD sentinel) -- not one blob per file."""
    if "::" not in sym:
        return "whole_file"
    _, _, rest = sym.partition("::")
    if rest.startswith("__residue__::"):
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


# A named top-level entity or a whole (unparsed) file -- the symbols whose creation/removal is a
# *behavioral* change the user would recognize as a distinct piece of work. Deliberately excludes
# `residue`: residue is positional gap-bytes that shift merely because an entity was inserted
# before/after them (appending `sub` after `mul` "modifies" `__residue__::mul`), so counting it
# would attribute new behavior to a neighbour that did not change. Anchors/nested emit no bytes.
BEHAVIORAL_KINDS = frozenset({"whole_file", "entity"})


def is_behavioral(sym: str) -> bool:
    """True iff `sym` is a named entity or whole file -- a behavioral unit, not positional residue
    or ordering metadata. `is_content_bearing` is the *fold* predicate (residue carries bytes, so
    it is content); this is the *segmentation* predicate (residue is not new behaviour). They
    differ only on `residue`, and that difference is the point."""
    return _symbol_kind(sym) in BEHAVIORAL_KINDS


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
    """Frozen and hashable; nothing about a mined Op is mutable after minting. Correcting a
    wrong result happens by minting a new op (revert/rework) or, for a wrong identity weld,
    ``identity split``/``identity join`` (U11) -- never by editing one in place."""

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
    off_chain: bool = False  # R18: a hollow plan-intake op, not yet fulfilled. Lifecycle/storage
    # state (which store directory holds it), not content -- excluded from the id so fulfillment
    # (which flips this to False and splices real images) doesn't need to re-derive an identity
    # a human's concurrent edit could have already reserved.
    derived: bool = False  # S4/U27: touches a generated/vendored file (e.g. a lockfile) --
    # advisory only, like intent/provenance/attribution, so review surfaces can collapse it;
    # excluded from the id since re-tagging a path derived/not-derived is not a content change.
    resolves: frozenset[str] = frozenset()  # D5: op id(s) of the fork tip(s) this op reconciles
    # (structured counterpart to the free-text `intent` a `merge-op` hollow already carries) --
    # advisory only, like intent/attribution; excluded from the id since which fork an op resolves
    # is not itself part of its content.


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
    off_chain: bool = False,
    derived: bool = False,
    resolves: frozenset[str] = frozenset(),
) -> Op:
    """Construct an Op with its id computed from its content -- the only supported way to make
    one (never hand-assign ``.id``). ``attribution`` (like ``provenance``/``intent``/``derived``/
    ``resolves``) rides along but does not enter the id."""
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
        off_chain=off_chain,
        derived=derived,
        resolves=resolves,
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
