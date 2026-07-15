"""Tier-0: free, pure, deterministic static verification of a proposed candidate (plan U3).

Runs entirely in memory against `sgt.core.rewrite.build_candidate` -- no `stage`, no oracle, no
disk write beyond the read-only store/working-tree lookups `build_candidate` itself needs. Its
job is narrow: reject what an oracle round would reject for free, so the expensive Tier-1
(`sgt.core.oracle`) round only ever runs against a candidate that at least parses, forms a valid
ideal, and doesn't visibly still call what it was supposed to stop depending on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sgt.core import mine
from sgt.core.rewrite import RewriteDraft, RewriteError, build_candidate
from sgt.core.store import Store


def _leaf_name(symbol: str) -> str:
    return symbol.rpartition("::")[2].rpartition(".")[2]


@dataclass(frozen=True)
class Tier0Result:
    """`ok=False` always carries a `residual` explaining why -- fed back to the backend as the
    next attempt's `feedback` (`RepairRequest.feedback`).

    Known blind spot (documented, not fixed here): the lexical check below only catches the
    removed symbol's own bare name appearing as an identifier -- an indirect reference (calling
    some other, still-live symbol that itself still calls the removed one) is invisible to a
    per-hollow static check. Tier-1 (the real oracle run) is the backstop for that case."""

    ok: bool
    residual: str = ""


def tier0(repo: str | Path, draft: RewriteDraft, images: dict[str, bytes]) -> Tier0Result:
    """Checks a candidate built from `images` (hollow id -> proposed bytes): (1) every proposed
    image parses, (2) none of the proposed images still name the reverted target's own symbol,
    (3) the resulting candidate ideal is valid.

    Check (2) is lexical, not graph-based, deliberately: by the time a candidate is built, the
    reverted target's own definition is gone from the candidate's codebase, so a graph built over
    that codebase (`build_entity_graph`) can never resolve a call to it -- an unresolved name is
    dropped, not turned into a dangling edge (see `sgt/entities/graph.py`) -- so a graph-only
    check here would silently never fire for exactly the case it exists to catch."""
    repo = Path(repo)
    store = Store(repo)

    target_op = store.get(draft.target)
    removed_symbol = next(iter(target_op.footprint)) if target_op else None
    removed_leaf = _leaf_name(removed_symbol) if removed_symbol else None
    removed_pattern = re.compile(rf"\b{re.escape(removed_leaf)}\b") if removed_leaf else None

    for hollow_id, image in images.items():
        hollow = store.get_hollow(hollow_id)
        if hollow is None:
            return Tier0Result(ok=False, residual=f"hollow {hollow_id[:12]} not found")
        sym = next(iter(hollow.footprint))
        path = sym.partition("::")[0]
        if mine._parse_has_error(path, image):
            return Tier0Result(ok=False, residual=f"{sym}: proposed image does not parse")
        if removed_pattern is not None and removed_pattern.search(image.decode("utf-8", errors="replace")):
            return Tier0Result(ok=False, residual=f"{sym}: still references removed {removed_symbol}")

    try:
        build_candidate(repo, draft, images)
    except RewriteError as e:
        return Tier0Result(ok=False, residual=str(e))

    return Tier0Result(ok=True)
