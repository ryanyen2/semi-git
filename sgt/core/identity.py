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

from sgt.config import IdentityConstraints
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
    tokens: frozenset[bytes]  # byte tokens (split on raw bytes) -- fuzzy tier is a heuristic
    # signal only, never stored/materialized, so no decode is needed here.
    ntok: int


def snapshot(entities: list[Entity], source: bytes | str) -> list[Snap]:
    """Wrap each entity with its extraction-time hashes plus a token set (for the fuzzy tier).

    Byte-native (`start_byte`/`end_byte`) so tokenizing never round-trips through a lossy
    decode; `source` accepts `str` too for ergonomic hand-written callers/tests (encoded once,
    losslessly, via UTF-8 -- matching `extract_file`'s own contract)."""
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


def _pair(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))


def _forced_links(
    before: list[Snap], after: list[Snap], matched_b: set[str], matched_a: set[str],
    force_link: frozenset[tuple[str, str]],
) -> list[tuple[Snap, Snap]]:
    """`identity join` (U11, R14): a durable, human-corrected pair that must link regardless of
    what the hash/fuzzy tiers below would find on their own -- checked first so it always wins."""
    if not force_link:
        return []
    by_before = {s.ent.id: s for s in before}
    by_after = {s.ent.id: s for s in after}
    links: list[tuple[Snap, Snap]] = []
    for x, y in force_link:
        b = by_before.get(x) or by_before.get(y)
        a = by_after.get(y) if by_before.get(x) is not None else by_after.get(x)
        if b is None or a is None or b.ent.id in matched_b or a.ent.id in matched_a:
            continue
        matched_b.add(b.ent.id)
        matched_a.add(a.ent.id)
        links.append((b, a))
    return links


def _link_pass(
    before: list[Snap],
    after: list[Snap],
    matched_b: set[str],
    matched_a: set[str],
    constraints: IdentityConstraints | None = None,
) -> list[tuple[Snap, Snap]]:
    """Tiers 2 + 3 (content-hash then guarded fuzzy). Marks matched ids in place and
    returns ``(old, new)`` rename/move links. Shared by the per-file and cross-file passes.

    `constraints` (U11, R14's `identity split`/`identity join` escape hatch) is optional and
    empty by default -- every existing caller is unaffected. `never_link` skips a would-be match
    for that exact pair (trying the next hash-bucket candidate, or simply not linking in the
    fuzzy tier); `force_link` links a human-corrected pair up front, before either heuristic
    runs, so a false negative (two renamed entities the hashes/fuzzy score missed) still merges
    into one chain."""
    # Normalized to sorted pairs regardless of how the caller's constraints stored them --
    # `sgt.config.load_identity_constraints` always sorts, but this stays correct for any
    # caller (e.g. a test) that constructs `IdentityConstraints` directly with an unsorted pair.
    never_link = frozenset(_pair(x, y) for x, y in (constraints.never_link if constraints else ()))
    links = _forced_links(
        before, after, matched_b, matched_a, constraints.force_link if constraints else frozenset()
    )

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
            if not bucket:
                continue
            idx = next(
                (i for i, b in enumerate(bucket) if _pair(b.ent.id, a.ent.id) not in never_link), None
            )
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
            if b.ent.id in matched_b or _pair(b.ent.id, a.ent.id) in never_link:
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


def match_pair(
    before: list[Snap], after: list[Snap], constraints: IdentityConstraints | None = None
) -> Match:
    """Match the entities of one file across a commit (tiers 1 + 2 + 3). `constraints` is U11's
    `identity split`/`identity join` escape hatch (optional; empty by default)."""
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

    links = _link_pass(before, after, matched_b, matched_a, constraints)
    added = [s for s in after if s.ent.id not in matched_a]
    removed = [s for s in before if s.ent.id not in matched_b]
    return Match(modified=modified, links=links, added=added, removed=removed)


def link_residual(
    removed: list[Snap], added: list[Snap], constraints: IdentityConstraints | None = None
) -> tuple[list[tuple[Snap, Snap]], set[str], set[str]]:
    """Cross-file move detection: match a commit's leftover removals against its leftover
    additions (tiers 2 + 3 only -- surface ids never coincide across files). This is the
    safe generalization of sem's file-rename move pass: a function cut from one file and
    pasted into another links by body, not by name. `constraints` is U11's escape hatch
    (optional; empty by default)."""
    matched_r: set[str] = set()
    matched_a: set[str] = set()
    links = _link_pass(removed, added, matched_r, matched_a, constraints)
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
