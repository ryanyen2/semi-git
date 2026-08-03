"""Tests for sgt.intent.activity -- the local agent-action feed a `PostToolUse` hook appends to (one
row per Edit/Write). Contract is the *inverse* of turns: NO dedupe (two edits to one file are two
rows), and a fixed ring-buffer cap so the transient feed never grows unbounded."""

from __future__ import annotations

from sgt.intent import activity
from sgt.store.gitbind import init_store


def test_record_then_retrieve_round_trips(tmp_path):
    init_store(tmp_path)
    rec = activity.record_activity(tmp_path, tool="Edit", file="a.py", session_id="s1", ts=1.0)
    assert rec is not None
    got = activity.load_activity(tmp_path)
    assert len(got) == 1
    assert got[0]["tool"] == "Edit"
    assert got[0]["file"] == "a.py"
    assert got[0]["session_id"] == "s1"
    assert got[0]["seq"] == 0


def test_repeated_edits_to_one_file_are_distinct_rows(tmp_path):
    init_store(tmp_path)
    activity.record_activity(tmp_path, tool="Edit", file="a.py", ts=1.0)
    activity.record_activity(tmp_path, tool="Edit", file="a.py", ts=2.0)

    got = activity.load_activity(tmp_path)
    assert len(got) == 2  # NOT content-addressed -- identical events do not collapse
    assert [e["seq"] for e in got] == [0, 1]


def test_ring_buffer_trims_to_cap_keeping_most_recent(tmp_path):
    init_store(tmp_path)
    for i in range(activity._MAX + 5):
        activity.record_activity(tmp_path, tool="Write", file=f"f{i}.py", ts=float(i))

    got = activity.load_activity(tmp_path)
    assert len(got) == activity._MAX
    # oldest 5 dropped; the newest event is the last one recorded
    assert got[-1]["file"] == f"f{activity._MAX + 4}.py"
    assert got[0]["file"] == "f5.py"
    # seq keeps climbing past the cap so order stays stable after trims
    assert got[-1]["seq"] == activity._MAX + 4


def test_recent_activity_is_newest_first_and_limited(tmp_path):
    init_store(tmp_path)
    for i in range(5):
        activity.record_activity(tmp_path, tool="Edit", file=f"f{i}.py", ts=float(i))

    recent = activity.recent_activity(tmp_path, limit=3)
    assert [e["file"] for e in recent] == ["f4.py", "f3.py", "f2.py"]


def test_empty_tool_is_a_no_op(tmp_path):
    init_store(tmp_path)
    assert activity.record_activity(tmp_path, tool="", file="a.py", ts=1.0) is None
    assert activity.load_activity(tmp_path) == []
