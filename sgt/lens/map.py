"""Build-and-save orchestration for the feature tree (plan U13): the one place that turns the
live op store + ideal into a labeled, persisted map. Kept out of `sgt.api` so `import sgt.api`
stays dependency-light -- this module is the igraph/leidenalg/LLM-touching entry the CLI (`sgt
map`) and TUI call before reading `sgt.api.map_view`'s pure projection of what got saved.
"""

from __future__ import annotations

from pathlib import Path

from sgt.core.lens import current_ideal
from sgt.core.store import Store
from sgt.lens import tree


def build_map(repo: str | Path) -> dict:
    """Cluster every alive symbol over the fused coupling graph (`tree.build`, carrying feature
    identity across runs via Greene matching against the last committed tree and honoring
    `.sgt/pins/pins.json`), label the result (LLM with a deterministic offline fallback -- golden-
    safe with no API key), and persist it to `.sgt/tree/tree.json`. Assumes the op store is
    already current; mine-on-contact (`sgt.core.lens.get`) is the caller's job."""
    repo = Path(repo)
    ops = Store(repo).all_ops()
    ideal = current_ideal(repo)
    result = tree.build(repo, ops, ideal)
    labeler = tree.label_tree(result, repo)
    labeler.save()  # persist the member-hash label cache so an unchanged cluster never re-pays
    # the (non-deterministic) LLM call on the next build -- without this the cache is rebuilt cold
    # every run and stable features get relabeled on every `sgt map`.
    tree.save(repo, result)
    return result
