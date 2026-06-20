"""Tree-CRDT statement identity: density, concurrent-insert determinism, body ordering."""

from __future__ import annotations

from sgt.effects.stmt import Body, PosId, between, from_eid, sorted_positions


def test_between_is_strictly_ordered():
    a = between(None, None, "R1", 0)          # first ever
    b = between(a, None, "R1", 1)             # after a
    mid = between(a, b, "R1", 2)              # strictly between
    assert a < mid < b


def test_dense_repeated_insertion_between_neighbours_never_collides():
    lo = between(None, None, "R1", 0)
    hi = between(lo, None, "R1", 1)
    seen = {lo.key, hi.key}
    left = lo
    for n in range(200):  # hammer the same gap; allocator must keep finding room
        p = between(left, hi, "R1", 10 + n)
        assert lo < p < hi
        assert p.key not in seen
        seen.add(p.key)
        left = p


def test_concurrent_inserts_same_gap_distinct_and_deterministic():
    lo = between(None, None, "R0", 0)
    hi = between(lo, None, "R0", 1)
    # two replicas insert "at the same place" with no coordination
    p_r1 = between(lo, hi, "R1", 5)
    p_r2 = between(lo, hi, "R2", 5)
    assert p_r1.digits == p_r2.digits   # same slot...
    assert p_r1 != p_r2                  # ...but distinct identities
    # deterministic order on every replica (author tie-break)
    assert sorted_positions([p_r2, p_r1]) == sorted_positions([p_r1, p_r2])
    assert (p_r1 < p_r2) == ("R1" < "R2")


def test_from_eid_uses_effect_identity_as_tiebreak():
    lo = between(None, None, "R0", 0)
    p = from_eid(lo, None, "abc:7")
    assert p.author == "abc" and p.counter == 7


def test_posid_round_trips():
    p = between(None, None, "R1", 3)
    assert PosId.from_dict(p.to_dict()) == p


def test_body_insert_orders_payloads():
    b = Body()
    b.insert(0, "a", "R1", 0)
    b.insert(1, "c", "R1", 1)
    b.insert(1, "b", "R1", 2)   # between a and c
    assert b.payloads() == ["a", "b", "c"]


def test_body_concurrent_inserts_converge_regardless_of_apply_order():
    # Same two inserts applied in opposite orders must yield the same final sequence.
    def build(order):
        b = Body()
        pa = b.insert(0, "base", "R0", 0)
        slots = {"base": pa}
        for who, ctr, label, lo_key, hi_key in order:
            lo = slots.get(lo_key)
            hi = slots.get(hi_key)
            pid = between(lo, hi, who, ctr)
            b.slots.append((pid, label))
            slots[label] = pid
        return b.payloads()

    ins = [("R1", 1, "x", "base", None), ("R2", 1, "y", "base", None)]
    assert build(ins) == build(list(reversed(ins)))
