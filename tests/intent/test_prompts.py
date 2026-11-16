"""Tests for ugh.intent.prompts -- the intent overlay's live prompt capture sidecar (U3/KTD5):
write-once by key, a committed G-Set-by-key merge, and its wiring into `loop.plan.intake` and
`core.session.start` (the two proven capture points; MCP's `tool_plan_intake` funnels through
`loop.plan.intake` and so is covered transitively, with no separate wiring needed)."""

from __future__ import annotations

from ugh.intent import prompts
from ugh.store.gitbind import init_store


def test_record_then_retrieve_round_trips(tmp_path):
    init_store(tmp_path)
    assert prompts.record_prompt(tmp_path, "plan-1", "fix login bug")
    assert prompts.prompt_for(tmp_path, "plan-1") == "fix login bug"


def test_second_write_on_existing_key_is_a_no_op(tmp_path):
    init_store(tmp_path)
    prompts.record_prompt(tmp_path, "plan-1", "fix login bug")

    changed = prompts.record_prompt(tmp_path, "plan-1", "something else entirely")

    assert changed is False
    assert prompts.prompt_for(tmp_path, "plan-1") == "fix login bug"


def test_missing_key_returns_none(tmp_path):
    init_store(tmp_path)
    assert prompts.prompt_for(tmp_path, "no-such-key") is None


def test_empty_key_or_text_is_a_no_op(tmp_path):
    init_store(tmp_path)
    assert prompts.record_prompt(tmp_path, "", "text") is False
    assert prompts.record_prompt(tmp_path, "k", "") is False
    assert prompts.prompt_for(tmp_path, "k") is None


def test_merge_unions_distinct_keys(tmp_path):
    ours = {"a": "prompt a"}
    theirs = {"b": "prompt b"}
    merged = prompts.merge(ours, theirs)
    assert merged == {"a": "prompt a", "b": "prompt b"}


def test_merge_same_key_both_sides_is_deterministic_no_crash(tmp_path):
    ours = {"a": "prompt one"}
    theirs = {"a": "prompt two"}
    merged_1 = prompts.merge(ours, theirs)
    merged_2 = prompts.merge(theirs, ours)
    assert merged_1 == merged_2  # symmetric, no duplicate/crash on a genuine same-key collision


def test_plan_intake_records_a_retrievable_prompt_keyed_by_session_id(tmp_path):
    from ugh.loop import plan as plan_mod

    init_store(tmp_path)
    session = plan_mod.intake(tmp_path, "Fix the login bug across auth and session store.")

    assert prompts.prompt_for(tmp_path, session.session_id) == (
        "Fix the login bug across auth and session store."
    )
