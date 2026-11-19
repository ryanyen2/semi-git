"""Entity matching across a commit, after sem's ``match_entities``: never link entities by name
alone. Tiers of decreasing confidence, keyed on the hashes ``Entity`` carries:

    tier 1   exact surface id (``file::name``)      content differs -> modified
    tier 2   identical body (content hash)          rename / move  (link)
    tier 2b  identical structure (structural hash)  rename / move  (link)
    tier 3   fuzzy Jaccard >= 0.80, size-guarded    rename / move  (link)

The size, kind and threshold guards keep two unrelated ``__init__`` methods from linking.
"""

from __future__ import annotations

from dataclasses import dataclass

from ugh.entities.extract import Entity

_FUZZY = 0.80        # sem THRESHOLD: min token-Jaccard to call a rename/move
_SIZE_RATIO = 0.50   # sem SIZE_RATIO_CUTOFF: reject pairs whose token counts differ > 2x


@dataclass(frozen=True)
class Snap:
    """An entity plus the signals sem matches on: body hashes (from the Entity) + token set."""

    ent: Entity
    content_hash: str
    structural_hash: str
    tokens: frozenset[bytes]  # raw byte tokens: only the fuzzy tier reads them, so no decode
    ntok: int


def snapshot(entities: list[Entity], source: bytes | str) -> list[Snap]:
    """Each entity with its hashes and the token set of its bytes in `source` (a `str` is encoded
    as UTF-8)."""
    src = source if isinstance(source, bytes) else source.encode("utf-8")
    out: list[Snap] = []
    for e in entities:
        toks = src[e.start_byte : e.end_byte].split()
        out.append(Snap(e, e.content_hash, e.structural_hash, frozenset(toks), len(toks)))
    return out


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a) + len(b) - inter
    return inter / union if union else 0.0


def _is_import(entity_id: str) -> bool:
    """Whether an entity id names an import statement. An import's id already holds its file and
    its module, so tier 1 matches the same import; one reaching the rename and move tiers is a
    different import, removed in one place and added in another, and must not link."""
    return "::__import__::" in entity_id


def _may_link(before_id: str, after_id: str) -> bool:
    """Whether two entities are allowed to be the same thing across a commit."""
    if _is_import(before_id) or _is_import(after_id):
        return before_id == after_id
    return True


def _link_pass(
    before: list[Snap],
    after: list[Snap],
    matched_b: set[str],
    matched_a: set[str],
) -> list[tuple[Snap, Snap]]:
    """Tiers 2, 2b and 3. Marks matched ids in place and returns ``(old, new)`` rename/move
    links. Shared by the per-file and cross-file passes."""
    links: list[tuple[Snap, Snap]] = []

    # tiers 2 and 2b: exact bytes first, then the same code modulo formatting and comments
    for key in ("content_hash", "structural_hash"):
        pool: dict[str, list[Snap]] = {}
        for s in before:
            h = getattr(s, key)
            if s.ent.id not in matched_b and h:
                pool.setdefault(h, []).append(s)
        for a in after:
            h = getattr(a, key)
            if a.ent.id in matched_a or not h:
                continue
            bucket = pool.get(h)
            if not bucket:
                continue
            idx = next((i for i, b in enumerate(bucket) if _may_link(b.ent.id, a.ent.id)), None)
            if idx is None:
                continue
            b = bucket.pop(idx)
            matched_b.add(b.ent.id)
            matched_a.add(a.ent.id)
            links.append((b, a))

    # tier 3 -- fuzzy: only same-kind candidates, size-guarded, best strict-improving score.
    by_kind: dict[str, list[Snap]] = {}
    for s in before:
        if s.ent.id not in matched_b:
            by_kind.setdefault(s.ent.kind, []).append(s)
    for a in after:
        if a.ent.id in matched_a:
            continue
        cands = by_kind.get(a.ent.kind)
        if not cands:
            continue
        best: Snap | None = None
        best_score = 0.0
        for b in cands:
            if b.ent.id in matched_b or not _may_link(b.ent.id, a.ent.id):
                continue
            lo, hi = sorted((a.ntok, b.ntok))
            if hi and lo / hi < _SIZE_RATIO:
                continue
            score = _jaccard(a.tokens, b.tokens)
            if score >= _FUZZY and score > best_score:
                best, best_score = b, score
        if best is not None:
            matched_b.add(best.ent.id)
            matched_a.add(a.ent.id)
            links.append((best, a))

    return links


@dataclass
class Match:
    """Outcome of matching one before/after entity set."""

    modified: list[Snap]              # same surface id, content changed
    links: list[tuple[Snap, Snap]]    # (old, new) rename/move pairs to union + record
    added: list[Snap]                 # unmatched after
    removed: list[Snap]               # unmatched before


def match_pair(before: list[Snap], after: list[Snap]) -> Match:
    """Match the entities of one file across a commit (tiers 1 + 2 + 3)."""
    by_id_before = {s.ent.id: s for s in before}
    matched_b: set[str] = set()
    matched_a: set[str] = set()
    modified: list[Snap] = []

    # tier 1 -- exact surface id: unchanged (skip) or modified (content differs).
    for a in after:
        b = by_id_before.get(a.ent.id)
        if b is not None:
            matched_b.add(b.ent.id)
            matched_a.add(a.ent.id)
            if b.content_hash != a.content_hash:
                modified.append(a)

    links = _link_pass(before, after, matched_b, matched_a)
    added = [s for s in after if s.ent.id not in matched_a]
    removed = [s for s in before if s.ent.id not in matched_b]
    return Match(modified=modified, links=links, added=added, removed=removed)


def link_residual(
    removed: list[Snap], added: list[Snap]
) -> tuple[list[tuple[Snap, Snap]], set[str], set[str]]:
    """Cross-file moves: match a commit's leftover removals against its leftover additions by
    tiers 2, 2b and 3 (surface ids never coincide across files)."""
    matched_r: set[str] = set()
    matched_a: set[str] = set()
    links = _link_pass(removed, added, matched_r, matched_a)
    return links, matched_r, matched_a
