"""Community detection over the fused coupling graph (plan U12, R15/R16): the design's bet is
Leiden with the Constant Potts Model (resolution-limit-free, finds real communities rather than
one connected-components blob). Promoted from `experiments/patch_clustering/leiden_cluster.py`
(see [[experiments-patch-clustering-findings]]), re-sourced from the kernel's op store instead of
a standalone `patches.json`.

Nodes are every content-bearing symbol (`sgt.core.op.is_content_bearing`) alive at the ideal's
frontier -- entities, residue segments, and whole-file paths alike, so a lane can claim a
whole-file YAML chain, not just parsed defs. Edges fuse two signals:

  - co-change: symbols that appear together in one op's footprint. This is a *tighter* signal
    than raw commit co-occurrence, because U2's mining already def-use-untangles a tangled commit
    into several ops -- co-membership in one op's footprint is already a coherent group.
  - structural: calls/imports/contains at HEAD (`sgt.entities.graph.build_entity_graph`), with
    degree-based hub suppression (`hub_normalize`) so a god-class doesn't fuse unrelated features.

A third, weaker signal -- conventional-commit scope -- binds symbols that changed under the same
declared scope even across different ops, which plain co-change misses on a young repo.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import igraph as ig
import leidenalg as la

from sgt.core.ideal import Ideal
from sgt.core.op import BOTTOM, Op, is_content_bearing
from sgt.entities.graph import build_entity_graph
from sgt.store.gitbind import GitBinding

HUB_OP_FRAC = 0.15  # a symbol touched by >= this fraction of all mined ops is a hub, stripped
# from the co-change signal so a "touched everywhere" symbol (a base class, a shared constant)
# doesn't glue unrelated features into one blob.
MAX_FOOTPRINT = 20  # an op touching more than this many alive symbols contributes no co-change
# edges -- likely a mechanical mass-edit (a repo-wide rename), not a feature-shaped group.
SEED = 42  # Leiden's own seed -- deterministic partitions for the same graph+resolution.


def alive_nodes(ideal: Ideal, ops: list[Op]) -> set[str]:
    """Every content-bearing symbol with a live (non-`BOTTOM`) tip at `ideal`'s frontier -- the
    clustering graph's node universe. Mirrors `Ideal.covered_paths`'s liveness test, at symbol
    rather than path granularity."""
    by_id = {op.id: op for op in ops}
    frontier = ideal.frontier(ops)
    return {
        sym for sym, op_id in frontier.items()
        if by_id[op_id].footprint[sym][1] != BOTTOM and is_content_bearing(sym)
    }


def _dominant_dir(members: list[str]) -> str:
    """The most common `dir/subdir` prefix among a group's symbol ids -- a cheap, deterministic
    label fallback and a display hint even when an LLM label exists."""
    def prefix(sym: str) -> str:
        parts = sym.split("::", 1)[0].split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    return Counter(prefix(m) for m in members).most_common(1)[0][0]


_SCOPE_RE = re.compile(r"^\w+\(([^)]+)\)")


def commit_scope(subject: str) -> str | None:
    """The conventional-commit scope the author declared: ``feat(store): ...`` -> ``store``.
    Falls back to the commit type (``feat``/``fix``/``docs``) so every commit lands somewhere."""
    m = _SCOPE_RE.match(subject)
    if m:
        return m.group(1)
    m2 = re.match(r"^(\w+)[:(]", subject)
    return m2.group(1) if m2 else None


def scope_edges(
    ops: list[Op], subjects: dict[str, str], nodes: set[str], hubs: set[str],
    scale: float = 10.0, max_scope: int = 80,
) -> dict[frozenset, float]:
    """Intent signal: symbols changed under the same declared scope, even in *different* ops --
    the density plain co-change lacks on a young repo. Weight is down-scaled by scope size so a
    broad scope contributes weak all-to-all glue, not a blob."""
    scope_syms: dict[str, set[str]] = defaultdict(set)
    for op in ops:
        scope = None
        for sha in op.provenance:
            subject = subjects.get(sha)
            if subject:
                scope = commit_scope(subject)
                break
        if not scope:
            continue
        for sym in op.footprint:
            if sym in nodes and sym not in hubs:
                scope_syms[scope].add(sym)

    edges: dict[frozenset, float] = defaultdict(float)
    for members_set in scope_syms.values():
        members = sorted(members_set)
        if not (2 <= len(members) <= max_scope):
            continue
        w = scale / (len(members) - 1)
        for a, b in combinations(members, 2):
            edges[frozenset((a, b))] += w
    return dict(edges)


def hub_normalize(structural: dict[frozenset, float]) -> dict[frozenset, float]:
    """Suppress structural hubs so a god-class / universal-import bus stops fusing unrelated
    features into one blob. Scales each edge by ``1/sqrt(deg(a)*deg(b))`` (bibliometric-style hub
    suppression) so a link to a universal hub counts for little while a focused feature-internal
    link counts fully, then rescales to preserve the total structural weight -- this *demotes*
    hubs rather than stripping their edges, which measurably held coverage better on this repo's
    own history (see the promoted-from experiment's findings)."""
    deg: dict[str, float] = defaultdict(float)
    for pair, w in structural.items():
        a, b = tuple(pair)
        deg[a] += w
        deg[b] += w
    raw = {
        pair: w / (deg[tuple(pair)[0]] * deg[tuple(pair)[1]]) ** 0.5
        for pair, w in structural.items()
    }
    total = sum(structural.values())
    scale = total / sum(raw.values()) if raw else 1.0
    return {pair: w * scale for pair, w in raw.items()}


def _fuse(*weight_maps: dict) -> dict:
    out: dict[frozenset, float] = defaultdict(float)
    for d in weight_maps:
        for k, v in d.items():
            out[k] += v
    return dict(out)


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


def signals(
    repo: Path, ops: list[Op], ideal: Ideal,
) -> tuple[set[str], set[str], dict[frozenset, float], dict[frozenset, float]]:
    """The clustering graph's raw ingredients: ``(nodes, hubs, cochange, structural)``. `ops`
    should be the *full* mined history (`Store.all_ops()`), not just `ideal`'s own op-set --
    co-change is a historical fact even about symbols whose current chain tip came from a later
    op. Structural edges are read from the ideal's materialized tree at HEAD (round-trip laws
    guarantee this equals `fold.code(ideal, ops)`)."""
    gb = GitBinding(repo)
    nodes = alive_nodes(ideal, ops)

    op_freq: Counter = Counter()
    for op in ops:
        op_freq.update(set(op.footprint) & nodes)
    hub_cut = max(2, int(HUB_OP_FRAC * len(ops))) if ops else 2
    hubs = {sym for sym, freq in op_freq.items() if freq >= hub_cut}

    cochange: dict[frozenset, float] = defaultdict(float)
    for op in ops:
        alive = [s for s in op.footprint if s in nodes and s not in hubs]
        if 2 <= len(alive) <= MAX_FOOTPRINT:
            w = 1.0 / (len(alive) - 1)
            for a, b in combinations(sorted(alive), 2):
                cochange[frozenset((a, b))] += w

    structural: dict[frozenset, float] = defaultdict(float)
    head = gb.head()
    if head is not None:
        for edge in build_entity_graph(gb.tree_at(head)).edges:
            if edge.src in nodes and edge.dst in nodes and edge.src != edge.dst:
                structural[frozenset((edge.src, edge.dst))] += 1.0

    return nodes, hubs, dict(cochange), dict(structural)
