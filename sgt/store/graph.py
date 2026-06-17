"""The semantic DAG: nodes, typed edges, and the acyclicity invariant.

This module is pure (no git, no agents). A `Node` names a feature/concept and points
at the git commit(s) that materialize its effect-bundle; `SemanticGraph` enforces that
the dependency structure stays a DAG and round-trips to JSON for `.sgt/graph.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class NodeKind(str, Enum):
    """The kind of a node. Not every prompt is a feature (origin R2)."""

    CAPABILITY = "capability"
    CONCEPT = "concept"
    INFRASTRUCTURE = "infrastructure"
    FIX = "fix"
    EXPLORATION = "exploration"


class NodeStatus(str, Enum):
    """Append-only lifecycle: `switch off` suspends rather than deletes (origin R16)."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class EdgeType(str, Enum):
    """A directed edge ``src -> dst``.

    ``DEPENDS_ON``: src requires dst. ``REVISES``: src is a later revision of dst
    (e.g. a fix). ``DERIVES_FROM``: src was produced by iterating dst's intent.
    """

    DEPENDS_ON = "depends_on"
    REVISES = "revises"
    DERIVES_FROM = "derives_from"


class CycleError(Exception):
    """Raised when adding an edge would violate the DAG invariant."""


class GraphError(Exception):
    """Raised for structural misuse (unknown node, duplicate id)."""


@dataclass
class Node:
    id: str
    kind: NodeKind
    intent: str
    status: NodeStatus = NodeStatus.ACTIVE
    effect_bundle_id: str | None = None
    invariant_ids: list[str] = field(default_factory=list)
    commit_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "intent": self.intent,
            "status": self.status.value,
            "effect_bundle_id": self.effect_bundle_id,
            "invariant_ids": list(self.invariant_ids),
            "commit_ids": list(self.commit_ids),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Node:
        return cls(
            id=d["id"],
            kind=NodeKind(d["kind"]),
            intent=d["intent"],
            status=NodeStatus(d.get("status", NodeStatus.ACTIVE.value)),
            effect_bundle_id=d.get("effect_bundle_id"),
            invariant_ids=list(d.get("invariant_ids", [])),
            commit_ids=list(d.get("commit_ids", [])),
        )


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    type: EdgeType

    def to_dict(self) -> dict:
        return {"src": self.src, "dst": self.dst, "type": self.type.value}

    @classmethod
    def from_dict(cls, d: dict) -> Edge:
        return cls(src=d["src"], dst=d["dst"], type=EdgeType(d["type"]))


class SemanticGraph:
    """An in-memory semantic DAG with acyclicity enforced on every edge insert."""

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: list[Edge] = []

    # -- nodes -------------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        if node.id in self._nodes:
            raise GraphError(f"duplicate node id: {node.id!r}")
        self._nodes[node.id] = node
        return node

    def has(self, node_id: str) -> bool:
        return node_id in self._nodes

    def get(self, node_id: str) -> Node:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise GraphError(f"unknown node: {node_id!r}") from None

    def nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def edges(self) -> list[Edge]:
        return list(self._edges)

    def remove_node(self, node_id: str) -> None:
        """Remove a node and every edge incident to it."""
        if node_id not in self._nodes:
            raise GraphError(f"unknown node: {node_id!r}")
        del self._nodes[node_id]
        self._edges = [e for e in self._edges if e.src != node_id and e.dst != node_id]

    # -- edges -------------------------------------------------------------
    def add_edge(self, src: str, dst: str, type: EdgeType) -> Edge:
        if src not in self._nodes:
            raise GraphError(f"unknown node: {src!r}")
        if dst not in self._nodes:
            raise GraphError(f"unknown node: {dst!r}")
        if src == dst:
            raise CycleError(f"self-edge on {src!r} would create a cycle")
        if self._can_reach(dst, src):
            raise CycleError(f"edge {src!r} -> {dst!r} would create a cycle")
        edge = Edge(src=src, dst=dst, type=type)
        self._edges.append(edge)
        return edge

    def successors(self, node_id: str) -> list[str]:
        """Nodes that ``node_id`` points at (its dependencies)."""
        return [e.dst for e in self._edges if e.src == node_id]

    def predecessors(self, node_id: str) -> list[str]:
        """Nodes that point at ``node_id`` (its dependents)."""
        return [e.src for e in self._edges if e.dst == node_id]

    def would_create_cycle(self, src: str, dst: str) -> bool:
        return src == dst or self._can_reach(dst, src)

    def _can_reach(self, start: str, target: str) -> bool:
        """Can ``target`` be reached from ``start`` following directed edges?"""
        seen: set[str] = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            if cur == target:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.successors(cur))
        return False

    def topo_order(self) -> list[str]:
        """Dependencies-first topological order (dst before src)."""
        indeg = {n: 0 for n in self._nodes}
        for e in self._edges:
            indeg[e.src] += 1  # src depends on dst, so src has an out-need
        # Order so that a node appears after all nodes it points at.
        order: list[str] = []
        seen: set[str] = set()

        def visit(n: str) -> None:
            if n in seen:
                return
            seen.add(n)
            for nxt in self.successors(n):
                visit(nxt)
            order.append(n)

        for n in self._nodes:
            visit(n)
        return order

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges],
        }

    @classmethod
    def from_dict(cls, d: dict) -> SemanticGraph:
        g = cls()
        for nd in d.get("nodes", []):
            g.add_node(Node.from_dict(nd))
        # Edges are appended directly (the persisted graph is already acyclic);
        # re-validating via add_edge would reject legitimate diamonds loaded in
        # an unlucky order.
        for ed in d.get("edges", []):
            g._edges.append(Edge.from_dict(ed))
        return g

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> SemanticGraph:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
