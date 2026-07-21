"""Stage B Step 0 — baseline measurement of the feature clustering.

Reports, for a repo:
  - feature (leaf) count + subsystem depth
  - ops-per-feature distribution (the god-cluster test)
  - the labels themselves (eyeball reflection-Q1/Q2)
  - id-churn + label-churn across two consecutive builds (Greene stability)

Usage: python experiments/stageB_measure.py [repo]   (default: cwd)
Reads only; build_map writes .sgt as `sgt map` normally would.
"""
from __future__ import annotations

import statistics
import sys
from collections import Counter

from sgt.core import opindex
from sgt.core.lens import current_ideal, get
from sgt.lens import map as lens_map
from sgt.lens import tree as tree_mod


def _leaves(result: dict) -> dict[str, dict]:
    return {nid: nd for nid, nd in result["nodes"].items() if not nd["children"]}


def _ops_per_feature(result: dict) -> Counter:
    c: Counter = Counter()
    for leaf in result["op_leaf"].values():
        c[leaf] += 1
    return c


def _depth(result: dict) -> int:
    return max((nd["depth"] for nd in result["nodes"].values()), default=0)


def measure(repo: str) -> None:
    print(f"\n=== {repo} ===")
    get(repo)  # mine-on-contact
    result = lens_map.build_map(repo)
    leaves = _leaves(result)
    opf = _ops_per_feature(result)
    total_ops = sum(opf.values())
    counts = sorted(opf.values(), reverse=True)

    print(f"leaves(features): {len(leaves)}   subsystems(depth): {_depth(result)}   "
          f"total assigned ops: {total_ops}")
    if counts:
        print(f"ops/feature: max={counts[0]} median={int(statistics.median(counts))} "
              f"min={counts[-1]}   top feature holds {counts[0]/total_ops:.0%} of ops")
    print("\ntop features by op-count (the revert-unit legibility test):")
    for leaf, n in opf.most_common(8):
        nd = leaves.get(leaf, {})
        print(f"  {n:>5} ops  {nd.get('label','?'):<32}  [{nd.get('dir','?')}]  {leaf}")

    # id-churn + label-churn across a second consecutive build
    ops = opindex.index_ops(repo)
    ideal = current_ideal(repo)
    r2 = tree_mod.build(repo, ops, ideal, refresh_caches=False)
    tree_mod.label_tree(r2, repo)
    l1, l2 = set(leaves), set(_leaves(r2))
    labels1 = {nid: nd["label"] for nid, nd in leaves.items()}
    labels2 = {nid: nd["label"] for nid, nd in _leaves(r2).items()}
    common = l1 & l2
    relabeled = [nid for nid in common if labels1.get(nid) != labels2.get(nid)]
    print(f"\nid-churn (2 builds): {len(l1)}→{len(l2)} leaves; "
          f"{len(l1 - l2)} died, {len(l2 - l1)} born, {len(common)} stable; "
          f"{len(relabeled)} stable-id relabeled")


if __name__ == "__main__":
    measure(sys.argv[1] if len(sys.argv) > 1 else ".")
