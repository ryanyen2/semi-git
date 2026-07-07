"""Tests for sgt.core.order -- the <= relation and ideal validity (plan U4, R3/R4/R10)."""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from sgt.core.op import make_op
from sgt.core.order import (
    chain_edges,
    downset,
    frontier,
    is_fork_free,
    is_valid_ideal,
    reference_edges,
    upset,
)


def _chain(sym: str, n: int):
    """A linear n-op chain for one symbol: root(None->v0), op1(v0->v1), ..."""
    ops = []
    before = None
    for i in range(n):
        after = f"v{i}"
        ops.append(
            make_op({sym: (before, after)}, {sym: f"body{i}".encode()}, provenance=(f"c{i}",))
        )
        before = after
    return ops


def test_chain_edges_link_consecutive_versions_only():
    ops = _chain("a.py::foo", 3)
    edges = chain_edges(ops)
    assert edges == frozenset({(ops[0].id, ops[1].id), (ops[1].id, ops[2].id)})


def test_reference_edges_point_to_the_exact_version_producer():
    producer = make_op({"a.py::base": (None, "v0")}, {"a.py::base": b"x"})
    other_producer = make_op({"a.py::base2": (None, "v0")}, {"a.py::base2": b"z"})
    consumer = make_op(
        {"a.py::user": (None, "v0")}, {"a.py::user": b"y"},
        requires=frozenset({("a.py::base", "v0")}),
    )
    ops = [producer, other_producer, consumer]
    assert reference_edges(ops) == frozenset({(producer.id, consumer.id)})


def test_downward_closure_violation_is_unconstructible():
    ops = _chain("a.py::foo", 3)
    assert not is_valid_ideal(ops, {ops[2].id})  # skips its own prerequisites
    assert not is_valid_ideal(ops, {ops[0].id, ops[2].id})  # skips the middle
    assert is_valid_ideal(ops, {ops[0].id, ops[1].id})  # a valid prefix
    assert is_valid_ideal(ops, {op.id for op in ops})  # the full chain
    assert is_valid_ideal(ops, set())  # the empty ideal is always valid


def test_downward_closure_respects_reference_edges_too():
    base = make_op({"a.py::base": (None, "v0")}, {"a.py::base": b"x"})
    user = make_op(
        {"a.py::user": (None, "v0")}, {"a.py::user": b"y"},
        requires=frozenset({("a.py::base", "v0")}),
    )
    ops = [base, user]
    assert not is_valid_ideal(ops, {user.id})  # user without the base it requires
    assert is_valid_ideal(ops, {base.id, user.id})
    assert is_valid_ideal(ops, {base.id})  # base alone, with nothing depending on it


def test_chain_fork_detected_when_two_ops_share_before_version():
    root = make_op({"a.py::x": (None, "v0")}, {"a.py::x": b"root"})
    tip_a = make_op({"a.py::x": ("v0", "v1")}, {"a.py::x": b"a"}, provenance=("branch-a",))
    tip_b = make_op({"a.py::x": ("v0", "v2")}, {"a.py::x": b"b"}, provenance=("branch-b",))
    ops = [root, tip_a, tip_b]
    assert is_fork_free(ops, {root.id, tip_a.id})
    assert is_fork_free(ops, {root.id, tip_b.id})
    assert not is_fork_free(ops, {root.id, tip_a.id, tip_b.id})
    assert not is_valid_ideal(ops, {root.id, tip_a.id, tip_b.id})


def test_upset_of_mid_chain_op_is_itself_and_its_descendants():
    ops = _chain("a.py::foo", 4)
    assert upset(ops[1].id, ops) == frozenset({ops[1].id, ops[2].id, ops[3].id})
    assert upset(ops[0].id, ops) == frozenset(op.id for op in ops)
    assert upset(ops[3].id, ops) == frozenset({ops[3].id})  # the tip builds on nothing further


def test_downset_of_mid_chain_op_is_itself_and_its_prerequisites():
    ops = _chain("a.py::foo", 4)
    assert downset(ops[2].id, ops) == frozenset({ops[0].id, ops[1].id, ops[2].id})


def test_downset_includes_declared_edge_ancestors():
    a = make_op({"a.py::a": (None, "v0")}, {"a.py::a": b"a"})
    b = make_op({"b.py::b": (None, "v0")}, {"b.py::b": b"b"})  # unrelated by chain/reference
    ops = [a, b]
    declared = frozenset({(a.id, b.id)})  # `sgt after b a` -- a must precede b
    assert downset(b.id, ops) == frozenset({b.id})  # unrelated without the declared edge
    assert downset(b.id, ops, declared) == frozenset({a.id, b.id})
    assert is_valid_ideal(ops, {b.id})  # valid on its own
    assert not is_valid_ideal(ops, {b.id}, declared)  # but not once a is declared a prerequisite
    assert is_valid_ideal(ops, {a.id, b.id}, declared)


def test_frontier_is_the_chain_tip_for_a_full_ideal():
    ops = _chain("a.py::foo", 4)
    ideal_ids = {op.id for op in ops}
    assert frontier(ideal_ids, ops) == {"a.py::foo": ops[-1].id}


def test_frontier_is_the_prefix_tip_for_a_partial_ideal():
    ops = _chain("a.py::foo", 4)
    ideal_ids = {ops[0].id, ops[1].id}
    assert frontier(ideal_ids, ops) == {"a.py::foo": ops[1].id}


@given(st.integers(min_value=1, max_value=8), st.integers(min_value=0, max_value=7))
@settings(max_examples=40)
def test_frontier_agrees_with_naive_prefix_walk(n, cut):
    """Property: for any prefix length of a single chain, the frontier's tip is exactly the
    last op of that prefix -- the compact frontier view and a naive walk of the chain agree."""
    ops = _chain("a.py::foo", n)
    cut = min(cut, n - 1)
    prefix = ops[: cut + 1]
    ideal_ids = {op.id for op in prefix}
    assert frontier(ideal_ids, ops) == {"a.py::foo": prefix[-1].id}


@given(st.integers(min_value=2, max_value=6))
@settings(max_examples=40)
def test_revert_of_mid_chain_op_removes_its_upset_exactly(n):
    """Property (R20's verb-output-validity law, checked here at the constructor level per
    U4's Verification): subtracting a mid-chain op's up-set from a full valid ideal always
    yields another valid ideal -- the prefix up to (not including) that op."""
    ops = _chain("a.py::foo", n)
    full = {op.id for op in ops}
    for cut in range(n):
        target = ops[cut].id
        remaining = full - upset(target, ops)
        assert is_valid_ideal(ops, remaining)
        assert remaining == {op.id for op in ops[:cut]}
