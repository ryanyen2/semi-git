"""Mine an operation stream from git history.

Each commit is diffed against its first parent. A parseable file is split into its top-level
definitions (with their nested members), one residue gap after each definition plus the file head,
and one anchor fact per definition (which definition precedes it); a non-parseable or opaque path is
one whole-file symbol. A commit yields one op per unit it touched: a definition with its members,
trailing gap and anchor, the file head, or a whole file. A rename or move is one op that removes the
old id and creates the new one.

Every version is `op.version(symbol, previous, content)`, where `previous` is the symbol's version at
the commit's parent, read from history (`_Versions`). So the ops are a pure function of history since
the horizon: mining everything again gives the same ids as the incremental mines that built the
store, and a rebase that keeps each symbol's content sequence keeps its ids.

A merge settles every symbol on which its parents differ: unless it took one side's change whole
over sides that left the symbol as the merge base had it, the symbol gets a new version at the
merge and each parent's version is joined into it by an op of the merge. A symbol's chain may
therefore branch where two lines of history changed it, and always joins again at the merge that
brought them together, so a ref's history has one tip per symbol.
"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Parser

from ugh.core import tiers
from ugh.core.identity import link_residual, match_pair, snapshot
from ugh.core.op import Images, Op, is_bottom, make_op, unit_of, version
from ugh.entities.extract import Entity, _language, _language_for, extract_file
from ugh.entities.graph import build_entity_graph
from ugh.store.gitbind import GitBinding


@dataclass
class _Touch:
    """One symbol's change at one commit, before it is grouped into an Op."""

    symbol: str
    before: str | None
    after: str
    image: bytes | None
    bucket: str
    via_move: bool = False
    derived: bool = False
    requires: frozenset[tuple[str, str]] = frozenset()


# Content-addressed parse-error cache (a pure function of language and bytes), bounded LRU.
_PARSE_ERR_CACHE: "OrderedDict[tuple, bool]" = OrderedDict()
_PARSE_ERR_CACHE_MAX = 4096


def _parse_has_error(path: str, source: bytes) -> bool:
    """True if tree-sitter could not cleanly parse `source` as `path`'s language. It tells a file
    with no entities (a module of constants) from one unparseable mid-edit, which is mined whole."""
    lang = _language_for(path)
    if lang is None:
        return False
    key = (lang, hashlib.sha256(source).digest())
    cached = _PARSE_ERR_CACHE.get(key)
    if cached is not None:
        _PARSE_ERR_CACHE.move_to_end(key)
        return cached
    tree = Parser(_language(lang)).parse(source)
    result = tree.root_node.has_error
    _PARSE_ERR_CACHE[key] = result
    if len(_PARSE_ERR_CACHE) > _PARSE_ERR_CACHE_MAX:
        _PARSE_ERR_CACHE.popitem(last=False)
    return result


def _is_unparseable_whole_file(path: str, raw: bytes | None) -> bool:
    """True iff `raw` is non-empty, has no entities and does not parse cleanly, so mining
    represents it as one whole-file symbol. A parseable file with no entities is residue only."""
    return bool(raw) and not extract_file(path, raw) and _parse_has_error(path, raw)


def _entity_bytes(source: bytes, entity: Entity) -> bytes:
    """One entity's span, sliced by byte offsets without decoding, so every byte survives."""
    return source[entity.start_byte : entity.end_byte]


_RESIDUE_HEAD = "\x00HEAD\x00"  # the gap before a file's first top-level entity, or the whole file


def _residue_segments(raw: bytes, entities: list[Entity]) -> dict[str, bytes]:
    """The file's bytes outside its top-level entities, one segment per gap, keyed by the entity
    before it (`_RESIDUE_HEAD` for the first). The entities in order, each followed by its gap,
    are the file exactly."""
    top = sorted((e for e in entities if e.container is None), key=lambda e: e.start_byte)
    if not top:
        return {_RESIDUE_HEAD: raw}
    segments = {_RESIDUE_HEAD: raw[: top[0].start_byte]}
    for i, e in enumerate(top):
        end = top[i + 1].start_byte if i + 1 < len(top) else len(raw)
        segments[e.name] = raw[e.end_byte : end]
    return segments


_ANCHOR_FIRST = "\x00FIRST\x00"  # this entity is first in its file's top-level order


def _top_level_anchor_facts(entities: list[Entity]) -> dict[str, str | None]:
    """For each top-level entity, the top-level entity just before it, or None for the first. A
    nested member's position follows its container."""
    top_level = sorted((e for e in entities if e.container is None), key=lambda e: e.start_line)
    facts: dict[str, str | None] = {}
    prev: str | None = None
    for e in top_level:
        facts[e.name] = prev
        prev = e.name
    return facts


_SYM_IMAGES_CACHE: "OrderedDict[tuple, dict[str, bytes]]" = OrderedDict()
_SYM_IMAGES_CACHE_MAX = 4096


def _symbol_images(gb: GitBinding, tree_ish: str, path: str) -> dict[str, bytes]:
    """Every symbol mining represents `path` with at `tree_ish`, with its bytes: the whole file,
    or its entities, residue gaps and anchor facts. Empty when the path is absent or ignored."""
    key = (gb.repo_key(), tree_ish, path)
    hit = _SYM_IMAGES_CACHE.get(key)
    if hit is not None:
        _SYM_IMAGES_CACHE.move_to_end(key)
        return hit
    raw = gb.blob_bytes(tree_ish, path)
    out: dict[str, bytes] = {}
    if raw is not None:
        tier = tiers.resolve_tier(path, tiers.load_tiers_at(gb, tree_ish))
        if tier == "opaque" or (tier == "entity" and _is_unparseable_whole_file(path, raw)):
            out[path] = raw
        elif tier == "entity":
            ents = extract_file(path, raw)
            out.update((e.id, _entity_bytes(raw, e)) for e in ents)
            out.update((f"{path}::__residue__::{a}", seg) for a, seg in _residue_segments(raw, ents).items())
            out.update((f"{path}::__anchor__::{n}", (p or _ANCHOR_FIRST).encode("utf-8"))
                       for n, p in _top_level_anchor_facts(ents).items())
    _SYM_IMAGES_CACHE[key] = out
    if len(_SYM_IMAGES_CACHE) > _SYM_IMAGES_CACHE_MAX:
        _SYM_IMAGES_CACHE.popitem(last=False)
    return out


class _Versions:
    """A symbol's version at a commit, from history: the version the nearest commit that touched it
    produced, walking first parents through the commits that changed its path. A merge that took a
    symbol's content from another parent continues on that parent. Touches come from this run, else
    from the store's ops by their provenance (the store is a memo of the same function)."""

    def __init__(self, gb: GitBinding, repo: Path) -> None:
        self.gb = gb
        self.repo = repo
        self.run: dict[str, dict[str, str]] = {}
        self._stored: dict[str, dict[str, str]] | None = None
        self._logs: dict[tuple[str, str], list[tuple[str, list[str]]]] = {}
        self._memo: dict[tuple[str, str], str | None] = {}

    def _touched(self, sha: str) -> dict[str, str]:
        if sha in self.run:
            return self.run[sha]
        if self._stored is None:
            from ugh.core import opindex
            from ugh.core.op import MINER_VERSION

            self._stored = {}
            if (self.repo / ".sgt" / "ops").is_dir():
                for op in opindex.index_ops(self.repo):
                    if op.miner_version != MINER_VERSION:
                        continue
                    for c in op.provenance:
                        row = self._stored.setdefault(c, {})
                        row.update((sym, after) for sym, (_b, after) in op.footprint.items())
        return self._stored.get(sha, {})

    def _log(self, sha: str, path: str) -> list[tuple[str, list[str]]]:
        # ponytail: one `git log -- path` per (commit, path) looked up; a per-run path index if
        # mining long histories in one call gets slow.
        key = (sha, path)
        if key not in self._logs:
            self._logs[key] = self.gb.first_parent_path_log(sha, path)
        return self._logs[key]

    def at(self, sha: str | None, sym: str) -> str | None:
        """`sym`'s version at commit `sha`: None if it never existed there, a removal version if it
        was removed."""
        if sha is None:
            return None
        key = (sha, sym)
        if key in self._memo:
            return self._memo[key]
        path = sym.split("::", 1)[0]
        result = None
        for d, parents in self._log(sha, path):
            touched = self._touched(d)
            if sym in touched:
                result = touched[sym]
                break
            if len(parents) > 1:
                image = _symbol_images(self.gb, d, path).get(sym)
                if image != _symbol_images(self.gb, parents[0], path).get(sym):
                    other = next((p for p in parents[1:]
                                  if _symbol_images(self.gb, p, path).get(sym) == image), None)
                    if other is not None:
                        result = self.at(other, sym)
                        break
        self._memo[key] = result
        return result


def _settled(gb: GitBinding, versions: _Versions, sha: str, parents: list[str],
             excluded: set[str]) -> dict[str, tuple[str, list[str], bytes | None]]:
    """The symbols merge `sha` settles: every symbol on which its parents differ. Each maps to its
    version at the merge, the parent versions joined into it, and its content. A symbol whose
    content equals a parent's keeps that parent's version; otherwise it gets a new version from
    the first parent that changed it. A parent version that is the merge base's needs no join."""
    base = gb.merge_base(parents[0], parents[1])
    first = set(gb.changed_paths(base, parents[0]))
    paths: set[str] = set()
    for other in parents[1:]:
        theirs = set(gb.changed_paths(base, other))
        paths |= ((first & theirs) | ((theirs - first) & set(gb.changed_paths(other, sha)))
                  | ((first - theirs) & set(gb.changed_paths(parents[0], sha))))
    paths = {p for p in paths if not p.startswith(".sgt/") and p not in excluded}
    paths -= {p for c in [*parents, sha] for p in gb.symlink_paths(c, sorted(paths))}
    out: dict[str, tuple[str, list[str], bytes | None]] = {}
    for path in sorted(paths):
        at = {p: _symbol_images(gb, p, path) for p in parents}
        here = _symbol_images(gb, sha, path)
        for sym in sorted(set().union(*at.values())):
            vs = [versions.at(p, sym) for p in parents]
            if len(set(vs)) == 1:
                continue
            vb = versions.at(base, sym)
            joined = list(dict.fromkeys(v for v in vs if v is not None and v != vb))
            image = here.get(sym)
            kept = next((v for p, v in zip(parents, vs) if v is not None and at[p].get(sym) == image), None)
            target = kept if kept is not None else version(sym, joined[0], image)
            out[sym] = (target, [v for v in joined if v != target], image)
    return out


def _mine_one(
    gb: GitBinding, versions: _Versions, sha: str, parent: str | None, is_pending: bool = False,
    excluded_paths: set[str] | None = None,
) -> list[_Touch]:
    """One commit's touched symbols, each versioned from its version at `parent`. `sha` need only
    be a tree-ish (the dirty pass mines `GitBinding.working_tree_snapshot()` against HEAD)."""
    excluded_paths = set() if excluded_paths is None else excluded_paths
    tier_cfgs = tiers.load_tiers_at_many(gb, [sha, parent] if parent else [sha])
    tier_cfg = tier_cfgs[sha]
    tier_cfg_parent = tier_cfgs.get(parent) if parent else None
    graph_after = build_entity_graph(gb.tree_at(sha))
    uses_by_src: dict[str, set[str]] = {}
    for e in graph_after.edges:
        if e.type == "uses":
            uses_by_src.setdefault(e.src, set()).add(e.dst)

    touches: list[_Touch] = []
    moves: list[tuple[str, str]] = []  # (old id, new id): a rename or move is one op
    added: list = []
    removed: list = []

    def emit(sym: str, image: bytes | None, *, via_move: bool = False, derived: bool = False) -> None:
        before = versions.at(parent, sym)
        touches.append(_Touch(sym, before, version(sym, before, image), image,
                              f"{sha}:{unit_of(sym)}", via_move=via_move, derived=derived))

    def close_entity_rep(path: str, raw: bytes) -> None:
        # A file that stops being parsed as entities closes every entity and gap it had.
        ents = extract_file(path, raw)
        for e in ents:
            emit(e.id, None)
        for anchor, seg in _residue_segments(raw, ents).items():
            if seg:
                emit(f"{path}::__residue__::{anchor}", None)

    diffs = gb.diff_name_and_text(parent, sha)
    other_parents = [p for p in gb.parents(sha) if p != parent] if not is_pending and parent else []
    settled = _settled(gb, versions, sha, [parent, *other_parents], excluded_paths) if other_parents else {}
    if other_parents:
        # A merge mints only the paths it resolved differently from every parent.
        differs_from_all_sides = {fc.path for fc in diffs}
        for op2 in other_parents:
            differs_from_all_sides &= {fc.path for fc in gb.diff_name_and_text(op2, sha)}
        diffs = [fc for fc in diffs if fc.path in differs_from_all_sides]
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
    symlinked = gb.symlink_paths(sha, [fc.path for fc in diffs])
    if parent is not None:
        symlinked |= gb.symlink_paths(parent, [fc.old_path or fc.path for fc in diffs])

    for fc in diffs:
        old_ref = fc.old_path or fc.path
        if (fc.path.startswith(".sgt/") or old_ref.startswith(".sgt/")
                or fc.path in excluded_paths or old_ref in excluded_paths
                or fc.path in symlinked or old_ref in symlinked):
            # ugh's own state is never mined, and a rename carries the exclusion to its new path.
            excluded_paths.add(fc.path)
            excluded_paths.add(old_ref)
            continue
        tier = tiers.resolve_tier(fc.path, tier_cfg)
        new_bytes = new_blobs[(sha, fc.path)]
        old_raw = old_blobs.get((parent, old_ref)) if parent is not None else None
        parent_tier = (tiers.resolve_tier(old_ref, tier_cfg_parent)
                       if parent is not None and tier_cfg_parent is not None else None)
        parent_present = old_raw is not None and parent_tier != "ignored"
        parent_whole_file = parent_present and (
            parent_tier == "opaque" or _is_unparseable_whole_file(old_ref, old_raw))
        parent_entity_rep = parent_present and not parent_whole_file

        if tier == "ignored":
            continue

        derived = tiers.is_derived(fc.path)
        if tier == "opaque" or (new_bytes and not extract_file(fc.path, new_bytes)
                                and _parse_has_error(fc.path, new_bytes)):
            # A whole-file symbol: no grammar, an opaque override, or unparseable mid-edit.
            if not is_pending and parent_entity_rep:
                close_entity_rep(old_ref, old_raw)
            if old_ref != fc.path and parent_whole_file:
                emit(old_ref, None, derived=derived)
            emit(fc.path, new_bytes, derived=derived)
            continue

        new_entities = extract_file(fc.path, new_bytes) if new_bytes else []
        old_entities = extract_file(old_ref, old_raw) if old_raw else []
        if not is_pending and parent_whole_file:
            emit(old_ref, None)  # whole-file -> entities: close the whole-file symbol
            old_entities = []
            old_raw = None

        old_snaps = snapshot(old_entities, old_raw or b"")
        new_snaps = snapshot(new_entities, new_bytes or b"")
        m = match_pair(old_snaps, new_snaps)
        for a in m.modified:
            emit(a.ent.id, _entity_bytes(new_bytes, a.ent))
        for old, new in m.links:  # rename within one file: the old id goes, the new one comes
            moved = old.ent.kind == new.ent.kind  # a change of scope is a delete and an add
            emit(old.ent.id, None, via_move=moved)
            emit(new.ent.id, _entity_bytes(new_bytes, new.ent), via_move=moved)
            if moved:
                moves.append((old.ent.id, new.ent.id))
        added.extend(m.added)
        removed.extend(m.removed)

        old_segments = _residue_segments(old_raw, old_entities) if old_raw is not None else {}
        new_segments = _residue_segments(new_bytes, new_entities) if new_bytes is not None else {}
        for anchor in sorted(set(old_segments) | set(new_segments)):
            if old_segments.get(anchor) != new_segments.get(anchor):
                emit(f"{fc.path}::__residue__::{anchor}", new_segments.get(anchor))

        old_facts = _top_level_anchor_facts(old_entities) if old_raw is not None else {}
        new_facts = _top_level_anchor_facts(new_entities) if new_bytes is not None else {}
        for name in sorted(new_facts):
            if name not in old_facts or old_facts[name] != new_facts[name]:
                emit(f"{fc.path}::__anchor__::{name}", (new_facts[name] or _ANCHOR_FIRST).encode("utf-8"))

    cross_links, matched_r, matched_a = link_residual(removed, added)
    for old, new in cross_links:  # a definition cut from one file and pasted into another
        raw = new_blobs.get((sha, new.ent.file)) or gb.blob_bytes(sha, new.ent.file) or b""
        moved = old.ent.kind == new.ent.kind
        emit(old.ent.id, None, via_move=moved)
        emit(new.ent.id, _entity_bytes(raw, new.ent), via_move=moved)
        if moved:
            moves.append((old.ent.id, new.ent.id))
    for s in added:
        if s.ent.id not in matched_a:
            raw = new_blobs.get((sha, s.ent.file)) or gb.blob_bytes(sha, s.ent.file) or b""
            emit(s.ent.id, _entity_bytes(raw, s.ent))
    for s in removed:
        if s.ent.id not in matched_r:
            emit(s.ent.id, None)

    if other_parents:
        # A merge settles what its parents differ on (`_settled`); any other symbol whose content
        # equals another parent's keeps that parent's version.
        touches = [t for t in touches if t.symbol not in settled and not any(
            _symbol_images(gb, p, t.symbol.split("::", 1)[0]).get(t.symbol) == t.image
            for p in other_parents)]
        touches += [_Touch(sym, v, target, image, f"{sha}:{unit_of(sym)}" + (f":{i}" if i else ""),
                           derived=tiers.is_derived(sym.split("::", 1)[0]))
                    for sym, (target, joined, image) in settled.items()
                    for i, v in enumerate(joined)]

    bucket_of = {unit_of(old): f"{sha}:{unit_of(new)}" for old, new in moves}
    for t in touches:
        t.bucket = bucket_of.get(unit_of(t.symbol), t.bucket)
    touched = {t.symbol for t in touches}
    for t in touches:
        if t.image is not None:
            uses = uses_by_src.get(t.symbol, ())
            t.requires = frozenset(
                (dst, v) for dst in uses
                if dst not in touched and (v := versions.at(parent, dst)) is not None and not is_bottom(v))
    if not is_pending:
        versions.run[sha] = {t.symbol: t.after for t in touches}
    return touches


def mine(
    repo: Path | str,
    since: str | None = None,
    treat_as_root: str | None = None,
    include_dirty: bool = False,
    target: str = "HEAD",
    deadline: float | None = None,
    history_override: list[tuple[str, str | None, str]] | None = None,
) -> tuple[list[Op], str | None]:
    """Mine an ordered op stream from `repo`'s history, oldest first. `since` restricts mining to
    `since..target`; each commit is still diffed against its own first parent and versioned from
    history, so incremental mining gives the same ops as mining everything. `treat_as_root` mines
    that one commit against the empty tree (the genesis horizon). `include_dirty` also mines the
    working tree against HEAD; those ops carry `provenance=()`. `deadline` (a `time.monotonic()`
    cutoff) stops the loop after the commit in flight (at least one commit is always mined), and
    skips the dirty pass when history is left.

    Returns `(ops, last_sha)`: the last commit mined, or None when there was nothing to mine."""
    repo = Path(repo)
    gb = GitBinding(repo)
    from ugh.entities import extract as _extract
    from ugh.entities import graph as _graph_mod

    _extract.attach_persistent_cache(repo)
    _graph_mod.attach_persistent_refs_cache(repo)
    versions = _Versions(gb, repo)
    ops: list[Op] = []
    excluded_paths: set[str] = set()
    history = history_override if history_override is not None else gb.history(since, target)
    last_sha: str | None = None
    hit_deadline = False
    for sha, parent, _subject in history:
        if sha == treat_as_root:
            parent = None
        ops.extend(_build_ops(_mine_one(gb, versions, sha, parent, excluded_paths=excluded_paths),
                              (sha,)))
        last_sha = sha
        if deadline is not None and time.monotonic() >= deadline:
            hit_deadline = sha != history[-1][0]
            break
    if include_dirty and not hit_deadline:
        ops.extend(_build_ops(_mine_one(gb, versions, gb.working_tree_snapshot(), gb.head(),
                                        is_pending=True, excluded_paths=excluded_paths), ()))
    _extract.flush_persistent_cache()
    _graph_mod.flush_persistent_refs_cache()
    return ops, last_sha


def _born(before: str | None) -> bool:
    return before is None or is_bottom(before)


def _build_ops(touches: list[_Touch], provenance: tuple[str, ...]) -> list[Op]:
    """One op per unit a commit touched (a rename or move is one op)."""
    by_bucket: dict[str, list[_Touch]] = {}
    for t in touches:
        by_bucket.setdefault(t.bucket, []).append(t)
    saved = {t.symbol for t in touches}
    ops: list[Op] = []
    for group in by_bucket.values():
        footprint = {t.symbol: (t.before, t.after) for t in group}
        images: Images = {t.symbol: t.image for t in group}
        requires = {r for t in group for r in t.requires if r[0] not in saved}
        if any(t.via_move for t in group):
            kind = "move"
        elif all(is_bottom(t.after) for t in group):
            kind = "prune"
        elif all(_born(t.before) for t in group):
            kind = "add"
        else:
            kind = "extend" if any(_born(t.before) for t in group) else "rework"
        ops.append(make_op(footprint, images, requires=frozenset(requires), kind=kind,
                           provenance=provenance, derived=any(t.derived for t in group)))
    return ops
