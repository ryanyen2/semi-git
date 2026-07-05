"""Recursive (<=4 level) subsystem -> ... -> feature-lane hierarchy so flat lanes become browsable.

Reuses the flat pipeline's machinery verbatim (``_signals`` / ``hub_normalize`` / ``scope_edges`` /
``_leiden``). Instead of the old fixed two levels it now subdivides RECURSIVELY: cluster the fused
coupling graph at a coarse resolution to get subsystems, then subdivide each subsystem's *induced*
subgraph at a finer resolution, and so on — up to ``MAX_DEPTH`` levels. Nesting is guaranteed by
construction (children only ever split within a parent's own edges). Three rules make the tree
honest rather than a mechanical over-split:

  - NO-ORPHAN: a sub-``MIN_LANE`` cluster is never dropped. It is folded into the sibling it is most
    coupled to, so every HEAD entity lands in some leaf (the old ``if len(c) >= MIN_LANE`` filter
    silently dropped these, capping coverage/retention).
  - STOP-SPLIT: a node is only split when it is big enough (> ``MAX_LEAF``) AND the split actually
    separates it into >= 2 real groups. A split that yields one dominant child + dust is refused —
    that is exactly the over-split that made the labeler emit the same label for sibling lanes.
  - DEDUP: after labeling, same-label siblings are merged (residual over-split), then any remaining
    cross-subsystem label collision is disambiguated by folder — so the operation rollups never show
    a duplicate label (e.g. "Plan Editing Verbs" ×3 before this change).

Before fusing, the structural signal is passed through ``hub_normalize`` (degree-based hub
suppression) so a god-class / universal-import bus doesn't glue unrelated features together.

Output is additive: the full tree lives in ``nodes`` (id -> node, with parent/children/depth), and a
flattened ``supers`` (top-level nodes, children = their leaf lanes) + ``lanes`` (leaves) + a per-leaf
``commit_lanes`` projection are kept so operations.py and the flat grid renderer keep working
unchanged.

    .venv/bin/python experiments/patch_clustering/hierarchy.py [max_depth]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from experiments.patch_clustering.label import Labeler  # noqa: E402
from experiments.patch_clustering.leiden_cluster import (  # noqa: E402
    _leiden, _signals, hub_normalize, scope_edges,
)

_OUT = Path(__file__).resolve().parent / "out"

MIN_LANE = 4       # a node must own >= this many entities to stand alone (else folded into a sibling)
MAX_DEPTH = 4      # hard cap: levels 0..3 (subsystem = level 0; no leaf deeper than level 3)
MAX_LEAF = 24      # stop splitting a node this small — it is coherent enough to be a leaf
# CPM resolution gets finer as we descend: coarse subsystems at the top, tight lanes at the bottom.
GAMMA_BY_DEPTH = [0.004, 0.02, 0.05, 0.1]
ESCALATE = (1, 2, 4, 8)  # multipliers tried in turn until a node breaks into >= 2 real groups


def _fuse(*dicts: dict) -> dict:
    out: dict = defaultdict(float)
    for d in dicts:
        for k, v in d.items():
            out[k] += v
    return dict(out)


def _dom_dir(members: list[str]) -> str:
    def pfx(e: str) -> str:
        p = e.split("::", 1)[0].split("/")
        return "/".join(p[:2]) if len(p) >= 2 else p[0]
    return Counter(pfx(m) for m in members).most_common(1)[0][0]


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
    """Fold each sub-MIN cluster into the big sibling it couples to most. No entity is dropped —
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


def _split_once(members: list[str], fused: dict, adj: dict, base_gamma: float) -> list[list[str]] | None:
    """Try progressively finer resolutions until the induced subgraph breaks into >= 2 groups of
    >= MIN_LANE, then fold the sub-MIN dust into those groups (no-orphan). Returns None when even
    the finest resolution can't separate it — a genuinely cohesive blob that should stay one leaf.

    Escalation is what stops the old failure mode: a lazy split at one gamma yields ``1 big + dust``
    and the ``>= 2 groups`` rule refuses it, leaving a 66-entity subsystem un-subdivided. A finer
    gamma breaks that core; a true god-facade (project.py) survives every step and stays whole."""
    induced = _induced(fused, set(members))
    for mult in ESCALATE:
        parts = _leiden(sorted(members), induced, base_gamma * mult)
        big = sorted((c for c in parts if len(c) >= MIN_LANE), key=lambda c: -len(c))
        if len(big) >= 2:
            small = [c for c in parts if len(c) < MIN_LANE]
            return _attach_orphans(big, small, adj)
    return None


def _subdivide(members: list[str], fused: dict, adj: dict, depth: int) -> dict:
    """Recursively split ``members`` into a node tree. A node with empty ``children`` is a leaf."""
    node = {"members": sorted(members), "size": len(members), "dir": _dom_dir(members), "depth": depth}
    node["children"] = []
    if depth < MAX_DEPTH - 1 and len(members) > MAX_LEAF:
        groups = _split_once(members, fused, adj, GAMMA_BY_DEPTH[depth + 1])
        if groups is not None:
            node["children"] = [_subdivide(sorted(c), fused, adj, depth + 1) for c in groups]
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


def _roll_commits(nodes: dict, roots: list[str], data: dict) -> dict[int, set[str]]:
    """Per-leaf touched-commits from change_sets, then union up to every ancestor. Returns the
    per-commit set of touched LEAF ids (the commit_lanes projection)."""
    leaves = {nid: nd for nid, nd in nodes.items() if not nd["children"]}
    ent2leaf = {e: nid for nid, nd in leaves.items() for e in nd["members"]}
    commits = data["commits"]
    change_sets = {int(k): v for k, v in data["change_sets"].items()}

    for nd in nodes.values():
        nd.pop("commits", None)
        nd.pop("subjects", None)
    commit_leaves: dict[int, set[str]] = {}
    for o in range(len(commits)):
        touched = {ent2leaf[e] for e in change_sets.get(o, []) if e in ent2leaf}
        commit_leaves[o] = touched
        for nid in touched:
            leaves[nid].setdefault("commits", []).append(o)
            leaves[nid].setdefault("subjects", []).append(commits[o]["subject"])
    # roll leaf commits up to intermediate + root nodes
    for rid in roots:
        for nid in _post_order(nodes, rid):
            nd = nodes[nid]
            if nd["children"]:
                cs = sorted({o for c in nd["children"] for o in nodes[c].get("commits", [])})
                nd["commits"] = cs
                nd["subjects"] = [commits[o]["subject"] for o in cs]
            if nd.get("commits"):
                nd["birth"], nd["last"] = nd["commits"][0], nd["commits"][-1]
    return commit_leaves


def _post_order(nodes: dict, nid: str) -> list[str]:
    out: list[str] = []
    for c in nodes[nid]["children"]:
        out += _post_order(nodes, c)
    out.append(nid)
    return out


def _label_tree(nodes: dict, roots: list[str], labeler: Labeler) -> None:
    """Label every node bottom-up: leaves from members, intermediates from their children's labels
    (a single-child node reuses its child's label rather than inventing one)."""
    for rid in roots:
        for nid in _post_order(nodes, rid):
            nd = nodes[nid]
            if not nd["children"]:
                fl = labeler.label(nd["members"], subjects=nd.get("subjects"))
                nd["label"], nd["why"] = fl.label, fl.rationale
            elif len(nd["children"]) == 1:
                only = nodes[nd["children"][0]]
                nd["label"], nd["why"] = only["label"], only["why"]
            else:
                kid_labels = [nodes[c]["label"] for c in nd["children"]]
                files = sorted({m.split("::", 1)[0] for m in nd["members"]})[:8]
                fl = labeler.label_super(kid_labels, files)
                nd["label"], nd["why"] = fl.label, fl.rationale


def _count_dup_leaf_labels(nodes: dict) -> list[tuple[str, int]]:
    lc: Counter = Counter(nd["label"] for nd in nodes.values() if not nd["children"])
    return [(k, v) for k, v in lc.most_common() if v > 1]


def _dedup(nodes: dict, roots: list[str]) -> int:
    """Merge same-label siblings (residual over-split) into one leaf, then disambiguate any leftover
    cross-subsystem label collision by folder. Returns the number of sibling-merges performed.

    Same-label siblings mean the split invented a distinction the labeler couldn't name — collapse
    them to a single leaf holding the union of members (their internal sub-structure was spurious).
    Applied bottom-up so deeper merges settle before shallower ones."""
    merges = 0
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
                # collapse the over-split siblings into their first id, as a single leaf
                keep = kids[0]
                members = sorted({m for c in kids for m in nodes[c]["members"]})
                for c in kids[1:]:
                    del nodes[c]
                nodes[keep] = {
                    "id": keep, "parent": nid, "depth": nodes[keep]["depth"],
                    "members": members, "size": len(members), "dir": _dom_dir(members),
                    "children": [], "label": label, "why": nodes[keep]["why"],
                }
                new_children.append(keep)
                merges += 1
            nd["children"] = new_children

    # disambiguate remaining cross-subsystem collisions so no two distinct leaves share a label
    leaves = [nid for nid, nd in nodes.items() if not nd["children"]]
    by_label = defaultdict(list)
    for nid in leaves:
        by_label[nodes[nid]["label"]].append(nid)
    for label, ids in by_label.items():
        if len(ids) < 2:
            continue
        seen: dict[str, int] = defaultdict(int)
        for nid in ids:
            tail = nodes[nid]["dir"].split("/")[-1]
            suffix = tail
            seen[tail] += 1
            if seen[tail] > 1:  # same folder too — fall back to a counter
                suffix = f"{tail} {seen[tail]}"
            nodes[nid]["label"] = f"{label} · {suffix}"
    return merges


def build(data: dict, repo: Path, max_depth: int = MAX_DEPTH) -> dict:
    head_entities, hubs, _cut, cochange, structural = _signals(data, repo)
    scope = scope_edges(data, head_entities, hubs)
    # Demote structural hubs before fusing — otherwise a god-class / universal-import bus fuses
    # unrelated features into one giant subsystem.
    structural = hub_normalize(structural)
    fused = _fuse(structural, cochange, scope)
    adj = _adjacency(fused)
    all_nodes = sorted(head_entities)

    global MAX_DEPTH
    MAX_DEPTH = max_depth

    # level-0 coarse partition = subsystems; fold tiny coarse clusters into a sibling (no-orphan at top)
    coarse = _leiden(all_nodes, fused, GAMMA_BY_DEPTH[0])
    big0 = sorted((c for c in coarse if len(c) >= MIN_LANE), key=lambda c: -len(c))
    small0 = [c for c in coarse if len(c) < MIN_LANE]
    roots_members = _attach_orphans(big0, small0, adj) if big0 else [all_nodes]
    tree = [_subdivide(sorted(c), fused, adj, 0) for c in roots_members]

    nodes: dict[str, dict] = {}
    counter = [0]
    roots = [_register(nodes, n, None, counter) for n in tree]

    _roll_commits(nodes, roots, data)  # leaf commits + subjects, for the labeler hint
    labeler = Labeler()
    _label_tree(nodes, roots, labeler)

    dups_before = _count_dup_leaf_labels(nodes)
    merges = _dedup(nodes, roots)
    dups_after = _count_dup_leaf_labels(nodes)
    labeler.save()

    commit_leaves = _roll_commits(nodes, roots, data)  # recompute after dedup changed the leaves

    # cross-leaf coupling -> "related lanes" per leaf (top fused weight to another leaf)
    leaf_of = {}
    for rid in roots:
        for lid in _leaf_ids(nodes, rid):
            leaf_of[lid] = rid
    ent2leaf = {e: nid for nid, nd in nodes.items() if not nd["children"] for e in nd["members"]}
    pair_w: dict[frozenset, float] = defaultdict(float)
    for pair, w in fused.items():
        a, b = tuple(pair)
        la_, lb_ = ent2leaf.get(a), ent2leaf.get(b)
        if la_ and lb_ and la_ != lb_:
            pair_w[frozenset((la_, lb_))] += w
    rel: dict[str, list] = defaultdict(list)
    for pair, w in pair_w.items():
        a, b = tuple(pair)
        rel[a].append((b, w))
        rel[b].append((a, w))

    # additive projections: supers (top-level, children = leaf lanes) + lanes (leaves) + commit_lanes
    supers = []
    for rid in roots:
        nd = nodes[rid]
        supers.append({
            "id": rid, "children": _leaf_ids(nodes, rid), "members": nd["members"],
            "dir": nd["dir"], "size": nd["size"], "label": nd["label"], "why": nd["why"],
            "commits": nd.get("commits", []), "birth": nd.get("birth"), "last": nd.get("last"),
        })
    lanes = {}
    for lid, nd in nodes.items():
        if nd["children"]:
            continue
        top = sorted(rel.get(lid, []), key=lambda x: -x[1])[:3]
        lanes[lid] = {
            "members": nd["members"], "dir": nd["dir"], "size": nd["size"], "super": leaf_of[lid],
            "commits": nd.get("commits", []), "subjects": nd.get("subjects", []),
            "birth": nd.get("birth"), "last": nd.get("last"),
            "related": [{"lane": x, "w": round(w, 2)} for x, w in top],
            "label": nd["label"], "why": nd["why"],
        }

    covered = sum(1 for o in range(len(data["commits"])) if commit_leaves[o])
    depths = Counter(nd["depth"] for nd in nodes.values() if not nd["children"])
    print(f"tree nodes: {len(nodes)}   subsystems: {len(supers)}   feature lanes (leaves): {len(lanes)}")
    print(f"leaf depth distribution: {dict(sorted(depths.items()))}   max depth reached: {max(depths)}")
    print(f"largest leaf: {max(l['size'] for l in lanes.values())}   "
          f"entities in leaves: {sum(l['size'] for l in lanes.values())}   coverage: {covered}/{len(data['commits'])}")
    print(f"duplicate leaf labels: before={sum(v for _, v in dups_before)} in {len(dups_before)} groups "
          f"-> after={sum(v for _, v in dups_after)} in {len(dups_after)} groups  ({merges} sibling-merges)")
    if dups_before:
        print(f"   was: {dups_before}")
    print(labeler.cost_line())

    return {
        "max_depth": max_depth, "gamma_by_depth": GAMMA_BY_DEPTH[:max_depth],
        # aliases so the flat grid renderer's stats line keeps working against the new schema
        "gamma_coarse": GAMMA_BY_DEPTH[0], "gamma_fine": GAMMA_BY_DEPTH[1],
        "nodes": nodes, "roots": roots,
        "supers": supers, "lanes": lanes,
        "commit_lanes": {k: sorted(v) for k, v in commit_leaves.items()},
        "commits": data["commits"], "cost": labeler.cost_line(),
    }


if __name__ == "__main__":
    md = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_DEPTH
    data = json.loads((_OUT / "patches.json").read_text(encoding="utf-8"))
    result = build(data, _REPO_ROOT, md)
    (_OUT / "hierarchy.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {_OUT / 'hierarchy.json'}")
