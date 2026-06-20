"""The T0 merge engine: delta transport-shape + union + two-check re-gate.

This is the core, so the algorithm is deliberately explicit:

* **union by eid** — appending a delta is idempotent; re-pull is a no-op (EC1/EC2).
* **two-check re-gate** in total order — a node is active iff it neither *concurrently
  conflicts* with an already-active node (structural, catches the LWW-hidden statement
  races) nor *fails to apply sequentially* on the active set (materialize-try, catches
  def-level collisions, joint-invalidity, and tombstone-orphaned edits). First node by
  total order wins; losers are durable conflicts (reusing `QUARANTINED` + witness).
* **re-derive edges** so cross-replica dependencies exist for revert-closure.

Materialization is never blocked: losers are excluded by status, so the merged tree always
materializes and stays invariant-valid (I1), and the result is independent of merge
direction (I2) because everything is keyed on the per-effect total order, not arrival order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sgt.effects.invariants import codebase_valid
from sgt.effects.model import Effect, EffectError, materialize
from sgt.engine.commute import static_commute
from sgt.engine.confluence import INVARIANT_VIOLATED, NON_COMMUTING_PREFIX, PRECONDITION_FAILED
from sgt.store.graph import EdgeType, Node, NodeStatus
from sgt.store.oplog import LogEntry
from sgt.store.replica import ReplicaIdentity


@dataclass
class Delta:
    """What crosses between replicas: new log entries, tombstones, and node metadata.

    Node metadata travels because a node's kind/intent live in the graph, not the log; the
    receiver needs them to render an incoming node. Tombstones travel so a revert on one
    replica is visible to the other (EC9).
    """

    entries: list[LogEntry] = field(default_factory=list)
    tombstones: set[str] = field(default_factory=set)
    nodes: list[Node] = field(default_factory=list)


@dataclass
class MergeReport:
    landed: list[str] = field(default_factory=list)      # node ids active after merge
    conflicts: list[str] = field(default_factory=list)   # node ids held by the merge
    message: str = ""


def export_delta(project, peer_frontier) -> Delta:
    """Everything `peer_frontier` has not observed: entries past the peer's per-author count."""
    entries = [
        e for e in project.log.entries
        if ReplicaIdentity.parse(e.eid)[1] >= peer_frontier.get(ReplicaIdentity.parse(e.eid)[0])
    ]
    touched = {e.node_id for e in entries}
    nodes = [project.graph.get(nid) for nid in touched if project.graph.has(nid)]
    return Delta(entries=entries, tombstones=set(project.log.tombstones), nodes=list(nodes))


def _ingest(project, delta: Delta) -> None:
    """Union the delta into the local store (idempotent by eid / set semantics)."""
    for node in delta.nodes:
        if not project.graph.has(node.id):
            project.graph.add_node(Node(id=node.id, kind=node.kind, intent=node.intent,
                                        status=node.status))
            if node.id not in project.order:
                project.order.append(node.id)
    known = {e.eid for e in project.log.entries}
    for entry in delta.entries:
        if entry.eid not in known:
            project.log.append(entry)
            known.add(entry.eid)
    project.log.tombstone(set(delta.tombstones))
    project.vv = project.log.frontier()


def _node_entries(project) -> dict[str, list[LogEntry]]:
    out: dict[str, list[LogEntry]] = {}
    for e in project.log.live_entries():
        out.setdefault(e.node_id, []).append(e)
    return out


def _concurrent_conflict(node_entries: list[LogEntry], active_entries: list[LogEntry]) -> str | None:
    """Return a conflicting active node id if any node effect concurrently clashes with it.

    Structural and apply-free: only fires for `static_commute is False` between *concurrent*
    (vv-incomparable) effects — i.e. the statement-level races materialization would hide.
    """
    for ne in node_entries:
        for ae in active_entries:
            if ne.vv.concurrent(ae.vv) and static_commute(ne.effect, ae.effect) is False:
                return ae.node_id
    return None


def _regate(project) -> MergeReport:
    """Recompute node statuses as a projection of the unioned log (two checks, total order)."""
    by_node = _node_entries(project)
    # Skip user-suspended nodes (their exclusion is intentional, not a merge decision).
    candidates = [
        nid for nid in by_node
        if project.graph.has(nid) and project.graph.get(nid).status is not NodeStatus.SUSPENDED
    ]
    candidates.sort(key=lambda nid: min(e.order_key for e in by_node[nid]))

    active_entries: list[LogEntry] = []
    active_effects: list[Effect] = []
    report = MergeReport()

    for nid in candidates:
        entries = sorted(by_node[nid], key=lambda e: e.order_key)
        effects = [e.effect for e in entries]

        loser_against = _concurrent_conflict(entries, active_entries)
        reason = ""
        if loser_against is not None:
            reason = f"{NON_COMMUTING_PREFIX}{loser_against}"
        else:
            try:
                trial = active_effects + effects
                if codebase_valid(materialize(trial)):
                    project.graph.get(nid).status = NodeStatus.ACTIVE
                    project.witnesses.pop(nid, None)
                    active_entries.extend(entries)
                    active_effects = trial
                    report.landed.append(nid)
                    continue
                reason = INVARIANT_VIOLATED
            except EffectError:
                reason = PRECONDITION_FAILED

        # held → durable conflict
        project.graph.get(nid).status = NodeStatus.QUARANTINED
        against = [loser_against] if loser_against else []
        project.witnesses[nid] = {
            "reason": reason,
            "held": [f"{e.op.value} {e.target}" for e in effects],
            "against": sorted(set(against)),
        }
        for dep in against:
            if (project.graph.has(dep) and dep != nid
                    and not project.graph.would_create_cycle(nid, dep)):
                project.graph.add_edge(nid, dep, EdgeType.DEPENDS_ON)
        report.conflicts.append(nid)

    _reinfer_edges(project, report.landed)
    return report


def _reinfer_edges(project, landed: list[str]) -> None:
    """Re-derive cross-replica DEPENDS_ON edges over the merged active set (EC12)."""
    for nid in landed:
        effects = project.bundles.get(nid, [])
        for dep in project._infer_dependencies(effects):
            if (dep != nid and project.graph.has(dep)
                    and dep not in project.graph.successors(nid)
                    and not project.graph.would_create_cycle(nid, dep)):
                project.graph.add_edge(nid, dep, EdgeType.DEPENDS_ON)


def merge(project, delta: Delta) -> MergeReport:
    """Merge an incoming `delta` into `project`: union → re-gate → re-derive edges."""
    _ingest(project, delta)
    return _regate(project)
