"""Tests for ugh.core.order -- the <= relation and ideal validity (plan U4, R3/R4/R10)."""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from ugh.core.op import make_op
from ugh.core.order import frontier, is_valid_ideal


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


def test_reduce_to_ideal_is_memoized_correct_and_bounded():
    """U8: `reduce_to_ideal` is memoized by its present id set (its result is a pure function
    of that over content-addressed ops). The memo returns the identical result, agrees with a
    fresh computation, and is size-bounded so a long-running process over an append-only store never
    accumulates one entry per save."""
    from ugh.core import order

    root = make_op({"a.py::x": (None, "v0")}, {"a.py::x": b"r"})
    tip_a = make_op({"a.py::x": ("v0", "v1")}, {"a.py::x": b"a"}, provenance=("branch-a",))
    tip_b = make_op({"a.py::x": ("v0", "v2")}, {"a.py::x": b"b"}, provenance=("branch-b",))
    ops = [root, tip_a, tip_b]
    ids = {root.id, tip_a.id, tip_b.id}

    order._REDUCE_CACHE.clear()
    r1 = order.reduce_to_ideal(ids, ops)
    assert r1 == frozenset(ids)
    assert frozenset(ids) in order._REDUCE_CACHE
    r2 = order.reduce_to_ideal(ids, ops)
    assert r2 is r1                                 # the cached object, not a recomputation

    # bound: pushing more distinct keys than the cap never grows the cache past it.
    order._REDUCE_CACHE.clear()
    for i in range(order._REDUCE_CACHE_MAX + 20):
        op = make_op({f"z{i}.py::f": (None, "v0")}, {f"z{i}.py::f": b"x"})
        order.reduce_to_ideal({op.id}, [op])
    assert len(order._REDUCE_CACHE) == order._REDUCE_CACHE_MAX


def test_reduce_to_ideal_memo_is_sound_when_the_op_universe_shrinks():
    """Regression: the memo must key on the *present* ops, not the raw id set. A `git switch` can
    remove committed `.sgt/ops` while a gitignored ideal table survives, referencing ops the new ref
    no longer has. Reducing the same id set first against the full universe then against the shrunk
    one must return only the surviving ops -- never the stale full-universe result, which would then
    fail `Ideal.from_ops` against the smaller universe (the `ugh switch` invalid-ideal crash)."""
    from ugh.core import order

    sym = "a.py::foo"
    add = make_op({sym: (None, "v0")}, {sym: b"b0"}, provenance=("c0",))
    mod = make_op({sym: ("v0", "v1")}, {sym: b"b1"}, provenance=("c1",))
    ids = {add.id, mod.id}

    order._REDUCE_CACHE.clear()
    full = order.reduce_to_ideal(ids, [add, mod])
    assert full == frozenset(ids)                    # both present -> valid ideal as-is

    # Same id set, but `mod`'s op has vanished from the universe (checkout swapped the store).
    shrunk = order.reduce_to_ideal(ids, [add])
    assert shrunk == frozenset({add.id})             # NOT the cached {add, mod}
    assert is_valid_ideal([add], shrunk)             # constructible against the shrunk universe


def test_ordered_chains_walks_a_slice_that_starts_mid_chain():
    """`_ordered_chains` used to index `steps[None]` on the assumption that any set it is handed
    is downward-closed and so carries the symbol's birth. `plan_restore` breaks that on purpose:
    when the reduced source has parked a symbol's chain it widens resolution to the whole store,
    and that slice can start mid-chain -- a ghost whose birth an earlier revert excluded. That
    raised a bare `KeyError: None` out of `ugh restore`, one of the daily-spine verbs.
    """
    from ugh.core.order import _ordered_chains

    sym = "a.py::foo"
    ops = _chain(sym, 4)
    headless = [op.id for op in ops[1:]]  # the birth is gone; v0->v1 is now the earliest link

    chains = _ordered_chains(headless, ops)

    assert chains[sym] == headless, "the walk should start at the earliest link present"


def test_ordered_chains_is_unchanged_for_a_proper_downward_closed_ideal():
    sym = "a.py::foo"
    ops = _chain(sym, 4)
    from ugh.core.order import _ordered_chains

    assert _ordered_chains([op.id for op in ops], ops)[sym] == [op.id for op in ops]


def test_a_chain_branched_by_two_lines_and_joined_at_their_merge_has_one_tip():
    """Two lines change one symbol from v0; the merge gives it vM from its first parent's version
    and joins the other line's version into vM (rule 4). Whichever line the walk takes, the tip
    produces vM."""
    sym = "a.py::foo"
    root = make_op({sym: (None, "v0")}, {sym: b"0"})
    ours = make_op({sym: ("v0", "vA")}, {sym: b"a"})
    theirs = make_op({sym: ("v0", "vB")}, {sym: b"b"})
    merge = make_op({sym: ("vA", "vM")}, {sym: b"m"})
    join = make_op({sym: ("vB", "vM")}, {sym: b"m"})
    ops = [root, ours, theirs, merge, join]
    ids = {op.id for op in ops}

    assert is_valid_ideal(ops, ids)
    tip = frontier(ids, ops)[sym]
    assert {op.id: op for op in ops}[tip].footprint[sym][1] == "vM"
