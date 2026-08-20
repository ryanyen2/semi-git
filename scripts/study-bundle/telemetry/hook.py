#!/usr/bin/env python3
"""Claude Code hook receiver.

One script for every hook event. It reads the hook payload on stdin, writes one
line to the local log, and exits zero no matter what. Hooks are configured with
`async: true`, so nothing here can make the assistant feel slow, and the broad
try/except is deliberate: a telemetry bug must never be able to interrupt a
participant mid-request.

The payload is stored defensively. Claude Code's per-event fields are read by
name where we know them, and anything unrecognised is kept under `extra`, so a
version that renames a field costs us a label rather than the measurement.

  usage: hook.py <HookEventName>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import client  # noqa: E402

TEXT_LIMIT = 8000
BLOB_LIMIT = 400

# Fields that are plumbing rather than data. Everything else on the payload
# ends up in `extra`.
BORING = {
    "session_id",
    "transcript_path",
    "cwd",
    "hook_event_name",
    "permission_mode",
    "prompt_id",
    "effort",
    "tool_input",
    "tool_response",
    "tool_name",
    "prompt",
}


def clip(value: object, limit: int = TEXT_LIMIT) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > limit:
        return text[:limit] + f"… [{len(text) - limit} more chars]"
    return text


def describe_tool(name: str, tool_input: dict) -> tuple[str | None, list[str]]:
    """A one-line summary of a tool call, plus the paths it touched."""
    paths: list[str] = []
    for key in ("file_path", "path", "notebook_path"):
        if isinstance(tool_input.get(key), str):
            paths.append(tool_input[key])
    for key in ("file_paths", "paths"):
        if isinstance(tool_input.get(key), list):
            paths.extend(str(p) for p in tool_input[key])

    if name in ("Bash", "BashOutput"):
        return clip(tool_input.get("command")), paths
    if name in ("Edit", "MultiEdit"):
        return clip(
            f"{paths[0] if paths else '?'} :: {clip(tool_input.get('old_string'), BLOB_LIMIT)}"
            f" -> {clip(tool_input.get('new_string'), BLOB_LIMIT)}"
        ), paths
    if name in ("Write", "NotebookEdit"):
        body = tool_input.get("content") or tool_input.get("new_source") or ""
        return clip(f"{paths[0] if paths else '?'} ({len(str(body))} chars)"), paths
    if name in ("Read", "Grep", "Glob"):
        bits = [str(tool_input.get(k)) for k in ("pattern", "file_path", "path", "glob") if tool_input.get(k)]
        return clip(" ".join(bits)), paths
    return clip(tool_input, BLOB_LIMIT * 2), paths


def main() -> int:
    event_name = sys.argv[1] if len(sys.argv) > 1 else "Unknown"

    # The setup check asks the assistant one question to prove the key works.
    # That question ran through these hooks and was recorded as a prompt the
    # participant had written -- "Reply with exactly: ok" sits in the first
    # pilot's log as one of its two prompts. Before a session starts it only
    # pads the raw stream, but the session shell offers `study-doctor` for
    # re-running the checks, and a re-run mid-session would drop a fake prompt
    # inside a request window, where prompt count, prompt length and prompt
    # specificity are all measured.
    if os.environ.get("STUDY_NO_LOG"):
        return 0

    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""

    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {"unparsed": clip(raw, BLOB_LIMIT)}

    try:
        session_id = payload.get("session_id")
        extra = {k: v for k, v in payload.items() if k not in BORING}
        extra["hookEvent"] = event_name

        if event_name == "UserPromptSubmit":
            prompt = payload.get("prompt") or payload.get("user_prompt") or payload.get("message")
            client.append(
                "prompt",
                name="user",
                text=clip(prompt),
                sessionId=session_id,
                extra=extra or None,
            )

        elif event_name in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            tool_name = payload.get("tool_name") or "?"
            tool_input = payload.get("tool_input") or {}
            if not isinstance(tool_input, dict):
                tool_input = {"value": tool_input}
            text, paths = describe_tool(tool_name, tool_input)
            # Only the Pre event carries the full command, so Post events are
            # recorded as outcomes rather than as a second copy of the call.
            ok = None
            if event_name == "PostToolUse":
                ok = True
            elif event_name == "PostToolUseFailure":
                ok = False
            client.append(
                "tool",
                name=tool_name,
                text=text if event_name == "PreToolUse" else None,
                paths=paths or None,
                ok=ok,
                sessionId=session_id,
                extra={**extra, "phase": event_name} or None,
            )

        else:
            client.append(
                "session",
                name=event_name,
                sessionId=session_id,
                extra=extra or None,
            )

        # The end of a turn is a natural, unhurried moment to push. Detached, so
        # a slow network cannot hold the assistant's prompt back.
        #
        # Called through the interpreter that is already running rather than
        # through the shell wrapper in bin/, because that only depends on the
        # file existing and not on its permission bit surviving however the
        # bundle was unpacked.
        if event_name in ("Stop", "SessionEnd"):
            sync = Path(__file__).resolve().parent / "sync.py"
            if sync.exists():
                subprocess.Popen(
                    [sys.executable, str(sync), "--quiet"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
    except Exception as exc:  # pragma: no cover
        try:
            (client.telemetry_dir() / "hook-errors.log").open("a").write(
                f"{event_name}: {exc}\n"
            )
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
