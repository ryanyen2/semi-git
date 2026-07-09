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
from sgt.core.identity import Snap, detect_splits_merges, link_residual, match_pair, snapshot
from sgt.core.op import BOTTOM, Images, Op, make_op
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


def _parse_has_error(path: str, source: bytes) -> bool:
    """True if tree-sitter could not cleanly parse `source` as `path`'s language -- the signal
    that separates "legitimately no entities" (e.g. a pure-constants module) from "unparseable
    mid-edit", which must degrade to a whole-file symbol rather than report zero entities (R7)."""
    lang = _language_for(path)
    if lang is None:
        return False
    tree = Parser(_language(lang)).parse(source)
    return tree.root_node.has_error


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
    emit_entity, old: Snap, new: Snap, new_raw: bytes, calls_by_src, entity_version
) -> None:
    """A link the identity matcher found by body/structure similarity, but across a *kind*
    change (function -> method or vice versa) -- a genuine scope reshape, not a rename. The
    matcher tiers stay verbatim (kind is deliberately not part of tiers 2/2b's match key), but
    mine.py refuses to weld two different scopes into one chain: both ops still land from this
    commit (split provenance), just as delete + add rather than a silent move."""
    emit_entity(old.ent.id, _positional_version(old.ent.id, old.content_hash), BOTTOM, None, frozenset())
    emit_entity(
        new.ent.id, None, _positional_version(new.ent.id, new.content_hash),
        _entity_bytes(new_raw, new.ent), _requires_of(new.ent.id, calls_by_src, entity_version),
    )


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
    excluded_paths = set() if excluded_paths is None else excluded_paths
    """One commit's touched symbols -- the loop body `mine()` runs once per real commit, plus
    (when `include_dirty=True`) once more for the working tree's uncommitted state, diffed
    against real HEAD exactly the same way (Gap 2, U7.5). `sha` need only be a tree-ish (a real
    commit, or `GitBinding.working_tree_snapshot()`'s synthetic tree object) -- every
    `GitBinding` read used below accepts either."""
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

    def emit_other(sym: str, before, after, image, requires=frozenset()) -> None:
        other_touches.append(
            _Touch(
                order, sym, before, after, image, requires,
                bucket=f"{sha}:{sym}", is_pending=is_pending,
            )
        )

    for fc in gb.diff_name_and_text(parent, sha):
        old_ref_path = fc.old_path or fc.path
        if (
            fc.path.startswith(".sgt/")
            or old_ref_path.startswith(".sgt/")
            or fc.path in excluded_paths
            or old_ref_path in excluded_paths
        ):
            # sgt's own state, never mined as codebase content -- and once excluded, a rename
            # carries the exclusion to its new path too (e.g. a `.sgt/` -> `.sgt.bak/` migration),
            # so a later plain delete of that destination doesn't mine an ungrounded prune for a
            # path whose "add" was never recorded.
            excluded_paths.add(fc.path)
            excluded_paths.add(old_ref_path)
            continue
        lang = _language_for(fc.path)
        new_bytes = gb.blob_bytes(sha, fc.path)
        old_ref = old_ref_path

        if lang is None:
            # Whole-file pseudo-symbol (R7): unsupported language, config, docs, binary.
            # Versioned by git blob OID uniformly (not content-hashed text vs. OID'd binary
            # separately) -- before_version is always looked up via blob_oid, so after_version
            # must use the same scheme or a text file's chain could never link across commits.
            before_version = gb.blob_oid(parent, old_ref) if parent else None
            if new_bytes is None:
                emit_other(fc.path, before_version, BOTTOM, None, frozenset())
            else:
                after_version = gb.blob_oid(sha, fc.path) or _content_version(new_bytes)
                emit_other(fc.path, before_version, after_version, new_bytes, frozenset())
            continue

        new_entities = extract_file(fc.path, new_bytes) if new_bytes else []
        old_raw = gb.blob_bytes(parent, old_ref) if parent else None
        old_entities = extract_file(old_ref, old_raw) if old_raw else []

        if new_bytes and not new_entities and _parse_has_error(fc.path, new_bytes):
            # Unparseable mid-edit: degrade to whole-file for this path at this commit
            # rather than report zero entities (R7) -- no layout/residue this commit either,
            # since the file isn't meaningfully entity-decomposed right now.
            before_version = gb.blob_oid(parent, old_ref) if parent else None
            emit_other(
                fc.path, before_version, _content_version(new_bytes), new_bytes, frozenset()
            )
            continue

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
                _emit_scope_reshape(emit_entity, old, new, new_bytes, calls_by_src, entity_version)
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
        old_segments = _residue_segments(old_raw, old_entities) if old_raw is not None else {}
        new_segments = _residue_segments(new_bytes, new_entities) if new_bytes is not None else {}
        for anchor in sorted(set(old_segments) | set(new_segments)):
            old_seg = old_segments.get(anchor)
            new_seg = new_segments.get(anchor)
            if old_seg == new_seg:
                continue
            sym = f"{fc.path}::__residue__::{anchor}"
            before_v = _content_version(old_seg) if old_seg is not None else None
            if new_seg is None:
                emit_other(sym, before_v, BOTTOM, None)
            else:
                emit_other(sym, before_v, _content_version(new_seg), new_seg)

    # Cross-file moves: a function cut from one file and pasted into another links by body.
    cross_links, matched_r, matched_a = link_residual(commit_removed, commit_added, constraints)
    for old, new in cross_links:
        new_file_raw = gb.blob_bytes(sha, new.ent.file) or b""
        if old.ent.kind != new.ent.kind:
            _emit_scope_reshape(emit_entity, old, new, new_file_raw, calls_by_src, entity_version)
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
        raw = gb.blob_bytes(sha, s.ent.file) or b""
        emit_entity(
            s.ent.id, None, _positional_version(s.ent.id, s.content_hash),
            _entity_bytes(raw, s.ent), _requires_of(s.ent.id, calls_by_src, entity_version),
        )
    for s in res_removed:
        emit_entity(s.ent.id, _positional_version(s.ent.id, s.content_hash), BOTTOM, None, frozenset())

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

    return entity_touches + other_touches


def mine(
    repo: Path | str,
    since: str | None = None,
    treat_as_root: str | None = None,
    include_dirty: bool = False,
) -> list[Op]:
    """Mine an ordered op stream from `repo`'s history. `since`, if given, restricts mining to
    commits after that witness SHA (`since..HEAD`) -- each commit is still diffed against its
    own true parent, so incremental mining is exact, not an approximation. `treat_as_root`, if
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

    history = gb.history(since)
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
        for t in group:
            canon = uf.find(t.surface_id)
            footprint[canon] = (t.before_version, t.after_version)
            images[canon] = t.image
            requires.update((uf.find(r_id), r_ver) for r_id, r_ver in t.requires)
            any_added = any_added or t.before_version is None
            any_removed = any_removed or t.after_version == BOTTOM
            any_move = any_move or t.via_move

        requires = {(r_id, r_ver) for r_id, r_ver in requires if r_id not in footprint}  # never self
        if any_move and not any_added and not any_removed:
            kind = "move"
        elif any_removed and all(t.after_version == BOTTOM for t in group):
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
            )
        )
    return ops
