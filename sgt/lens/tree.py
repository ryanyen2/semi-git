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

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from sgt.core.op import Op
from sgt.lens import cluster
from sgt.lens.cluster import _dominant_dir, _fuse, _leiden
from sgt.lens.pins import Pins, _expand_members, apply_must_link, enforce_cannot_link, load_pins
from sgt.store.gitbind import GitBinding

MIN_LANE = 4        # a node must own >= this many symbols to stand alone (else folded into a sibling)
MAX_LEAF = 24        # a node this small is coherent enough to stay a leaf -- stop splitting
MAX_DEPTH = 4        # hard cap: levels 0..3
TARGET_ARITY = (5, 9)  # desired child count per split (plan D2)
GAMMA_LO = 1e-4
GAMMA_HI = 1.0
MAX_SEARCH_ITER = 20
THETA = 0.5         # Greene member-overlap threshold for feature identity across runs (plan D5)


def _induced(fused: dict, member_set: set[str]) -> dict:
    return {pair: w for pair, w in fused.items() if all(x in member_set for x in pair)}


def _adjacency(fused: dict) -> dict[str, list[tuple[str, float]]]:
    adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for pair, w in fused.items():
        a, b = tuple(pair)
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
    members: list[str], fused: dict, adj: dict, min_lane: int = MIN_LANE,
    target: tuple[int, int] = TARGET_ARITY, lo: float = GAMMA_LO, hi: float = GAMMA_HI,
    max_iter: int = MAX_SEARCH_ITER,
) -> SplitResult:
    """Binary-search the CPM resolution (log-scale) for a gamma whose partition has between
    `target[0]` and `target[1]` groups of size >= `min_lane` (sub-MIN groups are folded in via
    NO-ORPHAN either way). Too few groups means the split is too coarse -- search finer (higher
    gamma); too many means too fine -- search coarser (lower gamma). Keeps the closest-to-target
    result seen across the search as a fallback when no gamma in range lands exactly in range."""
    induced = _induced(fused, set(members))
    lo_log, hi_log = math.log(lo), math.log(hi)
    best_big: list[list[str]] | None = None
    best_small: list[list[str]] = []
    best_gap = None

    for _ in range(max_iter):
        mid_log = (lo_log + hi_log) / 2
        gamma = math.exp(mid_log)
        parts = _leiden(sorted(members), induced, gamma)
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
    members: list[str], fused: dict, adj: dict, depth: int, max_depth: int,
    min_lane: int = MIN_LANE, max_leaf: int = MAX_LEAF,
) -> dict:
    """Recursively split `members` into a node tree. A node with empty `children` is a leaf."""
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

    result = _split_once(members, fused, adj, min_lane=min_lane)
    if result.groups is None:
        node["split_reason"] = result.reason
        return node

    node["children"] = [
        _subdivide(sorted(g), fused, adj, depth + 1, max_depth, min_lane, max_leaf)
        for g in result.groups
    ]
    node["split_reason"] = result.reason
    return node


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


def _leaf_ids(nodes: dict, nid: str) -> list[str]:
    nd = nodes[nid]
    if not nd["children"]:
        return [nid]
    out: list[str] = []
    for c in nd["children"]:
        out += _leaf_ids(nodes, c)
    return out


def _leaf_member_index(nodes: dict) -> dict[str, str]:
    return {m: nid for nid, nd in nodes.items() if not nd["children"] for m in nd["members"]}


def assign_ops_to_leaves(nodes: dict, ops: list[Op]) -> dict[str, str]:
    """Every op -> the leaf its footprint's symbols plurality-vote for (tie-break: smallest leaf
    id, for determinism, not numeric order). An op whose footprint touches no leaf-assigned
    symbol (fully dead, or off-chain) gets no entry -- this is the hook U13's blame (`sym ->
    max-op-in-I -> feature`) and feature verbs (`merge` unions "op-sets") consume."""
    member_leaf = _leaf_member_index(nodes)
    op_leaf: dict[str, str] = {}
    for op in ops:
        votes = Counter(member_leaf[sym] for sym in op.footprint if sym in member_leaf)
        if not votes:
            continue
        top_count = votes.most_common(1)[0][1]
        winner = min(leaf for leaf, count in votes.items() if count == top_count)
        op_leaf[op.id] = winner
    return op_leaf


def fused_graph(repo: Path, ops: list[Op], ideal) -> tuple[list[str], dict[frozenset, float]]:
    """The fused (structural ⊕ co-change ⊕ scope) coupling graph over every alive symbol --
    shared by `build` (the full recursive tree) and `sgt.lens.verbs.plan_split` (a one-off split
    of a single feature's induced subgraph), so both start from the identical signal."""
    gb = GitBinding(repo)
    nodes_set, hubs, cochange, structural = cluster.signals(repo, ops, ideal)
    subjects = {sha: subject for sha, _parent, subject in gb.history()}
    scope = cluster.scope_edges(ops, subjects, nodes_set, hubs)
    structural = cluster.hub_normalize(structural)
    fused = _fuse(structural, cochange, scope)
    return sorted(nodes_set), fused


def build(
    repo: Path, ops: list[Op], ideal, max_depth: int = MAX_DEPTH, pins: Pins | None = None,
    previous: dict | None = None,
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
    ids -- they are structural groupings, re-derived each run, not identity-bearing."""
    if pins is None:
        pins = load_pins(repo)
    if previous is None:
        previous = load(repo)

    all_nodes, fused = fused_graph(repo, ops, ideal)

    contracted_nodes, contracted_fused, expansion = apply_must_link(all_nodes, fused, pins)
    contracted_adj = _adjacency(contracted_fused)
    root = _subdivide(contracted_nodes, contracted_fused, contracted_adj, 0, max_depth)
    _expand_members(root, expansion)

    nodes: dict[str, dict] = {}
    counter = [0]
    root_id = _register(nodes, root, None, counter)
    roots = [root_id]

    real_adj = _adjacency(fused)  # per-real-member weights, for cannot-link's reassignment choice
    cannot_link_moves = enforce_cannot_link(nodes, pins, real_adj)

    op_leaf = assign_ops_to_leaves(nodes, ops)

    result = {
        "nodes": nodes, "roots": roots, "op_leaf": op_leaf, "max_depth": max_depth,
        "cannot_link_moves": cannot_link_moves,
    }

    old_leaves = _leaf_members(previous["nodes"]) if previous else {}
    id_map, events = match_identities(old_leaves, _leaf_members(nodes))
    _apply_id_map(result, id_map)
    _apply_assign_pins(result, pins)
    result["identity_events"] = events
    return result


# --- feature identity across runs (Greene member-overlap matching, plan D5) -------------------


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _leaf_members(nodes: dict) -> dict[str, frozenset[str]]:
    return {nid: frozenset(nd["members"]) for nid, nd in nodes.items() if not nd["children"]}


def _fresh_id_gen(old_ids: set[str]):
    """Monotonic ``F<n>`` ids that never collide with an existing feature id -- start past the
    largest ``F<n>`` already in `old_ids` so a birth never reuses a dead feature's id."""
    used = [int(x[1:]) for x in old_ids if x.startswith("F") and x[1:].isdigit()]
    n = max(used) + 1 if used else 0
    while True:
        yield f"F{n}"
        n += 1


def match_identities(
    old: dict[str, frozenset[str]], new: dict[str, frozenset[str]], theta: float = THETA,
) -> tuple[dict[str, str], list[dict]]:
    """Greene member-overlap matching between the previous run's leaves and this run's. `old`/`new`
    map a leaf id to its member set; `old` uses stable feature ids, `new` uses build-local ids.

    Returns ``(id_map, events)``: `id_map` maps each *new* build-leaf id to the stable feature id
    it should adopt (continuation/merge keep the matched feature id; birth/split mint a fresh one);
    `events` is a sorted list of ``{"event", "feature_id", ...}`` facts naming what happened.

    Matching is mutual-best over Jaccard >= `theta` (tie-break: higher overlap, then smaller id):
    a new leaf whose best old is mutual is a **continuation** (>1 old pointing at it => **merge**);
    a new leaf matching an old that prefers a different new is a **split**; an unmatched new is a
    **birth**; an old that nothing continues/merges is a **death**."""
    pairs = [
        (oid, nid, _jaccard(om, nm))
        for oid, om in old.items()
        for nid, nm in new.items()
        if _jaccard(om, nm) >= theta
    ]

    def _best(cands: list[tuple[str, float]]) -> str | None:
        return min(cands, key=lambda t: (-t[1], t[0]))[0] if cands else None

    old_best = {oid: _best([(nid, j) for (o, nid, j) in pairs if o == oid]) for oid in old}
    new_best = {nid: _best([(oid, j) for (oid, n, j) in pairs if n == nid]) for nid in new}

    gen = _fresh_id_gen(set(old))
    id_map: dict[str, str] = {}
    events: list[dict] = []
    for nid in sorted(new):
        olds_here = sorted(o for o, bn in old_best.items() if bn == nid)
        nb = new_best.get(nid)
        if nb is not None and nb in olds_here:  # mutual best -> continuation (adopt old feature id)
            id_map[nid] = nb
            if len(olds_here) > 1:
                events.append({"event": "merge", "feature_id": nb, "merged_from": olds_here})
            else:
                events.append({"event": "continuation", "feature_id": nb})
        elif nb is not None:  # matched an old that prefers another new -> split off it
            fid = next(gen)
            id_map[nid] = fid
            events.append({"event": "split", "feature_id": fid, "parent": nb})
        else:
            fid = next(gen)
            id_map[nid] = fid
            events.append({"event": "birth", "feature_id": fid})

    consumed = set(id_map.values())
    for e in events:
        if e["event"] == "merge":
            consumed.update(e["merged_from"])
    for oid in sorted(old):
        if oid not in consumed:
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
    """Override the feature id of any leaf holding an `assign`-pinned member with its pinned id --
    the deterministic guarantee behind "a pinned op never leaves its assigned feature" (D3).
    Must-link contraction already guarantees one leaf per assign target, so this is a plain
    rename; a genuine conflict is a pin contradiction (`pins.find_contradictions`), not resolved
    here."""
    if not pins.assign:
        return
    member_leaf = {m: nid for nid, nd in result["nodes"].items() if not nd["children"] for m in nd["members"]}
    amap = {
        leaf: fid
        for member, fid in pins.assign.items()
        if (leaf := member_leaf.get(member)) is not None and leaf != fid
    }
    if amap:
        _apply_id_map(result, amap)


# --- persistence (.sgt/tree/tree.json, committed -- plan D5) -----------------------------------


def _tree_path(repo: str | Path) -> Path:
    return Path(repo) / ".sgt" / "tree" / "tree.json"


def load(repo: str | Path) -> dict | None:
    """The last committed tree (`.sgt/tree/tree.json`), or None on first run. Feeds `build`'s
    Greene matching as the `previous` run."""
    path = _tree_path(repo)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save(repo: str | Path, result: dict) -> None:
    """Persist the built tree so the next run's Greene matching can preserve feature ids."""
    path = _tree_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
            by_label: dict[str, list[str]] = defaultdict(list)
            for c in nd["children"]:
                by_label[nodes[c]["label"]].append(c)
            new_children: list[str] = []
            for label, kids in by_label.items():
                if len(kids) == 1:
                    new_children.append(kids[0])
                    continue
                keep = kids[0]
                members = sorted({m for c in kids for m in nodes[c]["members"]})
                for c in kids[1:]:
                    remap[c] = keep
                    del nodes[c]
                nodes[keep] = {
                    "id": keep, "parent": nid, "depth": nodes[keep]["depth"],
                    "members": members, "size": len(members), "dir": _dominant_dir(members),
                    "children": [], "label": label, "why": nodes[keep]["why"],
                    "split_reason": nodes[keep].get("split_reason"),
                }
                new_children.append(keep)
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
) -> object:
    """Label every node bottom-up (leaves from members, a single-child node reuses its child's
    label, an internal node from its children's labels), then DEDUP. Mutates `result` in place:
    adds `label`/`why` to every node, collapses over-split siblings, and remaps `op_leaf` for any
    leaf a merge removed. Returns the `Labeler` (for `cost_line()` / `save()`).

    Labeling is intentionally separate from `build` so the tree exists deterministically offline;
    the labeler carries its own member-hash cache and deterministic fallback (`sgt.lens.label`).

    After DEDUP, any leaf whose feature id has a user-pinned label (`pins.labels`, U13's
    `rename` verb) has that label substituted verbatim -- a user rename always wins over the
    LLM/fallback label, and survives every future re-cluster as long as the id persists."""
    from sgt.lens.label import Labeler

    if labeler is None:
        labeler = Labeler(repo)
    if pins is None:
        pins = load_pins(repo)
    nodes = result["nodes"]
    subjects_by_leaf = subjects_by_leaf or {}
    for rid in result["roots"]:
        for nid in _post_order(nodes, rid):
            nd = nodes[nid]
            if not nd["children"]:
                fl = labeler.label(nd["members"], subjects=subjects_by_leaf.get(nid))
                nd["label"], nd["why"] = fl.label, fl.rationale
            elif len(nd["children"]) == 1:
                only = nodes[nd["children"][0]]
                nd["label"], nd["why"] = only["label"], only["why"]
            else:
                kid_labels = [nodes[c]["label"] for c in nd["children"]]
                files = sorted({m.split("::", 1)[0] for m in nd["members"]})[:8]
                fl = labeler.label_super(kid_labels, files)
                nd["label"], nd["why"] = fl.label, fl.rationale

    remap = _dedup(nodes, result["roots"])
    if remap:
        result["op_leaf"] = {op: remap.get(leaf, leaf) for op, leaf in result["op_leaf"].items()}

    for nid, label in pins.labels.items():
        node = nodes.get(nid)
        if node is not None:
            node["label"] = label

    return labeler
