"""Robust entity matching across a before/after snapshot.

Promoted verbatim from ``experiments/patch_clustering/identity_match.py`` (plan U2) -- the
matcher tiers and guard constants are unchanged; only the module's location and this docstring
moved. Originally ported from sem (``references/sem/src/model/identity.rs``), which taught us
the one rule our miner broke: *never link entities by name alone*. sem's ``match_entities``
walks tiers of decreasing confidence -- exact id, identical body (content hash), then fuzzy
token-Jaccard with size guards -- so ``foo -> bar`` with the same body is ONE renamed entity
(not delete + add), while two unrelated ``__init__``s never link because the size + kind +
threshold guards reject them. That last property is exactly what a name-set heuristic lacks
(the ``__init__``/``main``/``run`` collision noted in the mining findings).

We port sem's tiers, keyed on the hashes ``sgt.entities.extract.Entity`` carries
(``content_hash`` / ``structural_hash``, computed once at extraction from the parsed AST):

    tier 1   exact surface id (``file::name``)      content differs -> modified
    tier 2   identical body (content hash)          rename / move  (link)
    tier 2b  identical structure (structural hash)  rename / move  (link)  -- sem's Phase 2 fallback
    tier 3   fuzzy Jaccard >= 0.80, size-guarded    rename / move  (link)

Tier 2b is what an earlier port lacked: an AST structural hash catches a move/reformat whose
raw bytes differ (reflow, comment edit) but whose code is the same -- deterministically, without
leaning on the fuzzy threshold. Deterministic and offline.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sgt.entities.extract import Entity

_FUZZY = 0.80        # sem THRESHOLD: min token-Jaccard to call a rename/move
_SIZE_RATIO = 0.50   # sem SIZE_RATIO_CUTOFF: reject pairs whose token counts differ > 2x
_CONTAIN = 0.60      # min fraction of one body's tokens found in another (split/merge containment)


@dataclass(frozen=True)
class Snap:
    """An entity plus the signals sem matches on: body hashes (from the Entity) + token set."""

    ent: Entity
    content_hash: str
    structural_hash: str
    tokens: frozenset[str]
    ntok: int


def snapshot(entities: list[Entity], source: str) -> list[Snap]:
    """Wrap each entity with its extraction-time hashes plus a token set (for the fuzzy tier)."""
    lines = source.splitlines()
    out: list[Snap] = []
    for e in entities:
        toks = "\n".join(lines[e.start_line - 1 : e.end_line]).split()
        out.append(Snap(e, e.content_hash, e.structural_hash, frozenset(toks), len(toks)))
    return out


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a) + len(b) - inter
    return inter / union if union else 0.0


def _link_pass(
    before: list[Snap],
    after: list[Snap],
    matched_b: set[str],
    matched_a: set[str],
) -> list[tuple[Snap, Snap]]:
    """Tiers 2 + 3 (content-hash then guarded fuzzy). Marks matched ids in place and
    returns ``(old, new)`` rename/move links. Shared by the per-file and cross-file passes."""
    links: list[tuple[Snap, Snap]] = []

    # tiers 2 + 2b -- identical body then identical structure. Index unmatched before-entities
    # by each hash; match after-entities against content_hash first (exact bytes), then
    # structural_hash (same code modulo formatting/comments -- sem's Phase 2 fallback).
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
            if bucket:
                b = bucket.pop()
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
            if b.ent.id in matched_b:
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
    """Cross-file move detection: match a commit's leftover removals against its leftover
    additions (tiers 2 + 3 only -- surface ids never coincide across files). This is the
    safe generalization of sem's file-rename move pass: a function cut from one file and
    pasted into another links by body, not by name."""
    matched_r: set[str] = set()
    matched_a: set[str] = set()
    links = _link_pass(removed, added, matched_r, matched_a)
    return links, matched_r, matched_a


def _contains(inner: frozenset[str], outer: frozenset[str]) -> float:
    """Fraction of ``inner``'s tokens present in ``outer`` (directional overlap)."""
    return len(inner & outer) / len(inner) if inner else 0.0


def detect_splits_merges(
    removed: list[Snap], added: list[Snap]
) -> tuple[list[dict], list[dict]]:
    """One-to-many / many-to-one body relationships among a commit's *residual* removals and
    additions (the ones 1-1 linking above didn't consume). This is the lifecycle sem's pairwise
    matcher can't express: a function that was *split* into several, or several that were *merged*
    into one -- a within-file restructuring, keyed on token containment rather than equality.

        SPLIT   one removed R whose tokens are spread across >= 2 added, each mostly a subset of R,
                and R covered by their union.        R  ->  {A1, A2, ...}
        MERGE   >= 2 removed each mostly a subset of one added A, and A covered by their union.
                {R1, R2, ...}  ->  A

    Same-file only (a split/merge is a local reshape, not a cross-file move -- those already linked
    as 1-1 moves). Greedy and deterministic; an entity is consumed by at most one relationship.
    Returns ``(splits, merges)`` of surface ids: split ``{"from": id, "to": [ids], "file": f}``,
    merge ``{"from": [ids], "to": id, "file": f}``."""
    by_file_add: dict[str, list[Snap]] = defaultdict(list)
    by_file_rem: dict[str, list[Snap]] = defaultdict(list)
    for a in added:
        by_file_add[a.ent.file].append(a)
    for r in removed:
        by_file_rem[r.ent.file].append(r)
    used_a: set[str] = set()
    used_r: set[str] = set()

    splits: list[dict] = []
    for f, rem in by_file_rem.items():
        adds = by_file_add.get(f, [])
        for r in rem:
            if r.ent.id in used_r or not r.tokens:
                continue
            kids = [a for a in adds if a.ent.id not in used_a and a.tokens
                    and _contains(a.tokens, r.tokens) >= _CONTAIN]
            if len(kids) >= 2 and _contains(r.tokens, frozenset().union(*(k.tokens for k in kids))) >= _CONTAIN:
                splits.append({"from": r.ent.id, "to": [k.ent.id for k in kids], "file": f})
                used_r.add(r.ent.id)
                used_a.update(k.ent.id for k in kids)

    merges: list[dict] = []
    for f, adds in by_file_add.items():
        rem = by_file_rem.get(f, [])
        for a in adds:
            if a.ent.id in used_a or not a.tokens:
                continue
            parents = [r for r in rem if r.ent.id not in used_r and r.tokens
                       and _contains(r.tokens, a.tokens) >= _CONTAIN]
            if len(parents) >= 2 and _contains(a.tokens, frozenset().union(*(p.tokens for p in parents))) >= _CONTAIN:
                merges.append({"from": [p.ent.id for p in parents], "to": a.ent.id, "file": f})
                used_a.add(a.ent.id)
                used_r.update(p.ent.id for p in parents)

    return splits, merges
