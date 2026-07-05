"""Cluster the mined patch stream into candidate features (deterministic, free, no LLM).

Two coupling signals, fused as an adjacency relation:
  - co-change: entities that change together across commits (Zimmermann/ROSE), down-weighted
    so a 60-entity "drop a whole subsystem" commit doesn't glue everything into one blob, and
    with omnipresent hubs stripped (Rigi) so a util everyone touches isn't a bridge.
  - structural: calls/imports/contains edges from the entity graph at HEAD (reused from sgt).

Grouping reuses ``sgt.entities.cluster.cluster_features`` (union-find + Jaccard-stable ids).
This first pass clusters the *whole history at once* over entities alive at HEAD — the point
is to eyeball whether co-change clustering recovers real subsystems before adding the temporal
lane dimension. Tunable knobs are module constants; the reflection loop adjusts them.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sgt.entities.cluster import cluster_features  # noqa: E402
from sgt.entities.graph import build_entity_graph  # noqa: E402
from sgt.store.gitbind import GitBinding  # noqa: E402

_OUT = Path(__file__).resolve().parent / "out"

# -- knobs the reflection loop tunes -----------------------------------------
MAX_COMMIT_SIZE = 20   # co-change ignores commits touching more entities than this (mega-drops)
MIN_CO_OCCUR = 2       # an entity pair must co-change in at least this many commits to link
HUB_COMMIT_FRAC = 0.15  # entities changed in >= this fraction of commits are hubs (stripped from co-change)


def _dominant_dir(members: list[str]) -> str:
    """The most common 2-level path prefix among members — a coherence eyeball, not a label."""
    def prefix(eid: str) -> str:
        parts = eid.split("::", 1)[0].split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    return Counter(prefix(m) for m in members).most_common(1)[0][0]


def build_clusters(data: dict, repo: Path) -> dict:
    commits = data["commits"]
    change_sets = {int(k): v for k, v in data["change_sets"].items()}
    n_commits = len(commits)

    # entities alive at HEAD = the current codebase's features
    gb = GitBinding(repo)
    head = gb.head()
    head_entities = {e.id for e in _head_entities(gb, head)}

    # hub detection: how many commits each entity appears in
    commit_freq: Counter = Counter()
    for ids in change_sets.values():
        commit_freq.update(set(ids))
    hub_cut = max(2, int(HUB_COMMIT_FRAC * n_commits))
    hubs = {e for e, f in commit_freq.items() if f >= hub_cut}

    # co-change edges: pairs co-occurring in >= MIN_CO_OCCUR focused, non-hub commits
    co_count: Counter = Counter()
    for ids in change_sets.values():
        alive = [e for e in ids if e in head_entities and e not in hubs]
        if 2 <= len(alive) <= MAX_COMMIT_SIZE:
            for a, b in combinations(sorted(alive), 2):
                co_count[frozenset((a, b))] += 1
    co_edges = {p for p, c in co_count.items() if c >= MIN_CO_OCCUR}

    # structural edges from the entity graph at HEAD
    struct_edges: set[frozenset] = set()
    eg = build_entity_graph(gb.tree_at(head))
    for e in eg.edges:
        if e.src in head_entities and e.dst in head_entities and e.src != e.dst:
            struct_edges.add(frozenset((e.src, e.dst)))

    adjacency = co_edges | struct_edges
    members = sorted(head_entities)
    clusters = cluster_features(members, adjacency)

    return {
        "clusters": [c.to_dict() for c in clusters],
        "stats": {
            "head_entities": len(head_entities),
            "hubs_stripped": sorted(hubs),
            "hub_cut_commits": hub_cut,
            "co_edges": len(co_edges),
            "struct_edges": len(struct_edges),
            "n_clusters": len(clusters),
        },
    }


def _head_entities(gb: GitBinding, head: str):
    from sgt.entities.extract import extract_codebase
    return extract_codebase(gb.tree_at(head))


def _report(result: dict) -> None:
    s = result["stats"]
    clusters = sorted(result["clusters"], key=lambda c: -len(c["members"]))
    print(f"HEAD entities: {s['head_entities']}   clusters: {s['n_clusters']}")
    print(f"co-change edges: {s['co_edges']}   structural edges: {s['struct_edges']}")
    print(f"hubs stripped (>= {s['hub_cut_commits']} commits): {len(s['hubs_stripped'])}")
    for h in s["hubs_stripped"][:12]:
        print(f"    hub  {h}")
    sizes = Counter(len(c["members"]) for c in clusters)
    print(f"\ncluster size histogram: {dict(sorted(sizes.items()))}")
    print(f"singletons: {sizes.get(1, 0)} / {len(clusters)}\n")
    print("clusters with >= 3 members (dominant dir | size | sample members):")
    for c in clusters:
        if len(c["members"]) >= 3:
            dom = _dominant_dir(c["members"])
            sample = ", ".join(m.split("::", 1)[1] for m in c["members"][:5])
            print(f"  [{len(c['members']):2d}] {dom:22s} {sample}")


if __name__ == "__main__":
    data = json.loads((_OUT / "patches.json").read_text(encoding="utf-8"))
    result = build_clusters(data, _REPO_ROOT)
    _report(result)
    (_OUT / "clusters.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwrote {_OUT / 'clusters.json'}")
