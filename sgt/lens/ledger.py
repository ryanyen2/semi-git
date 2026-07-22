"""The save-time ownership ledger (plan U5): assign every new symbol a lane deterministically the
moment it is saved, so no op is ever invisible on the grid between full reclusters (R1).

The assignment cascade, in fixed order (KTD1), stops at the first case that fires:

  1. chain continuation  -- an op on a symbol some lane already owns stays in that lane (a dict
     lookup against the current membership index). The dominant case, free.
  2. residue / anchor     -- a residue/anchor pseudo-symbol follows its anchor entity's lane. Owned
     by `sgt.lens.tree.assign_ops_to_leaves` (U4), not repeated here.
  3. local move           -- a genuinely-new symbol enters `local_move_assign`: bounded to the
     symbol and its 1-hop owned, non-hub neighbours, it runs leidenalg's own local-moving phase to
     convergence with every owned neighbour *frozen*, so only the new symbols can move (KTD3). This
     is the real local-moving step of incremental Leiden, not a one-shot greedy attach, yet it
     never touches an already-owned symbol outside the boundary.
  4. new-lane fallback    -- a new symbol with no owned neighbour at all seeds a fresh lane.

`local_move_assign` is the algorithmic heart and is pure/deterministic: a fixed (sorted) vertex
order and `cluster.SEED` pin leidenalg's visit order and RNG, so two saves of identical content
produce byte-identical assignment. Wiring the cascade into `sgt save` and persisting the result as
durable authored-feature CRDT state is U6; the suggestion queue that lets clustering *propose*
merges/splits without auto-applying is U7.
"""

from __future__ import annotations

import math

import igraph as ig
import leidenalg as la

from sgt.lens import cluster, tree

# Boundary bound (KTD3): only a new symbol's TOP_K highest-weight owned, non-hub neighbours enter
# the local graph, so the local-move cost is proportional to genuinely new work, never the repo
# size -- a new symbol edging into an artificially high-degree owned symbol can't blow it up.
TOP_K = 50

# The default CPM resolution when a lane carries no persisted split gamma to inherit (KTD3 source
# 3): the geometric midpoint of the clusterer's own search bounds, so the local move resolves at
# the same scale a full recluster's binary search centres on.
_GAMMA_MIDPOINT = math.exp((math.log(tree.GAMMA_LO) + math.log(tree.GAMMA_HI)) / 2)


def local_move_assign(
    new_symbols: set[str],
    member_leaf: dict[str, str],
    fused: dict,
    hubs: set[str],
    *,
    gamma: float | None = None,
    top_k: int = TOP_K,
) -> dict[str, str | None]:
    """Assign each genuinely-new symbol a lane by a bounded Leiden local move (KTD3, cascade step
    3). `member_leaf` is the current symbol->lane index (owned symbols); `fused` the clustering
    coupling graph (`cluster` signals, keyed by `frozenset({a, b}) -> weight`); `hubs` the
    hub-suppressed symbols. Returns `{symbol: lane_id | None}` -- `None` for a symbol with no owned,
    non-hub neighbour at all (the caller's new-lane fallback, step 4).

    Deterministic: the boundary is built in sorted vertex order and the optimiser's RNG is pinned
    to `cluster.SEED`, so two calls on identical input converge to the identical partition -- the
    property a save-time assignment must have (identical content -> identical lanes)."""
    adj = tree._adjacency(fused)

    # Score each owned, non-hub neighbour of the new symbols by summed coupling weight.
    scores: dict[str, float] = {}
    for s in sorted(new_symbols):
        for other, w in adj.get(s, ()):
            if other in member_leaf and other not in hubs and other not in new_symbols:
                scores[other] = scores.get(other, 0.0) + w
    neighbours = sorted(scores, key=lambda o: (-scores[o], o))[:top_k]
    if not neighbours:
        return {s: None for s in new_symbols}  # step 4: new-lane fallback

    if gamma is None:
        gamma = _GAMMA_MIDPOINT

    boundary = sorted(set(new_symbols) | set(neighbours))
    induced = tree._induced(fused, set(boundary))

    # Seed the partition: each owned neighbour is FIXED at its lane's index; each new symbol starts
    # in its own free singleton community. Because a fixed node can never move, its final community
    # index is always its lane index -- so resolving a new symbol's lane after convergence is a
    # direct inverse lookup, never a fuzzy vote.
    lane_of_neighbour = {o: member_leaf[o] for o in neighbours}
    lane_index = {lane: i for i, lane in enumerate(sorted(set(lane_of_neighbour.values())))}
    membership: list[int] = []
    fixed: list[bool] = []
    next_free = len(lane_index)
    for node in boundary:
        if node in lane_of_neighbour:
            membership.append(lane_index[lane_of_neighbour[node]])
            fixed.append(True)
        else:
            membership.append(next_free)
            next_free += 1
            fixed.append(False)

    idx = {n: i for i, n in enumerate(boundary)}
    edges, weights = [], []
    for pair, w in induced.items():
        a, b = tuple(pair)
        if a in idx and b in idx and w > 0:
            edges.append((idx[a], idx[b]))
            weights.append(w)
    g = ig.Graph(n=len(boundary), edges=edges)
    g.vs["name"] = boundary
    g.es["weight"] = weights

    part = la.CPMVertexPartition(
        g, initial_membership=membership, weights="weight", resolution_parameter=gamma,
    )
    opt = la.Optimiser()
    opt.set_rng_seed(cluster.SEED)
    opt.move_nodes(part, is_membership_fixed=fixed, consider_comms=la.ALL_NEIGH_COMMS)

    lane_of_community = {i: lane for lane, i in lane_index.items()}
    final = dict(zip(boundary, part.membership))
    # A new symbol that converged into a fixed community maps back to that lane; one that stayed in
    # (or formed) a free community has no owned lane -> None (new-lane fallback for it too).
    return {s: lane_of_community.get(final[s]) for s in new_symbols}


def assign_new_symbols(
    new_symbols: set[str],
    member_leaf: dict[str, str],
    fused: dict,
    hubs: set[str],
    *,
    gamma: float | None = None,
) -> dict[str, str | None]:
    """The save-time cascade over genuinely-new symbols (steps 3 + 4). Only real code *entities*
    enter the local move: a residue/anchor pseudo-symbol is deliberately excluded here because it
    follows its anchor entity's lane through `sgt.lens.tree.assign_ops_to_leaves` (U4) once the new
    entities are in `member_leaf` -- assigning it a lane of its own would fight that. Returns
    `{entity_symbol: lane_id | None}` (None -> a new lane the caller mints, step 4)."""
    from sgt.core.op import _symbol_kind

    entities = {s for s in new_symbols if _symbol_kind(s) in ("entity", "nested", "whole_file")}
    if not entities:
        return {}
    return local_move_assign(entities, member_leaf, fused, hubs, gamma=gamma)
