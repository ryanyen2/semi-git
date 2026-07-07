"""Mine an operation stream from git history (ADR S3.2; plan R1, R2, R7, R12, R22).

Promoted from ``experiments/patch_clustering/mine.py`` (the "kernel embryo", plan U2) and
extended with what the experiment didn't need: whole-file pseudo-symbols for non-parseable
paths (config, docs, binaries -- R7), one residue pseudo-symbol per file for module-level
statements outside any entity span, one layout pseudo-symbol per file for top-level slot order,
def-use untangling of a single commit's touched entities into separate ops (ClusterChanges-
style -- BET-A), and content-addressed `Op` construction stamped with the miner version (R12)
via `sgt.core.op.make_op`.

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
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Parser

from sgt.core.identity import Snap, detect_splits_merges, link_residual, match_pair, snapshot
from sgt.core.op import Images, Op, make_op
from sgt.entities.extract import Entity, _language, _language_for, extract_file
from sgt.entities.graph import EntityEdge, build_entity_graph
from sgt.store.gitbind import GitBinding

_BOTTOM = "⊥"  # the ADR's "removed" version/image sentinel


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
    requires: frozenset[str]
    via_move: bool = False
    bucket: str | None = None  # filled in after untangling; None only transiently


def _content_version(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _is_binary(data: bytes) -> bool:
    """The same heuristic git itself uses: a NUL byte means binary."""
    return b"\x00" in data


def _parse_has_error(path: str, source: str) -> bool:
    """True if tree-sitter could not cleanly parse `source` as `path`'s language -- the signal
    that separates "legitimately no entities" (e.g. a pure-constants module) from "unparseable
    mid-edit", which must degrade to a whole-file symbol rather than report zero entities (R7)."""
    lang = _language_for(path)
    if lang is None:
        return False
    tree = Parser(_language(lang)).parse(bytes(source, "utf-8"))
    return tree.root_node.has_error


def _entity_bytes(source: str, entity: Entity) -> bytes:
    """Verbatim bytes for one entity's line span -- the same line-granularity addressing
    `Entity.start_line`/`end_line` and `FileChange.new_ranges` already use elsewhere in sgt."""
    lines = source.splitlines()
    return "\n".join(lines[entity.start_line - 1 : entity.end_line]).encode("utf-8")


def _residue_lines(source: str, entities: list[Entity]) -> str:
    """Module-level source outside every entity's line span -- imports, constants, `__main__`
    blocks (ADR S3.5: residue is "module-level statement groups ... with ordinary chains").
    One pseudo-symbol per file, not one per statement."""
    covered: set[int] = set()
    for e in entities:
        covered.update(range(e.start_line, e.end_line + 1))
    lines = source.splitlines()
    return "\n".join(line for i, line in enumerate(lines, start=1) if i not in covered)


def _layout_image(entities: list[Entity]) -> bytes:
    """The file's top-level slot order -- the anchor list the fold (U5) linearizes (R6). Only
    top-level entities (no container) define slot order; nested methods follow their class."""
    order = [e.name for e in sorted(entities, key=lambda e: e.start_line) if e.container is None]
    return "\n".join(order).encode("utf-8")


def _emit_scope_reshape(emit_entity, old: Snap, new: Snap, new_src: str, calls_by_src) -> None:
    """A link the identity matcher found by body/structure similarity, but across a *kind*
    change (function -> method or vice versa) -- a genuine scope reshape, not a rename. The
    matcher tiers stay verbatim (kind is deliberately not part of tiers 2/2b's match key), but
    mine.py refuses to weld two different scopes into one chain: both ops still land from this
    commit (split provenance), just as delete + add rather than a silent move."""
    emit_entity(old.ent.id, old.content_hash, _BOTTOM, None, frozenset())
    emit_entity(
        new.ent.id, None, new.content_hash,
        _entity_bytes(new_src, new.ent), frozenset(calls_by_src.get(new.ent.id, ())),
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


def mine(repo: Path | str, since: str | None = None) -> list[Op]:
    """Mine an ordered op stream from `repo`'s history. `since`, if given, restricts mining to
    commits after that witness SHA (`since..HEAD`) -- each commit is still diffed against its
    own true parent, so incremental mining is exact, not an approximation."""
    repo = Path(repo)
    gb = GitBinding(repo)
    uf = _UnionFind()
    touches: list[_Touch] = []

    for order, (sha, parent, _subject) in enumerate(gb.history(since)):
        codebase_after = gb.tree_at(sha)
        graph_after = build_entity_graph(codebase_after)
        calls_by_src: dict[str, set[str]] = {}
        for e in graph_after.edges:
            if e.type in ("calls", "imports"):
                calls_by_src.setdefault(e.src, set()).add(e.dst)

        entity_touches: list[_Touch] = []
        other_touches: list[_Touch] = []
        commit_added: list[Snap] = []
        commit_removed: list[Snap] = []

        def emit_entity(sym: str, before, after, image, requires, via_move=False) -> None:
            uf.add(sym)
            entity_touches.append(_Touch(order, sym, before, after, image, requires, via_move))

        def emit_other(sym: str, before, after, image, requires=frozenset()) -> None:
            other_touches.append(
                _Touch(order, sym, before, after, image, requires, bucket=f"{sha}:{sym}")
            )

        for fc in gb.diff_name_and_text(parent, sha):
            lang = _language_for(fc.path)
            new_bytes = gb.blob_bytes(sha, fc.path)
            old_ref = fc.old_path or fc.path

            if lang is None:
                # Whole-file pseudo-symbol (R7): unsupported language, config, docs, binary.
                before_version = gb.blob_oid(parent, old_ref) if parent else None
                if new_bytes is None:
                    emit_other(fc.path, before_version, _BOTTOM, None, frozenset())
                elif _is_binary(new_bytes):
                    after_version = gb.blob_oid(sha, fc.path) or _content_version(new_bytes)
                    emit_other(fc.path, before_version, after_version, new_bytes, frozenset())
                else:
                    emit_other(
                        fc.path, before_version, _content_version(new_bytes), new_bytes, frozenset()
                    )
                continue

            new_src = new_bytes.decode("utf-8", "replace") if new_bytes else ""
            new_entities = extract_file(fc.path, new_src) if new_src else []
            old_src = gb.file_at(parent, old_ref) if parent else None
            old_entities = extract_file(old_ref, old_src) if old_src else []

            if new_src and not new_entities and _parse_has_error(fc.path, new_src):
                # Unparseable mid-edit: degrade to whole-file for this path at this commit
                # rather than report zero entities (R7) -- no layout/residue this commit either,
                # since the file isn't meaningfully entity-decomposed right now.
                before_version = gb.blob_oid(parent, old_ref) if parent else None
                emit_other(
                    fc.path, before_version, _content_version(new_bytes), new_bytes, frozenset()
                )
                continue

            old_snaps = snapshot(old_entities, old_src or "")
            new_snaps = snapshot(new_entities, new_src)
            by_id_before = {s.ent.id: s for s in old_snaps}
            m = match_pair(old_snaps, new_snaps)

            for a in m.modified:
                b = by_id_before[a.ent.id]
                emit_entity(
                    a.ent.id, b.content_hash, a.content_hash,
                    _entity_bytes(new_src, a.ent), frozenset(calls_by_src.get(a.ent.id, ())),
                )
            for old, new in m.links:  # rename / move within one file
                if old.ent.kind != new.ent.kind:
                    _emit_scope_reshape(emit_entity, old, new, new_src, calls_by_src)
                    continue
                uf.union(old.ent.id, new.ent.id)
                emit_entity(
                    new.ent.id, old.content_hash, new.content_hash,
                    _entity_bytes(new_src, new.ent), frozenset(calls_by_src.get(new.ent.id, ())),
                    via_move=True,
                )

            commit_added.extend(m.added)
            commit_removed.extend(m.removed)

            # Layout + residue pseudo-symbols -- once per changed file per commit, only when
            # they actually changed.
            old_layout = _layout_image(old_entities) if old_entities else b""
            new_layout = _layout_image(new_entities) if (new_bytes is not None) else b""
            if old_layout != new_layout:
                sym = f"{fc.path}::__layout__"
                before_v = _content_version(old_layout) if old_entities else None
                if new_bytes is None:
                    emit_other(sym, before_v, _BOTTOM, None)
                else:
                    emit_other(sym, before_v, _content_version(new_layout), new_layout)

            old_residue = _residue_lines(old_src, old_entities) if old_src else ""
            new_residue = _residue_lines(new_src, new_entities) if new_src else ""
            if old_residue != new_residue:
                sym = f"{fc.path}::__residue__"
                residue_bytes = new_residue.encode("utf-8")
                before_v = _content_version(old_residue.encode("utf-8")) if old_src else None
                if new_bytes is None:
                    emit_other(sym, before_v, _BOTTOM, None)
                else:
                    emit_other(sym, before_v, _content_version(residue_bytes), residue_bytes)

        # Cross-file moves: a function cut from one file and pasted into another links by body.
        cross_links, matched_r, matched_a = link_residual(commit_removed, commit_added)
        for old, new in cross_links:
            new_file_src = gb.file_at(sha, new.ent.file) or ""
            if old.ent.kind != new.ent.kind:
                _emit_scope_reshape(emit_entity, old, new, new_file_src, calls_by_src)
                continue
            uf.union(old.ent.id, new.ent.id)
            emit_entity(
                new.ent.id, old.content_hash, new.content_hash,
                _entity_bytes(new_file_src, new.ent), frozenset(calls_by_src.get(new.ent.id, ())),
                via_move=True,
            )
        res_added = [s for s in commit_added if s.ent.id not in matched_a]
        res_removed = [s for s in commit_removed if s.ent.id not in matched_r]
        for s in res_added:
            src = gb.file_at(sha, s.ent.file) or ""
            emit_entity(
                s.ent.id, None, s.content_hash,
                _entity_bytes(src, s.ent), frozenset(calls_by_src.get(s.ent.id, ())),
            )
        for s in res_removed:
            emit_entity(s.ent.id, s.content_hash, _BOTTOM, None, frozenset())

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

        touches.extend(entity_touches)
        touches.extend(other_touches)

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
        footprint: dict[str, tuple[str | None, str]] = {}
        images: Images = {}
        requires: set[str] = set()
        any_added = False
        any_removed = False
        any_move = False
        for t in group:
            canon = uf.find(t.surface_id)
            footprint[canon] = (t.before_version, t.after_version)
            images[canon] = t.image
            requires.update(uf.find(r) for r in t.requires)
            any_added = any_added or t.before_version is None
            any_removed = any_removed or t.after_version == _BOTTOM
            any_move = any_move or t.via_move

        requires -= set(footprint)  # a symbol never "requires" itself
        if any_move and not any_added and not any_removed:
            kind = "move"
        elif any_removed and all(t.after_version == _BOTTOM for t in group):
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
                provenance=(sha,),
            )
        )
    return ops
