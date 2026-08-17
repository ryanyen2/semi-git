"""The restore gap report: what a revert removed that a restore leaves removed.

Unit-level, against fakes, because the behavior under test is the walk itself
and the walk was wrong twice before this test existed: a bare removed-minus-after
diff missed subtraction reverts entirely (they remove nothing, they *introduce*
splices), and the post-apply call found the restore's own journal entry instead
of the revert it was looking for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sgt.cli import ideal_edit


@dataclass
class FakePreview:
    verb: str = "restore"
    after_ids: frozenset = frozenset()


@dataclass
class FakeOp:
    id: str
    footprint: tuple = field(default_factory=tuple)


@pytest.fixture()
def fake_graph(monkeypatch):
    """A journal, an op index, and a frontier the test controls."""
    state = {"events": [], "ops": [], "tips": {}}

    from sgt.core import oplog, opindex, order

    monkeypatch.setattr(oplog, "_ref_key", lambda repo: "main")
    monkeypatch.setattr(oplog, "load", lambda repo: {"main": state["events"]})
    monkeypatch.setattr(opindex, "index_ops", lambda repo: state["ops"])
    monkeypatch.setattr(order, "frontier", lambda ideal, ops: state["tips"])
    return state


def test_dropped_ops_still_absent_are_reported(fake_graph):
    fake_graph["events"] = [
        {"kind": "ideal_edit", "ideal": ["a", "b", "drop1"], "result": ["a", "b"]},
    ]
    fake_graph["ops"] = [FakeOp("drop1", ("coursecraft/enrollment.py::drop",))]
    gap = ideal_edit._restore_gap(".", FakePreview(after_ids=frozenset({"a", "b", "x"})))
    assert gap == {
        "still_removed_op_count": 1,
        "still_removed_symbols": ["coursecraft/enrollment.py::drop"],
    }


def test_subtraction_revert_is_caught_via_its_surviving_splices(fake_graph):
    # A subtraction revert removes no op ids at all: it introduces splice ops.
    # The symbol stays subtracted exactly while a splice is still its tip.
    fake_graph["events"] = [
        {"kind": "ideal_edit", "ideal": ["a", "b"], "result": ["a", "b", "splice1"]},
    ]
    fake_graph["ops"] = [FakeOp("splice1", ("coursecraft/enrollment.py::__anchor__::drop",))]
    fake_graph["tips"] = {"coursecraft/enrollment.py::drop": "splice1"}
    gap = ideal_edit._restore_gap(".", FakePreview(after_ids=frozenset({"a", "b", "splice1", "r1"})))
    assert gap is not None
    assert gap["still_removed_op_count"] == 1
    # The layout infix is collapsed to the spelling every other report uses.
    assert gap["still_removed_symbols"] == ["coursecraft/enrollment.py::drop"]


def test_a_full_round_trip_reports_nothing(fake_graph):
    fake_graph["events"] = [
        {"kind": "ideal_edit", "ideal": ["a", "drop1"], "result": ["a"]},
    ]
    gap = ideal_edit._restore_gap(".", FakePreview(after_ids=frozenset({"a", "drop1"})))
    assert gap is None


def test_a_superseded_splice_reports_nothing(fake_graph):
    # The splice survives in the ideal but a later op moved the tip past it:
    # the symbol is live again, so there is nothing to warn about.
    fake_graph["events"] = [
        {"kind": "ideal_edit", "ideal": ["a"], "result": ["a", "splice1"]},
    ]
    fake_graph["ops"] = [FakeOp("splice1", ("f.py::__anchor__::g",))]
    fake_graph["tips"] = {"f.py::g": "restored-op"}
    gap = ideal_edit._restore_gap(".", FakePreview(after_ids=frozenset({"a", "splice1", "restored-op"})))
    assert gap is None


def test_events_with_no_delta_are_walked_past(fake_graph):
    fake_graph["events"] = [
        {"kind": "ideal_edit", "ideal": ["a", "drop1"], "result": ["a"]},
        {"kind": "ideal_edit", "ideal": ["a"], "result": ["a"]},  # e.g. a no-op entry
    ]
    fake_graph["ops"] = [FakeOp("drop1", ("f.py::g",))]
    gap = ideal_edit._restore_gap(".", FakePreview(after_ids=frozenset({"a"})))
    assert gap is not None and gap["still_removed_op_count"] == 1


def test_an_empty_journal_reports_nothing(fake_graph):
    assert ideal_edit._restore_gap(".", FakePreview(after_ids=frozenset({"a"}))) is None
