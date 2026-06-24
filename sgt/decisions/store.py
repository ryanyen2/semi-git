"""Recover decisions from the effect log; persist the frontier and authored metadata.

The structural shape of a decision is *derived* from the log every time (group entries by
``(node_id, landing)``), so it can never drift from the effects. Two small sidecars in
``.sgt/`` hold the things the log does not: ``decisions.json`` (authored intent +
alternatives, keyed by decision id) and ``frontier.json`` (the in-force selection).
"""

from __future__ import annotations

import json
from pathlib import Path

from sgt.decisions.model import Alternative, Decision, Frontier, Intent, LifecycleKind
from sgt.store.graph import EdgeType

META_FILE = "decisions.json"
FRONTIER_FILE = "frontier.json"
TAGS_FILE = "frontier_tags.json"


def _is_entity_key(key: str) -> bool:
    """A footprint key names a real def-level entity (``file::name``), not an import/module stmt.

    The distiller emits one effect per import line (``file::from x import y``) and per module-level
    statement; those are not entities the entity graph carries, so they neither ground a lane nor a
    builds-on edge.
    """
    target = key.split("::", 1)[1] if "::" in key else key
    return not (target.startswith("from ") or target.startswith("import ") or target.startswith("__"))


def _lineage_maps(graph) -> dict[str, str]:
    """``derives_to`` — for each node, the node it forks from (``DERIVES_FROM`` = a new lane)."""
    derives_to: dict[str, str] = {}
    for e in graph.edges():
        if e.type is EdgeType.DERIVES_FROM:
            derives_to.setdefault(e.src, e.dst)
    return derives_to


def _assign_lanes(groups, graph) -> dict[str, str]:
    """Footprint-grounded lane assignment (plan R13), robust to the distiller's fix-node splitting.

    A lane = a feature. Two nodes are the *same* lane when they revise the same code: they share an
    owned entity (same ``file::def``), or a ``REVISES`` edge links them. (A ``DERIVES_FROM`` fork is
    deliberately NOT merged — a fork is its own lane.) Union-find groups nodes accordingly; the lane
    id is the group's earliest node, so a fix-node that rewrites an existing def folds into that
    def's original lane as a revise rather than spawning a phantom lane + a duplicate-owner clash.
    """
    node_keys: dict[str, set[str]] = {}
    first_landing: dict[str, int] = {}
    for (nid, landing), entries in groups.items():
        node_keys.setdefault(nid, set()).update(
            k for e in entries for k in [f"{e.effect.file}::{e.effect.target}"] if _is_entity_key(k)
        )
        first_landing[nid] = min(first_landing.get(nid, landing), landing)

    parent: dict[str, str] = {nid: nid for nid in node_keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    owner_of_key: dict[str, str] = {}
    for nid, keys in node_keys.items():
        for k in keys:
            if k in owner_of_key:
                union(nid, owner_of_key[k])
            else:
                owner_of_key[k] = nid
    for e in graph.edges():
        if e.type is EdgeType.REVISES and e.src in parent and e.dst in parent:
            union(e.src, e.dst)

    # lane id = the group's earliest node (stable: by first landing, then id)
    rep: dict[str, str] = {}
    for nid in node_keys:
        root = find(nid)
        cur = rep.get(root)
        if cur is None or (first_landing[nid], nid) < (first_landing[cur], cur):
            rep[root] = nid
    return {nid: rep[find(nid)] for nid in node_keys}


def build_decisions(project) -> list[Decision]:
    """Project the log into decisions: one per ``(node_id, landing)`` group.

    Footprint = the entity keys (``file::target``) the group's effects touched (the same
    key convention ``timeframe_view`` uses). Lifecycle is derived from intra-node landing
    order plus the graph's ``REVISES`` / ``DERIVES_FROM`` edges. Authored intent and
    alternatives are merged in from the ``decisions.json`` sidecar when present.
    """
    log, graph = project.log, project.graph
    derives_to = _lineage_maps(graph)
    meta = load_meta(project.sgt_dir)

    groups: dict[tuple[str, int], list] = {}
    for e in log.live_entries():
        groups.setdefault((e.node_id, e.landing), []).append(e)

    lane_of = _assign_lanes(groups, graph)

    # Build provisional decisions, then derive lifecycle by per-lane landing order.
    provisional = []
    for (nid, landing), entries in groups.items():
        node = graph.get(nid) if graph.has(nid) else None
        footprint = sorted({f"{e.effect.file}::{e.effect.target}" for e in entries})
        did = f"{nid}@{landing}"
        m = meta.get(did, {})
        provisional.append((
            did, nid, lane_of.get(nid, nid), landing, footprint,
            list(node.commit_ids) if node else [],
            Intent(decision=m.get("decision") or (node.intent if node else nid),
                   context=m.get("context"), consequence=m.get("consequence")),
            [Alternative.from_dict(a) for a in m.get("alternatives", [])],
        ))

    # order within each lane, so a later landing revises the prior one on the same lane
    by_lane: dict[str, list] = {}
    for p in provisional:
        by_lane.setdefault(p[2], []).append(p)
    for lane in by_lane:
        by_lane[lane].sort(key=lambda p: (p[3], p[1]))
    # the latest decision id of each lane — what a fork descends from
    last_of_lane = {lane: items[-1][0] for lane, items in by_lane.items()}

    decisions: list[Decision] = []
    for lane, items in by_lane.items():
        for i, (did, nid, feature, landing, footprint, commits, intent, alts) in enumerate(items):
            if i > 0:
                kind, of = LifecycleKind.REVISE, items[i - 1][0]
            elif nid in derives_to and lane_of.get(derives_to[nid]) != lane:
                kind, of = LifecycleKind.FORK, last_of_lane.get(lane_of.get(derives_to[nid]))
            else:
                kind, of = LifecycleKind.INTRODUCE, None
            decisions.append(Decision(
                id=did, node_id=nid, feature=feature, landing=landing, intent=intent,
                footprint=footprint, commits=commits, alternatives=alts,
                lifecycle_kind=kind, lifecycle_of=of,
            ))
    decisions.sort(key=lambda d: (d.landing, d.node_id))
    return decisions


# -- persistence -----------------------------------------------------------

def load_meta(sgt_dir) -> dict:
    path = Path(sgt_dir) / META_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_meta(sgt_dir, meta: dict) -> None:
    path = Path(sgt_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_frontier(project, decisions: list[Decision]) -> Frontier:
    """The persisted frontier, or the default tip if none / stale.

    Any lane whose pinned decision no longer exists falls back to that lane's tip, so a
    revert or reconcile that removes a decision can't leave a dangling HEAD.
    """
    default = Frontier.tip_of(decisions)
    path = Path(project.sgt_dir) / FRONTIER_FILE
    if not path.exists():
        return default
    stored = Frontier.from_dict(json.loads(path.read_text(encoding="utf-8")))
    valid_ids = {d.id for d in decisions}
    selection = dict(default.selection)
    for feature, dec_id in stored.selection.items():
        if dec_id in valid_ids:
            selection[feature] = dec_id
    return Frontier(selection=selection)


def save_frontier(project, frontier: Frontier) -> None:
    path = Path(project.sgt_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / FRONTIER_FILE).write_text(json.dumps(frontier.to_dict(), indent=2), encoding="utf-8")


def load_tags(sgt_dir) -> dict[str, dict[str, str]]:
    """Named frontier snapshots: ``{tag_name: {feature: decision_id}}``."""
    path = Path(sgt_dir) / TAGS_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_tags(sgt_dir, tags: dict[str, dict[str, str]]) -> None:
    path = Path(sgt_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / TAGS_FILE).write_text(json.dumps(tags, indent=2), encoding="utf-8")
