"""The hierarchical feature tree over ops (plan U12, R15/R16/R17): a recursive subsystem ->
... -> feature-lane partition of every content-bearing symbol, built on `sgt.lens.cluster`'s
fused coupling graph. Promoted from `experiments/patch_clustering/hierarchy.py`, with the fixed
gamma ladder replaced by a binary search targeting 5-9 children per split (plan D2) and the
module-global ``MAX_DEPTH`` mutation removed.

Two rules the original experiment already validated keep the tree honest rather than a
mechanical over-split:

  - NO-ORPHAN: a sub-``MIN_LANE`` cluster is never dropped -- it is folded into the sibling it is
    most coupled to, so every alive symbol lands in some leaf.
  - STOP-SPLIT: a node is only split when the search can actually separate it into >= 2 real
    groups; a split that yields one dominant child + dust is refused.

The tree is single-rooted: `roots` is always ``[root_id]``, whose node holds every alive symbol;
real subsystems (if any) are its children. This avoids a depth-bookkeeping split between "the top
level" and every deeper level -- `_subdivide` is the one recursive rule, applied uniformly from
depth 0.

Every leaf that can't be split further carries a `split_reason` explaining why (`"max_depth"` /
`"max_leaf"` / `"stop_split"`); a node that DID split but missed the [5,9] arity target carries
`"closest_arity"` instead of `None` -- the tree never silently violates the arity invariant
without saying why.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from sgt import state
from sgt.core.op import Op, _symbol_kind
from sgt.lens import cluster
from sgt.lens.cluster import (
    _dominant_dir, _fuse, _leiden_graph, _leiden_graph_prior, _leiden_partition,
    _leiden_partition_prior,
)
from sgt.lens.pins import (
    Pins, _expand_members, _must_link_groups, apply_must_link, enforce_cannot_link, load_pins,
)
from sgt.store.gitbind import GitBinding

MIN_LANE = 4        # a node must own >= this many symbols to stand alone (else folded into a sibling)
MAX_LEAF = 24        # a node this small is coherent enough to stay a leaf -- stop splitting
MAX_DEPTH = 4        # hard cap: levels 0..3
TARGET_ARITY = (5, 9)  # desired child count per split (plan D2)
GAMMA_LO = 1e-4
GAMMA_HI = 1.0
MAX_SEARCH_ITER = 20
PLATEAU_LOG_WIDTH = 0.1  # the gamma search stops early on a repeated partition only once the log-
# bracket is this narrow (all remaining probes within a ~1.1x gamma factor) -- a repeat while the
# bracket is still wide just means the search hasn't reached the transition yet, not that the curve
# is flat (a 2-clique graph repeats its one-blob partition for several probes before splitting).
THETA = 0.5         # Greene member-overlap threshold for feature identity across runs (plan D5)
MIN_EDGE_SIGNAL = 0.1  # a leaf-pair cross-edge below this weight is noise, not a real coupling --
# used only to detect a *gain*/*loss* of significant coupling between two previous leaves
# (Phase 2's cross-edge dirtying trigger), not to threshold clustering itself.
INTERNAL_DIRTY_FRAC = 0.25  # a previous leaf is internally dirty (should be re-clustered) when its
# summed |Δw| over pairs internal to the leaf is >= this fraction of its old internal mass -- the
# intra-leaf counterpart to the cross-edge trigger (plan §3.3). PROVISIONAL: swept in the harness.
ABS_FLOOR = 0.5  # absolute floor for the internal-dirty trigger, so a small leaf with little
# internal mass isn't marked dirty by sub-episode noise (≈ half a co-commit episode edge, scale
# 1.0). Threshold = max(ABS_FLOOR, INTERNAL_DIRTY_FRAC × old internal mass). PROVISIONAL: swept.


def _induced(adj: dict, member_set: set[str]) -> dict:
    """The subgraph induced on `member_set`, read off the adjacency index rather than by scanning
    every fused edge: O(Σ degree over `member_set`) instead of O(|all edges|), which is what makes
    a deep recursion of small-subtree splits cheap -- each level only ever touches its own edges."""
    out: dict[tuple[str, str], float] = {}
    for m in sorted(member_set):  # sorted => the output's edge order never depends on set hash order
        for o, w in adj.get(m, ()):
            if m < o and o in member_set:
                out[(m, o)] = w
    return out


def _adjacency(fused: dict) -> dict[str, list[tuple[str, float]]]:
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for (a, b), w in fused.items():
        adj[a].append((b, w))
        adj[b].append((a, w))
    return adj


def _attach_orphans(
    big: list[list[str]], small: list[list[str]], adj: dict[str, list[tuple[str, float]]]
) -> list[list[str]]:
    """Fold each sub-MIN cluster into the big sibling it couples to most. No symbol is dropped --
    an orphan with no coupling to any sibling joins the largest one (``big`` is size-sorted)."""
    groups = [list(c) for c in big]
    sets = [set(c) for c in groups]
    for s in small:
        sset = set(s)
        best_i, best_w = 0, -1.0
        for i, ms in enumerate(sets):
            w = sum(wt for e in sset for (o, wt) in adj.get(e, ()) if o in ms)
            if w > best_w:
                best_i, best_w = i, w
        groups[best_i].extend(s)
        sets[best_i].update(s)
    return groups


@dataclass
class SplitResult:
    groups: list[list[str]] | None  # None => refused (see `reason`)
    reason: str | None  # None (hit target), "closest_arity", or "stop_split"


def _split_once(
    members: list[str], adj: dict, min_lane: int = MIN_LANE,
    target: tuple[int, int] = TARGET_ARITY, lo: float = GAMMA_LO, hi: float = GAMMA_HI,
    max_iter: int = MAX_SEARCH_ITER, prior_leaf_of: dict[str, str] | None = None,
    alpha: float = 0.0, norm: str = cluster.ANCHOR_NORM,
) -> SplitResult:
    """Binary-search the CPM resolution (log-scale) for a gamma whose partition has between
    `target[0]` and `target[1]` groups of size >= `min_lane` (sub-MIN groups are folded in via
    NO-ORPHAN either way). Too few groups means the split is too coarse -- search finer (higher
    gamma); too many means too fine -- search coarser (lower gamma). Keeps the closest-to-target
    result seen across the search as a fallback when no gamma in range lands exactly in range.

    The search stops early when a probe returns the identical partition as the probe before it
    AND the bracket has already narrowed below `PLATEAU_LOG_WIDTH`: the bisection has collapsed
    onto a flat stretch of the count-vs-gamma curve (typically the graph's own connected-component
    floor, which no gamma can go below), and every further probe re-optimizes the same answer at
    full Leiden cost -- on this repo's root graph 13 of 20 probes were byte-identical repeats.
    Repeats never improve `best_gap` (strict `<`), so stopping reproduces the exhaustive search's
    result whenever the plateau persists; the width guard keeps an early wide-bracket repeat (a
    search still marching toward its transition) exploring as before.

    When `alpha > 0` and a `prior_leaf_of` map is given, the induced graph is augmented with the
    Phase B temporal prior (`cluster._leiden_graph_prior`) so the search resists gratuitously
    shattering a previous leaf; `alpha == 0` (the default) is the exact pre-prior path."""
    induced = _induced(adj, set(members))
    sorted_members = sorted(members)
    use_prior = bool(alpha > 0 and prior_leaf_of)
    if use_prior:  # augmented graph built once; only `gamma` changes across the search
        g, aug_nodes, node_sizes, init, n_real = _leiden_graph_prior(
            sorted_members, induced, prior_leaf_of, alpha, norm
        )
    else:
        g = _leiden_graph(sorted_members, induced)  # built once; only `gamma` changes across the search
    lo_log, hi_log = math.log(lo), math.log(hi)
    best_big: list[list[str]] | None = None
    best_small: list[list[str]] = []
    best_gap = None
    prev_parts: list[list[str]] | None = None

    for _ in range(max_iter):
        mid_log = (lo_log + hi_log) / 2
        gamma = math.exp(mid_log)
        parts = (
            _leiden_partition_prior(g, aug_nodes, node_sizes, init, n_real, gamma)
            if use_prior else _leiden_partition(g, sorted_members, gamma)
        )
        if parts == prev_parts and hi_log - lo_log < PLATEAU_LOG_WIDTH:
            break  # plateau: same partition inside a collapsed bracket -- see docstring
        prev_parts = parts
        big = sorted((p for p in parts if len(p) >= min_lane), key=lambda p: -len(p))
        small = [p for p in parts if len(p) < min_lane]
        count = len(big)
        gap = 0 if target[0] <= count <= target[1] else min(abs(count - target[0]), abs(count - target[1]))

        if best_gap is None or gap < best_gap:
            best_big, best_small, best_gap = big, small, gap
        if gap == 0:
            break
        if count < target[0]:
            lo_log = mid_log  # too coarse -- search finer (bigger gamma)
        else:
            hi_log = mid_log  # too fine -- search coarser (smaller gamma)

    if best_big is None or len(best_big) < 2:
        return SplitResult(None, "stop_split")
    groups = _attach_orphans(best_big, best_small, adj)
    return SplitResult(groups, None if best_gap == 0 else "closest_arity")


def _subdivide(
    members: list[str], adj: dict, depth: int, max_depth: int,
    min_lane: int = MIN_LANE, max_leaf: int = MAX_LEAF,
    prior_leaf_of: dict[str, str] | None = None, alpha: float = 0.0,
    norm: str = cluster.ANCHOR_NORM,
) -> dict:
    """Recursively split `members` into a node tree. A node with empty `children` is a leaf. The
    Phase B temporal prior (`prior_leaf_of`/`alpha`) is threaded to `_split_once` at every level."""
    node = {
        "members": sorted(members), "size": len(members), "dir": _dominant_dir(members),
        "depth": depth, "children": [], "split_reason": None,
    }
    if depth >= max_depth - 1:
        node["split_reason"] = "max_depth"
        return node
    if len(members) <= max_leaf:
        node["split_reason"] = "max_leaf"
        return node

    result = _split_once(members, adj, min_lane=min_lane,
                         prior_leaf_of=prior_leaf_of, alpha=alpha, norm=norm)
    if result.groups is None:
        node["split_reason"] = result.reason
        return node

    node["children"] = [
        _subdivide(sorted(g), adj, depth + 1, max_depth, min_lane, max_leaf,
                   prior_leaf_of, alpha, norm)
        for g in result.groups
    ]
    node["split_reason"] = result.reason
    return node


def _resplit_real(
    real_members: list[str], real_fused: dict, pins: Pins, depth: int, max_depth: int,
    min_lane: int = MIN_LANE, max_leaf: int = MAX_LEAF,
    prior_leaf_of: dict[str, str] | None = None, alpha: float = 0.0,
    norm: str = cluster.ANCHOR_NORM,
) -> dict:
    """Cluster `real_members` from scratch in real (uncontracted) member space: contract must-link
    pins scoped to this exact member set (`apply_must_link` self-restricts to `real_members`), run
    the ordinary `_subdivide` over the contracted graph, then expand the synthetic pin-group
    vertices back to real members. The one path that actually invokes Leiden -- both `build`'s
    cold-start root and `_dirty_subdivide`'s per-subtree resplit (Phase 2) funnel through here, so
    a dirty subtree is clustered exactly as if it had been built from scratch on its own.

    The Phase B temporal prior is keyed by real symbol (`prior_leaf_of`); a synthetic pin-group
    vertex isn't in it, so it simply gets no anchor (a documented simplification -- pins are rare)."""
    contracted_nodes, contracted_fused, expansion = apply_must_link(real_members, real_fused, pins)
    contracted_adj = _adjacency(contracted_fused)
    node = _subdivide(contracted_nodes, contracted_adj, depth, max_depth,
                      min_lane, max_leaf, prior_leaf_of, alpha, norm)
    _expand_members(node, expansion)
    return node


def _splice(previous_nodes: dict, nid: str, depth: int) -> dict:
    """Deep-copy a previous tree node (and its descendants) into the nested-dict shape `_subdivide`
    produces, ready for `_register` -- verbatim reuse of a subtree Phase 2 decided is unchanged."""
    nd = previous_nodes[nid]
    members = list(nd["members"])
    return {
        "members": members, "size": len(members), "dir": _dominant_dir(members), "depth": depth,
        "children": [_splice(previous_nodes, c, depth + 1) for c in nd["children"]],
        "split_reason": nd.get("split_reason"),
    }


def _leaf_cross_edges(
    leaf_of: dict[str, str], fused: dict[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    """Roll `fused`'s symbol-pair edges up to leaf-pair totals, for whichever symbols `leaf_of`
    covers (cross-leaf pairs only). Used both sides of Phase 2's cross-edge dirtying diff -- once
    against the cached previous fused graph, once against the current one, same `leaf_of`."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for (a, b), w in fused.items():
        la, lb = leaf_of.get(a), leaf_of.get(b)
        if la is None or lb is None or la == lb:
            continue
        totals[(la, lb) if la < lb else (lb, la)] += w
    return dict(totals)


def _cross_edge_dirty_leaves(
    old_leaf_of: dict[str, str], old_fused: dict, new_fused: dict, threshold: float = MIN_EDGE_SIGNAL,
) -> set[str]:
    """Previous leaves whose coupling to some other previous leaf crossed `threshold` (gained or
    lost significant coupling) between the cached previous fused graph and the current one --
    Phase 2's cross-edge dirtying trigger, independent of whether either leaf's own membership
    changed."""
    old_cross = _leaf_cross_edges(old_leaf_of, old_fused)
    new_cross = _leaf_cross_edges(old_leaf_of, new_fused)
    dirty: set[str] = set()
    for pair in old_cross.keys() | new_cross.keys():
        if (old_cross.get(pair, 0.0) >= threshold) != (new_cross.get(pair, 0.0) >= threshold):
            dirty.update(pair)
    return dirty


def _internal_dirty_leaves(
    old_leaf_of: dict[str, str], old_fused: dict, new_fused: dict,
    frac: float = INTERNAL_DIRTY_FRAC, abs_floor: float = ABS_FLOOR,
) -> set[str]:
    """Previous leaves whose *internal* coupling changed significantly between the cached previous
    fused graph and the current one: ``Σ_{pairs ⊆ L} |Δw| >= max(abs_floor, frac × Σ_{pairs ⊆ L}
    w_old)`` (plan §3.3). Closes `_cross_edge_dirty_leaves`'s intra-leaf blindness -- a leaf whose
    members re-coupled among themselves (so it should now split, or a stale sub-split should
    dissolve) while its coupling to *other* leaves stayed the same. One pass over the union of both
    graphs' edges; the temporal prior (§3.1) then keeps whatever re-cluster this triggers anchored
    to the previous shape wherever the evidence still supports it."""
    delta: dict[str, float] = defaultdict(float)         # Σ |Δw| over pairs internal to each leaf
    old_internal: dict[str, float] = defaultdict(float)  # Σ w_old over pairs internal to each leaf
    for pair in old_fused.keys() | new_fused.keys():
        a, b = pair
        la, lb = old_leaf_of.get(a), old_leaf_of.get(b)
        if la is None or la != lb:  # only pairs internal to one previous leaf
            continue
        w_old = old_fused.get(pair, 0.0)
        delta[la] += abs(new_fused.get(pair, 0.0) - w_old)
        old_internal[la] += w_old
    return {
        leaf for leaf, d in delta.items()
        if d >= max(abs_floor, frac * old_internal.get(leaf, 0.0))
    }


def _dirty_subdivide(
    members: list[str], real_adj: dict, depth: int, max_depth: int,
    previous_nodes: dict, prev_id: str | None, dirty_leaves: set[str], pins: Pins,
    min_lane: int = MIN_LANE, max_leaf: int = MAX_LEAF,
    prior_leaf_of: dict[str, str] | None = None, alpha: float = 0.0,
    norm: str = cluster.ANCHOR_NORM,
) -> dict:
    """Phase 2: splice a previous subtree through verbatim wherever its member set and cross-leaf
    coupling are both unchanged; only re-cluster (`_resplit_real`) the subtrees that actually
    changed. `prev_id`/`previous_nodes` locate the previous tree's node at this same logical
    position (None, or a leaf, when there's no further previous structure to delegate to -- then a
    plain resplit, same as a from-scratch build of just this subtree).

    Operates in *real* (uncontracted) member space throughout, matching `previous_nodes`'s own
    member space (`_expand_members` already ran on it at the end of the previous build) --
    must-link contraction happens only locally, inside `_resplit_real`, for whichever subtree
    actually needs reclustering. `build` guarantees this is only called when every must-link
    group's alive members already sit in one previous leaf (see its `pins_consistent` check), so a
    group can never be split across two independently-resplit subtrees here."""
    cur = frozenset(members)
    prev_node = previous_nodes.get(prev_id) if prev_id is not None else None

    if prev_node is not None and frozenset(prev_node["members"]) == cur:
        leaves_here = set(_leaf_ids(previous_nodes, prev_id))
        if not (leaves_here & dirty_leaves):
            return _splice(previous_nodes, prev_id, depth)

    if prev_node is None or not prev_node["children"]:
        # Hand the resplit only this subtree's own induced edges (read off the adjacency index),
        # not the whole fused graph -- `apply_must_link` scans every edge it is given, and a full
        # scan per resplit subtree was the warm build's single largest cost.
        return _resplit_real(sorted(members), _induced(real_adj, cur), pins, depth, max_depth,
                             min_lane, max_leaf, prior_leaf_of, alpha, norm)

    prev_children_ids = prev_node["children"]
    assigned: dict[str, set[str]] = {
        cid: set(previous_nodes[cid]["members"]) & cur for cid in prev_children_ids
    }
    covered: set[str] = set().union(*assigned.values()) if assigned else set()
    for m in sorted(cur - covered):  # a brand-new real member with no previous home in this subtree
        best_cid, best_w = prev_children_ids[0], -1.0
        for cid in prev_children_ids:
            w = sum(wt for (o, wt) in real_adj.get(m, ()) if o in assigned[cid])
            if w > best_w:
                best_cid, best_w = cid, w
        assigned[best_cid].add(m)

    children = [
        _dirty_subdivide(
            sorted(assigned[cid]), real_adj, depth + 1, max_depth,
            previous_nodes, cid, dirty_leaves, pins, min_lane, max_leaf,
            prior_leaf_of, alpha, norm,
        )
        for cid in prev_children_ids
    ]
    return {
        "members": sorted(members), "size": len(members), "dir": _dominant_dir(members),
        "depth": depth, "children": children, "split_reason": prev_node.get("split_reason"),
    }


def _tree_fingerprint(nodes: dict) -> str:
    """A content hash of `nodes`'s leaf structure -- ties a cached fused-graph snapshot to the
    exact tree it was derived from, so a stale or foreign snapshot (e.g. `reconcile` building
    against a `previous` other than the last local build) is detected and skipped rather than
    misapplied."""
    leaves = sorted((nid, tuple(sorted(nd["members"]))) for nid, nd in nodes.items() if not nd["children"])
    return hashlib.sha256(repr(leaves).encode("utf-8")).hexdigest()


def _load_fused_snapshot(repo: Path, fingerprint: str) -> dict | None:
    snap = state.load_json(repo, "fused_snapshot")
    if snap is None or snap.get("fingerprint") != fingerprint:
        return None
    return {(a, b): w for a, b, w in snap["fused"]}  # entries persisted sorted -- key is canonical


def _save_fused_snapshot(repo: Path, fingerprint: str, fused: dict[tuple[str, str], float]) -> None:
    state.save_json(repo, "fused_snapshot", {
        "fingerprint": fingerprint,
        "fused": [[a, b, w] for (a, b), w in fused.items()],  # keys already sorted; same on-disk
        # shape as the frozenset-keyed writer this replaced, so existing snapshots stay readable
    })


def _restamp_depth(node: dict, depth: int) -> None:
    node["depth"] = depth
    for c in node["children"]:
        _restamp_depth(c, depth + 1)


def _regroup_flat_root(root: dict, max_arity: int = TARGET_ARITY[1]) -> None:
    """Turn a flat root into a navigable hierarchy. Single-pass Leiden on this graph (weakly-linked
    file-cliques -- the resolution curve has no 5-9 partition) fans the root out to ~100+ children,
    most of them lone leaves. When there are more than `max_arity`, group the root's children by
    their dominant package directory into synthetic subsystem nodes, so `sgt map` / the Gantt show
    ~a dozen packages rather than one endless list.

    Deterministic and structural-only: it re-parents children and mints synthetic *internal* nodes
    (build-local `N*` ids, assigned later by `_register`), never touching a leaf's id or members --
    so Greene identity, pins, and `op_leaf` are all unaffected (leaves are identity, subsystems are
    just grouping, re-derived every build). A package with a single child stays flat (don't wrap a
    lone node); if every child already has a distinct dir (e.g. an already-regrouped splice), the
    grouping is a no-op. Mutates `root` in place, before `_register`."""
    children = root["children"]
    if len(children) <= max_arity:
        return
    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in children:
        buckets[c["dir"]].append(c)
    if len(buckets) <= 1 or len(buckets) == len(children):
        return  # nothing to gain: all one package, or all already distinct
    new_children: list[dict] = []
    for key in sorted(buckets):
        group = buckets[key]
        if len(group) == 1:
            new_children.append(group[0])
            continue
        members = sorted({m for c in group for m in c["members"]})
        new_children.append({
            "members": members, "size": len(members), "dir": key,
            "depth": root["depth"] + 1, "children": group, "split_reason": "regrouped",
        })
    root["children"] = new_children
    _restamp_depth(root, root["depth"])


def _register(nodes: dict, node: dict, parent: str | None, counter: list[int]) -> str:
    """DFS: give every tree node a stable id, replace child dicts with child ids, index into nodes."""
    nid = f"N{counter[0]}"
    counter[0] += 1
    kids = node.pop("children")
    node["id"] = nid
    node["parent"] = parent
    node["children"] = [_register(nodes, k, nid, counter) for k in kids]
    nodes[nid] = node
    return nid


def _prune_empty_leaves(nodes: dict, roots: list[str]) -> None:
    """Drop every leaf that carries no members. An empty leaf is not a feature -- it can hold no op,
    so it shows `0 ops` on every surface and, because `grid_view` omits an op-less lane while
    `map_view` emits all tree leaves, it is exactly the phantom that made the two rosters disagree
    (17 vs 16). It also accretes: `_splice` copies an unchanged subtree verbatim, so once one exists
    every later build carries it forward. Removing it here -- at the single point all construction
    paths (`_splice`, `_resplit_real`, `_dirty_subdivide`) funnel through, `_register`'s output, and
    BEFORE any feature id is minted for it -- fixes it at the source rather than hiding it downstream.
    Cascades: an internal node left childless becomes an empty leaf and is pruned in turn. A lone
    empty root is kept so the forest is never wholly empty (a degenerate no-member build)."""
    root_set = set(roots)
    changed = True
    while changed:
        changed = False
        for nid in list(nodes):
            nd = nodes.get(nid)
            if nd is None or nd["children"] or nd["members"]:
                continue
            if nid in root_set and len(nodes) == 1:
                continue  # keep a lone empty root so downstream never faces an empty forest
            del nodes[nid]
            parent = nd["parent"]
            if parent is not None and parent in nodes:
                nodes[parent]["children"] = [c for c in nodes[parent]["children"] if c != nid]
            if nid in root_set:
                roots.remove(nid)
                root_set.discard(nid)
            changed = True


def _leaf_ids(nodes: dict, nid: str) -> list[str]:
    nd = nodes[nid]
    if not nd["children"]:
        return [nid]
    out: list[str] = []
    for c in nd["children"]:
        out += _leaf_ids(nodes, c)
    return out


def leaf_member_index(nodes: dict) -> dict[str, str]:
    return {m: nid for nid, nd in nodes.items() if not nd["children"] for m in nd["members"]}


def _anchor_entity_of(sym: str) -> str | None:
    """The top-level entity a residue/anchor pseudo-symbol is anchored to (`path::__residue__::foo`
    -> `path::foo`), or None if `sym` names no real anchor entity -- a plain entity, a whole-file
    symbol, or a file-head residue whose anchor is the HEAD sentinel (which names no entity, so its
    synthesized id simply isn't a live member and the caller falls back)."""
    if _symbol_kind(sym) not in ("residue", "anchor"):
        return None
    path, _, rest = sym.partition("::")
    _, _, anchor = rest.partition("::")  # rest == "__residue__::{anchor}" / "__anchor__::{anchor}"
    return f"{path}::{anchor}" if anchor else None


def _member_leaf_for(sym: str, member_leaf: dict[str, str]) -> str | None:
    """The leaf a footprint symbol votes for. A residue/anchor symbol follows its anchor ENTITY's
    lane -- so a feature owns the whitespace after its own entities, keeping a feature-scoped revert
    or materialization coherent (U4/R3, the U32 fix) -- rather than the residue symbol's own
    clustered leaf. Falls back to the symbol's own leaf when the anchor entity has no lane."""
    anchor_entity = _anchor_entity_of(sym)
    if anchor_entity is not None and anchor_entity in member_leaf:
        return member_leaf[anchor_entity]
    return member_leaf.get(sym)


def assign_ops_to_leaves(nodes: dict, ops: list[Op]) -> dict[str, str]:
    """Every op -> the leaf its footprint's symbols plurality-vote for (tie-break: smallest leaf
    id, for determinism, not numeric order). A residue/anchor symbol votes for its anchor entity's
    lane, not its own cluster (`_member_leaf_for`, U4). An op whose footprint touches no
    leaf-assigned symbol (fully dead, or off-chain) gets no entry -- this is the hook U13's blame
    (`sym -> max-op-in-I -> feature`) and feature verbs (`merge` unions "op-sets") consume."""
    member_leaf = leaf_member_index(nodes)
    # Footprint symbols repeat across thousands of ops; resolve each symbol's vote once (memoized)
    # and count votes in a plain dict -- a Counter per op was this function's dominant cost.
    resolved: dict[str, str | None] = {}
    op_leaf: dict[str, str] = {}
    for op in ops:
        if len(op.footprint) == 1:  # the overwhelmingly common op shape: no vote to tally
            (sym,) = op.footprint
            try:
                leaf = resolved[sym]
            except KeyError:
                leaf = resolved[sym] = _member_leaf_for(sym, member_leaf)
            if leaf is not None:
                op_leaf[op.id] = leaf
            continue
        votes: dict[str, int] = {}
        for sym in op.footprint:
            try:
                leaf = resolved[sym]
            except KeyError:
                leaf = resolved[sym] = _member_leaf_for(sym, member_leaf)
            if leaf is not None:
                votes[leaf] = votes.get(leaf, 0) + 1
        if not votes:
            continue
        if len(votes) == 1:
            op_leaf[op.id] = next(iter(votes))
            continue
        top_count = max(votes.values())
        op_leaf[op.id] = min(leaf for leaf, count in votes.items() if count == top_count)
    return op_leaf


def fused_graph_with_hubs(
    repo: Path, ops: list[Op], ideal, *, refresh_structural_cache: bool = True,
    head: str | None = None,
) -> tuple[list[str], dict[tuple[str, str], float], set[str]]:
    """`fused_graph`, additionally returning the hub-suppressed symbol set `cluster.signals`
    computed. The save-time ledger's local move (`sgt.lens.ledger.assign_at_save`) needs both the
    fused graph *and* `hubs` from ONE `cluster.signals` call -- a second `signals`/`fused_graph`
    would reparse the whole codebase twice. `fused_graph` routes through this and drops the hubs, so
    the two can never diverge.

    `head` (default `gb.head()`) selects the commit the structural signal is read at -- see
    `cluster.signals`; a historical replay passes the point it is reconstructing."""
    gb = GitBinding(repo)
    nodes_set, hubs, cochange, structural = cluster.signals(
        repo, ops, ideal, refresh_cache=refresh_structural_cache, head=head,
    )
    subjects = {sha: subject for sha, _parent, subject in gb.history()}
    scope = cluster.scope_edges(ops, subjects, nodes_set, hubs)
    commit = cluster.commit_edges(ops, nodes_set, hubs)
    path = cluster.path_edges(nodes_set, hubs)
    structural = cluster.hub_normalize(structural)
    fused = _fuse(structural, cochange, scope, commit, path)
    return sorted(nodes_set), fused, hubs


def fused_graph(
    repo: Path, ops: list[Op], ideal, *, refresh_structural_cache: bool = True,
    head: str | None = None,
) -> tuple[list[str], dict[tuple[str, str], float]]:
    """The fused (structural ⊕ co-change ⊕ scope ⊕ co-commit ⊕ path) coupling graph over every
    alive symbol -- shared by `build` (the full recursive tree) and `sgt.lens.verbs.plan_split` (a
    one-off split of a single feature's induced subgraph), so both start from the identical signal.
    Co-commit (`cluster.commit_edges`) is the dense episode signal that recovers what U2's def-use
    untangling strips from per-op co-change (single-symbol ops -> empty co-change); path
    (`cluster.path_edges`) is the weak file-cohesion tissue that keeps the ~72% of symbols with no
    other coupling out of one god-lane.

    `refresh_structural_cache=False` (passed by `build` for `land`/`reconcile`) still reads the
    head-keyed structural-edge cache but never writes it -- see `cluster._structural_edges_at`.
    `head` (default `gb.head()`) selects the commit the structural signal is read at -- see
    `cluster.signals`; a historical replay passes the point it is reconstructing."""
    nodes, fused, _hubs = fused_graph_with_hubs(
        repo, ops, ideal, refresh_structural_cache=refresh_structural_cache, head=head,
    )
    return nodes, fused


def feature_edges(nodes: dict, fused: dict[tuple[str, str], float]) -> list[dict]:
    """Roll the fused symbol-pair coupling graph up to leaf-feature pairs: for every `fused` edge
    whose two symbols land in different leaves, add its weight to that leaf pair's total. Used by
    `sgt.api.map_view` to expose cross-feature structural dependency edges (the same signal
    `plan_split` already reads, at feature-pair rather than symbol-pair granularity)."""
    member_leaf = leaf_member_index(nodes)
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for (a, b), w in fused.items():
        leaf_a, leaf_b = member_leaf.get(a), member_leaf.get(b)
        if leaf_a is None or leaf_b is None or leaf_a == leaf_b:
            continue
        totals[(leaf_a, leaf_b) if leaf_a < leaf_b else (leaf_b, leaf_a)] += w
    edges = [{"a": pair[0], "b": pair[1], "weight": w} for pair, w in totals.items()]
    edges.sort(key=lambda e: (-e["weight"], e["a"], e["b"]))
    return edges


def build(
    repo: Path, ops: list[Op], ideal, max_depth: int = MAX_DEPTH, pins: Pins | None = None,
    previous: dict | None = None, force_rebuild: bool = False, refresh_caches: bool = False,
    head: str | None = None, stability_alpha: float | None = None,
) -> dict:
    """Build the tree from `ops`/`ideal` with stable feature ids and durable pins (no labeling --
    that is `tree.label_tree` / `sgt.lens.label`).

    `pins` defaults to `load_pins(repo)` (the committed `.sgt/pins/pins.json`), mirroring how
    `mine()` auto-consults `load_identity_constraints`. Must-link (explicit + assign-derived) is
    applied as graph contraction *before* clustering so pinned members are structurally
    guaranteed to land in one leaf, regardless of what Leiden alone would have chosen; cannot-link
    is enforced by post-hoc leaf reassignment *after* the tree is built.

    Feature identity (leaf ids) is carried across runs by Greene member-overlap matching (D5):
    `previous` defaults to `load(repo)` (the committed `.sgt/tree/tree.json`), and every leaf that
    continues a prior feature keeps that feature's id. An `assign`-pinned leaf overrides Greene and
    keeps its pinned feature id verbatim (D3). Internal (subsystem) nodes carry build-local `N*`
    ids -- they are structural groupings, re-derived each run, not identity-bearing.

    `force_rebuild=True` (the `sgt map --rebuild` escape hatch) skips dirty-subtree splicing
    entirely and re-clusters every alive symbol from scratch, regardless of `previous` -- feature
    identity across the rebuild is still carried by the ordinary Greene matching below.

    `refresh_caches=True` (only `sgt.lens.map.build_map` passes this) lets this build refresh the
    head-keyed structural-edge cache. Default `False` because `land`/`reconcile` may build a
    candidate tree it discards -- that cache is not git-tracked, so a write here would survive a
    rolled-back land attempt (R7's "no trace" guarantee), even though the cache is itself always
    content-safe (see `cluster._structural_edges_at`).

    `head` (default `gb.head()`) selects the commit the structural signal is read at, threaded to
    `fused_graph`/`cluster.signals`. Production leaves it None (reads HEAD); a historical replay
    (`experiments/patch_clustering`) passes the commit it is reconstructing so the structural edges
    match that point in time.

    `stability_alpha` (default `cluster.STABILITY_ALPHA`) sets the Phase B temporal-prior strength:
    every re-cluster of a subtree that has a `previous` cut augments its induced graph with anchors
    tying each previous leaf's surviving members together (see `_split_once`), so the new cut
    resists gratuitously shattering a previous leaf. `force_rebuild` forces alpha=0 (the `--rebuild`
    escape hatch stays cold, reflecting only current evidence); the α sweep passes it explicitly."""
    if pins is None:
        pins = load_pins(repo)
    if previous is None:
        previous = load(repo)
    alpha = 0.0 if force_rebuild else (
        cluster.STABILITY_ALPHA if stability_alpha is None else stability_alpha
    )
    prior_leaf_of = (
        leaf_member_index(previous["nodes"]) if (previous and previous.get("nodes")) else None
    )

    all_nodes, fused = fused_graph(repo, ops, ideal, refresh_structural_cache=refresh_caches, head=head)
    real_adj = _adjacency(fused)  # per-real-member weights: cannot-link's reassignment choice, and
    # `_dirty_subdivide`'s new-member-attachment choice below.

    # A signal-recipe change (cluster.SIGNALS_VERSION) triggers one full re-optimization rather than
    # a splice: dirty-subtree splicing carries a previous leaf through verbatim on unchanged
    # membership, so it can't tell that a leaf should now split *internally* under the new signals.
    # This is *prior-guided* (α as normal, Phase C/§8), not cold -- the temporal prior anchors the
    # re-cluster to the previous shape wherever the new evidence still supports it, retiring the old
    # "signal bump ⇒ id-safe but shape-amnesiac tree" behavior. Greene identity below still carries
    # feature ids across the rebuild wherever members overlap. `force_rebuild` alone stays cold (α=0).
    stale_signals = previous is not None and previous.get("signals_version") != cluster.SIGNALS_VERSION
    if force_rebuild or stale_signals:
        root = _resplit_real(all_nodes, fused, pins, 0, max_depth, prior_leaf_of=prior_leaf_of, alpha=alpha)
    else:
        root = _build_root(repo, all_nodes, fused, real_adj, pins, previous, max_depth,
                           prior_leaf_of=prior_leaf_of, alpha=alpha)
    _regroup_flat_root(root)  # a navigable package hierarchy over the flat Leiden root; idempotent
    # (a no-op once children have distinct package dirs), so a splice that inherits an already-
    # regrouped structure isn't re-nested.

    nodes: dict[str, dict] = {}
    counter = [0]
    root_id = _register(nodes, root, None, counter)
    roots = [root_id]
    _prune_empty_leaves(nodes, roots)  # a member-less leaf is not a feature -- drop it before any
    # feature id is minted for it, so the phantom never reaches identity/op-assignment or the tree.

    cannot_link_moves = enforce_cannot_link(nodes, pins, real_adj)

    op_leaf = assign_ops_to_leaves(nodes, ops)

    result = {
        "nodes": nodes, "roots": roots, "op_leaf": op_leaf, "max_depth": max_depth,
        "cannot_link_moves": cannot_link_moves, "signals_version": cluster.SIGNALS_VERSION,
    }

    old_leaves = _leaf_members(previous["nodes"]) if previous else {}
    # A continuation always carries its old feature id, so a curated feature (a pin-referenced id or
    # an authored `af-` id, U6/R3/KTD3) keeps its id across a rebuild without any protection list;
    # `assign` values are force-applied below regardless.
    id_map, events = match_identities(
        old_leaves, _leaf_members(nodes), founding=_founding_ops(op_leaf),
    )
    _apply_id_map(result, id_map)
    _apply_assign_pins(result, pins)
    result["identity_events"] = events
    result["_fused"] = fused  # not serialized -- `save()` pops this to write the fused-snapshot
    # cache, so a speculative `build()` that's never `save()`d (land/reconcile evaluating a
    # candidate it may discard) writes no local state at all.
    return result


def _build_root(
    repo: Path, all_nodes: list[str], fused: dict, real_adj: dict, pins: Pins,
    previous: dict | None, max_depth: int,
    prior_leaf_of: dict[str, str] | None = None, alpha: float = 0.0,
) -> dict:
    """Decide whether this build can splice unchanged subtrees of `previous` through verbatim
    (Phase 2), or must fall back to a full from-scratch resplit. Dirty-subtree splicing is only
    sound when every must-link group's currently-alive members already sit in exactly one previous
    leaf -- otherwise a group could be split across two independently-resplit subtrees, since
    `_dirty_subdivide`/`_resplit_real` only contract must-link *within* whatever subtree they're
    handed. A pin edit (rare, manual curation) or a first-ever build simply falls back to a full
    resplit; the common "small code edit" case is unaffected. `prior_leaf_of`/`alpha` carry the
    Phase B temporal prior into every re-cluster below."""
    if previous is None or not previous.get("nodes"):
        return _resplit_real(all_nodes, fused, pins, 0, max_depth, prior_leaf_of=prior_leaf_of, alpha=alpha)

    previous_nodes = previous["nodes"]
    old_leaf_of = leaf_member_index(previous_nodes)
    alive_now = set(all_nodes)
    groups = _must_link_groups(pins)
    for grp in groups.values():
        alive_members = grp & alive_now
        if not alive_members:
            continue
        if not all(m in old_leaf_of for m in alive_members):
            return _resplit_real(all_nodes, fused, pins, 0, max_depth,  # a pinned member is new
                                 prior_leaf_of=prior_leaf_of, alpha=alpha)
        if len({old_leaf_of[m] for m in alive_members}) > 1:
            return _resplit_real(all_nodes, fused, pins, 0, max_depth,  # pins now span 2+ leaves
                                 prior_leaf_of=prior_leaf_of, alpha=alpha)

    fingerprint = _tree_fingerprint(previous_nodes)
    old_fused = _load_fused_snapshot(repo, fingerprint)
    dirty_leaves = (
        _cross_edge_dirty_leaves(old_leaf_of, old_fused, fused)
        | _internal_dirty_leaves(old_leaf_of, old_fused, fused)  # Phase C: intra-leaf delta trigger
        if old_fused is not None else set()
    )
    prev_root_id = previous["roots"][0] if previous.get("roots") else None
    return _dirty_subdivide(
        all_nodes, real_adj, 0, max_depth, previous_nodes, prev_root_id, dirty_leaves, pins,
        prior_leaf_of=prior_leaf_of, alpha=alpha,
    )


# --- feature identity across runs (Greene member-overlap matching, plan D5) -------------------


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _leaf_members(nodes: dict) -> dict[str, frozenset[str]]:
    return {nid: frozenset(nd["members"]) for nid, nd in nodes.items() if not nd["children"]}


def _founding_ops(op_leaf: dict[str, str]) -> dict[str, str]:
    """Each leaf's *founding op*: the lexicographically-smallest (content-addressed) op id assigned
    to it. The seed for a birth/split's content-addressed feature id (`_content_birth_id`) -- a pure
    function of which ops land in the leaf, so replica-independent given a shared op store."""
    founding: dict[str, str] = {}
    for op_id, leaf in op_leaf.items():
        cur = founding.get(leaf)
        if cur is None or op_id < cur:
            founding[leaf] = op_id
    return founding


def _content_birth_id(members: frozenset[str], founding: str | None, used: set[str]) -> str:
    """A content-derived, replica-independent feature id for a birth/split leaf
    (U21/D6): ``f-<min founding op id>`` -- the founding op is the lexicographically-smallest
    (content-addressed) op id assigned to the leaf, so two replicas that cluster the same members
    over the same (LAW-0 byte-identical) op store mint the identical id with no coordination. A leaf
    with no plurality-assigned op (rare) derives from its member set instead, still content-
    addressed. On the pathological collision with an already-used id (a modern id carried by a
    concurrent continuation of a since-reassigned op) it falls back to the member hash, so the tree
    never aliases two distinct leaves under one id."""
    if founding is not None:
        candidate = f"f-{founding}"
        if candidate not in used:
            return candidate
    digest = hashlib.sha256("\x00".join(sorted(members)).encode("utf-8")).hexdigest()
    candidate = f"f-m{digest}"
    n = 0
    while candidate in used:
        n += 1
        candidate = f"f-m{digest}-{n}"
    return candidate


def match_identities(
    old: dict[str, frozenset[str]], new: dict[str, frozenset[str]], theta: float = THETA,
    founding: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[dict]]:
    """Greene member-overlap matching between the previous run's leaves and this run's. `old`/`new`
    map a leaf id to its member set; `old` uses stable feature ids, `new` uses build-local ids.
    `founding` (optional) maps each *new* build-leaf id to its founding op id (the min op assigned
    to it) -- births/splits mint content-addressed ``f-<founding>`` ids (replica-independent,
    U21/D6); when a new leaf has no founding op, minting falls back to a member-set hash, still
    content-addressed.

    Returns ``(id_map, events)``: `id_map` maps each *new* build-leaf id to the feature id it should
    adopt; `events` is a sorted list of ``{"event", "feature_id", ...}`` facts naming what happened.

    Matching is mutual-best over Jaccard >= `theta` (tie-break: higher overlap, then smaller id):
    a new leaf whose best old is mutual is a **continuation** (>1 old pointing at it => **merge**);
    a new leaf matching an old that prefers a different new is a **split**; an unmatched new is a
    **birth**; an old that nothing continues/merges is a **death**. A continuation/merge always
    carries the old id (stability -- a curated feature keeps its id as it evolves)."""
    pairs = [
        (oid, nid, j)
        for oid, om in old.items()
        for nid, nm in new.items()
        for j in (_jaccard(om, nm),)
        if j >= theta
    ]
    # Bucket `pairs` once by old-id and by new-id, rather than re-scanning all of `pairs` per id to
    # find each id's best match -- that made this O(|pairs|·(|old|+|new|)), quadratic on a large
    # recluster. Each (oid, nid) is unique in `pairs`, so the (-j, id) tie-break is total within a
    # bucket; appending in `pairs` order reproduces the old per-id comprehension's order exactly.
    pairs_by_old: dict[str, list[tuple[str, float]]] = defaultdict(list)
    pairs_by_new: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for oid, nid, j in pairs:
        pairs_by_old[oid].append((nid, j))
        pairs_by_new[nid].append((oid, j))

    def _best(cands: list[tuple[str, float]]) -> str | None:
        return min(cands, key=lambda t: (-t[1], t[0]))[0] if cands else None

    old_best = {oid: _best(pairs_by_old.get(oid, [])) for oid in old}
    new_best = {nid: _best(pairs_by_new.get(nid, [])) for nid in new}

    used: set[str] = set(old)  # never mint an id that collides with a carried old id

    def _mint(nid: str) -> str:
        founding_op = founding.get(nid) if founding is not None else None
        fid = _content_birth_id(new[nid], founding_op, used)
        used.add(fid)
        return fid

    id_map: dict[str, str] = {}
    events: list[dict] = []
    continued_old: set[str] = set()
    for nid in sorted(new):
        olds_here = sorted(o for o, bn in old_best.items() if bn == nid)
        nb = new_best.get(nid)
        if nb is not None and nb in olds_here:  # mutual best -> continuation / merge
            fid = nb  # carry the id -- a curated feature keeps its id as it evolves
            used.add(fid)
            id_map[nid] = fid
            continued_old.update(olds_here)
            if len(olds_here) > 1:
                events.append({"event": "merge", "feature_id": fid, "merged_from": olds_here})
            else:
                events.append({"event": "continuation", "feature_id": fid})
        elif nb is not None:  # matched an old that prefers another new -> split off it
            fid = _mint(nid)
            id_map[nid] = fid
            events.append({"event": "split", "feature_id": fid, "parent": nb})
        else:
            fid = _mint(nid)
            id_map[nid] = fid
            events.append({"event": "birth", "feature_id": fid})

    for oid in sorted(old):
        if oid not in continued_old:
            events.append({"event": "death", "feature_id": oid})

    events.sort(key=lambda e: (e["event"], e["feature_id"]))
    return id_map, events


def _apply_id_map(result: dict, id_map: dict[str, str]) -> None:
    """Rename leaf node ids per `id_map`, in place, across `nodes` keys, every `parent`/`children`
    pointer, `roots`, and `op_leaf`. `id_map` only ever covers leaf ids, so internal `N*` ids and
    the fresh `F*` ids never collide."""
    nodes = result["nodes"]
    renamed: dict[str, dict] = {}
    for nid, nd in nodes.items():
        rid = id_map.get(nid, nid)
        nd["id"] = rid
        nd["children"] = [id_map.get(c, c) for c in nd["children"]]
        if nd["parent"] is not None:
            nd["parent"] = id_map.get(nd["parent"], nd["parent"])
        renamed[rid] = nd
    result["nodes"] = renamed
    result["roots"] = [id_map.get(r, r) for r in result["roots"]]
    result["op_leaf"] = {op: id_map.get(leaf, leaf) for op, leaf in result["op_leaf"].items()}


def _apply_assign_pins(result: dict, pins: Pins) -> None:
    """Override the feature id of the leaf holding an `assign`-pinned member with its pinned id --
    the deterministic guarantee behind "a pinned op never leaves its assigned feature" (D3).

    Must-link contraction *normally* keeps every member of one assign target in a single leaf, so
    the override is a plain rename. But a target orphaned in the previous tree is spliced verbatim
    rather than reclustered, so its members can scatter across several current leaves. Renaming
    *every* such leaf to the pinned id would alias them onto one node (duplicate children -> `_dedup`
    crash), so -- exactly as `_authored_leaf_claims` does -- each pinned id resolves to the one leaf
    holding the plurality of its live members (tie -> smallest leaf id), and each leaf is claimed by
    at most the strongest pin. A genuine conflict is a pin contradiction (`pins.find_contradictions`),
    not resolved here."""
    if not pins.assign:
        return
    member_leaf = leaf_member_index(result["nodes"])
    by_fid: dict[str, Counter] = defaultdict(Counter)
    for member, fid in pins.assign.items():
        if (leaf := member_leaf.get(member)) is not None:
            by_fid[fid][leaf] += 1
    amap: dict[str, str] = {}
    strength: dict[str, int] = {}
    for fid in sorted(by_fid):
        counts = by_fid[fid]
        leaf = min(counts, key=lambda l: (-counts[l], l))
        if counts[leaf] > strength.get(leaf, 0):
            amap[leaf] = fid
            strength[leaf] = counts[leaf]
    # Drop self-renames (a leaf already carrying its winning pin's id) only AFTER the winner is
    # chosen, so each leaf's winner is a pure function of (counts, sorted fid) -- independent of the
    # leaf's *current* id. Folding `leaf != fid` into the selection instead let a pin self-skip
    # whenever its id happened to equal the current leaf, handing that leaf to a weaker pin; since
    # the prior rebuild had renamed the leaf to some pin's id, a different pin self-skipped each
    # pass -> a deterministic 2-cycle (the `af-` id oscillation). Choosing first, filtering second,
    # converges to a fixpoint: the strongest (tie -> smallest) pin always wins and holds.
    amap = {leaf: fid for leaf, fid in amap.items() if leaf != fid}
    if amap:
        _apply_id_map(result, amap)


def _authored_leaf_claims(nodes: dict, authored: dict) -> dict:
    """Each leaf an authored feature claims -> the claiming `AuthoredFeature` (U6/R3, KTD4). An
    authored feature is a per-symbol user claim that *overrides* the clustered leaf, mirroring the
    assign-pin override (`_apply_assign_pins`): it resolves to the one leaf holding the plurality of
    its live members (tie -> smallest leaf id), so a leaf a user has named shows that feature's
    label/id and the clustering keeps only the leaves no one authored. When two features contend for
    one leaf, the stronger claim (more members there) wins deterministically. `authored` maps
    `af-id -> AuthoredFeature`; empty when the repo has none, in which case this is a no-op."""
    if not authored:
        return {}
    member_leaf = leaf_member_index(nodes)
    claims: dict[str, object] = {}
    strength: dict[str, int] = {}
    for fid in sorted(authored):
        feat = authored[fid]
        counts = Counter(l for m in feat.live_members() if (l := member_leaf.get(m)) is not None)
        if not counts:
            continue
        leaf = min(counts, key=lambda l: (-counts[l], l))
        if counts[leaf] > strength.get(leaf, 0):
            claims[leaf] = feat
            strength[leaf] = counts[leaf]
    return claims


# --- persistence (.sgt/tree/tree.json, committed -- plan D5) -----------------------------------


def load(repo: str | Path) -> dict | None:
    """The last committed tree (`.sgt/tree/tree.json`), or None on first run. Feeds `build`'s
    Greene matching as the `previous` run."""
    return state.load_json(repo, "tree")


def save(repo: str | Path, result: dict, *, refresh_fused_snapshot: bool = False) -> None:
    """Persist the built tree so the next run's Greene matching can preserve feature ids. Skips
    the write when byte-identical to what's already on disk (see `state.save_json_if_changed`).

    `refresh_fused_snapshot=True` also persists `build()`'s `_fused` payload (Phase 2's dirty-
    subtree cache, `.sgt/local/fused_snapshot.json`). Default `False` because, unlike `tree.json`,
    that cache is not git-tracked, so `land`'s CAS-retry rollback (`GitBinding.restore_worktree_to`)
    cannot undo a write to it -- `save` is called on every `land`/`reconcile` attempt *before* the
    CAS resolves win or lose, so refreshing the cache there would leak local state across a rolled-
    back attempt (the R7 "no trace" guarantee). Only `build_map` (the actual `sgt map` command,
    which has no such retry loop) passes `True` -- see `sgt.lens.map.build_map`."""
    fused = result.pop("_fused", None)
    state.save_json_if_changed(repo, "tree", result)
    if refresh_fused_snapshot and fused is not None:
        _save_fused_snapshot(repo, _tree_fingerprint(result["nodes"]), fused)


# --- labeling + DEDUP (plan R15/R17, promoted from the experiment's hierarchy.py) --------------


def _post_order(nodes: dict, nid: str) -> list[str]:
    out: list[str] = []
    for c in nodes[nid]["children"]:
        out += _post_order(nodes, c)
    out.append(nid)
    return out


def _dedup(nodes: dict, roots: list[str]) -> dict[str, str]:
    """DEDUP (plan R15): merge same-label sibling leaves -- a shared label means the split invented
    a distinction the labeler couldn't name -- then disambiguate any leftover cross-subsystem label
    collision by folder so no two leaves share a label. Mutates `nodes` in place and returns a
    ``{removed_leaf_id -> surviving_leaf_id}`` remap the caller applies to `op_leaf`."""
    remap: dict[str, str] = {}
    for rid in roots:
        for nid in _post_order(nodes, rid):
            nd = nodes[nid]
            if len(nd["children"]) < 2:
                continue
            # Only sibling *leaves* are merged: a shared label there means the split invented a
            # distinction the labeler couldn't name. Internal (subsystem) nodes are never merged --
            # flattening one to a leaf would orphan its whole subtree -- so a label collision among
            # internal siblings is left to the folder-suffix pass below.
            leaves_by_label: dict[str, list[str]] = defaultdict(list)
            for c in nd["children"]:
                if not nodes[c]["children"]:
                    leaves_by_label[nodes[c]["label"]].append(c)
            new_children: list[str] = []
            for c in nd["children"]:
                if c not in nodes:
                    continue  # a non-first same-label leaf already merged away (deleted) below
                if nodes[c]["children"]:
                    new_children.append(c)  # internal node: keep as-is
                    continue
                dupes = leaves_by_label[nodes[c]["label"]]
                if c != dupes[0]:
                    continue  # a non-first leaf of a same-label group -> merged into dupes[0] below
                if len(dupes) == 1:
                    new_children.append(c)
                    continue
                members = sorted({m for k in dupes for m in nodes[k]["members"]})
                for k in dupes[1:]:
                    remap[k] = c
                    del nodes[k]
                nodes[c] = {
                    "id": c, "parent": nid, "depth": nodes[c]["depth"],
                    "members": members, "size": len(members), "dir": _dominant_dir(members),
                    "children": [], "label": nodes[c]["label"], "why": nodes[c]["why"],
                    "split_reason": nodes[c].get("split_reason"),
                }
                new_children.append(c)
            nd["children"] = new_children

    leaves = [nid for nid, nd in nodes.items() if not nd["children"]]
    by_label: dict[str, list[str]] = defaultdict(list)
    for nid in leaves:
        by_label[nodes[nid]["label"]].append(nid)
    for label, ids in by_label.items():
        if len(ids) < 2:
            continue
        seen: dict[str, int] = defaultdict(int)
        for nid in sorted(ids):
            tail = nodes[nid]["dir"].split("/")[-1]
            seen[tail] += 1
            suffix = tail if seen[tail] == 1 else f"{tail} {seen[tail]}"
            nodes[nid]["label"] = f"{label} · {suffix}"

    for k in list(remap):  # resolve any chained merges to the final survivor
        v = remap[k]
        while v in remap:
            v = remap[v]
        remap[k] = v
    return remap


def label_tree(
    result: dict, repo: str | Path = ".", labeler=None,
    subjects_by_leaf: dict[str, list[str]] | None = None, pins: Pins | None = None,
    kinds_by_leaf: dict[str, str] | None = None, weights: dict[str, float] | None = None,
    relabel: bool = False, subject_counts_by_leaf: dict[str, dict[str, int]] | None = None,
) -> object:
    """Label every node bottom-up (leaves from members, a single-child node reuses its child's
    label, an internal node from its children's labels), then DEDUP. Mutates `result` in place:
    adds `label`/`why` to every node, collapses over-split siblings, and remaps `op_leaf` for any
    leaf a merge removed. Returns the `Labeler` (for `cost_line()` / `save()`).

    Labeling is intentionally separate from `build` so the tree exists deterministically offline;
    the labeler carries its own cache and deterministic fallback (`sgt.lens.label`). Leaf labels
    are cached by feature id with graded, generation-anchored reuse (`weights` = op-touch counts
    used for the weighted-Jaccard drift budget; None ⇒ unit weights), so a small membership change
    keeps the name instead of forcing a fresh LLM call (§3.2).

    Runs level-by-level bottom-up (a node is only labeled once every child already is), but *all*
    nodes ready in the same wave -- across every root, leaves and multi-child subsystems alike --
    are named in one batched, concurrent `Labeler.label_many` call (Phase 3): far fewer serial
    network round-trips than one call per node. A single-child node just inherits its child's
    label/rationale, no call needed.

    After DEDUP, any leaf whose feature id has a user-pinned label (`pins.labels`, U13's
    `rename` verb) has that label substituted verbatim -- a user rename always wins over the
    LLM/fallback label, and survives every future re-cluster as long as the id persists."""
    from sgt.lens.label import Labeler, subject_label

    if labeler is None:
        # `relabel` is `--rebuild`'s "name everything again": it bypasses both the cached LLM label
        # and the fallback backoff, so a user who fixed their credential has an immediate way to
        # re-earn real names without waiting out a retry window.
        labeler = Labeler(repo, relabel=relabel)
    if pins is None:
        pins = load_pins(repo)
    nodes = result["nodes"]
    subjects_by_leaf = subjects_by_leaf or {}
    kinds_by_leaf = kinds_by_leaf or {}

    remaining: set[str] = set()
    for rid in result["roots"]:
        remaining.update(_post_order(nodes, rid))

    while remaining:
        ready = [nid for nid in remaining if not (set(nodes[nid]["children"]) & remaining)]
        batch: list[tuple[str, tuple[str, str, list[str]]]] = []  # (nid, (key, prompt, members))
        for nid in ready:
            nd = nodes[nid]
            if not nd["children"]:
                # Prefer the developer's own words. When one commit subject carries most of this
                # leaf's mass, that subject IS the feature's name -- no LLM call, no paraphrase of
                # something they already wrote, and nothing on this path that can be slow or
                # non-reproducible. Clusters spanning several episodes fall through to the labeler,
                # which is the case a synthesized name is actually for.
                own_words = subject_label(
                    subjects_by_leaf.get(nid) or [],
                    (subject_counts_by_leaf or {}).get(nid),
                )
                if own_words is not None:
                    nd["label"], nd["why"] = own_words.label, own_words.rationale
                    continue
                batch.append((nid, labeler.leaf_request(
                    nid, nd["members"], weights,
                    subjects_by_leaf.get(nid), kinds_by_leaf.get(nid))))
            elif len(nd["children"]) == 1:
                only = nodes[nd["children"][0]]
                nd["label"], nd["why"] = only["label"], only["why"]
            else:
                kid_labels = [nodes[c]["label"] for c in nd["children"]]
                files = sorted({m.split("::", 1)[0] for m in nd["members"]})[:8]
                batch.append((nid, labeler.super_request(kid_labels, files)))

        if batch:
            outs = labeler.label_many([entry for _nid, entry in batch])
            for (nid, _entry), fl in zip(batch, outs):
                nodes[nid]["label"], nodes[nid]["why"] = fl.label, fl.rationale

        remaining -= set(ready)

    remap = _dedup(nodes, result["roots"])
    if remap:
        result["op_leaf"] = {op: remap.get(leaf, leaf) for op, leaf in result["op_leaf"].items()}

    for nid, label in pins.labels.items():
        node = nodes.get(nid)
        if node is not None:
            node["label"] = label

    # Authored features (U6/R3) are the authority over the clustered proposal (KTD4): a leaf a user
    # has claimed under a *deliberately named* feature shows that feature's label, exactly as the
    # pin-label override above substitutes a rename -- un-authored leaves keep their LLM/fallback
    # label untouched. The label override fires only for a non-empty label: the save-time new-lane
    # cascade (`ledger.assign_at_save`) seeds an authored feature with an *empty* label register on
    # purpose, so its clustered/LLM label stands here until a real `rename` names it -- an empty
    # register is "claimed but unnamed", not a rename to blank.
    from sgt.lens.authored import load_authored
    for leaf, feat in _authored_leaf_claims(nodes, load_authored(repo)).items():
        if feat.label:
            nodes[leaf]["label"] = feat.label

    return labeler
