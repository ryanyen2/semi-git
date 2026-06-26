"""U1 tests: semantic graph model + DAG invariant + JSON round-trip."""

import pytest

from sgt.store.graph import (
    CycleError,
    EdgeType,
    GraphError,
    Node,
    NodeKind,
    NodeStatus,
    SemanticGraph,
)


def cap(id_: str, intent: str = "x") -> Node:
    return Node(id=id_, kind=NodeKind.CAPABILITY, intent=intent)


def test_quarantined_status_round_trips():
    n = Node(id="q", kind=NodeKind.CAPABILITY, intent="held work",
             status=NodeStatus.QUARANTINED)
    assert Node.from_dict(n.to_dict()).status is NodeStatus.QUARANTINED


def test_add_and_get_node():
    g = SemanticGraph()
    g.add_node(cap("a", "add login"))
    assert g.has("a")
    assert g.get("a").intent == "add login"
    assert [n.id for n in g.nodes()] == ["a"]


def test_duplicate_node_rejected():
    g = SemanticGraph()
    g.add_node(cap("a"))
    with pytest.raises(GraphError):
        g.add_node(cap("a"))


def test_edge_to_unknown_node_rejected():
    g = SemanticGraph()
    g.add_node(cap("a"))
    with pytest.raises(GraphError):
        g.add_edge("a", "missing", EdgeType.DEPENDS_ON)


def test_add_edge_and_neighbors():
    g = SemanticGraph()
    g.add_node(cap("a"))
    g.add_node(Node(id="b", kind=NodeKind.CONCEPT, intent="api-keys"))
    g.add_edge("a", "b", EdgeType.DEPENDS_ON)
    assert g.successors("a") == ["b"]
    assert g.predecessors("b") == ["a"]


def test_self_edge_rejected():
    g = SemanticGraph()
    g.add_node(cap("a"))
    with pytest.raises(CycleError):
        g.add_edge("a", "a", EdgeType.DEPENDS_ON)


def test_cycle_rejected():
    g = SemanticGraph()
    for n in ("a", "b", "c"):
        g.add_node(cap(n))
    g.add_edge("a", "b", EdgeType.DEPENDS_ON)
    g.add_edge("b", "c", EdgeType.DEPENDS_ON)
    # c -> a would close the loop a -> b -> c -> a
    with pytest.raises(CycleError):
        g.add_edge("c", "a", EdgeType.DEPENDS_ON)
    assert g.would_create_cycle("c", "a") is True


def test_diamond_is_allowed():
    # a -> b, a -> c, b -> d, c -> d : a DAG, not a cycle.
    g = SemanticGraph()
    for n in ("a", "b", "c", "d"):
        g.add_node(cap(n))
    g.add_edge("a", "b", EdgeType.DEPENDS_ON)
    g.add_edge("a", "c", EdgeType.DEPENDS_ON)
    g.add_edge("b", "d", EdgeType.DEPENDS_ON)
    g.add_edge("c", "d", EdgeType.DEPENDS_ON)  # must not raise
    assert set(g.successors("a")) == {"b", "c"}


def test_remove_node_removes_incident_edges():
    g = SemanticGraph()
    g.add_node(cap("a"))
    g.add_node(cap("b"))
    g.add_edge("a", "b", EdgeType.DEPENDS_ON)
    g.remove_node("b")
    assert not g.has("b")
    assert g.edges() == []
    assert g.successors("a") == []


def test_topo_order_puts_dependencies_first():
    g = SemanticGraph()
    for n in ("a", "b", "c"):
        g.add_node(cap(n))
    g.add_edge("a", "b", EdgeType.DEPENDS_ON)
    g.add_edge("b", "c", EdgeType.DEPENDS_ON)
    order = g.topo_order()
    assert order.index("c") < order.index("b") < order.index("a")


def test_json_roundtrip(tmp_path):
    g = SemanticGraph()
    g.add_node(
        Node(
            id="a",
            kind=NodeKind.CAPABILITY,
            intent="rate limit",
            status=NodeStatus.QUARANTINED,
            effect_bundle_id="eb1",
            invariant_ids=["ref-integrity"],
            commit_ids=["deadbeef"],
        )
    )
    g.add_node(Node(id="b", kind=NodeKind.CONCEPT, intent="api-keys"))
    g.add_edge("a", "b", EdgeType.DEPENDS_ON)

    path = tmp_path / ".sgt" / "graph.json"
    g.save(path)
    loaded = SemanticGraph.load(path)

    a = loaded.get("a")
    assert a.kind is NodeKind.CAPABILITY
    assert a.status is NodeStatus.QUARANTINED
    assert a.effect_bundle_id == "eb1"
    assert a.commit_ids == ["deadbeef"]
    assert loaded.successors("a") == ["b"]
