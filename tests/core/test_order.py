"""Tests for sgt.core.order -- the <= relation and ideal validity (plan U4, R3/R4/R10)."""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from sgt.core.op import make_op
from sgt.core.order import (
    chain_edges,
    components_in,
    downset,
    downset_in,
    frontier,
    is_fork_free,
    is_valid_ideal,
    reference_edges,
    upset,
    upset_in,
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


def test_frontier_survives_a_revert_to_an_earlier_byte_identical_value():
    """Regression: add(None->v0) -> modify(v0->v1) -> revert(v1->v0) -- the revert's after-value
    collides with the add's after-value. The old after-value-keyed dict-overwrite bookkeeping
    (both `frontier`'s `superseded` set and the `chain_edges` producer map `is_valid_ideal` used
    to walk) either lost the revert as the tip, or built a nonsense edge that rejected a
    legitimate prefix's validity as depending on an op it doesn't even include."""
    sym = "a.py::foo"
    add = make_op({sym: (None, "v0")}, {sym: b"body0"}, provenance=("c0",))
    modify = make_op({sym: ("v0", "v1")}, {sym: b"body1"}, provenance=("c1",))
    revert = make_op({sym: ("v1", "v0")}, {sym: b"body0"}, provenance=("c2",))
    ops = [add, modify, revert]

    assert is_valid_ideal(ops, {add.id, modify.id})  # a valid prefix, no revert yet

    full = {op.id for op in ops}
    assert is_valid_ideal(ops, full)
    assert frontier(full, ops) == {sym: revert.id}


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


def _revert_chain(sym: str):
    """add(None->v0) -> modify(v0->v1) -> revert(v1->v0): the after-value collision (add and
    revert both land on v0) the ideal-relative up/down-set must survive (U8, plan U7.5 fix)."""
    add = make_op({sym: (None, "v0")}, {sym: b"b0"}, provenance=("c0",))
    mod = make_op({sym: ("v0", "v1")}, {sym: b"b1"}, provenance=("c1",))
    rev = make_op({sym: ("v1", "v0")}, {sym: b"b0"}, provenance=("c2",))
    return add, mod, rev


def test_is_valid_ideal_rejects_an_originless_revert_cycle():
    """The `{modify, revert}` pair lifted out of add->modify->revert is existentially closed
    (each produces the other's before-version) but has no chain head -- grounding rejects it."""
    sym = "a.py::foo"
    add, mod, rev = _revert_chain(sym)
    ops = [add, mod, rev]
    assert not is_valid_ideal(ops, {mod.id, rev.id})  # no origin -> not grounded
    assert is_valid_ideal(ops, {add.id})
    assert is_valid_ideal(ops, {add.id, mod.id})
    assert is_valid_ideal(ops, {add.id, mod.id, rev.id})


def test_upset_in_reverting_a_head_removes_the_whole_chain_despite_the_collision():
    sym = "a.py::foo"
    add, mod, rev = _revert_chain(sym)
    ops = [add, mod, rev]
    ideal = {add.id, mod.id, rev.id}
    # reverting the head removes the entire chain -- not just the add, leaving a headless cycle
    assert upset_in(add.id, ideal, ops) == ideal
    # reverting the middle removes middle + revert (revert built on v1, which only modify made)
    assert upset_in(mod.id, ideal, ops) == {mod.id, rev.id}
    assert is_valid_ideal(ops, ideal - upset_in(mod.id, ideal, ops))
    # reverting the tip removes only the tip
    assert upset_in(rev.id, ideal, ops) == {rev.id}
    assert is_valid_ideal(ops, ideal - {rev.id})


def test_upset_in_cascades_a_declared_successor():
    a = make_op({"a.py::a": (None, "v0")}, {"a.py::a": b"a"})
    b = make_op({"b.py::b": (None, "v0")}, {"b.py::b": b"b"})  # independent of a by chain/reference
    ops = [a, b]
    declared = frozenset({(a.id, b.id)})  # a <= b
    assert upset_in(a.id, {a.id, b.id}, ops) == {a.id}  # no edge, b survives
    assert upset_in(a.id, {a.id, b.id}, ops, declared) == {a.id, b.id}  # declared edge pulls b


def test_downset_in_walks_chain_order_through_a_revert_collision():
    sym = "a.py::foo"
    add, mod, rev = _revert_chain(sym)
    ops = [add, mod, rev]
    ideal = {add.id, mod.id, rev.id}
    assert downset_in(rev.id, ideal, ops) == ideal            # revert <- modify <- add
    assert downset_in(mod.id, ideal, ops) == {add.id, mod.id}
    assert downset_in(add.id, ideal, ops) == {add.id}


def test_components_in_links_ops_by_a_chain_edge():
    ops = _chain("a.py::foo", 2)
    comps = components_in({ops[0].id, ops[1].id}, ops)
    assert comps == [frozenset({ops[0].id, ops[1].id})]


def test_components_in_does_not_walk_through_an_op_outside_the_restricted_set():
    # a -> x -> b: x links a and b, but x is excluded from the restricted set.
    ops = _chain("a.py::foo", 3)
    a, x, b = ops
    comps = components_in({a.id, b.id}, ops)
    assert set(comps) == {frozenset({a.id}), frozenset({b.id})}


def test_components_in_ignores_an_op_whose_origin_is_outside_the_set():
    """The exact shape that crashed the old tier() implementation: a modify op is included
    without its chain head. components_in must not raise -- the modify op is simply its own
    singleton component."""
    ops = _chain("a.py::foo", 3)
    add, modify, tip = ops
    comps = components_in({modify.id, tip.id}, ops)
    assert frozenset({modify.id, tip.id}) in comps  # modify<->tip still linked directly


def test_components_in_finds_no_edge_when_the_producer_op_is_excluded():
    ops = _chain("a.py::foo", 3)
    add, modify, tip = ops
    comps = components_in({add.id, tip.id}, ops)  # modify (the link) excluded
    assert set(comps) == {frozenset({add.id}), frozenset({tip.id})}


def test_components_in_includes_declared_edges():
    a = make_op({"a.py::a": (None, "v0")}, {"a.py::a": b"a"})
    b = make_op({"b.py::b": (None, "v0")}, {"b.py::b": b"b"})  # unrelated by chain/reference
    ops = [a, b]
    declared = frozenset({(a.id, b.id)})
    assert set(components_in({a.id, b.id}, ops)) == {frozenset({a.id}), frozenset({b.id})}
    assert components_in({a.id, b.id}, ops, declared) == [frozenset({a.id, b.id})]


def test_components_in_empty_set_is_empty():
    ops = _chain("a.py::foo", 2)
    assert components_in(frozenset(), ops) == []


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
