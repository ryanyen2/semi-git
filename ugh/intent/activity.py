"""Agent activity: the last `_MAX` tool events a `PostToolUse` hook appended (one per Edit or
Write), oldest first. Unlike turns, two edits to one file are two events, so nothing is deduped.
Writes hold `turns.capture_lock`, not the store lock, because a hook can fire inside a verb that
already holds the store lock."""

from __future__ import annotations

import time
from pathlib import Path

from ugh import state
from ugh.intent.turns import capture_lock

_ARTIFACT = "intent_activity"
_MAX = 200


def load_activity(repo: str | Path) -> list[dict]:
    """Every recorded event, oldest first."""
    events = state.load_json(repo, _ARTIFACT, default=[])
    return events if isinstance(events, list) else []


def record_activity(repo: str | Path, *, tool: str, file: str | None = None,
                    session_id: str | None = None, ts: float | None = None) -> dict | None:
    """Append one tool event, trim to the last `_MAX`, and return it (`None` for an empty `tool`).
    `seq` continues from the last event, so order survives trimming and equal timestamps."""
    if not tool:
        return None
    with capture_lock(repo):
        events = load_activity(repo)
        seq = events[-1]["seq"] + 1 if events else 0
        rec = {
            "seq": seq, "tool": tool, "file": file, "session_id": session_id,
            "ts": time.time() if ts is None else ts,
        }
        events.append(rec)
        if len(events) > _MAX:
            events = events[-_MAX:]
        state.save_json_if_changed(repo, _ARTIFACT, events)
    return rec
