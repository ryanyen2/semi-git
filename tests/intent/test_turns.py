"""Tests for sgt.intent.turns -- the intent ledger's local, keep-everything conversation-turn
capture (M1), the raw evidence layer under the committed prompt sidecar. Covers the store's own
contract (idempotent content-addressed capture, per-key ordering, no-op guards) and its wiring into
the two live capture points `intent_prompts` already funnels through (`loop.plan.intake` and
`core.session.start`)."""

from __future__ import annotations

from sgt.intent import turns
from sgt.store.gitbind import init_store


def test_record_then_retrieve_round_trips(tmp_path):
    init_store(tmp_path)
    tid = turns.record_turn(tmp_path, key="plan-1", key_kind="plan", actor="human",
                            channel="cli", text="fix login bug", ts=1.0)
    assert tid is not None
    got = turns.turns_for(tmp_path, "plan-1")
    assert len(got) == 1
    assert got[0]["text"] == "fix login bug"
    assert got[0]["key_kind"] == "plan"
    assert got[0]["actor"] == "human"
    assert got[0]["channel"] == "cli"
    assert got[0]["seq"] == 0


def test_identical_capture_is_idempotent(tmp_path):
    init_store(tmp_path)
    first = turns.record_turn(tmp_path, key="plan-1", key_kind="plan", actor="human",
                              channel="cli", text="fix login bug", ts=1.0)
    second = turns.record_turn(tmp_path, key="plan-1", key_kind="plan", actor="human",
                               channel="cli", text="fix login bug", ts=2.0)

    assert first == second  # same content under same key -> same id, no duplicate
    got = turns.turns_for(tmp_path, "plan-1")
    assert len(got) == 1
    assert got[0]["ts"] == 1.0  # the original record is preserved, not overwritten


def test_distinct_turns_under_one_key_order_by_seq(tmp_path):
    init_store(tmp_path)
    turns.record_turn(tmp_path, key="s1", key_kind="session", actor="human",
                      channel="cli", text="add auth", ts=1.0)
    turns.record_turn(tmp_path, key="s1", key_kind="session", actor="human",
                      channel="note", text="use cookies not tokens", ts=2.0)

    got = turns.turns_for(tmp_path, "s1")
    assert [t["seq"] for t in got] == [0, 1]
    assert [t["text"] for t in got] == ["add auth", "use cookies not tokens"]


def test_same_text_different_channel_stays_distinct(tmp_path):
    init_store(tmp_path)
    turns.record_turn(tmp_path, key="s1", key_kind="session", actor="human",
                      channel="hook", text="make it faster", ts=1.0)
    turns.record_turn(tmp_path, key="s1", key_kind="session", actor="agent",
                      channel="note", text="make it faster", ts=2.0)

    got = turns.turns_for(tmp_path, "s1")
    assert len(got) == 2  # channel/actor are part of identity, so these do not collapse


def test_empty_key_or_text_is_a_no_op(tmp_path):
    init_store(tmp_path)
    assert turns.record_turn(tmp_path, key="", key_kind="plan", actor="human",
                             channel="cli", text="text", ts=1.0) is None
    assert turns.record_turn(tmp_path, key="k", key_kind="plan", actor="human",
                             channel="cli", text="", ts=1.0) is None
    assert turns.turns_for(tmp_path, "k") == []


def test_turns_for_isolates_by_key_and_kind(tmp_path):
    init_store(tmp_path)
    turns.record_turn(tmp_path, key="a", key_kind="plan", actor="human",
                      channel="cli", text="plan a", ts=1.0)
    turns.record_turn(tmp_path, key="b", key_kind="session", actor="human",
                      channel="cli", text="task b", ts=2.0)

    assert [t["text"] for t in turns.turns_for(tmp_path, "a")] == ["plan a"]
    assert turns.turns_for(tmp_path, "a", key_kind="session") == []


def test_plan_intake_records_a_turn_keyed_by_session_id(tmp_path):
    from sgt.loop import plan as plan_mod

    init_store(tmp_path)
    session = plan_mod.intake(tmp_path, "Fix the login bug across auth and session store.")

    got = turns.turns_for(tmp_path, session.session_id, key_kind="plan")
    assert len(got) == 1
    assert got[0]["text"] == "Fix the login bug across auth and session store."
    assert got[0]["actor"] == "human"


def test_session_start_with_task_records_a_turn_keyed_by_name(tmp_path):
    from sgt.core import lens
    from sgt.core import session as session_mod
    from sgt.core.lens import get

    gb, _ = init_store(tmp_path)
    (tmp_path / "main.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("init")
    ideal = get(tmp_path)
    put_sha = lens.put(tmp_path, ideal, message="sgt: init")
    lens.record_ideal(tmp_path, ideal, put_sha)

    session_mod.start(tmp_path, "s1", task="add rate limiting")

    got = turns.turns_for(tmp_path, "s1", key_kind="session")
    assert len(got) == 1
    assert got[0]["text"] == "add rate limiting"


def test_session_start_without_task_records_no_turn(tmp_path):
    from sgt.core import lens
    from sgt.core import session as session_mod
    from sgt.core.lens import get

    gb, _ = init_store(tmp_path)
    (tmp_path / "main.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("init")
    ideal = get(tmp_path)
    put_sha = lens.put(tmp_path, ideal, message="sgt: init")
    lens.record_ideal(tmp_path, ideal, put_sha)

    session_mod.start(tmp_path, "s1")

    assert turns.turns_for(tmp_path, "s1") == []
