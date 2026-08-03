"""Local agent-action feed: a bounded ring buffer of the last few tool events a `PostToolUse` hook
appends (one row per Edit/Write), so `now_view` can surface a live "what the agent just did" signal.

Deliberately unlike `sgt.intent.turns`: a *turn* is a keep-everything, content-addressed record of
human utterance (idempotent re-capture is the point). An activity *event* is the opposite -- two
edits to the same file are two genuine events, so there is no dedupe; and the feed is transient
telemetry, not evidence reflection reasons over, so it is trimmed to a fixed cap (`_MAX`) rather than
kept forever. The store is a plain ordered list, newest last.

Shares `turns.capture_lock` (the dedicated `.sgt/local/intent.lock`, not the store flock -- flock is
non-reentrant and this may run inside a verb that holds the store lock) so a burst of hook fires
from two Claude Code windows can't lose an event to a load/append/save race. Reads are lock-free.
"""

from __future__ import annotations

import time
from pathlib import Path

from sgt import state
from sgt.intent.turns import capture_lock

_ARTIFACT = "intent_activity"
_MAX = 200  # ring-buffer cap: keep the most recent this-many events, drop the oldest


def load_activity(repo: str | Path) -> list[dict]:
    """The whole feed as an ordered list (oldest first) -- empty if nothing captured yet."""
    events = state.load_json(repo, _ARTIFACT, default=[])
    return events if isinstance(events, list) else []


def record_activity(repo: str | Path, *, tool: str, file: str | None = None,
                    session_id: str | None = None, ts: float | None = None) -> dict | None:
    """Append one tool event to the feed and trim to the last `_MAX`. `seq` is a monotonic counter
    (one past the current last event's, so it survives trimming) that gives events a stable order
    even within the same `ts`. Returns the appended record, or `None` for an empty `tool` (a no-op).
    """
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


def recent_activity(repo: str | Path, limit: int = 10) -> list[dict]:
    """The most recent `limit` events, newest first -- the glanceable tail `now_view` renders."""
    events = load_activity(repo)
    return list(reversed(events[-limit:])) if limit > 0 else []
