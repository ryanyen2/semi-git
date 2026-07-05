"""Community detection over the coupling graph — the design's actual bet (Leiden + CPM).

The CC baseline (cluster.py) collapses to one blob because connected-components can't find
dense subgroups. Leiden with the Constant Potts Model is resolution-limit-free and finds
communities, which is what "feature" means here. We build a weighted graph from two fused
signals and compare three lenses so the reflection can see what each contributes:

  - structural-only : calls/imports/contains at HEAD  (classic module recovery)
  - cochange-only   : entities that change together, down-weighted + hub-stripped
  - fused           : both, summed

CPM's resolution gamma trades cluster count for size; we sweep it and then dump the fused
partition at a chosen gamma for eyeballing. Deterministic via a fixed seed (the experiment
wants repeatability; production would let it drift per the design's "stable not deterministic").
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from statistics import median

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import igraph as ig  # noqa: E402
import leidenalg as la  # noqa: E402

from sgt.entities.extract import extract_codebase  # noqa: E402
from sgt.entities.graph import build_entity_graph  # noqa: E402
from sgt.store.gitbind import GitBinding  # noqa: E402

_OUT = Path(__file__).resolve().parent / "out"

MAX_COMMIT_SIZE = 20
HUB_COMMIT_FRAC = 0.15
CO_SCALE = 5.0   # lift co-change so a repeated focused pair competes with a structural edge
SEED = 42
GAMMAS = [0.01, 0.03, 0.05, 0.1, 0.2, 0.4]


def _signals(data: dict, repo: Path):
    change_sets = {int(k): v for k, v in data["change_sets"].items()}
    n_commits = len(data["commits"])
    gb = GitBinding(repo)
    head = gb.head()
    head_entities = {e.id for e in extract_codebase(gb.tree_at(head))}

    commit_freq: Counter = Counter()
    for ids in change_sets.values():
        commit_freq.update(set(ids))
    hub_cut = max(2, int(HUB_COMMIT_FRAC * n_commits))
    hubs = {e for e, f in commit_freq.items() if f >= hub_cut}

    cochange: dict[frozenset, float] = defaultdict(float)
    for ids in change_sets.values():
        alive = [e for e in ids if e in head_entities and e not in hubs]
        if 2 <= len(alive) <= MAX_COMMIT_SIZE:
            w = 1.0 / (len(alive) - 1)
            for a, b in combinations(sorted(alive), 2):
                cochange[frozenset((a, b))] += w

    structural: dict[frozenset, float] = defaultdict(float)
    for e in build_entity_graph(gb.tree_at(head)).edges:
        if e.src in head_entities and e.dst in head_entities and e.src != e.dst:
            structural[frozenset((e.src, e.dst))] += 1.0

    return head_entities, hubs, hub_cut, dict(cochange), dict(structural)


_SCOPE_RE = re.compile(r"^\w+\(([^)]+)\)")


def commit_scope(subject: str) -> str | None:
    """The conventional-commit scope the author declared: ``feat(store): ...`` -> ``store``.
    Falls back to the commit type (``feat``/``fix``/``docs``) so every commit lands somewhere."""
    m = _SCOPE_RE.match(subject)
    if m:
        return m.group(1)
    m2 = re.match(r"^(\w+)[:(]", subject)
    return m2.group(1) if m2 else None


def scope_edges(data: dict, head_entities: set[str], hubs: set[str], scale: float = 10.0,
                max_scope: int = 80) -> dict[frozenset, float]:
    """Intent signal: entities changed under the same conventional-commit scope bind, even in
    *different* commits — the density plain same-commit co-change lacks on a young repo. Weight
    is down-scaled by scope size so a broad scope contributes weak all-to-all glue, not a blob."""
    change_sets = {int(k): v for k, v in data["change_sets"].items()}
    scope_ents: dict[str, set[str]] = defaultdict(set)
    for o, c in enumerate(data["commits"]):
        s = commit_scope(c["subject"])
        if not s:
            continue
        for e in change_sets.get(o, []):
            if e in head_entities and e not in hubs:
                scope_ents[s].add(e)
    edges: dict[frozenset, float] = defaultdict(float)
    for ents in scope_ents.values():
        members = sorted(ents)
        if not (2 <= len(members) <= max_scope):
            continue
        w = scale / (len(members) - 1)
        for a, b in combinations(members, 2):
            edges[frozenset((a, b))] += w
    return dict(edges)


def hub_normalize(structural: dict[frozenset, float]) -> dict[frozenset, float]:
    """Suppress structural hubs so a god-class / universal-import bus stops fusing unrelated
    features into one blob.

    Co-change and scope already strip commit-frequency hubs, but the *structural* signal did
    not — so high-degree infra (``Effect.add_def`` deg-106, ``Node``, the ``Project`` facade)
    acted as a bus that glued a third of the repo into one coarse subsystem. The principle: an
    edge's clustering weight should reflect how *specific* the tie is. Scale each edge by
    ``1/sqrt(deg(a)*deg(b))`` on the weighted degree (bibliometric-style hub suppression), so a
    link to a universal hub counts for little while a focused feature-internal link counts fully.

    Rescaled to preserve the total structural weight, so structure stays the recall backbone in
    the fusion. This *demotes* hubs rather than stripping their edges — measured: stripping
    orphans entities and drops commit coverage (43/54), demotion breaks the 140-entity god-lane
    to 66 while holding coverage and retaining more entities. Deterministic."""
    deg: dict[str, float] = defaultdict(float)
    for pair, w in structural.items():
        a, b = tuple(pair)
        deg[a] += w
        deg[b] += w
    raw = {
        pair: w / math.sqrt(deg[tuple(pair)[0]] * deg[tuple(pair)[1]])
        for pair, w in structural.items()
    }
    total = sum(structural.values())
    scale = total / sum(raw.values()) if raw else 1.0
    return {pair: w * scale for pair, w in raw.items()}


def _leiden(nodes: list[str], weights: dict[frozenset, float], gamma: float) -> list[list[str]]:
    idx = {n: i for i, n in enumerate(nodes)}
    edges, ews = [], []
    for pair, w in weights.items():
        a, b = tuple(pair)
        if a in idx and b in idx and w > 0:
            edges.append((idx[a], idx[b]))
            ews.append(w)
    g = ig.Graph(n=len(nodes), edges=edges)
    g.vs["name"] = nodes
    g.es["weight"] = ews
    part = la.find_partition(
        g, la.CPMVertexPartition, resolution_parameter=gamma,
        weights="weight", seed=SEED, n_iterations=-1,
    )
    return [[nodes[i] for i in comm] for comm in part]


def _fuse(a: dict, b: dict) -> dict:
    out: dict[frozenset, float] = defaultdict(float)
    for d in (a, b):
        for k, v in d.items():
            out[k] += v
    return dict(out)


def _row(name: str, gamma: float, clusters: list[list[str]]) -> str:
    sizes = [len(c) for c in clusters]
    singles = sum(1 for s in sizes if s == 1)
    nontrivial = [s for s in sizes if s >= 2]
    return (f"  {name:12s} {gamma:<6} nclusters={len(clusters):<4} "
            f"singletons={singles:<4} largest={max(sizes):<5} "
            f"median(>=2)={median(nontrivial) if nontrivial else 0}")


def _dominant_dir(members: list[str]) -> str:
    def prefix(eid: str) -> str:
        parts = eid.split("::", 1)[0].split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    return Counter(prefix(m) for m in members).most_common(1)[0][0]


def run(data: dict, repo: Path, dump_gamma: float = 0.05) -> dict:
    head_entities, hubs, hub_cut, cochange, structural = _signals(data, repo)
    nodes = sorted(head_entities)
    fused = _fuse(cochange, structural)

    print(f"HEAD entities: {len(nodes)}   co-change edges: {len(cochange)}   "
          f"structural edges: {len(structural)}   hubs stripped: {len(hubs)} (>= {hub_cut} commits)")
    print("\nresolution sweep (Leiden-CPM):")
    graphs = {"structural": structural, "cochange": cochange, "fused": fused}
    for gname, w in graphs.items():
        for gamma in GAMMAS:
            print(_row(gname, gamma, _leiden(nodes, w, gamma)))

    chosen = _leiden(nodes, fused, dump_gamma)
    chosen.sort(key=lambda c: -len(c))
    print(f"\nfused @ gamma={dump_gamma}: clusters >= 3 members (dom dir | size | samples):")
    for c in chosen:
        if len(c) >= 3:
            samples = ", ".join(m.split("::", 1)[1] for m in sorted(c)[:5])
            print(f"  [{len(c):2d}] {_dominant_dir(c):24s} {samples}")

    return {
        "fused_gamma": dump_gamma,
        "clusters": [sorted(c) for c in chosen],
        "hubs": sorted(hubs),
    }


if __name__ == "__main__":
    data = json.loads((_OUT / "patches.json").read_text(encoding="utf-8"))
    result = run(data, _REPO_ROOT)
    (_OUT / "leiden_clusters.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nwrote {_OUT / 'leiden_clusters.json'}")
