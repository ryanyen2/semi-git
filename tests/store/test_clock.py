"""Version vector: dominance, concurrency, merge, and the SEC total-order key."""

from __future__ import annotations

from sgt.store.clock import VersionVector


def vv(**kw) -> VersionVector:
    return VersionVector(dict(kw))


def test_increment_dominates_predecessor():
    a = vv(R1=1)
    b = a.increment("R1")
    assert b.dominates(a)
    assert b.strictly_dominates(a)
    assert not a.dominates(b)


def test_concurrent_when_neither_dominates():
    a = vv(R1=1)
    b = vv(R2=1)
    assert a.concurrent(b)
    assert b.concurrent(a)
    assert not a.dominates(b)


def test_merge_is_pointwise_max_and_commutative():
    a = vv(R1=2, R2=1)
    b = vv(R1=1, R2=3, R3=1)
    m1 = a.merge(b)
    m2 = b.merge(a)
    assert m1 == m2
    assert m1.counts == {"R1": 2, "R2": 3, "R3": 1}


def test_rank_is_linear_extension_of_happens_before():
    # If b descends from a, sum(b) > sum(a): rank never places a cause after its effect.
    a = vv(R1=1, R2=2)
    b = a.increment("R2")  # b dominates a
    assert b.strictly_dominates(a)
    assert b.rank > a.rank


def test_rank_breaks_concurrent_by_effect_eid_not_vector():
    # Two concurrent vectors may share a rank; determinism comes from the eid tie-break.
    a = vv(R1=2)        # rank 2
    b = vv(R2=2)        # rank 2, concurrent with a
    assert a.concurrent(b)
    assert a.rank == b.rank  # the (replica_id, counter) of the effect id breaks this


def test_to_dict_is_sorted_for_stable_serialization():
    a = vv(R3=1, R1=1, R2=1)
    assert list(a.to_dict().keys()) == ["R1", "R2", "R3"]
    assert VersionVector.from_dict(a.to_dict()) == a
