"""Merge-time reconciliation of pins and the feature tree (plan U15, R19/AE4): the two pieces of
`sgt sync` that aren't just "union the op store and re-fold" (`sgt.core.sync` handles that part).

Pins are unioned structurally -- a dict/set merge, latest-wins on a genuine key collision (D3) --
then checked for contradictions the same way a single clone's pin edit already is
(`sgt.lens.pins.find_contradictions`), never raising. The tree is *rebuilt*, not merged: it is a
deterministic clustering overlay derived from the op store, not a source of truth, so once the op
store is unioned the correct tree is whatever `tree.build` would produce from that union -- Greene
member-overlap matching against the caller's own last-known tree (`previous`) is what keeps our
own feature ids stable across the sync rather than being replaced by a coincidentally-numbered
`theirs` id.
"""

from __future__ import annotations

from pathlib import Path

from sgt.core.op import Op
from sgt.lens import tree
from sgt.lens.pins import Contradiction, Pins, find_contradictions


def union_pins(ours: Pins, theirs: Pins) -> tuple[Pins, list[Contradiction]]:
    """Dict-merge `assign`/`labels` (theirs wins a same-key collision -- latest-wins, D3),
    set-union `must_link`/`cannot_link`. Any contradiction the union introduces (e.g. one clone's
    must-link colliding with the other's cannot-link) is surfaced via `find_contradictions`, never
    raised -- `sgt sync` reports it and proceeds with the merged pins file regardless."""
    merged = Pins(
        assign={**ours.assign, **theirs.assign},
        must_link=ours.must_link | theirs.must_link,
        cannot_link=ours.cannot_link | theirs.cannot_link,
        labels={**ours.labels, **theirs.labels},
    )
    return merged, find_contradictions(merged)


def reconcile_tree(
    repo: Path, ops: list[Op], ideal, pins: Pins, ours_tree: dict | None,
) -> dict:
    """Rebuild the feature tree from the unioned op store/ideal, Greene-matched against
    `ours_tree` (our own last-committed tree, not a merge of both sides' trees) so a feature id we
    already assigned stays stable across the sync. Mirrors `sgt.lens.map.build_map`'s
    build-then-label sequence, but takes `ops`/`ideal`/`pins` explicitly rather than reading them
    off disk -- mid-sync, the unioned op store and ideal exist only in memory until the merge
    commit lands."""
    result = tree.build(repo, ops, ideal, pins=pins, previous=ours_tree)
    tree.label_tree(result, repo, pins=pins)
    return result
