"""Captured conversation turns, local to this machine and never pruned. A turn is one utterance
under a key (a chat id or a commit sha; `key_kind` says which), ordered by `seq` within the key and
content-addressed, so capturing it twice records it once. `channel` says where it came from:
`hook` is the person's verbatim prompt, `agent` an agent relaying the person's words, `note` an
agent's paraphrase, `cli` a command argument.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from ugh import state

_ARTIFACT = "intent_turns"


@contextmanager
def capture_lock(repo: str | Path):
    """Serialize a read-modify-write of the local capture tables against concurrent capture. Its own
    lock file, not the store's: capture runs inside verbs that may hold the store flock, which is
    not reentrant."""
    p = Path(repo) / ".sgt" / "local" / "intent.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load_turns(repo: str | Path) -> dict[str, dict]:
    """Every captured turn, `{turn id: record}`."""
    return state.load_json(repo, _ARTIFACT, default={})


def _turn_id(key_kind: str, key: str, actor: str, channel: str, text: str) -> str:
    """A content address over a turn's identifying fields, without `seq` and `ts`, so one utterance
    captured twice is one turn and the same text on another channel is another."""
    payload = json.dumps([key_kind, key, actor, channel, text], ensure_ascii=False)
    return "t-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_turn(repo: str | Path, *, key: str, key_kind: str, actor: str, channel: str,
                text: str, ts: float | None = None) -> str | None:
    """Record one turn under `key` and return its id, also when it was already recorded; None for an
    empty `key` or `text`. `seq` is the count of turns already under the key."""
    if not key or not text:
        return None
    with capture_lock(repo):
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


# `turns_for`'s index, store path -> ((mtime_ns, size), {key: records in seq order}), so a read per
# save does not parse the whole store each time. Writers read through `load_turns`.
_BY_KEY: dict[str, tuple[tuple[int, int], dict[str, list[dict]]]] = {}


def _turns_by_key(repo: str | Path) -> dict[str, list[dict]]:
    path = state.path(repo, _ARTIFACT)
    try:
        st = os.stat(path)
    except OSError:
        return {}
    stamp, real = (st.st_mtime_ns, st.st_size), os.path.realpath(path)
    hit = _BY_KEY.get(real)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    index: dict[str, list[dict]] = {}
    for t in load_turns(repo).values():
        index.setdefault(t["key"], []).append(t)
    for records in index.values():
        records.sort(key=lambda t: t["seq"])
    _BY_KEY[real] = (stamp, index)
    return index


def turns_for(repo: str | Path, key: str, key_kind: str | None = None) -> list[dict]:
    """Copies of every turn under `key` in `seq` order, of any kind unless `key_kind` is given."""
    return [dict(t) for t in _turns_by_key(repo).get(key, ())
            if key_kind is None or t["key_kind"] == key_kind]
