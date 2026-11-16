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

We port sem's tiers, keyed on the hashes ``ugh.entities.extract.Entity`` carries
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


def _is_import(entity_id: str) -> bool:
    """Whether an entity id names an import statement.

    An import's identity is the file it sits in and the module it names, and both halves are
    already in the id. Two imports are the same import when both halves match, which the exact
    surface id tier has already handled by the time the rename and move tiers run. So an import
    reaching those tiers can only be a different import, and linking it to one is wrong in both
    directions.

    Same file, different module: `import { Run } from './run'` becoming
    `import { Wrapped } from './probe2'` reads almost the same and sits on the same line, which
    is enough for the structural and fuzzy tiers to call the second a rename of the first. It is
    a prune and an add.

    Different file, same module: an import of `./run` leaving one file while another file that
    imports `./run` is added in the same commit is not one import moving between them. Both
    files import that module on their own account. This one is worse than a cosmetic
    mislabelling: the resulting `move` op is dropped when the ideal is reduced, taking the rest
    of the commit's ops for both files with it, and `ugh save` then refuses the whole edit with
    `put() would overwrite uncommitted changes`. Extracting a module and importing it where the
    old import used to be is the most ordinary refactor there is, and it could not be saved."""
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
    """Cross-file move detection: match a commit's leftover removals against its leftover
    additions (tiers 2 + 3 only -- surface ids never coincide across files). This is the
    safe generalization of sem's file-rename move pass: a function cut from one file and
    pasted into another links by body, not by name."""
    matched_r: set[str] = set()
    matched_a: set[str] = set()
    links = _link_pass(removed, added, matched_r, matched_a)
    return links, matched_r, matched_a
