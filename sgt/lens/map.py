"""Build-and-save orchestration for the feature tree (plan U13): the one place that turns the
live op store + ideal into a labeled, persisted map. Kept out of `sgt.api` so `import sgt.api`
stays dependency-light -- this module is the igraph/leidenalg/LLM-touching entry the CLI (`sgt
map`) and TUI call before reading `sgt.api.map_view`'s pure projection of what got saved.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from sgt.core import opindex
from sgt.core.lens import current_ideal
from sgt.lens import tree


def _op_touch_weights(ops: list) -> dict[str, float]:
    """Per-symbol op-touch count: how many mined ops list this symbol in their footprint. Feeds
    the weighted-Jaccard drift budget for graded label reuse (§3.2) so a label follows the
    feature's center of historical mass, not its raw symbol count -- dropping one heavily-worked
    symbol should cost more than dropping a one-touch residue segment."""
    w: Counter = Counter()
    for op in ops:
        for sym in op.footprint:
            w[sym] += 1
    return dict(w)


def build_map(repo: str | Path, rebuild: bool = False) -> dict:
    """Cluster every alive symbol over the fused coupling graph (`tree.build`, carrying feature
    identity across runs via Greene matching against the last committed tree and honoring
    `.sgt/pins/pins.json`), label the result (LLM with a deterministic offline fallback -- golden-
    safe with no API key), and persist it to `.sgt/tree/tree.json`. Assumes the op store is
    already current; mine-on-contact (`sgt.core.lens.get`) is the caller's job.

    `rebuild=True` forces a full from-scratch recluster (skips dirty-subtree splicing) -- the
    user-facing escape hatch when a locally re-optimized tree isn't good enough."""
    repo = Path(repo)
    ops = opindex.index_ops(repo)  # footprint/provenance only -- clustering never reads .images
    ideal = current_ideal(repo)
    result = tree.build(repo, ops, ideal, force_rebuild=rebuild, refresh_caches=True)
    labeler = tree.label_tree(
        result, repo, weights=_op_touch_weights(ops), relabel=rebuild, ops=ops,
    )
    labeler.save()  # persist the member-hash label cache so an unchanged cluster never re-pays
    # the (non-deterministic) LLM call on the next build -- without this the cache is rebuilt cold
    # every run and stable features get relabeled on every `sgt map`.
    tree.save(repo, result, refresh_fused_snapshot=True)
    return result
