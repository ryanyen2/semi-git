"""Resolve a user ref to a decision, lane, and node — one resolver for every verb.

A ref the user types names one of: a **decision id** (``node@landing``), a **node id**, a **lane**
(a feature — itself a node id), an **entity key** (``file::name``, the footprint join key), or a
**phrase** matched against decisions' intent/slug/footprint. The single ``resolve`` returns all of
``node_id`` / ``lane`` / ``decision_id`` so each caller takes the field it needs: ``show``/
``--fulfills`` use ``node_id``; ``revert``/``restore`` use ``lane`` (and ``decision_id`` to pin).

Resolution is deterministic and offline. On one hit we resolve; on several we report ``ambiguous``
with the candidates; on none we report ``missing`` (origin R6, AE1).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ref:
    kind: str  # "decision" | "node" | "entity" | "resolved" | "ambiguous" | "missing"
    node_id: str | None = None
    lane: str | None = None
    decision_id: str | None = None  # a specific decision when the ref pinned one; None = lane tip
    matches: list[str] = field(default_factory=list)  # candidates (lanes/ids) for disambiguation

    @property
    def ok(self) -> bool:
        return self.kind not in ("ambiguous", "missing")


def resolve(project, ref: str) -> Ref:
    from sgt.decisions.store import build_decisions

    ref = ref.strip()
    decisions = build_decisions(project)
    by_id = {d.id: d for d in decisions}
    # lane (feature) of a node: a lane's id is its earliest node, but any node maps to its lane.
    lane_of_node: dict[str, str] = {}
    tip_of_lane: dict[str, tuple[int, str, str]] = {}  # lane -> (landing, decision_id, node_id)
    for d in decisions:
        lane_of_node.setdefault(d.node_id, d.feature)
        cur = tip_of_lane.get(d.feature)
        if cur is None or d.landing > cur[0]:
            tip_of_lane[d.feature] = (d.landing, d.id, d.node_id)

    def lane_hit(lane: str, kind: str) -> Ref:
        tip = tip_of_lane.get(lane)
        node = tip[2] if tip else lane
        return Ref(kind, node_id=node, lane=lane, matches=[lane])

    # 1. exact decision id (node@landing) — pins that specific decision
    if ref in by_id:
        d = by_id[ref]
        return Ref("decision", node_id=d.node_id, lane=d.feature, decision_id=ref, matches=[d.id])

    # 2. exact node id — resolves to its lane (a lane id is a node id, so this covers lanes too)
    if project.graph.has(ref):
        return Ref("node", node_id=ref, lane=lane_of_node.get(ref, ref), matches=[ref])

    # 3. entity key (file::name) — the lane whose footprint owns it
    if "::" in ref:
        owners = sorted({d.feature for d in decisions if ref in d.footprint})
        if len(owners) == 1:
            return lane_hit(owners[0], "entity")
        if len(owners) > 1:
            return Ref("ambiguous", matches=owners)

    # 4. phrase — match over decision text + footprint names, deduped to lanes
    needle = ref.lower()
    matched_lanes: list[str] = []
    for d in decisions:
        text = " ".join(filter(None, [d.intent.decision, d.intent.slug, d.feature, d.node_id]))
        names = " ".join(k.split("::", 1)[-1] for k in d.footprint)
        if needle in text.lower() or needle in names.lower():
            matched_lanes.append(d.feature)
    uniq = sorted(set(matched_lanes))
    if len(uniq) == 1:
        return lane_hit(uniq[0], "resolved")
    if len(uniq) > 1:
        return Ref("ambiguous", matches=uniq)
    return Ref("missing", matches=[])
