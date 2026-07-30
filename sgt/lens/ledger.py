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

import hashlib
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


def _new_lane_id(symbol: str) -> str:
    """The id a new-lane fallback (cascade step 4) mints for a genuinely-new symbol. Content-
    addressed on the symbol -- NOT `uuid4` -- so the ledger's own guarantee holds: two saves of
    identical content produce byte-identical assignment (the module docstring's core invariant). A
    random id violates it -- the same symbol saved twice would seed two different lanes, and every
    rebuild in between would see a fresh competing assign-pin, the churn `_apply_assign_pins` then
    oscillates over. The `af-` prefix keeps it distinguishable from a clustered `f-` lane; the
    `m<sha256>` shape mirrors `tree._content_birth_id`'s member-hash form."""
    return f"af-m{hashlib.sha256(symbol.encode('utf-8')).hexdigest()}"


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


# -- save-time wiring (U6): assignments land as durable pins + authored CRDT state ---------------


def dual_claims(af: dict) -> list[tuple[str, list[str]]]:
    """Every symbol that is a live member of MORE THAN ONE authored feature -- a cross-clone
    dual-lane membership (the Risks & Dependencies "Cross-clone dual-lane membership" case): two
    clones each ran the local move on the same new symbol against a locally-different owned-neighbour
    view, landing it live in two different `af-` features, which `authored.merge_feature`'s
    union-*within-one-id* logic never reconciles (the two claims live under different ids). Pure,
    read-only detection -- never a silent resolve; U6 wires it at the sync merge site to surface each
    as a `conflict` in U7's suggestion queue. Returns `[(symbol, [feature_id, ...]), ...]`, sorted,
    only for symbols with >= 2 claiming features."""
    claimants: dict[str, list[str]] = {}
    for fid in sorted(af):
        for m in af[fid].live_members():
            claimants.setdefault(m, []).append(fid)
    return [(sym, fids) for sym, fids in sorted(claimants.items()) if len(fids) > 1]


def assign_at_save(repo, ideal, ops) -> dict | None:
    """Wire the save-time cascade (U6, the crux): assign every genuinely-new symbol a durable lane
    the moment it is saved, so no op is ever invisible on the grid between full reclusters and a
    rebuild can never silently move it (R1/R2). Two durability mechanisms, both mirroring
    `sgt.lens.verbs.apply_move`/`apply_split` verbatim (KTD5 -- no new merge logic is authored):

      * an **assign pin** (`pins.assign[symbol] = lane`) gives LOCAL durability -- `tree.build`'s
        must-link contraction + `_apply_assign_pins` hold the symbol in that lane across every future
        recluster, and it syncs cross-clone via `reconcile.union_pins`;
      * an **authored feature** (`authored.add_member` for an existing lane, `authored.create` for a
        new one) is the CRDT that carries the assignment across sync and lets a two-clone dual-claim
        surface as a conflict (`dual_claims`, above).

    `save` does not rebuild the tree, and `grid_view`/`map`/`blame` read the *persisted* `op_leaf`,
    so a new symbol is invisible until a rebuild even with a pin. FIX (the visibility patch): after
    the cascade, add each assigned symbol to its lane node's `members` and re-run
    `assign_ops_to_leaves` (cheap -- a pure vote over tree membership, no reclustering), so the new
    op appears in its lane's cell immediately; the assign pin guarantees a later full rebuild agrees.

    Returns a `{"assigned": {symbol: lane}, "new_lanes": [af-id, ...]}` summary (or `None` when the
    tree hasn't been built yet), for testability."""
    from dataclasses import replace
    from pathlib import Path

    from sgt.core.op import _symbol_kind
    from sgt.lens import authored, verbs
    from sgt.lens.pins import load_pins
    from sgt.store.gitbind import GitBinding

    repo = Path(repo)
    previous = tree.load(repo)
    if not previous or not previous.get("nodes"):
        return None  # the first build owns the initial clustering; nothing to cascade

    nodes = previous["nodes"]
    member_leaf = tree.leaf_member_index(nodes)
    frontier = ideal.frontier(ops)  # symbol -> id of its maximal in-ideal op
    new_symbols = {
        s for s in frontier
        if _symbol_kind(s) in ("entity", "nested", "whole_file") and s not in member_leaf
    }
    if not new_symbols:
        return {"assigned": {}, "new_lanes": []}  # the common modify-only save -- no cost paid

    _all, fused, hubs = tree.fused_graph_with_hubs(repo, ops, ideal)
    assignments = assign_new_symbols(new_symbols, member_leaf, fused, hubs)
    if not assignments:
        return {"assigned": {}, "new_lanes": []}

    pins = load_pins(repo)
    af = authored.load_authored(repo)  # loaded ONCE and saved ONCE -- `_open_authored` reloads per
    # call, which would drop a prior lane's write when a save assigns to two lanes at once.
    assign = dict(pins.assign)
    head = GitBinding(repo).head()

    assigned: dict[str, str] = {}
    new_lanes: list[str] = []
    for symbol in sorted(assignments):
        lane = assignments[symbol]
        if lane is not None:
            # Attach to an existing lane -- mirror `verbs.apply_move`: pin the symbol, ensure the
            # lane's authored feature exists (seeded from the leaf, `verbs._open_authored`'s body,
            # inlined so `af` isn't reloaded per lane) and add the member, and add it to the lane
            # node's members so the visibility patch's re-vote maps its op here.
            assign[symbol] = lane
            node = nodes[lane]
            aid = verbs._authored_id_for(lane)
            if aid not in af:
                # Seed the register with an EMPTY label, exactly like the new-lane fallback below:
                # a save-time cascade records *membership*, never a *name*. The leaf's node label is
                # a clustered/LLM proposal (or, for a provisional lane, a guessed file path) -- and
                # `tree.label_tree` lets any non-empty authored label permanently override the
                # rebuild's own label. Seeding it here would freeze the lane's name at this snapshot
                # (and, for a provisional lane, shadow it with a file path). Only a deliberate
                # `sgt rename` fills the register; until then the rebuild owns the name.
                af[aid] = replace(
                    authored.create(node["members"], "", witness=head), id=aid,
                )
            if symbol not in af[aid].live_members():  # guard: add_member mints a fresh tag each call
                af[aid] = authored.add_member(af[aid], symbol)
            if symbol not in node["members"]:
                node["members"] = sorted(node["members"] + [symbol])
                node["size"] = len(node["members"])
            assigned[symbol] = lane
        else:
            # New-lane fallback (KTD2): a fresh `af-<uuid>` lane. The lane id IS the authored id, so
            # pin/tree/authored all agree. The leaf attaches as a new root (`parent=None`), mirroring
            # `verbs.apply_split`'s childless-root case -- a disconnected new symbol is a top-level
            # lane, not a child of any existing one. No `gamma` is recorded: `AuthoredFeature` has no
            # such field (KTD3 sources 1/2 are deferred, the geometric midpoint is the shipped
            # default), so inventing one is out of scope here.
            # The file the symbol lives in is the lane's *provisional display* label -- what the grid
            # shows in the window between this save and the next full recluster. It is NOT written
            # into the authored feature's label register: that register is the LWW name a deliberate
            # `sgt rename` sets, and seeding it with a guessed file path would permanently shadow the
            # clustered/LLM label a rebuild computes for the lane (`tree.label_tree` only lets a
            # *non-empty* authored label override the clustered proposal). So the register starts
            # empty -- the rebuild names the lane, and a later `rename` overrides that.
            provisional = symbol.split("::", 1)[0]
            lane_id = _new_lane_id(symbol)
            existing = af.get(lane_id)
            if existing is not None:
                # Re-entry: the symbol was minted before (then deleted while its register record
                # survived), and the content-addressed id collides by design. Reuse the record --
                # mirroring the attach path's `if aid not in af` guard above -- because recreating
                # it would reset the CRDT clock and silently drop the label and any members added
                # since; sync would then see a rewrite, not a mergeable update.
                if symbol not in existing.live_members():
                    af[lane_id] = authored.add_member(existing, symbol)
            else:
                af[lane_id] = replace(authored.create([symbol], "", witness=head), id=lane_id)
            assign[symbol] = lane_id
            if lane_id not in nodes:
                nodes[lane_id] = {
                    "id": lane_id, "parent": None, "depth": 0,
                    "members": [symbol], "size": 1, "dir": cluster._dominant_dir([symbol]),
                    "children": [], "split_reason": None, "label": provisional, "why": "",
                }
                previous.setdefault("roots", []).append(lane_id)
            assigned[symbol] = lane_id
            new_lanes.append(lane_id)

    verbs._save_pins(repo, pins, assign=assign)  # stamps the introducing witness correctly (D6)
    authored.save_authored(repo, af)

    # Visibility patch (NO recluster): the new members now sit in their lane nodes, so re-voting
    # op_leaf over the patched membership makes each new op appear in its lane's cell immediately.
    previous["op_leaf"] = tree.assign_ops_to_leaves(previous["nodes"], ops)
    tree.save(repo, previous)
    return {"assigned": assigned, "new_lanes": new_lanes}
