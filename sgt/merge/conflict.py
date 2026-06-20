"""The Conflict projection over durable quarantine records.

A merge holds the losing side(s) as ``QUARANTINED`` nodes with a witness. ``Conflict`` reads
that back as a first-class object — the held node plus the active node(s) it lost to — so a
UI or a resolver (T1 collapse / T2 resynthesis, deferred) can act on it. It is a *projection*,
not new storage: T0 records everything it needs in the witness.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sgt.effects.model import Effect
from sgt.store.graph import NodeStatus


@dataclass
class Side:
    """One competing version: the node, who authored it, its intent, and its effects."""

    node_id: str
    intent: str
    effects: list[Effect] = field(default_factory=list)


@dataclass
class Conflict:
    """A held node and the active node(s) it lost to — the sides a resolution chooses among."""

    held: Side
    against: list[Side] = field(default_factory=list)
    reason: str = ""

    @property
    def node_id(self) -> str:
        return self.held.node_id


def conflicts(project) -> list[Conflict]:
    """Every open conflict in the project, projected from the quarantine witnesses."""
    out: list[Conflict] = []
    for nid, w in project.witnesses.items():
        if not (project.graph.has(nid) and project.graph.get(nid).status is NodeStatus.QUARANTINED):
            continue
        held = Side(nid, project.graph.get(nid).intent, list(project.bundles.get(nid, [])))
        against = [
            Side(aid, project.graph.get(aid).intent, list(project.bundles.get(aid, [])))
            for aid in w.get("against", []) if project.graph.has(aid)
        ]
        out.append(Conflict(held=held, against=against, reason=w.get("reason", "")))
    return out
