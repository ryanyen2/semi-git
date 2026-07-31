"""Local conversation-turn capture (intent-ledger M1, design doc
`docs/plans/2026-07-31-002-intent-ledger-architecture.md` §4.1): the raw, keep-everything
*evidence* layer under the committed prompt sidecar (`sgt.intent.prompts`).

Where `intent_prompts` stores one write-once string per provenance key -- the shareable digest a
teammate reads via `sgt.api._atom_prompt` -- a *turn* is one captured utterance in a conversation:
the user's plan text, a `--task`, a mid-session correction. Turns are keyed on the same three
provenance keys `Attribution` carries (a plan-intake session id, a `sgt session` name, or a commit
sha), so the same fallback join reaches them, but they differ in three deliberate ways:

- **Local, never committed.** Raw conversation is the fact reflection reasons over; it stays on the
  machine that captured it. Only the derived rationale (a later slice) is ever shared. So there is
  no `merge` here -- turns never travel, never collide across clones, never need a CRDT join.
- **Multi-turn and ordered.** A key accumulates many turns; `seq` records capture order within a
  key so reflection reads them in sequence. `intent_prompts` keeps only the first string per key.
- **Kept, not pruned.** A weak early reflector's failure to cite a turn must not delete it -- a
  better later reflector needs exactly the turns the current one missed. Nothing here prunes; a
  manual GC is a future, opt-in concern.

Content-addressed by (key_kind, key, actor, channel, text): capturing the identical utterance twice
under the same key is a no-op, so a retried verb or a re-run hook never double-records. `channel`
distinguishes capture provenance -- `hook` is verbatim human input, `note` is an agent's paraphrase
of the human, `cli` is a command/tool argument -- because downstream weighting must not treat an
agent's "per your request..." note as the user's own voice. `Op`/`Attribution` stay untouched: turns
attach to a plan/session, never per-op, and free conversational text has no business in the frozen,
content-addressed `Op`.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from sgt import state

_ARTIFACT = "intent_turns"


def load_turns(repo: str | Path) -> dict[str, dict]:
    """The whole local turn store, `{turn-id: record}` -- empty dict if none captured yet."""
    return state.load_json(repo, _ARTIFACT, default={})


def _turn_id(key_kind: str, key: str, actor: str, channel: str, text: str) -> str:
    """A content address over a turn's identifying fields. `seq`/`ts` are excluded so that the
    same utterance captured twice under one key collapses to a single turn (idempotent capture),
    while two genuinely different utterances -- even identical text via different channels -- stay
    distinct."""
    payload = json.dumps([key_kind, key, actor, channel, text], ensure_ascii=False)
    return "t-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_turn(repo: str | Path, *, key: str, key_kind: str, actor: str, channel: str,
                text: str, ts: float | None = None) -> str | None:
    """Append one captured turn under `key` (a plan-id, session-name, or commit sha; `key_kind`
    names which). Returns the turn id -- for a fresh capture *or* an identical prior one (capture is
    idempotent, so a caller always gets the id its text maps to) -- or `None` for an empty
    `key`/`text`, which is a deliberate no-op. `seq` is assigned as the count of turns already under
    this key, so it orders captures within a key and stays stable under idempotent re-capture."""
    if not key or not text:
        return None
    turns = load_turns(repo)
    tid = _turn_id(key_kind, key, actor, channel, text)
    if tid in turns:
        return tid
    seq = sum(1 for t in turns.values() if t["key"] == key and t["key_kind"] == key_kind)
    turns[tid] = {
        "id": tid, "key": key, "key_kind": key_kind, "seq": seq,
        "actor": actor, "channel": channel, "text": text,
        "ts": time.time() if ts is None else ts,
    }
    state.save_json_if_changed(repo, _ARTIFACT, turns)
    return tid


def turns_for(repo: str | Path, key: str, key_kind: str | None = None) -> list[dict]:
    """Every captured turn under `key`, in capture order (`seq`). Pass `key_kind` to disambiguate
    the rare case of the same string used as both, e.g. a session name and a plan id; omit it to
    take any kind. Empty list for an unknown key -- never raises."""
    hits = [
        t for t in load_turns(repo).values()
        if t["key"] == key and (key_kind is None or t["key_kind"] == key_kind)
    ]
    return sorted(hits, key=lambda t: t["seq"])
