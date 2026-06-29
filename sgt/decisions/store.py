"""Recover decisions from the effect log; persist the frontier and authored metadata.

The structural shape of a decision is *derived* from the log every time (group entries by
``(node_id, landing)``), so it can never drift from the effects. Two small sidecars in
``.sgt/`` hold the things the log does not: ``decisions.json`` (authored intent +
alternatives, keyed by decision id) and ``frontier.json`` (the in-force selection).
"""

from __future__ import annotations

import json
from pathlib import Path

from sgt.decisions.model import (
    Alternative,
    Decision,
    DecisionStatus,
    Frontier,
    Intent,
    LifecycleKind,
)
from sgt.store.graph import EdgeType, NodeStatus

META_FILE = "decisions.json"
FRONTIER_FILE = "frontier.json"
TAGS_FILE = "frontier_tags.json"

# Sentinel frontier value: a lane explicitly out of force (the suspend state). Distinct from a
# lane simply absent from the selection, which defaults to that lane's tip — OFF is a *decision*
# to exclude and survives ``load_frontier``'s tip-defaulting.
OFF = "off"


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

    A lane = a feature. A node folds into an *existing* def's lane only when it introduces **no new
    def of its own** — a pure fix/revise (the distiller's fix-node split, which re-owns a def already
    owned elsewhere). Such a node unions with exactly **one** existing lane, its *primary*: a def it
    declares it ``provides``, else the oldest lane it touches. A ``REVISES`` edge also unions; a
    ``DERIVES_FROM`` fork never does (a fork is its own lane).

    The "introduces a fresh def → own lane, never fold" half is the anti-weld rule. Earlier this
    unioned a node with the owner of *every* def it touched, so a single checkpoint that co-edited two
    functions (e.g. a ``generate`` dispatch that also forwarded through ``run_pipeline``) fused their
    lanes — and transitively collapsed unrelated capabilities into one spine (the "flattened graph").
    A node that adds new code is its own capability; its edits to other defs are recorded in its
    footprint (so the projection still draws a builds-on edge) but never weld a shared lane.
    """
    node_keys: dict[str, set[str]] = {}
    first_landing: dict[str, int] = {}
    for (nid, landing), entries in groups.items():
        node_keys.setdefault(nid, set()).update(
            k for e in entries for k in [f"{e.effect.file}::{e.effect.target}"] if _is_entity_key(k)
        )
        first_landing[nid] = min(first_landing.get(nid, landing), landing)
    provides_of = {nid: set(graph.get(nid).provides) if graph.has(nid) else set() for nid in node_keys}

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

    # Process oldest-first so the earliest toucher of a def owns it; a later node sees that def as
    # already-owned ("shared") rather than fresh.
    owner_of_key: dict[str, str] = {}
    for nid in sorted(node_keys, key=lambda n: (first_landing[n], n)):
        keys = node_keys[nid]
        fresh = {k for k in keys if k not in owner_of_key}
        shared = keys - fresh
        if shared and not fresh:  # pure fix/revise — fold into ONE existing lane, never several
            def _rank(k: str, _nid=nid) -> tuple:
                owner = owner_of_key[k]
                provided = k.split("::", 1)[1] in provides_of[_nid]
                return (0 if provided else 1, first_landing[owner], owner, k)
            union(nid, owner_of_key[min(shared, key=_rank)])
        for k in fresh:  # claim newly-introduced defs
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
                   slug=m.get("slug"), context=m.get("context"), consequence=m.get("consequence")),
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
                lifecycle_kind=kind, lifecycle_of=of, status=DecisionStatus.LANDED,
            ))
    decisions.sort(key=lambda d: (d.landing, d.node_id))
    base_landing = max((d.landing for d in decisions), default=0)
    planned = _planned_decisions(
        project, landed_nids={nid for (nid, _l) in groups}, meta=meta, base_landing=base_landing)
    _fold_planned_revisions(planned, decisions, graph)
    decisions.extend(planned)
    return decisions


def _fold_planned_revisions(planned: list[Decision], landed: list[Decision], graph) -> None:
    """Fold a planned node that *redefines* an existing entity into that entity's lane (R: revise).

    A planned capability whose ``provides`` names a def a landed decision already owns (e.g.
    "enhance ``preprocess``") is not a new feature — it is the next revision of that lane. Folding it
    in-place (same ``feature``, ``REVISE`` of the lane's current tip) makes it stack directly above the
    decision it enhances with a connecting spine, instead of floating as its own one-dot column. A
    planned node that only *needs* existing names (its provides are new) stays its own lane and is
    linked by the cross-lane builds-on bridge in ``sgt.api`` instead. ``landed`` is sorted ascending,
    so the last footprint owner / decision seen per lane is that lane's tip.
    """
    name_to_lane: dict[str, str] = {}
    tip_of_lane: dict[str, str] = {}
    for d in landed:
        tip_of_lane[d.feature] = d.id
        for k in d.footprint:
            if "::" in k:
                name_to_lane.setdefault(k.split("::", 1)[1], d.feature)
    for pd in planned:
        node = graph.get(pd.node_id) if graph.has(pd.node_id) else None
        if node is None:
            continue
        for nm in node.provides:
            lane = name_to_lane.get(nm)
            if lane and lane != pd.feature:
                pd.feature = lane
                pd.lifecycle_kind = LifecycleKind.REVISE
                pd.lifecycle_of = tip_of_lane[lane]
                break


def _planned_decisions(project, landed_nids: set[str], meta: dict, base_landing: int = 0) -> list[Decision]:
    """Decisions for graph nodes that haven't landed any effects yet (plan R: hollow nodes).

    A node is planned when it has no landed decision and either carries ``NodeStatus.PLANNED``
    or simply produced no effects. Each planned capability is its own lane (``feature = id``)
    with an empty footprint and no commits. Their ``landing`` is a dependencies-first
    topological index over ``depends_on`` *offset above the latest landed landing*
    (``base_landing``), so a dependency still sorts before its dependents while the whole planned
    cohort floats to the top of the time axis (newest = not-yet-built) instead of colliding with
    landed landing integers and sinking below them.
    """
    graph = project.graph
    planned_nodes = [
        n for n in graph.nodes()
        if n.id not in landed_nids
        and (n.status is NodeStatus.PLANNED or not n.commit_ids)
    ]
    if not planned_nodes:
        return []
    planned_ids = {n.id for n in planned_nodes}

    # dependencies-first order over the planned subgraph's depends_on edges
    succ: dict[str, list[str]] = {nid: [] for nid in planned_ids}
    for e in graph.edges():
        if e.type is EdgeType.DEPENDS_ON and e.src in planned_ids and e.dst in planned_ids:
            succ[e.src].append(e.dst)
    order: list[str] = []
    seen: set[str] = set()

    def visit(n: str) -> None:
        if n in seen:
            return
        seen.add(n)
        for dep in sorted(succ[n]):  # deps first, deterministic
            visit(dep)
        order.append(n)

    for nid in sorted(planned_ids):
        visit(nid)
    landing_of = {nid: base_landing + i + 1 for i, nid in enumerate(order)}

    out: list[Decision] = []
    for n in sorted(planned_nodes, key=lambda n: landing_of[n.id]):
        m = meta.get(n.id, {})
        out.append(Decision(
            id=n.id, node_id=n.id, feature=n.id, landing=landing_of[n.id],
            intent=Intent(decision=m.get("decision") or n.intent,
                          slug=m.get("slug"), context=m.get("context"), consequence=m.get("consequence")),
            footprint=[], commits=[],
            alternatives=[Alternative.from_dict(a) for a in m.get("alternatives", [])],
            lifecycle_kind=LifecycleKind.INTRODUCE, lifecycle_of=None,
            status=DecisionStatus.PLANNED,
        ))
    return out


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
        # A stored OFF survives (an explicit suspend); a pinned id survives only while it still
        # exists, else the lane falls back to its tip so HEAD can't dangle.
        if dec_id == OFF or dec_id in valid_ids:
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
