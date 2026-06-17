"""U3 tests: revert closure + cascade GC (origin AE2)."""

from sgt.engine.closure import dependents_closure, revert_set
from sgt.store.graph import EdgeType, Node, NodeKind, SemanticGraph


def _g():
    g = SemanticGraph()
    return g


def cap(g, id_):
    g.add_node(Node(id=id_, kind=NodeKind.CAPABILITY, intent=id_))


def concept(g, id_):
    g.add_node(Node(id=id_, kind=NodeKind.CONCEPT, intent=id_))


def test_dependents_closure_includes_transitive_dependents():
    g = _g()
    cap(g, "base")
    cap(g, "mid")
    cap(g, "top")
    g.add_edge("mid", "base", EdgeType.DEPENDS_ON)  # mid depends on base
    g.add_edge("top", "mid", EdgeType.DEPENDS_ON)   # top depends on mid
    # reverting base must take mid and top with it
    assert dependents_closure(g, "base") == {"base", "mid", "top"}


def test_revert_gcs_orphaned_dependency():
    # rate_limit depends on api_keys; api_keys has no other user -> GC'd on revert
    g = _g()
    cap(g, "rate_limit")
    concept(g, "api_keys")
    g.add_edge("rate_limit", "api_keys", EdgeType.DEPENDS_ON)
    assert revert_set(g, "rate_limit") == {"rate_limit", "api_keys"}


def test_revert_keeps_shared_dependency():
    # both rate_limit and dashboard depend on api_keys -> api_keys survives
    g = _g()
    cap(g, "rate_limit")
    cap(g, "dashboard")
    concept(g, "api_keys")
    g.add_edge("rate_limit", "api_keys", EdgeType.DEPENDS_ON)
    g.add_edge("dashboard", "api_keys", EdgeType.DEPENDS_ON)
    result = revert_set(g, "rate_limit")
    assert result == {"rate_limit"}
    assert "api_keys" not in result  # still referenced by dashboard


def test_revert_unrelated_feature_is_isolated():
    g = _g()
    cap(g, "a")
    cap(g, "b")  # unrelated, no edges
    assert revert_set(g, "a") == {"a"}
