"""What the developer is working on right now, derived rather than declared.

The agentic loop (`sgt plan intake`) asks an agent to *declare* its plan before working, and when it
does, the graph gets a precise, step-by-step account. But that is not how most work arrives. A
developer using Claude Code plans in plan mode, or with a planning plugin, or just types a sentence
and lets the agent go -- and in every one of those cases sgt is told nothing, so the surface that
exists to answer "what am I working on" answers with op counts.

It does not have to. The `UserPromptSubmit` hook already records every prompt verbatim
(`sgt.intent.turns`, `key_kind="chat"`), which means the answer is on disk before anyone asks: the
developer said what they wanted, in their own words, at a known time. The last thing they asked for
that has not been saved yet IS the current task, and no declaration step is needed to know it.

So this module reads, never writes, and never guesses beyond what was actually said. When a plan
session *is* active its curated step title wins (someone took the trouble to state it); otherwise
the latest unsaved prompt stands in. When neither exists there is no current task, and the honest
answer is nothing at all rather than a manufactured one.
"""

from __future__ import annotations

from pathlib import Path

# How much of the ask a status row can hold before it has to be elided.
_MAX_TITLE = 72

# How long a prompt stays "current" once there is nothing unsaved to attribute to it. With work in
# the tree the prompt is plainly still live, whatever the clock says; with a clean tree it is only
# still live if it was just asked and the agent has not produced anything yet. Without this,
# `sgt now` reported "working on X (9h ago)" directly above "next: nothing pending" -- two lines
# that contradict each other, which teaches the developer that the first one cannot be trusted.
_IDLE_PROMPT_SECONDS = 1800


def _shorten(text: str) -> str:
    """The ask inside `text`, trimmed to what a status row can hold (`sgt.intent.gist`)."""
    from sgt.intent.gist import ask_gist

    return ask_gist(text, _MAX_TITLE)


def _whole_ask(text: str) -> str:
    """The same ask, unclipped -- what a suggested `sgt save -m "..."` pastes. An ellipsis in a
    command is not pasteable, and the raw prompt is a paragraph, so this is the ask clause whole."""
    from sgt.intent.gist import ask_gist

    return ask_gist(text, 10_000)


def latest_prompt(repo: str | Path, *, since_ts: float | None = None,
                  turns: list[dict] | None = None) -> dict | None:
    """The most recent human prompt, optionally only if it arrived after `since_ts`.

    `since_ts` is the last save's time: a prompt older than the last save has already been answered
    by that save, so treating it as current work would leave a finished task on screen forever.

    `turns` accepts an already-loaded list, newest first, so a caller that is reading the turn store
    anyway does not pay for a second parse. That store keeps every prompt ever typed and is never
    pruned, so re-reading it is a cost that grows with the repo's whole conversation history.
    """
    if turns is None:
        from sgt.intent import turns as turns_mod
        turns = sorted(turns_mod.load_turns(repo).values(),
                       key=lambda t: t.get("ts", 0), reverse=True)
    return next(
        (t for t in turns
         if t.get("key_kind") == "chat" and t.get("actor") == "human"
         and (since_ts is None or t.get("ts", 0) > since_ts)),
        None,
    )


def working_on(repo: str | Path, *, active_plans: list[dict] | None = None,
               last_save_ts: float | None = None, has_unsaved: bool = True,
               now: float | None = None, turns: list[dict] | None = None) -> dict | None:
    """The current task: `{title, source, ts, session_id}`, or `None` when nothing is in progress.

    `source` says where the words came from, because a developer should be able to tell at a glance
    whether sgt is repeating their own sentence back (`prompt`) or a plan's curated step (`plan`) --
    the two carry very different amounts of deliberation, and blurring them would make the surface
    feel like it knows more than it does.
    """
    # `title` is for display and may be elided; `full_title` is the untruncated line, because a
    # suggested `sgt save -m "..."` has to be pasteable and an ellipsis in it is not.
    for plan in active_plans or []:
        title = plan.get("current_title")
        if title:
            return {"title": _shorten(title), "full_title": _whole_ask(title),
                    "source": "plan", "ts": None,
                    "session_id": plan.get("claude_session_id")}
    prompt = latest_prompt(repo, since_ts=last_save_ts, turns=turns)
    if prompt is None:
        return None
    if not has_unsaved:
        # Nothing in the tree to attribute to it, so this is only still the current task if it was
        # just asked. An old prompt with a clean tree was either answered or abandoned, and either
        # way saying "working on" is a claim the rest of the surface contradicts.
        import time as _t

        age = (now if now is not None else _t.time()) - (prompt.get("ts") or 0)
        if age > _IDLE_PROMPT_SECONDS:
            return None
    return {"title": _shorten(prompt["text"]), "full_title": _whole_ask(prompt["text"]),
            "source": "prompt", "ts": prompt.get("ts"), "session_id": prompt.get("key")}
