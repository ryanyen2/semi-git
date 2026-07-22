"""Phase 5 cohesion/stability harness (feature-timeline redesign plan, 2026-07-21): measures the
*production* clustering pipeline (`sgt.lens.cluster` / `sgt.lens.tree`) against the metrics Phase 5
is gated on, before any signal-weight change is trialed near `_fuse` (`tree.py`) / `cluster.signals`:

  - per-leaf co-commit cohesion: what fraction of each feature's co-commit (episode) edge weight
    stays within that feature vs. leaks to another one. 1.0 = every co-commit episode touching
    this feature's members stays inside it; near 0 = the feature is glued to others almost
    entirely by cross-feature episodes.
  - cross-feature edge mass: the fraction of the fused graph's total weight that
    `tree.feature_edges` rolls up across leaf boundaries -- the "how blobby is the cut" number.
  - Greene id-stability: rebuild from scratch (`force_rebuild=True`, the `sgt map --rebuild` path)
    against the currently persisted tree, never saved, and measure what fraction of previously-
    known feature ids continue (event="continuation"/"merge") rather than die.

Read-only against the target repo: every call passes `refresh_cache=False` / `refresh_caches=False`
and nothing is ever `.save()`d -- running this against a real checkout (default: this repo) never
mutates `.sgt/tree/tree.json`, pins, or any on-disk cache.

The plan's Phase 5 spec also names "the stress projects noted in memory" (`scripts/graph_stress/`,
a 5-project LLM-agent corpus) as an additional target. That corpus's source was removed from the
tree at 740594d5 ("Nl to sgt commands") -- recoverable via
`git show 740594d5~1:scripts/graph_stress/driver.py` -- and it exists to measure a *different*
thing (compose-conflict / drift rate for the contracts spike,
`docs/plans/2026-06-30-001-feat-contracts-substrate-spike-plan.md`), not clustering cohesion.
Reviving an LLM-agent-driven multi-project corpus is a large side-quest orthogonal to this gate;
this repo's own 200+-commit history is real, organic, feature-shaped history and is a sufficient
(and more directly relevant) subject for a clustering-quality regression gate. `run()` takes any
repo path, so pointing it at another checkout later is a one-line change if that need reappears.

Usage:
    .venv/bin/python -m experiments.patch_clustering.cohesion_harness [REPO_PATH ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sgt.core import opindex
from sgt.core.lens import current_ideal
from sgt.lens import cluster, tree


def cohesion(nodes: dict, ops: list, node_set: set[str], hubs: set[str]) -> dict[str, float]:
    """Per-leaf co-commit cohesion (see module docstring). Leaves with no scored co-commit weight
    at all (e.g. every member is a lone residue segment) are simply absent from the result -- there
    is nothing to be cohesive or incoherent about."""
    member_leaf = tree.leaf_member_index(nodes)
    commit = cluster.commit_edges(ops, node_set, hubs)
    internal: dict[str, float] = {}
    total: dict[str, float] = {}
    for pair, w in commit.items():
        a, b = tuple(pair)
        leaf_a, leaf_b = member_leaf.get(a), member_leaf.get(b)
        if leaf_a is None or leaf_b is None:
            continue
        for leaf in {leaf_a, leaf_b}:
            total[leaf] = total.get(leaf, 0.0) + w
        if leaf_a == leaf_b:
            internal[leaf_a] = internal.get(leaf_a, 0.0) + w
    return {leaf: internal.get(leaf, 0.0) / t for leaf, t in total.items() if t > 0}


def cross_feature_mass(nodes: dict, fused: dict[frozenset, float]) -> float | None:
    """Fraction of the fused graph's total edge weight that crosses a leaf boundary. `None` when
    the fused graph carries no weight at all (an empty/degenerate repo) -- there is no mass to
    divide."""
    total = sum(fused.values())
    if total <= 0:
        return None
    cross = sum(e["weight"] for e in tree.feature_edges(nodes, fused))
    return cross / total


def continuation_rate(previous_nodes: dict, events: list[dict]) -> dict:
    """What fraction of `previous_nodes`' leaves continue (are not named in a `death` event) after
    a rebuild. The number Phase 5's later signal-weight trials must not regress."""
    old_leaves = {nid for nid, nd in previous_nodes.items() if not nd["children"]}
    deaths = {e["feature_id"] for e in events if e["event"] == "death"}
    continued = old_leaves - deaths
    rate = len(continued) / len(old_leaves) if old_leaves else None
    by_kind = {
        kind: sum(1 for e in events if e["event"] == kind)
        for kind in ("continuation", "merge", "split", "birth", "death")
    }
    return {"old_leaf_count": len(old_leaves), "continuation_rate": rate, "events_by_type": by_kind}


def greene_stability(repo: Path, ops: list, ideal, previous: dict | None) -> dict:
    """Rebuild from scratch against the currently persisted tree (never saved) and score
    `continuation_rate` on the result. `previous=None` (a repo with no committed tree yet) reports
    an empty/no-op stability record rather than fabricating a rate with no denominator."""
    if previous is None or not previous.get("nodes"):
        return {"old_leaf_count": 0, "continuation_rate": None, "events_by_type": {}}
    rebuilt = tree.build(repo, ops, ideal, previous=previous, force_rebuild=True, refresh_caches=False)
    return continuation_rate(previous["nodes"], rebuilt["identity_events"])


def run(repo: str | Path) -> dict:
    repo = Path(repo)
    ops = opindex.index_ops(repo)  # footprint/provenance only, matching build_map's own read path
    ideal = current_ideal(repo)
    node_set, hubs, _cochange, _structural = cluster.signals(repo, ops, ideal, refresh_cache=False)
    _all_nodes, fused = tree.fused_graph(repo, ops, ideal, refresh_structural_cache=False)
    previous = tree.load(repo)

    report: dict = {"repo": str(repo), "signals_version": cluster.SIGNALS_VERSION, "n_ops": len(ops)}
    if previous is not None and previous.get("nodes"):
        coh_values = sorted(cohesion(previous["nodes"], ops, node_set, hubs).values())
        report["cohesion"] = {
            "median": coh_values[len(coh_values) // 2] if coh_values else None,
            "min": coh_values[0] if coh_values else None,
            "n_leaves_scored": len(coh_values),
        }
        report["cross_feature_edge_mass"] = cross_feature_mass(previous["nodes"], fused)
    else:
        report["cohesion"] = None
        report["cross_feature_edge_mass"] = None
    report["greene_stability"] = greene_stability(repo, ops, ideal, previous)
    return report


def main(argv: list[str]) -> int:
    repos = [Path(a) for a in argv] or [Path.cwd()]
    for repo in repos:
        print(json.dumps(run(repo), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
