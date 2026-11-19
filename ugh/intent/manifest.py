"""Per-save capture manifests: the turns and activity events of the capture window a save closed,
copied, with each new op's symbols. The turn store does not know which save a turn belongs to and
the activity feed forgets, so the join is made once, at the save. Write-once per sha. Each window
starts where the newest manifest ended, so windows never overlap. Local, like the turns.
"""

from __future__ import annotations

from pathlib import Path

from ugh import state
from ugh.intent.activity import load_activity
from ugh.intent.turns import capture_lock, load_turns

_ARTIFACT = "intent_manifests"


def load_manifests(repo: str | Path) -> dict[str, dict]:
    """Every manifest, `{commit sha: record}`."""
    return state.load_json(repo, _ARTIFACT, default={})


def record_manifest(repo: str | Path, *, sha: str, ops: list, end: float,
                    prev_save_ts: float | None = None) -> dict | None:
    """Close the capture window at `end` and store it under `sha` with the save's `ops`. The first
    window starts at `prev_save_ts`. Returns the record, the existing one for a `sha` already
    recorded, or None for an empty `sha`."""
    if not sha:
        return None
    with capture_lock(repo):
        manifests = load_manifests(repo)
        if sha in manifests:
            return manifests[sha]
        start = max([m["end"] for m in manifests.values()] + [prev_save_ts or 0.0])
        turns = sorted(
            (t for t in load_turns(repo).values() if start < t["ts"] <= end),
            key=lambda t: (t["ts"], t["seq"]),
        )
        events = [e for e in load_activity(repo) if start < e["ts"] <= end]
        record = {
            "sha": sha, "start": start, "end": end,
            "turns": turns,
            "events": events,
            "ops": [{"id": op.id, "symbols": sorted(op.footprint)} for op in ops],
        }
        manifests[sha] = record
        state.save_json_if_changed(repo, _ARTIFACT, manifests)
    return record
