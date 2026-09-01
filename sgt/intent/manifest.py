"""Per-save capture manifests (capture weave P1, docs/design/2026-09-01-capture-weave.md §4b):
the durable, per-commit record of the capture window a save closed.

The turn store keeps every utterance forever and the activity feed keeps the last 200 tool events,
but neither knows which *save* a captured moment belongs to -- and the feed forgets. A manifest is
that join, made once, at the only moment it is cheap: the save beat, when the window since the
previous save is small and the ops just minted are in hand. Each record carries copies of the
window's turns and events (self-sufficient by design -- the context pack must survive the source
transcript being compacted or a future turn GC) plus each new op's footprint symbols, the
(sha, footprint) anchor that survives a re-mine where a bare op-id would not.

Write-once per sha, like `intent_prompts`: a save closes its window exactly once, and a retried
verb must not harvest a second, different window under the same key. Windows chain without overlap
-- each starts where the newest existing manifest ended (falling back to the caller-supplied
previous-save time, so the first manifest ever does not swallow the whole pre-weave turn history)
-- which is what lets the stint derivation (P2) treat "the turn is in an earlier manifest and its
session has not spoken since" as an *open* stint rather than re-harvesting it.

Local tier forever, same reasoning as `intent_turns`: raw conversation stays on the machine that
captured it. Shares `turns.capture_lock` -- harvest is a read-modify-write racing the very hooks
it reads from.

Known bound, accepted for P1: a window with more than the activity feed's cap of events has
already lost the oldest ones by harvest time; the manifest records what survived, and the ops
those events would have grounded fall to the residual stint -- diminished and honest, never
misattributed.
"""

from __future__ import annotations

from pathlib import Path

from sgt import state
from sgt.intent.activity import load_activity
from sgt.intent.turns import capture_lock, load_turns

_ARTIFACT = "intent_manifests"


def load_manifests(repo: str | Path) -> dict[str, dict]:
    """The whole manifest store, `{commit-sha: record}` -- empty dict if no save has harvested."""
    return state.load_json(repo, _ARTIFACT, default={})


def record_manifest(repo: str | Path, *, sha: str, ops: list, end: float,
                    prev_save_ts: float | None = None) -> dict | None:
    """Close the capture window at `end` (the save beat) and persist it under `sha`.

    `ops` are the save's newly-minted `Op`s (footprints read, ids kept as a convenience); `end` is
    the moment the save happened, taken by the caller so the window edge and the witness commit
    agree on when "now" was. `prev_save_ts` seeds the very first window's start -- the previous
    witness commit's committer time -- after which the chain is self-sustaining. Returns the record,
    the existing one for an already-harvested `sha` (write-once), or `None` for an empty `sha` --
    a deliberate no-op, mirroring `record_turn`.
    """
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
