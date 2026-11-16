"""`ugh intent record` / `ugh intent activity`: the hidden entry points the Claude Code hooks
`ugh init` installs pipe their JSON payload into. Capture only -- no mining, no output, exit 0 even
on empty input."""

from __future__ import annotations

import argparse

from ._common import _emit_json


def register(subs, parent) -> None:
    p = subs.add_parser("intent", parents=[parent], help=argparse.SUPPRESS)
    p.add_argument("sub", choices=("record", "activity"))
    p.set_defaults(func=_cmd_intent)


def _cmd_intent(args) -> int:
    if args.sub == "record":
        return _record(".", args.as_json)
    return _activity(".", args.as_json)


# What `UserPromptSubmit` delivers that is NOT the user's voice: the harness routes background-task
# notifications, injected reminders, and slash-command markup through the same hook, each wrapped in
# a leading tag. A turn store that records these as `actor="human"` poisons every surface that
# trusts it -- a task-notification blob reads as "working on", and any prompt->op
# alignment weighs machine chatter as the developer's own words (dogfood 2026-09-01: 137 of 294
# captured turns were `<task-notification>`s). Only a LEADING tag marks an injection; a prompt that
# merely mentions one mid-sentence is still human and is kept verbatim.
_INJECTED_WRAPPERS = ("<task-notification>", "<system-reminder>", "<command-name>",
                      "<local-command-stdout>", "<command-message>")


def _record(repo: str, as_json: bool) -> int:
    """`ugh intent record`: the zero-burden capture entry a Claude Code `UserPromptSubmit` hook
    pipes into. Reads the hook's JSON payload from stdin (`{"session_id": ..., "prompt": ...}`)
    and stores the user's prompt verbatim as a local chat-keyed turn. Silent and fast by design:
    it runs on every prompt, so no mining, no output on success, exit 0 even on empty input --
    a capture hiccup must never disturb the user's conversation."""
    import json as _json
    import sys
    from pathlib import Path

    try:
        # Only capture into a repo that already opted in (`ugh init`): the hook fires wherever
        # Claude Code runs, and materializing `.sgt/` into an arbitrary cwd is pollution, not
        # capture (testbed 2026-07-31: the hook minted `/tmp/.sgt` on a stray fire).
        if (Path(repo) / ".sgt").is_dir():
            payload = _json.loads(sys.stdin.read() or "{}")
            chat, text = payload.get("session_id"), (payload.get("prompt") or "").strip()
            if chat and text and not text.startswith(_INJECTED_WRAPPERS):
                from ugh.intent.turns import record_turn
                record_turn(repo, key=chat, key_kind="chat", actor="human", channel="hook", text=text)
    except Exception:  # noqa: BLE001 -- never break the hook chain
        pass
    if as_json:
        return _emit_json({"ok": True})
    return 0


def _activity(repo: str, as_json: bool) -> int:
    """`ugh intent activity`: the zero-burden entry a Claude Code `PostToolUse` hook pipes into on
    every Edit/Write. Reads the hook's JSON payload from stdin (`{"tool_name", "tool_input":
    {"file_path"}, "session_id"}`) and appends one row to the local activity feed. Like `_record`
    it is silent and fast -- no mining, exit 0 even on empty input -- so a capture hiccup never
    disturbs the agent's turn."""
    import json as _json
    import sys
    from pathlib import Path

    try:
        # Same opt-in guard as `_record`: only feed a repo that ran `ugh init`, never mint `.sgt/`
        # into whatever cwd the hook happened to fire in.
        if (Path(repo) / ".sgt").is_dir():
            payload = _json.loads(sys.stdin.read() or "{}")
            tool = payload.get("tool_name") or ""
            file = (payload.get("tool_input") or {}).get("file_path")
            # The hook fires with cwd = the session's repo, but the edited file can live anywhere
            # (dogfood 2026-09-01: a sibling checkout's edits landed in this repo's feed). An edit
            # outside the repo root is another repo's motion, not this one's -- skip it.
            if file is not None and not Path(file).resolve().is_relative_to(Path(repo).resolve()):
                tool = ""
            if tool:
                from ugh.intent.activity import record_activity
                record_activity(repo, tool=tool, file=file, session_id=payload.get("session_id"))
    except Exception:  # noqa: BLE001 -- never break the hook chain
        pass
    if as_json:
        return _emit_json({"ok": True})
    return 0
