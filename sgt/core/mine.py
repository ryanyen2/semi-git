"""Mine an operation stream from git history (ADR S3.2; plan R1, R2, R7, R12, R22).

Promoted from ``experiments/patch_clustering/mine.py`` (the "kernel embryo", plan U2) and
extended with what the experiment didn't need: whole-file pseudo-symbols for non-parseable
paths (config, docs, binaries -- R7), one residue pseudo-symbol per file for module-level
statements outside any entity span, one anchor pseudo-symbol *per newly-added top-level entity*
recording which entity (if any) immediately precedes it -- independent per entity, not one
shared chain per file, so two unrelated insertions from different features never fork
("anchor-disjoint additions commute", ADR S3.5) -- def-use untangling of a single commit's
touched entities into separate ops (ClusterChanges-style -- BET-A), and content-addressed `Op`
construction stamped with the miner version (R12) via `sgt.core.op.make_op`.

Determinism: every dict/set iterated below is sorted or processed in a fixed insertion order;
`GitBinding.history` gives commit order via `git log --reverse`; tree-sitter's own walk is
deterministic. No wall clock, no network, no LLM.

Identity: rename/move resolution runs per `mine()` call via a union-find spanning the commits
in that call's range -- not persisted across separate calls. A persistent, cross-call identity
registry (so an incremental `mine(repo, since=X)` recognizes a rename against a symbol last
seen before `X`) is the store's job (U3/U6), which sees every previously-mined op.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Parser

from sgt.config import IdentityConstraints, load_identity_constraints
from sgt.core import tiers
from sgt.core.identity import Snap, link_residual, match_pair, snapshot
from sgt.core.op import BOTTOM, Images, Op, _symbol_kind, is_bottom, make_op, salted_bottom
from sgt.entities.extract import Entity, _language, _language_for, extract_file
from sgt.entities.graph import EntityEdge, build_entity_graph
from sgt.store.gitbind import GitBinding


class _UnionFind:
    """Links surface ids across renames so a moved entity resolves to one canonical root,
    stable across every commit seen within one `mine()` call."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:  # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        # `a` (the older/earlier-seen side of a rename or move) anchors the canonical id, so a
        # chain of renames within one mine() call collapses to the first surface id it ever had.
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class _Touch:
    """One symbol's change at one commit -- the raw material for an Op, before untangling
    assigns it a bucket and identity resolution canonicalizes its id."""

    order: int
    surface_id: str
    before_version: str | None
    after_version: str
    image: bytes | None
    requires: frozenset[tuple[str, str]]  # (required symbol id, version seen at mining time)
    via_move: bool = False
    bucket: str | None = None  # filled in after untangling; None only transiently
    is_pending: bool = False  # from the dirty-working-tree pass (Gap 2, U7.5) -- no real commit
    # witnesses it yet, so `_build_ops` must emit `provenance=()` rather than a commit sha.
    derived: bool = False  # U27/S4: this touch's path is a generated/vendored file (a lockfile,
    # by basename) -- advisory only, folded into the built op's `derived` flag.


def _requires_of(sym: str, calls_by_src: dict[str, set[str]], entity_version: dict[str, str]) -> frozenset[tuple[str, str]]:
    """The (symbol id, version) pairs `sym`'s current image depends on -- pinned to the exact
    version visible when this op is mined (R4), not just the symbol name, so U4's reference
    edges point at the specific op that produced that version."""
    return frozenset(
        (dst, entity_version[dst]) for dst in calls_by_src.get(sym, ()) if dst in entity_version
    )


def _content_version(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _positional_version(surface_id: str, content_hash: str) -> str:
    """An entity's chain version ties its content to its *current surface location*, not
    content alone -- otherwise a same-bytes cross-file move (content_hash unchanged) produces
    a footprint entry whose before_version equals its own after_version, and a later change
    against that same content_hash (from either side of the move) collides with it as a false
    chain fork. Keying on (surface_id, content_hash) makes a move a genuine version advance --
    before != after -- even when the bytes never change."""
    return hashlib.sha1(f"{surface_id}:{content_hash}".encode()).hexdigest()


def _prior_whole_file_version(
    gb, old_ref: str, parent: str | None, tier_cfg_parent, old_raw: bytes | None
) -> str | None:
    """The (symbol==path) whole-file chain's producing version at `parent`, or None if no such
    producer exists there -- either the path didn't exist yet, it was mined per-entity instead
    (U27/D4 promotion/demotion boundary), or its tier was `ignored` (no producer at all). Chaining
    a new whole-file touch onto a real git blob OID that no same-symbol op ever produced would
    leave it permanently ungrounded (`order.py`'s `_grounded` fixpoint), silently dropping it from
    every future ideal even though `mine()`'s raw output still lists it. `old_raw` is the
    caller's already-batch-fetched content at `(parent, old_ref)` -- reused here rather than a
    fresh `blob_bytes` subprocess per opaque/degraded file per commit."""
    if parent is None:
        return None
    parent_tier = tiers.resolve_tier(old_ref, tier_cfg_parent)
    if parent_tier == "ignored":
        return None
    if parent_tier == "opaque":
        return gb.blob_oid(parent, old_ref)
    if _is_unparseable_whole_file(old_ref, old_raw):
        return gb.blob_oid(parent, old_ref)
    return None


def _parse_has_error(path: str, source: bytes) -> bool:
    """True if tree-sitter could not cleanly parse `source` as `path`'s language -- the signal
    that separates "legitimately no entities" (e.g. a pure-constants module) from "unparseable
    mid-edit", which must degrade to a whole-file symbol rather than report zero entities (R7)."""
    lang = _language_for(path)
    if lang is None:
        return False
    tree = Parser(_language(lang)).parse(source)
    return tree.root_node.has_error


def _is_unparseable_whole_file(path: str, raw: bytes | None) -> bool:
    """True iff `path`'s bytes `raw` are entity-tier but do not parse cleanly, so mining
    represents them as a single whole-file symbol (the degrade at `_mine_one`) rather than
    entities. An empty-but-parseable file (zero entities, no error) is NOT whole-file -- it is
    entity-rep carrying only residue -- so this must AND both conditions, matching the degrade site."""
    return bool(raw) and not extract_file(path, raw) and _parse_has_error(path, raw)


def _entity_bytes(source: bytes, entity: Entity) -> bytes:
    """Verbatim bytes for one entity's span -- byte-native (`start_byte`/`end_byte`), so CRLF
    line endings, non-UTF-8 content, and control characters inside a body all survive exactly.
    No decode, no line join -- a decode-then-reslice pipeline is exactly what can silently
    truncate or corrupt bytes a line-based differ can't see (kernel byte-fidelity audit,
    2026-07-08)."""
    return source[entity.start_byte : entity.end_byte]


_RESIDUE_HEAD = "\x00HEAD\x00"  # sentinel: the gap before a file's first top-level entity (or
# the whole file, if it has none) -- mirrors _ANCHOR_FIRST's role for entity ordering.


def _residue_segments(raw: bytes, entities: list[Entity]) -> dict[str, bytes]:
    """Positional residue (ADR S3.5, byte-fidelity fold): the file's raw bytes *not* covered by
    any top-level entity, split into one segment per gap -- keyed by the name of the top-level
    entity immediately preceding it, or `_RESIDUE_HEAD` for the gap before the first entity (or
    a file with none at all). Concatenating every top-level entity in document order with its
    trailing gap reconstructs the file exactly: a verbatim byte partition, no synthesized
    separator anywhere. Only top-level (container-less) entities anchor a gap -- a nested
    entity's bytes are already inside its container's own span."""
    top = sorted((e for e in entities if e.container is None), key=lambda e: e.start_byte)
    if not top:
        return {_RESIDUE_HEAD: raw}
    segments = {_RESIDUE_HEAD: raw[: top[0].start_byte]}
    for i, e in enumerate(top):
        end = top[i + 1].start_byte if i + 1 < len(top) else len(raw)
        segments[e.name] = raw[e.end_byte : end]
    return segments


_ANCHOR_FIRST = "\x00FIRST\x00"  # sentinel: this entity was first in its file's top-level order


def _top_level_anchor_facts(entities: list[Entity]) -> dict[str, str | None]:
    """For each top-level entity (by current start_line order), the name of the top-level
    entity immediately before it, or None if it's first. Only top-level entities (no
    container) get an anchor; a nested method's position follows its class, not tracked here."""
    top_level = sorted((e for e in entities if e.container is None), key=lambda e: e.start_line)
    facts: dict[str, str | None] = {}
    prev: str | None = None
    for e in top_level:
        facts[e.name] = prev
        prev = e.name
    return facts


def _emit_scope_reshape(
    emit_entity, old: Snap, new: Snap, new_raw: bytes, calls_by_src, entity_version, bottom: str
) -> None:
    """A link the identity matcher found by body/structure similarity, but across a *kind*
    change (function -> method or vice versa) -- a genuine scope reshape, not a rename. The
    matcher tiers stay verbatim (kind is deliberately not part of tiers 2/2b's match key), but
    mine.py refuses to weld two different scopes into one chain: both ops still land from this
    commit (split provenance), just as delete + add rather than a silent move. `bottom` is the
    (salted, per U9) removal sentinel for this commit."""
    emit_entity(old.ent.id, _positional_version(old.ent.id, old.content_hash), bottom, None, frozenset())
    emit_entity(
        new.ent.id, None, _positional_version(new.ent.id, new.content_hash),
        _entity_bytes(new_raw, new.ent), _requires_of(new.ent.id, calls_by_src, entity_version),
    )


def _close_entity_rep(emit_entity, emit_other, path: str, raw: bytes, bottom: str) -> None:
    """Representation-flip transition (U9, R14): a file parseable at the parent but now whole-file
    (opaque, unparseable, or renamed across a language boundary) closes its losing-side entity and
    residue chains with a BOTTOM op, so the winning whole-file symbol is the *only* live
    representation of those bytes -- no two competing images, and `code(I)` reproduces the flip
    commit exactly. A later flip back re-births these symbols by chaining onto this bottom (see
    `_apply_rebirth_chaining`) rather than pseudo-forking on a second `(symbol, None)` birth. The
    `before_version`s here mirror exactly what the parent's own entity/residue ops produced, so the
    prune grounds on them. Nested entities are closed too (their chains exist even though the fold
    subsumes them into the container), matching the ordinary in-file removal path (`res_removed`)."""
    ents = extract_file(path, raw)
    for e in ents:
        emit_entity(e.id, _positional_version(e.id, e.content_hash), bottom, None, frozenset())
    for anchor, seg in _residue_segments(raw, ents).items():
        if seg:
            emit_other(f"{path}::__residue__::{anchor}", _content_version(seg), bottom, None)


def _present_symbols_at(gb: GitBinding, tree_ish: str, path: str) -> set[str]:
    """The content-bearing / chained symbol ids mining represents `path` with at `tree_ish` --
    `{path}` when whole-file (opaque tier or unparseable), else its entity ids (top-level and
    nested) plus non-empty residue-segment symbols. Anchors are excluded: they are ordering
    metadata mining never revises to BOTTOM, so they never *close* and never chain a rebirth.
    Empty when the path is absent or tier-ignored. The exact join the rebirth lookback compares
    across two trees to decide where a symbol went present -> absent (i.e. was closed)."""
    raw = gb.blob_bytes(tree_ish, path)
    if raw is None:
        return set()
    tier = tiers.resolve_tier(path, tiers.load_tiers_at(gb, tree_ish))
    if tier == "ignored":
        return set()
    if tier == "opaque" or _is_unparseable_whole_file(path, raw):
        return {path}
    ents = extract_file(path, raw)
    syms = {e.id for e in ents}
    for anchor, seg in _residue_segments(raw, ents).items():
        if seg:
            syms.add(f"{path}::__residue__::{anchor}")
    return syms


def _apply_rebirth_chaining(gb: GitBinding, sha: str, parent: str | None, touches: list[_Touch]) -> None:
    """Chain every fresh (`before_version is None`) content symbol at this commit onto the salted
    bottom of the ancestor commit that last *closed* it (R13/R14) -- so add->delete->re-add, and a
    representation flip back, form ONE chain instead of pseudo-forking on a second `(symbol, None)`
    birth that `order.fork_free` would then drop (the ~20% closure loss of FINDINGS U22.5).

    Detection is a pure function of git history (LAW-0, never the `.sgt` store): for the symbol's
    own path, walk the commits that touched it (newest-first, each diffed against its first parent
    exactly as `mine` does) and find the most recent one where the symbol was present in the parent
    tree but absent in the commit's own tree -- that commit's salted bottom is the `before_version`.
    Because the walk follows the path's history (not the mined range), it finds a deletion even when
    that deletion predates a `since`-restricted incremental mine; the prune op minted by the earlier
    mine already sits in the append-only store, so grounding holds. A symbol that was never present
    before (a genuinely new entity in a re-added file) matches nothing and stays a true `None` birth.
    Anchors are skipped -- they never close, so they can never chain (they may still coalesce or,
    if their predecessor changed, fork harmlessly since nothing builds on an anchor)."""
    if parent is None:
        return  # a root / genesis-horizon commit has no ancestry to have closed anything in
    fresh_by_path: dict[str, list[_Touch]] = {}
    for t in touches:
        if t.before_version is None and _symbol_kind(t.surface_id) != "anchor":
            fresh_by_path.setdefault(t.surface_id.split("::", 1)[0], []).append(t)
    for path, group in fresh_by_path.items():
        commits = gb.commits_touching(parent, path)  # (D, first_parent_of_D), newest-first, D < sha
        if not commits:
            continue
        remaining = {t.surface_id: t for t in group}
        for d_sha, d_parent in commits:
            if not remaining:
                break
            present_in_d = _present_symbols_at(gb, d_sha, path)
            present_in_parent = _present_symbols_at(gb, d_parent, path) if d_parent else set()
            for sym in [s for s in remaining if s in present_in_parent and s not in present_in_d]:
                remaining[sym].before_version = salted_bottom(d_sha)  # closed at d_sha -> chain here
                del remaining[sym]


def _untangle(touched_ids: set[str], edges: list[EntityEdge]) -> list[frozenset[str]]:
    """Split a commit's touched entities into groups by *direct* def-use connectivity (calls,
    imports, containment) -- ClusterChanges-style (BET-A). Two touched entities with no direct
    edge between them become separate ops even if both call some third, untouched helper: this
    errs toward finer granularity, which `merge-op` (U11) can always undo, whereas a wrongly-
    tangled op cannot be split without `split-op`'s manual image authoring."""
    parent = {t: t for t in touched_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for e in edges:
        if e.src in touched_ids and e.dst in touched_ids:
            union(e.src, e.dst)

    groups: dict[str, set[str]] = {}
    for t in touched_ids:
        groups.setdefault(find(t), set()).add(t)
    return [frozenset(g) for g in sorted(groups.values(), key=lambda g: sorted(g)[0])]


def _mine_one(
    gb: GitBinding, uf: _UnionFind, order: int, sha: str, parent: str | None, is_pending: bool = False,
    constraints: IdentityConstraints | None = None, excluded_paths: set[str] | None = None,
) -> list[_Touch]:
    """One commit's touched symbols -- the loop body `mine()` runs once per real commit, plus
    (when `include_dirty=True`) once more for the working tree's uncommitted state, diffed
    against real HEAD exactly the same way (Gap 2, U7.5). `sha` need only be a tree-ish (a real
    commit, or `GitBinding.working_tree_snapshot()`'s synthetic tree object) -- every
    `GitBinding` read used below accepts either."""
    excluded_paths = set() if excluded_paths is None else excluded_paths
    # LAW-0: read from the mined commit's own tree, so tier assignment stays a pure function of
    # the commit, never the current working tier map. Batched (one `git cat-file --batch` for
    # both `sha` and `parent`'s `.sgt/tiers.json` + `.sgtignore`) instead of up to 4 separate
    # `blob_bytes` subprocess spawns per commit.
    tier_cfgs = tiers.load_tiers_at_many(gb, [sha, parent] if parent else [sha])
    tier_cfg = tier_cfgs[sha]
    tier_cfg_parent = tier_cfgs.get(parent) if parent else None
    codebase_after = gb.tree_at(sha)
    graph_after = build_entity_graph(codebase_after)
    calls_by_src: dict[str, set[str]] = {}
    for e in graph_after.edges:
        if e.type in ("calls", "imports"):
            calls_by_src.setdefault(e.src, set()).add(e.dst)
    entity_version: dict[str, str] = {
        e.id: _positional_version(e.id, e.content_hash) for e in graph_after.entities
    }
    container_of: dict[str, str | None] = {e.id: e.container for e in graph_after.entities}

    entity_touches: list[_Touch] = []
    other_touches: list[_Touch] = []
    commit_added: list[Snap] = []
    commit_removed: list[Snap] = []
    new_entities_by_file: dict[str, list[Entity]] = {}

    def emit_entity(sym: str, before, after, image, requires, via_move=False) -> None:
        uf.add(sym)
        entity_touches.append(
            _Touch(order, sym, before, after, image, requires, via_move, is_pending=is_pending)
        )

    def emit_other(sym: str, before, after, image, requires=frozenset(), derived=False) -> None:
        other_touches.append(
            _Touch(
                order, sym, before, after, image, requires,
                bucket=f"{sha}:{sym}", is_pending=is_pending, derived=derived,
            )
        )

    # U9: a prune's after_version is a bottom salted by *this* commit, so a later re-add of the same
    # symbol chains onto its specific deletion (grounding) and identical-content rebirth cycles don't
    # re-collide into a fork. The dirty pass mines against a synthetic snapshot tree whose id is not a
    # stable commit sha, and must mint nothing permanent, so it keeps the bare (unsalted) BOTTOM.
    bottom = BOTTOM if is_pending else salted_bottom(sha)

    diffs = gb.diff_name_and_text(parent, sha)
    # Batched blob reads (one `git cat-file --batch` process for every changed file's new
    # content, one more for the old side) instead of a `blob_bytes` subprocess per file --
    # a commit touching dozens of files no longer spawns dozens of git processes to mine.
    new_blobs = dict(zip(
        ((sha, fc.path) for fc in diffs),
        gb.blob_bytes_many([(sha, fc.path) for fc in diffs]),
    ))
    old_blobs = (
        dict(zip(
            ((parent, fc.old_path or fc.path) for fc in diffs),
            gb.blob_bytes_many([(parent, fc.old_path or fc.path) for fc in diffs]),
        ))
        if parent is not None else {}
    )

    # Symlinks (git mode 120000) are unmanaged (R3): their blob is the target-path *string*, so
    # mining must never record them as ordinary content. Skip a path that is a symlink on either
    # side of the diff, scoped to just the changed paths so this stays one cheap `ls-tree`.
    symlinked = gb.symlink_paths(sha, [fc.path for fc in diffs])
    if parent is not None:
        symlinked |= gb.symlink_paths(parent, [fc.old_path or fc.path for fc in diffs])

    for fc in diffs:
        old_ref_path = fc.old_path or fc.path
        if (
            fc.path.startswith(".sgt/")
            or old_ref_path.startswith(".sgt/")
            or fc.path in excluded_paths
            or old_ref_path in excluded_paths
            or fc.path in symlinked
            or old_ref_path in symlinked
        ):
            # sgt's own state, never mined as codebase content -- and once excluded, a rename
            # carries the exclusion to its new path too (e.g. a `.sgt/` -> `.sgt.bak/` migration),
            # so a later plain delete of that destination doesn't mine an ungrounded prune for a
            # path whose "add" was never recorded.
            excluded_paths.add(fc.path)
            excluded_paths.add(old_ref_path)
            continue
        tier = tiers.resolve_tier(fc.path, tier_cfg)
        new_bytes = new_blobs[(sha, fc.path)]
        old_ref = old_ref_path
        old_raw = old_blobs.get((parent, old_ref)) if parent is not None else None

        # `path`'s representation at the parent, for flip bridging (U9, R14): a file that changes
        # scheme -- parseable entities <-> whole-file (opaque, unparseable), or a rename across a
        # language boundary -- must close the losing side's live symbols, or two competing images of
        # the same bytes both stay live and a later flip materializes empty or foreign content. The
        # transition ops are minted only for committed history, never the transient dirty pass.
        parent_tier = (
            tiers.resolve_tier(old_ref, tier_cfg_parent)
            if parent is not None and tier_cfg_parent is not None else None
        )
        parent_present = old_raw is not None and parent_tier != "ignored"
        parent_whole_file = parent_present and (
            parent_tier == "opaque" or _is_unparseable_whole_file(old_ref, old_raw)
        )
        parent_entity_rep = parent_present and not parent_whole_file

        if tier == "ignored":
            # Tier-3 (U27/D4): excluded from mining entirely -- no touch at all, not even a
            # whole-file one. Nothing already mined for this path is rewritten; it just stops
            # being tracked as of this commit forward.
            continue

        if tier == "opaque":
            # Whole-file pseudo-symbol: the built-in default for a path with no tree-sitter
            # grammar (R7 -- config, docs, binary), or an explicit opaque override/demotion
            # (U27/D4). Versioned by git blob OID uniformly (not content-hashed text vs. OID'd
            # binary separately) -- before_version is always looked up via blob_oid, so
            # after_version must use the same scheme or a text file's chain could never link
            # across commits. A demoted entity-tier path's prior entity/residue chains are left
            # untouched here (frozen at their tips, D4) -- `fold._fold_file`'s whole-file
            # short-circuit already prefers this new whole-file symbol over them, so there is
            # never a second, competing live representation of the same bytes.
            if not is_pending and parent_entity_rep:
                # Flip parseable-entities -> whole-file (e.g. a `.py` renamed to `.txt`, or an
                # entity-tier path demoted to opaque): close the parent's entity/residue chains.
                _close_entity_rep(emit_entity, emit_other, old_ref, old_raw, bottom)
            before_version = _prior_whole_file_version(
                gb, old_ref, parent, tier_cfg_parent, old_raw
            )
            derived = tiers.is_derived(fc.path)
            if new_bytes is None:
                emit_other(fc.path, before_version, bottom, None, frozenset(), derived=derived)
            else:
                after_version = gb.blob_oid(sha, fc.path) or _content_version(new_bytes)
                emit_other(fc.path, before_version, after_version, new_bytes, frozenset(), derived=derived)
            continue

        new_entities = extract_file(fc.path, new_bytes) if new_bytes else []
        old_entities = extract_file(old_ref, old_raw) if old_raw else []

        if new_bytes and not new_entities and _parse_has_error(fc.path, new_bytes):
            # Unparseable mid-edit: degrade to whole-file for this path at this commit
            # rather than report zero entities (R7) -- no layout/residue this commit either,
            # since the file isn't meaningfully entity-decomposed right now.
            if not is_pending and parent_entity_rep:
                _close_entity_rep(emit_entity, emit_other, old_ref, old_raw, bottom)  # flip -> whole-file
            before_version = _prior_whole_file_version(
                gb, old_ref, parent, tier_cfg_parent, old_raw
            )
            # Same blob-OID version scheme as the opaque tier (U9): a text file flickering
            # parseable <-> unparseable must chain across the flip, so `_prior_whole_file_version`
            # (which looks the producer up via `blob_oid`) and this after_version have to agree.
            after_version = gb.blob_oid(sha, fc.path) or _content_version(new_bytes)
            emit_other(
                fc.path, before_version, after_version, new_bytes, frozenset(),
                derived=tiers.is_derived(fc.path),
            )
            continue

        close_flip_to_entities = not is_pending and parent_whole_file
        if close_flip_to_entities:
            # Flip whole-file -> parseable entities (U9): close the parent's whole-file symbol so it
            # stops competing with the entity representation. The parent carried no entity/residue
            # symbols, so treat its entity side as empty; the entities and residue born below are
            # re-birthed against this bottom by `_apply_rebirth_chaining` rather than pseudo-forking.
            emit_other(
                old_ref, _prior_whole_file_version(gb, old_ref, parent, tier_cfg_parent, old_raw),
                bottom, None,
            )
            old_entities = []

        new_entities_by_file[fc.path] = new_entities
        old_snaps = snapshot(old_entities, old_raw or b"")
        new_snaps = snapshot(new_entities, new_bytes or b"")
        by_id_before = {s.ent.id: s for s in old_snaps}
        m = match_pair(old_snaps, new_snaps, constraints)

        for a in m.modified:
            b = by_id_before[a.ent.id]
            emit_entity(
                a.ent.id,
                _positional_version(a.ent.id, b.content_hash),
                _positional_version(a.ent.id, a.content_hash),
                _entity_bytes(new_bytes, a.ent), _requires_of(a.ent.id, calls_by_src, entity_version),
            )
        for old, new in m.links:  # rename / move within one file
            if old.ent.kind != new.ent.kind:
                _emit_scope_reshape(emit_entity, old, new, new_bytes, calls_by_src, entity_version, bottom)
                continue
            uf.union(old.ent.id, new.ent.id)
            emit_entity(
                new.ent.id,
                _positional_version(old.ent.id, old.content_hash),
                _positional_version(new.ent.id, new.content_hash),
                _entity_bytes(new_bytes, new.ent), _requires_of(new.ent.id, calls_by_src, entity_version),
                via_move=True,
            )

        commit_added.extend(m.added)
        commit_removed.extend(m.removed)

        # Positional residue (byte-fidelity fold, 2026-07-08): one touch per gap between
        # top-level entities (keyed by the preceding entity's name, or the HEAD sentinel),
        # not one blob per file. A gap that changed text, or whose existence changed (a file
        # coming into or out of existence needs a touch to register that even when a gap's
        # text happens to be identical -- e.g. both empty), gets emitted; an unchanged gap is
        # silently skipped. A rename of a gap's anchor entity orphans that gap's chain (a new
        # anchor name is a fresh add) rather than surviving the rename -- a documented v1
        # boundary, the same tier as the anchor-fact mechanism's own "never revised" limit.
        # On a whole-file -> entities flip the parent had no residue symbols, so treat its residue
        # as empty (U9): every current segment is a fresh birth that `_apply_rebirth_chaining` will
        # chain onto the flip's bottom, never a `rework` off the parent's whole-file bytes (which is
        # a different symbol -- the version-scheme mix that left a re-added file's residue ungrounded).
        old_segments = (
            {} if close_flip_to_entities
            else (_residue_segments(old_raw, old_entities) if old_raw is not None else {})
        )
        new_segments = _residue_segments(new_bytes, new_entities) if new_bytes is not None else {}
        for anchor in sorted(set(old_segments) | set(new_segments)):
            old_seg = old_segments.get(anchor)
            new_seg = new_segments.get(anchor)
            if old_seg == new_seg:
                continue
            sym = f"{fc.path}::__residue__::{anchor}"
            before_v = _content_version(old_seg) if old_seg is not None else None
            if new_seg is None:
                emit_other(sym, before_v, bottom, None)
            else:
                emit_other(sym, before_v, _content_version(new_seg), new_seg)

    # Cross-file moves: a function cut from one file and pasted into another links by body.
    cross_links, matched_r, matched_a = link_residual(commit_removed, commit_added, constraints)
    for old, new in cross_links:
        # `new.ent.file` was already touched this commit, so its bytes are already sitting in
        # `new_blobs` from the batched prefetch above -- a per-entity `blob_bytes` subprocess
        # here would re-spawn git once per cross-file-moved symbol.
        new_file_raw = new_blobs.get((sha, new.ent.file)) or gb.blob_bytes(sha, new.ent.file) or b""
        if old.ent.kind != new.ent.kind:
            _emit_scope_reshape(emit_entity, old, new, new_file_raw, calls_by_src, entity_version, bottom)
            continue
        uf.union(old.ent.id, new.ent.id)
        emit_entity(
            new.ent.id,
            _positional_version(old.ent.id, old.content_hash),
            _positional_version(new.ent.id, new.content_hash),
            _entity_bytes(new_file_raw, new.ent), _requires_of(new.ent.id, calls_by_src, entity_version),
            via_move=True,
        )
    res_added = [s for s in commit_added if s.ent.id not in matched_a]
    res_removed = [s for s in commit_removed if s.ent.id not in matched_r]
    for s in res_added:
        raw = new_blobs.get((sha, s.ent.file)) or gb.blob_bytes(sha, s.ent.file) or b""
        emit_entity(
            s.ent.id, None, _positional_version(s.ent.id, s.content_hash),
            _entity_bytes(raw, s.ent), _requires_of(s.ent.id, calls_by_src, entity_version),
        )
    for s in res_removed:
        emit_entity(s.ent.id, _positional_version(s.ent.id, s.content_hash), bottom, None, frozenset())

    # Anchor facts (R6 layout): for each top-level entity freshly added this commit, an
    # independent pseudo-symbol recording which top-level entity (if any) precedes it --
    # never revised after the fact (this v1 doesn't track re-ordering), so its chain is
    # always a single add. One symbol per entity, not one shared chain per file, is what
    # makes two unrelated insertions commute instead of forking on a coincidentally-shared
    # file-wide "before" state.
    anchor_facts_by_file: dict[str, dict[str, str | None]] = {}
    for t in entity_touches:
        if t.before_version is not None or container_of.get(t.surface_id) is not None:
            continue  # only fresh, top-level adds get an anchor
        path, _, name = t.surface_id.partition("::")
        file_entities = new_entities_by_file.get(path)
        if file_entities is None:
            continue  # defensive: no live entity list for this path (shouldn't happen)
        if path not in anchor_facts_by_file:
            anchor_facts_by_file[path] = _top_level_anchor_facts(file_entities)
        predecessor = anchor_facts_by_file[path].get(name)
        marker = (predecessor or _ANCHOR_FIRST).encode("utf-8")
        emit_other(f"{path}::__anchor__::{name}", None, _content_version(marker), marker)

    # Untangle this commit's touched entities into def-use-connected groups (BET-A), then
    # bucket each touch by its group's deterministic anchor (lexicographically-smallest
    # member) so grouping doesn't depend on dict/set iteration order.
    touched_ids = {t.surface_id for t in entity_touches}
    for group in _untangle(touched_ids, graph_after.edges):
        anchor = sorted(group)[0]
        bucket = f"{sha}:{anchor}"
        for t in entity_touches:
            if t.surface_id in group:
                t.bucket = bucket

    # Rebirth chaining (U9): re-point every fresh `(symbol, None)` birth this commit re-does onto
    # the salted bottom of the ancestor commit that last closed it, so add->delete->re-add (and a
    # representation flip back) is one chain, not a pseudo-fork both of whose tips `fork_free`
    # drops. Runs *after* the anchor pass (a reborn top-level entity still gets its anchor fact from
    # its still-`None` birth) and after untangling (which keys on surface_id, not before_version).
    all_touches = entity_touches + other_touches
    _apply_rebirth_chaining(gb, sha, parent, all_touches)
    return all_touches


def mine(
    repo: Path | str,
    since: str | None = None,
    treat_as_root: str | None = None,
    include_dirty: bool = False,
    target: str = "HEAD",
) -> list[Op]:
    """Mine an ordered op stream from `repo`'s history. `since`, if given, restricts mining to
    commits after that witness SHA (`since..target`) -- each commit is still diffed against its
    own true parent, so incremental mining is exact, not an approximation. `target` (default HEAD)
    mines a different tip: sync passes a fetched teammate's `theirs_sha` to mine
    `merge_base..theirs` without a checkout (U20, C3). `treat_as_root`, if
    given, forces exactly that one commit's diff to be against the empty tree regardless of its
    real git parent -- the genesis-horizon mechanism (R10): everything at that commit becomes
    one add-op per symbol, and deeper history is never mined at all. `include_dirty`, if set,
    additionally mines one virtual "pending commit" for the current uncommitted working tree
    (diffed against real HEAD) after the real-commit loop -- its ops carry `provenance=()`
    until a real commit later witnesses that exact content (Gap 2, U7.5)."""
    repo = Path(repo)
    gb = GitBinding(repo)
    uf = _UnionFind()
    touches: list[_Touch] = []
    constraints = load_identity_constraints(repo)  # U11 R14: identity split/join corrections
    excluded_paths: set[str] = set()  # a path renamed out of `.sgt/` stays excluded for the rest
    # of this call, e.g. a `.sgt/` -> `.sgt.bak/` migration -- otherwise a later plain delete of
    # the renamed destination mines an ungrounded prune (its "add" was never recorded). Per-call
    # only, same tier as the union-find above (R14): a `since`-restricted incremental mine() that
    # starts after such a rename won't see it either.

    history = gb.history(since, target)
    for order, (sha, parent, _subject) in enumerate(history):
        if sha == treat_as_root:
            parent = None
        touches.extend(
            _mine_one(gb, uf, order, sha, parent, constraints=constraints, excluded_paths=excluded_paths)
        )

    if include_dirty:
        touches.extend(
            _mine_one(
                gb, uf, len(history), gb.working_tree_snapshot(), gb.head(), is_pending=True,
                excluded_paths=excluded_paths,
                constraints=constraints,
            )
        )

    return _build_ops(touches, uf)


def _build_ops(touches: list[_Touch], uf: _UnionFind) -> list[Op]:
    """Second pass: resolve rename-stable canonical ids now that every union is known, then
    fold each bucket's touches into one content-addressed Op."""
    by_bucket: dict[str, list[_Touch]] = {}
    for t in touches:
        by_bucket.setdefault(t.bucket, []).append(t)

    ops: list[Op] = []
    for bucket, group in by_bucket.items():
        sha = bucket.split(":", 1)[0]
        is_pending = any(t.is_pending for t in group)
        footprint: dict[str, tuple[str | None, str]] = {}
        images: Images = {}
        requires: set[tuple[str, str]] = set()
        any_added = False
        any_removed = False
        any_move = False
        any_derived = False
        for t in group:
            canon = uf.find(t.surface_id)
            footprint[canon] = (t.before_version, t.after_version)
            images[canon] = t.image
            requires.update((uf.find(r_id), r_ver) for r_id, r_ver in t.requires)
            any_added = any_added or t.before_version is None
            any_removed = any_removed or is_bottom(t.after_version)
            any_move = any_move or t.via_move
            any_derived = any_derived or t.derived

        requires = {(r_id, r_ver) for r_id, r_ver in requires if r_id not in footprint}  # never self
        if any_move and not any_added and not any_removed:
            kind = "move"
        elif any_removed and all(is_bottom(t.after_version) for t in group):
            kind = "prune"
        elif any_added and all(t.before_version is None for t in group):
            kind = "add"
        else:
            kind = "extend" if any_added else "rework"

        ops.append(
            make_op(
                footprint,
                images,
                requires=frozenset(requires),
                kind=kind,
                provenance=() if is_pending else (sha,),
                derived=any_derived,
            )
        )
    return ops
