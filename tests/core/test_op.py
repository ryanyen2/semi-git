"""Tests for sgt.core.op -- structured attribution and its ACI merge (plan U22, D7).

The load-bearing property: attribution (like provenance and intent) is excluded from the content
address, so enriching an op with who/what produced it never mints a new id. `merge_attribution`
is the LAW-U reconciler for that structured provenance -- it must be associative, commutative, and
idempotent, and deterministic on a field conflict.
"""

from __future__ import annotations

from sgt.core.op import Attribution, make_op, merge_attribution


def test_attribution_is_excluded_from_the_id():
    """D7: an op carrying full structured attribution hashes to the same id as the bare op --
    provenance *and* attribution are both outside `compute_id`."""
    bare = make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"body"})
    attributed = make_op(
        {"a.py::foo": (None, "v0")}, {"a.py::foo": b"body"},
        provenance=("sha1",),
        attribution=(Attribution(sha="sha1", session="s1", agent="claude", plan="p1"),),
    )
    assert bare.id == attributed.id


def test_merge_attribution_is_commutative_and_idempotent():
    a = (Attribution(sha="s1", session="alice"),)
    b = (Attribution(sha="s1", agent="claude"), Attribution(sha="s2", plan="p1"))

    assert merge_attribution(a, b) == merge_attribution(b, a)  # commutative
    assert merge_attribution(a, a) == a  # idempotent (a is already normalized)
    assert merge_attribution(a, ()) == a  # unit


def test_merge_attribution_is_associative():
    a = (Attribution(sha="s1", session="alice"),)
    b = (Attribution(sha="s1", agent="claude"),)
    c = (Attribution(sha="s1", plan="p1"), Attribution(sha="s2", session="bob"))

    left = merge_attribution(merge_attribution(a, b), c)
    right = merge_attribution(a, merge_attribution(b, c))
    assert left == right
    # all three fields for s1 fused, plus s2's own entry
    assert left == (
        Attribution(sha="s1", session="alice", agent="claude", plan="p1"),
        Attribution(sha="s2", session="bob"),
    )


def test_merge_attribution_picks_min_on_a_field_conflict():
    """Two non-None, differing values for the same field converge on `min` -- order-independently,
    so any merge schedule lands the same result (LAW-U)."""
    x = (Attribution(sha="s1", session="zoe"),)
    y = (Attribution(sha="s1", session="amy"),)
    assert merge_attribution(x, y) == (Attribution(sha="s1", session="amy"),)
    assert merge_attribution(y, x) == (Attribution(sha="s1", session="amy"),)


def test_merge_attribution_drops_all_none_entries_and_sorts():
    a = (Attribution(sha="s2"), Attribution(sha="s1", session="a"))  # s2 all-None
    assert merge_attribution(a, ()) == (Attribution(sha="s1", session="a"),)


def test_resolves_is_excluded_from_the_id():
    """D5: an op carrying structured resolution provenance hashes to the same id as the bare op --
    `resolves` is outside `compute_id`, same as `intent`/`provenance`/`attribution`."""
    bare = make_op({"a.py::foo": (None, "v0")}, {"a.py::foo": b"body"})
    resolved = make_op(
        {"a.py::foo": (None, "v0")}, {"a.py::foo": b"body"},
        resolves=frozenset({"tip-a", "tip-b"}),
    )
    assert bare.id == resolved.id
    assert resolved.resolves == frozenset({"tip-a", "tip-b"})
