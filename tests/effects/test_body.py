"""Function body as a CRDT statement sequence: the merge-quality property.

The point of statement granularity: edits to *different* statements of one function commute
(both land, no conflict), while edits to the *same* statement are a detectable conflict.
"""

from __future__ import annotations

from sgt.effects.body import StatementSeq


def _seq():
    return StatementSeq.from_source("x = 1\ny = 2\nreturn x + y", author="R0")


def test_from_source_round_trips_statements():
    seq = _seq()
    assert [s.source for s in seq.ordered()] == ["x = 1", "y = 2", "return x + y"]


def test_render_indents_and_handles_empty():
    seq = _seq()
    rendered = seq.render()
    assert rendered.splitlines() == ["    x = 1", "    y = 2", "    return x + y"]
    empty = StatementSeq()
    assert empty.render().strip() == "pass"


def test_distinct_statement_edits_commute():
    # R1 edits the first statement; R2 edits the second. Applying in either order
    # must yield the same body — the win unit-granular replace_def cannot give.
    positions = _seq().positions()
    p0, p1 = positions[0], positions[1]

    def build(order):
        seq = _seq()
        for who in order:
            if who == "R1":
                seq.replace(p0, "x = 10", "R1", 0)
            else:
                seq.replace(p1, "y = 20", "R2", 0)
        return seq.render()

    assert build(["R1", "R2"]) == build(["R2", "R1"])
    assert "x = 10" in build(["R1", "R2"]) and "y = 20" in build(["R1", "R2"])


def test_concurrent_inserts_distinct_slots_both_survive():
    seq = _seq()
    positions = seq.positions()
    # two replicas insert after the first statement, concurrently
    seq.insert(positions[0], positions[1], "z1 = 1", "R1", 5)
    seq.insert(positions[0], positions[1], "z2 = 2", "R2", 5)
    bodies = [s.source for s in seq.ordered()]
    assert "z1 = 1" in bodies and "z2 = 2" in bodies  # neither clobbers the other


def test_same_statement_edit_is_a_detectable_conflict():
    p0 = _seq().positions()[0]
    a = _seq(); a.replace(p0, "x = 111", "R1", 0)
    b = _seq(); b.replace(p0, "x = 222", "R2", 0)
    assert [p.key for p in a.conflicts(b)] == [p0.key]   # same slot, different source


def test_same_statement_replace_resolves_by_lww_deterministically():
    p0 = _seq().positions()[0]
    s = _seq()
    s.replace(p0, "x = 111", "R1", 0)
    s.replace(p0, "x = 222", "R2", 0)   # R2 > R1 by author tie-break → wins
    assert s.ordered()[0].source == "x = 222"
    # order-independent: applying the lower-priority edit second does not win
    s2 = _seq()
    s2.replace(p0, "x = 222", "R2", 0)
    s2.replace(p0, "x = 111", "R1", 0)
    assert s2.ordered()[0].source == "x = 222"


def test_remove_is_a_tombstone():
    seq = _seq()
    p1 = seq.positions()[1]
    seq.remove(p1)
    assert [s.source for s in seq.ordered()] == ["x = 1", "return x + y"]
