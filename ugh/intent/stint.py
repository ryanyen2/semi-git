"""Stints: which captured ask grounds which ops of a save, derived from the capture manifests and
never stored. A stint is one turn, the activity events its session produced while that turn was the
session's latest, and the ops of the save whose files those events touched. Ownership is per
session, looked up across every manifest, so one prompt can ground several saves. A turn with no
events is no stint, and an op no event grounds is residual, never given to the nearest prompt.
`asks_for_ops` is the read side, and `dominant_ask` picks the ask a single line quotes.
"""

from __future__ import annotations

from bisect import bisect_right
from pathlib import Path


def _rel_file(file: str | None, root: Path) -> str | None:
    """An event's file as a repo-relative path, or `None` for no file or one outside the repo. A
    relative path is taken as already repo-relative."""
    if not file:
        return None
    p = Path(file)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except ValueError:
        return None


def _op_files(op_entry: dict) -> frozenset[str]:
    """The repo-relative files a manifest op anchor touches -- its footprint symbols' path halves
    (a whole-file pseudo-symbol is its own path already)."""
    return frozenset(sym.split("::", 1)[0] for sym in op_entry["symbols"])


def derive_stints(manifests: dict[str, dict], sha: str, root: str | Path) -> dict:
    """The stints of the save `sha`, from the manifests alone: `{"stints": [{"turn", "events",
    "files", "op_ids"}], "residual_op_ids"}`, stints in turn order. Empty when `sha` has no
    manifest."""
    target = manifests.get(sha)
    if target is None:
        return {"stints": [], "residual_op_ids": []}
    root = Path(root)

    # every chat turn up to this window's close, per session, in time order
    by_session: dict[str, list[dict]] = {}
    for m in manifests.values():
        if m["end"] > target["end"]:
            continue
        for t in m["turns"]:
            if t["key_kind"] == "chat":
                by_session.setdefault(t["key"], []).append(t)
    for turns in by_session.values():
        turns.sort(key=lambda t: (t["ts"], t["seq"]))

    # Own each of the target window's events: the session's latest turn at or before it.
    owned: dict[str, list[dict]] = {}  # turn id -> events
    for e in target["events"]:
        session = e.get("session_id")
        turns = by_session.get(session) if session else None
        if not turns:
            continue
        i = bisect_right([t["ts"] for t in turns], e["ts"])
        if i == 0:
            continue  # the session's first word came after this event: nothing owns it
        owned.setdefault(turns[i - 1]["id"], []).append(e)

    turn_by_id = {t["id"]: t for turns in by_session.values() for t in turns}
    claimed: set[str] = set()
    stints = []
    for tid, events in owned.items():
        files = frozenset(f for e in events if (f := _rel_file(e.get("file"), root)))
        op_ids = [o["id"] for o in target["ops"] if _op_files(o) & files]
        claimed.update(op_ids)
        stints.append({"turn": turn_by_id[tid], "events": events,
                       "files": sorted(files), "op_ids": op_ids})
    stints.sort(key=lambda s: (s["turn"]["ts"], s["turn"]["seq"]))
    residual = [o["id"] for o in target["ops"] if o["id"] not in claimed]
    return {"stints": stints, "residual_op_ids": residual}


def whole_save_turns(repo: str | Path, target: dict | None, sha: str) -> list[dict]:
    """The turns that claim the whole save `sha`: a sha-keyed turn (`save -m`, or a prompt an MCP
    save carried without a chat id), read from the turn store since it can land after the window
    closed, and an `agent`-channel chat turn in the window, since an MCP client without hooks has no
    events to ground it. A hook turn needs events, so it is never here."""
    from ugh.intent.turns import turns_for

    wide = list(turns_for(repo, sha, key_kind="sha"))
    if target:
        wide += [t for t in target["turns"] if t["key_kind"] == "chat" and t["channel"] == "agent"]
    return wide


# Where a captured ask came from, by channel (`ugh.intent.turns`), in words.
_SOURCE = {
    "hook": "you, in a Claude Code chat",
    "agent": "you, relayed by the assistant",
    "note": "the assistant's note",
    "cli": "your save message",
    "sidecar": "a recorded prompt",
}


def resumable(session_id: str | None) -> bool:
    """Whether `claude --resume <session_id>` would reopen anything: the session has a transcript
    under `CLAUDE_CONFIG_DIR` (default `~/.claude`). A surface offers the command only then."""
    if not session_id:
        return False
    import os

    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude")) / "projects"
    try:
        return any(root.glob(f"*/{session_id}.jsonl"))
    except OSError:
        return False


def ask_record(text: str, *, channel: str, actor: str = "human", ts: float | None = None,
               session: str | None = None, claimed: int = 0, scope: str = "stint") -> dict:
    """One captured ask as every surface renders it: `gist` the excerpt for one line, `trimmed`
    whether the prompt held more, `chars` its length, `source` who typed it, in words, and `text`
    the prompt verbatim."""
    from ugh.intent.gist import CARD_WIDTH, ask_parts

    parts = ask_parts(text, CARD_WIDTH)
    out = {"gist": parts.gist, "trimmed": parts.trimmed, "chars": len(text or ""),
           "channel": channel, "source": _SOURCE.get(channel, channel), "actor": actor,
           "ts": ts, "claude_session_id": session, "resumable": resumable(session),
           "claimed": claimed, "scope": scope}
    out["text"] = text
    return out


def asks_for_ops(repo: str | Path, op_ids, shas) -> list[dict]:
    """Every captured ask that grounds any of `op_ids` in the saves `shas`, in conversation order.
    Each carries `claimed`, how many of `op_ids` it grounds, and `scope`: "stint" for words grounded
    by events, "save" for a claim about the whole save. A save with no manifest contributes only its
    recorded prompt."""
    from ugh.intent.manifest import load_manifests
    from ugh.intent.prompts import prompt_for

    wanted = frozenset(op_ids)
    manifests = load_manifests(repo)
    order: list[str] = []
    by_key: dict[str, dict] = {}

    def add(key: str, *, text: str, channel: str, actor: str, ts: float | None,
            session: str | None, claimed: int, scope: str) -> None:
        """One ask per turn, its claim summed across the saves it reached."""
        if not (text or "").strip():
            return
        if key in by_key:
            by_key[key]["claimed"] += claimed
            return
        by_key[key] = ask_record(text, channel=channel, actor=actor, ts=ts, session=session,
                                 claimed=claimed, scope=scope)
        order.append(key)

    for sha in shas:
        target = manifests.get(sha)
        if target is not None:
            for st in derive_stints(manifests, sha, root=repo)["stints"]:
                claimed = len(wanted & frozenset(st["op_ids"]))
                if claimed:
                    t = st["turn"]
                    add(t["id"], text=t["text"], channel=t["channel"], actor=t["actor"],
                        ts=t["ts"], session=t["key"], claimed=claimed, scope="stint")
        save_ops = frozenset(o["id"] for o in target["ops"]) if target else frozenset()
        for t in whole_save_turns(repo, target, sha):
            add(t["id"], text=t["text"], channel=t["channel"], actor=t["actor"], ts=t["ts"],
                session=t["key"] if t["key_kind"] == "chat" else None,
                claimed=len(wanted & save_ops), scope="save")
        digest = prompt_for(repo, sha)
        # a recorded prompt that repeats a listed ask is the same words, not a second ask
        if digest and digest not in {a["text"] for a in by_key.values()}:
            add(f"sidecar:{sha}", text=digest, channel="sidecar", actor="human", ts=None,
                session=None, claimed=len(wanted & save_ops), scope="save")

    return [by_key[k] for k in order]


def dominant_ask(asks: list[dict]) -> dict | None:
    """The ask to put on a single line: the one grounding the most of the selection, the latest on
    a tie, since a correction is the standing word. `None` when there is none."""
    if not asks:
        return None
    return max(asks, key=lambda a: (a["claimed"], a["ts"] or 0.0))
