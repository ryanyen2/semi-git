"""What the developer is working on, derived from what they already said rather than declared.

The agentic loop asks an agent to call `sgt plan intake` before working. Most work does not arrive
that way: a developer using Claude Code plans in plan mode, or with a planning plugin, or just types
a sentence -- and sgt is told nothing. The prompt hook has recorded every ask verbatim all along, so
the answer is on disk before anyone asks for it.
"""

from __future__ import annotations

import time

from sgt.intent.turns import record_turn
from sgt.intent.working import working_on


def test_the_latest_prompt_is_the_current_task(tmp_path):
    record_turn(tmp_path, key="chat-1", key_kind="chat", actor="human", channel="hook",
                text="Add rate limiting to the API", ts=100.0)
    record_turn(tmp_path, key="chat-1", key_kind="chat", actor="human", channel="hook",
                text="Actually make it back off exponentially", ts=200.0)

    out = working_on(tmp_path)

    assert out["title"] == "Actually make it back off exponentially"
    assert out["source"] == "prompt"
    assert out["ts"] == 200.0


def test_a_prompt_the_last_save_already_answered_is_not_current_work(tmp_path):
    """Otherwise a finished task sits on the surface forever, which teaches the developer to stop
    reading the line."""
    record_turn(tmp_path, key="chat-1", key_kind="chat", actor="human", channel="hook",
                text="Add rate limiting", ts=100.0)

    assert working_on(tmp_path, last_save_ts=150.0) is None
    assert working_on(tmp_path, last_save_ts=50.0) is not None


def test_a_stated_plan_step_wins_over_a_raw_prompt(tmp_path):
    """Someone took the trouble to state the step, so it is the better description of the work;
    `source` records which kind of words these are so the surface never blurs the two."""
    record_turn(tmp_path, key="chat-1", key_kind="chat", actor="human", channel="hook",
                text="do the thing", ts=100.0)

    out = working_on(tmp_path, active_plans=[{"current_title": "Extract the retry helper"}])

    assert out["title"] == "Extract the retry helper"
    assert out["source"] == "plan"


def test_no_prompt_and_no_plan_means_no_current_task(tmp_path):
    """The honest answer to "what am I working on" with nothing recorded is nothing, not a
    manufactured one."""
    assert working_on(tmp_path) is None


def test_only_human_chat_turns_count(tmp_path):
    """An agent's own paraphrase (a checkpoint `note`) is evidence, not the developer's ask."""
    record_turn(tmp_path, key="plan-1", key_kind="plan", actor="agent", channel="note",
                text="I will now refactor the parser", ts=time.time())

    assert working_on(tmp_path) is None


def test_a_long_prompt_is_elided_for_display_but_kept_whole_for_reuse(tmp_path):
    """The display title has to fit a status row; the suggested `sgt save -m "..."` has to be
    pasteable, and an ellipsis in a shell command is not."""
    ask = ("Add rate limiting to the API so that repeated calls back off exponentially "
           "instead of erroring out immediately for the caller")
    record_turn(tmp_path, key="chat-1", key_kind="chat", actor="human", channel="hook",
                text=ask, ts=100.0)

    out = working_on(tmp_path)

    assert out["title"].endswith("…") and len(out["title"]) < len(ask)
    assert out["full_title"] == ask


def test_only_the_first_line_of_a_multi_line_prompt_is_the_ask(tmp_path):
    record_turn(tmp_path, key="chat-1", key_kind="chat", actor="human", channel="hook",
                text="Add rate limiting\n\nUse a token bucket, 100/min, and log rejections.",
                ts=100.0)

    assert working_on(tmp_path)["full_title"] == "Add rate limiting"
