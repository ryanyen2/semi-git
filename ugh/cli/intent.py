"""`ugh intent record` and `ugh intent activity`: the hidden entry points the Claude Code hooks pipe
their JSON payload into. They capture only: no mining, no output, exit 0 whatever the input."""

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


# The harness sends its own notifications through the same hook; a leading tag marks one.
_INJECTED_WRAPPERS = ("<task-notification>", "<system-reminder>", "<command-name>",
                      "<local-command-stdout>", "<command-message>")


def _record(repo: str, as_json: bool) -> int:
    """`ugh intent record`: read a `UserPromptSubmit` payload (`{"session_id", "prompt"}`) from
    stdin and store the prompt verbatim as a local turn keyed by the chat. It runs on every
    prompt, so it never raises and prints only under `--json`."""
    import json as _json
    import sys
    from pathlib import Path

    try:
        # the hook fires wherever Claude Code runs; capture only where `ugh init` ran
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
    """`ugh intent activity`: read a `PostToolUse` payload (`{"tool_name", "tool_input":
    {"file_path"}, "session_id"}`) from stdin and append one event to the activity feed. Like
    `_record`, it never raises and prints only under `--json`."""
    import json as _json
    import sys
    from pathlib import Path

    try:
        if (Path(repo) / ".sgt").is_dir():
            payload = _json.loads(sys.stdin.read() or "{}")
            tool = payload.get("tool_name") or ""
            file = (payload.get("tool_input") or {}).get("file_path")
            # an edit outside this repo belongs to another repo's feed
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
