"""Tests for sgt.core.ideal -- the validated Ideal wrapper (plan U4, R3)."""

from __future__ import annotations

import pytest

from sgt.core.ideal import Ideal
from sgt.core.op import BOTTOM, make_op


def _chain(sym: str, n: int):
    ops = []
    before = None
    for i in range(n):
        after = f"v{i}"
        ops.append(make_op({sym: (before, after)}, {sym: f"body{i}".encode()}))
        before = after
    return ops


def test_from_ops_accepts_a_valid_prefix():
    ops = _chain("a.py::foo", 3)
    ideal = Ideal.from_ops({ops[0].id, ops[1].id}, ops)
    assert ideal.op_ids == {ops[0].id, ops[1].id}


def test_from_ops_rejects_a_downward_closure_violation():
    ops = _chain("a.py::foo", 3)
    with pytest.raises(ValueError):
        Ideal.from_ops({ops[2].id}, ops)


def test_from_ops_rejects_a_fork():
    root = make_op({"a.py::x": (None, "v0")}, {"a.py::x": b"root"})
    tip_a = make_op({"a.py::x": ("v0", "v1")}, {"a.py::x": b"a"})
    tip_b = make_op({"a.py::x": ("v0", "v2")}, {"a.py::x": b"b"})
    ops = [root, tip_a, tip_b]
    with pytest.raises(ValueError):
        Ideal.from_ops({root.id, tip_a.id, tip_b.id}, ops)


def test_covered_paths_excludes_a_removed_symbol():
    add_op = make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"x"})
    remove_op = make_op({"a.py::foo": ("v0", BOTTOM)}, {"a.py::foo": None})
    ops = [add_op, remove_op]

    live = Ideal.from_ops({add_op.id}, ops)
    assert live.covered_paths(ops) == frozenset({"a.py"})

    removed = Ideal.from_ops({add_op.id, remove_op.id}, ops)
    assert removed.covered_paths(ops) == frozenset()


def test_diff_is_symmetric_difference():
    ops = _chain("a.py::foo", 3)
    a = Ideal.from_ops({ops[0].id}, ops)
    b = Ideal.from_ops({ops[0].id, ops[1].id}, ops)
    assert a.diff(b) == frozenset({ops[1].id})
    assert b.diff(a) == a.diff(b)  # symmetric
    assert a.diff(a) == frozenset()


def test_ideal_is_hashable_and_comparable():
    ops = _chain("a.py::foo", 2)
    a = Ideal.from_ops({ops[0].id}, ops)
    b = Ideal.from_ops({ops[0].id}, ops)
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1
