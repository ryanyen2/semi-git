"""A dependency-free MCP stdio server over ugh.

The protocol is JSON-RPC 2.0 over newline-delimited stdin/stdout (the MCP stdio transport). Only the
small surface a tool server needs is implemented -- ``initialize``, ``tools/list``, ``tools/call`` --
with ``handle_request`` a pure function that is unit-tested without a process.

Tools: ``ugh_init``, ``ugh_log``, ``ugh_check``, ``ugh_save`` (with ``as``), ``ugh_name``,
``ugh_revert``, ``ugh_restore``, ``ugh_show``, ``ugh_find``, ``ugh_plan``. Every operand is an
address (`ugh.address`) and every step in a result carries its address. Every tool mines the working
tree on contact first (R9), so it reflects whatever the agent just edited.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ugh", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Tool handlers — each takes (repo_path, arguments) and returns a JSON-able dict.
# The read tools delegate to the canonical JSON projection in ``ugh.api`` so the MCP surface
# and the CLI's ``--json`` mode return the identical schema and never drift.
# ---------------------------------------------------------------------------
def tool_init(repo_path: str, args: dict) -> dict:
    """Bind git + the kernel op store to a repo and mine its existing history. Idempotent."""
    from ugh.core.lens import init as kernel_init

    path = (args.get("path") or "").strip() or repo_path
    kernel_init(path)
    return {"ok": True, "message": f"initialized ugh kernel workspace at {path}"}


def tool_log(repo_path: str, args: dict) -> dict:
    """The saves, newest first, each with its steps (`ugh log --json`), the newest `limit` (30)."""
    from ugh.api import log_view
    from ugh.core.lens import get

    get(repo_path)
    return log_view(repo_path, limit=int(args.get("limit") or 30))


def tool_check(repo_path: str, args: dict) -> dict:
    from ugh.core.store import fsck as run_fsck

    report = run_fsck(repo_path)
    return {
        "ok": report.ok, "checked": report.checked,
        "bad_hash": list(report.bad_hash), "corrupt": list(report.corrupt),
    }


def _verb(repo_path: str, verb: str, ref: str, emit: bool, whole: bool = False) -> dict:
    """Resolve the address `ref`, plan, and apply unless `emit`."""
    from ugh.address import AddressError, resolve
    from ugh.core import planner
    from ugh.core.lens import DirtyWorkingTreeError

    try:
        steps = resolve(repo_path, ref)
    except AddressError as e:
        return {"ok": False, "verb": verb, "target": ref, "message": str(e), "hits": e.hits}
    p = planner.plan(repo_path, steps, verb=verb, target=ref, whole=whole, removal_set=emit)
    if emit or not p.ok or not p.files:
        return planner.to_json(p)
    try:
        done = planner.apply(repo_path, p)
    except DirtyWorkingTreeError as e:
        return {"ok": False, "verb": verb, "target": ref, "message": str(e)}
    return planner.to_json(done, applied=done.check != "not_run" and done.check["passed"])


def _carry_prompt(repo_path: str, args: dict, *, key: str | None = None,
                  key_kind: str = "chat") -> None:
    """Carry the user's ask into the turn store (capture weave P1, design doc 2026-09-01 §4a):
    an MCP client has no `UserPromptSubmit` hook, so the relaying agent is the only channel the
    driving prompt can arrive through. Recorded as channel `"agent"` -- an agent's claim of the
    user's verbatim words, trust-tiered below a harness capture (`hook`) and above a paraphrase
    (`note`) -- keyed by the agent's chat session id, or by whatever key the caller supplies when
    no session id was passed. Content-addressing dedupes the hook-captured twin. Guarded like
    every capture side-effect: never fail the verb it rides on."""
    try:
        prompt = (args.get("prompt") or "").strip()
        chat = (args.get("claude_session_id") or "").strip()
        if chat:
            key, key_kind = chat, "chat"
        if prompt and key:
            from ugh.intent.turns import record_turn
            record_turn(repo_path, key=key, key_kind=key_kind, actor="human", channel="agent",
                        text=prompt)
    except Exception:  # noqa: BLE001
        pass


def tool_revert(repo_path: str, args: dict) -> dict:
    """Take a piece of work out. `emit=true` previews with no write; `whole=true` takes the
    removal set."""
    from ugh.core.lens import get

    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    if not args.get("emit"):  # a preview is a read; only the applying call carries intent
        _carry_prompt(repo_path, args)
    get(repo_path)  # mine-on-contact before planning/applying the edit (R9)
    return _verb(repo_path, "revert", ref, bool(args.get("emit", False)), bool(args.get("whole", False)))


def tool_restore(repo_path: str, args: dict) -> dict:
    """Bring back what a revert took out. `emit=true` previews with no write."""
    from ugh.core.lens import get

    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    if not args.get("emit"):
        _carry_prompt(repo_path, args)
    get(repo_path)
    return _verb(repo_path, "restore", ref, bool(args.get("emit", False)))


def tool_find(repo_path: str, args: dict) -> dict:
    """Addresses whose names, save subjects, definition names or captured asks match (`ugh find`)."""
    from ugh.api import find_view
    from ugh.core.lens import get

    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "missing 'query'"}
    get(repo_path)
    return find_view(repo_path, query, limit=int(args.get("limit") or 10))


def tool_save(repo_path: str, args: dict) -> dict:
    """`ugh save`: commit the agent's edits as a save, naming its steps when `as` is given.
    Refused while a sync's two-version choice is pending. `ugh undo` reverses it."""
    from ugh.cli.porcelain import save

    # Carry the driving prompt: with a chat key, before the save so it lands in the capture window
    # this save closes; without one, after, keyed by the new commit sha.
    chat = (args.get("claude_session_id") or "").strip()
    if chat:
        _carry_prompt(repo_path, args)
    out = save(repo_path,
               message=(args.get("message") or "").strip() or None,
               as_label=(args.get("as") or "").strip() or None)
    if not chat and out.get("saved"):
        _carry_prompt(repo_path, args, key=out["commit"], key_kind="sha")
    return out


def tool_name(repo_path: str, args: dict) -> dict:
    """`ugh name <address> <label>`: give a label to the steps at an address."""
    from ugh import names
    from ugh.address import AddressError, Record, resolve
    from ugh.core.lens import get

    ref, label = (args.get("address") or "").strip(), (args.get("label") or "").strip()
    if not ref or not label:
        return {"error": "pass 'address' and 'label'"}
    get(repo_path)
    rec = Record(repo_path)
    try:
        steps = resolve(repo_path, ref, rec)
    except AddressError as e:
        return {"ok": False, "message": str(e), "hits": e.hits}
    names.name(repo_path, rec.ops(steps), label)
    rec.names = names.load(repo_path)
    return {"ok": True, "name": label, "steps": rec.steps(steps)}


def tool_show(repo_path: str, args: dict) -> dict:
    """`ugh show <address>`: its steps, the ask behind each save, and the planner's answer."""
    from ugh.api import show_view
    from ugh.core.lens import get

    ref = (args.get("address") or "").strip()
    if not ref:
        return {"error": "missing 'address'"}
    get(repo_path)
    return show_view(repo_path, ref)


def tool_plan(repo_path: str, args: dict) -> dict:
    """`ugh plan`: `declare` adds names, `done` clears the plan; no arguments is the status."""
    from ugh import names
    from ugh.api import plan_view
    from ugh.core.lens import get

    if args.get("done"):
        names.plan_done(repo_path)
        return {"ok": True, "plan": None}
    if args.get("declare"):
        names.declare(repo_path, [str(label) for label in args["declare"]])
    get(repo_path)
    return {"ok": True, "plan": plan_view(repo_path)}


# ---------------------------------------------------------------------------
# Tool registry (name -> (description, inputSchema, handler))
# ---------------------------------------------------------------------------
def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


# An MCP client has no `UserPromptSubmit` hook, so a mutating tool is the only door the user's ask
# can arrive through.
_DRIVING_PROMPT_PROP = {
    "type": "string",
    "description": "the user's ask that drove this work, verbatim as you received it (optional "
                   "but valuable): it is recorded as capture evidence, so `ugh show` can answer "
                   "with the user's own words. Pass their words, not your paraphrase.",
}
_CHAT_KEY_PROP = {
    "type": "string",
    "description": "your Claude Code session id (read $CLAUDE_CODE_SESSION_ID via Bash -- the "
                   "per-session UUID; do NOT use $CLAUDE_CODE_BRIDGE_SESSION_ID, which can carry "
                   "a parent session's id in nested runs). Keys the carried prompt to your "
                   "conversation so the work it produced can be traced back to it.",
}
_ADDRESS = ("An address is `<label>` (the steps with that name), `<label>@<rev>`, `<path>`, "
            "`<path>::<def>`, `<path>::<def>@<rev>` (one step), a `<rev>` (one save) or "
            "`<rev>..<rev>`. Anything else is refused with the nearest `ugh_find` hits.")


TOOLS: dict[str, tuple[str, dict, Any]] = {
    "ugh_init": (
        "Bind git + the kernel op store to a repo and mine its existing history. Idempotent.",
        _schema({"path": {"type": "string", "description": "repo path (defaults to the server's root)"}}, []),
        tool_init,
    ),
    "ugh_log": (
        "The saves, newest first: {saves: [{sha, subject, date, steps: [{address, kind, name, "
        "out}]}]}. `name` is the label a person or agent gave the step (null when unnamed); "
        "`out` is whether a revert took it out.",
        _schema({"limit": {"type": "integer", "description": "the newest N saves (default 30)"}}, []),
        tool_log,
    ),
    "ugh_check": (
        "Verify the op store's content-address integrity.",
        _schema({}, []),
        tool_check,
    ),
    "ugh_save": (
        "Commit your edits as a save. Pass `message`, your own words about what this work was, and "
        "`as` to name the save's steps. Returns the steps with their addresses. When the project "
        "configures a check, the save runs it on the commit and returns `observed` ({verdict, "
        "lines_recorded, delta: {added, removed} output lines against the parent's}) and each "
        "step's `ran` (did the check run any of its lines; null when it cannot say). Reversible "
        "(`ugh undo`). Refused while a sync left a definition with two versions: the error names "
        "each and the `ugh resolve` command a person runs to settle it.",
        _schema({"message": {"type": "string", "description": "what this work was, in your words"},
                 "as": {"type": "string", "description": "a name for this save's steps (optional)"},
                 "prompt": _DRIVING_PROMPT_PROP,
                 "claude_session_id": _CHAT_KEY_PROP},
                []),
        tool_save,
    ),
    "ugh_name": (
        "Give a label to the steps at an address: naming, merging into an existing name, splitting "
        "and moving are all this. A later edit carries no name unless it is saved with `as` or "
        "named again. " + _ADDRESS,
        _schema({"address": {"type": "string"}, "label": {"type": "string"}}, ["address", "label"]),
        tool_name,
    ),
    "ugh_show": (
        "The steps at an address with their names and whether they are out, the saves they came "
        "from with the user's ask behind each (`saves[].ask`, quote it rather than paraphrase) and "
        "what the check observed on each (`saves[].observed`, null when not observed), and the "
        "planner's answer (`plan`): what taking them out would take, which steps are held and why, "
        "whether the check ran each (`ran`), and the removal set. Read-only. Use it before "
        "proposing a revert. " + _ADDRESS,
        _schema({"address": {"type": "string"}}, ["address"]),
        tool_show,
    ),
    "ugh_find": (
        "Addresses whose names, save subjects, definition names or captured asks contain the "
        "words. Plain word matching. Use it when you do not already hold an address.",
        _schema({"query": {"type": "string", "description": "a few words"},
                 "limit": {"type": "integer", "description": "how many hits (default 10)"}},
                ["query"]),
        tool_find,
    ),
    "ugh_revert": (
        "Take the steps at an address out of the code as a new edit. Steps that other remaining "
        "work stands on are held, each with its reason and the saves that hold it; the project's "
        "check runs on the result before anything is committed. Each step carries `ran`: whether "
        "HEAD's observation ran any of its lines (null when it cannot say). `observed` is null when "
        "the project configures no check. Applied, it "
        "gives the output `delta`, the saves' own `saves_delta`, and `reverses`: true only when the "
        "output changed by exactly the reverse of `saves_delta`, null when those saves were not "
        "observed or never changed the output. Pass emit=true for a dry-run "
        "preview (writes nothing), whole=true to take out the removal set: the steps plus "
        "everything that stands on them. " + _ADDRESS,
        _schema({"ref": {"type": "string", "description": "an address"},
                 "emit": {"type": "boolean", "description": "dry-run preview only"},
                 "whole": {"type": "boolean", "description": "take out the removal set"},
                 "prompt": _DRIVING_PROMPT_PROP, "claude_session_id": _CHAT_KEY_PROP}, ["ref"]),
        tool_revert,
    ),
    "ugh_restore": (
        "Bring back steps a revert took out, by taking out the edit that took them out. Edits "
        "made since combine; where they rewrote the same lines nothing is written and both "
        "versions are reported. Pass emit=true for a dry-run preview. " + _ADDRESS,
        _schema({"ref": {"type": "string", "description": "an address"},
                 "emit": {"type": "boolean", "description": "dry-run preview only"},
                 "prompt": _DRIVING_PROMPT_PROP, "claude_session_id": _CHAT_KEY_PROP}, ["ref"]),
        tool_restore,
    ),
    "ugh_plan": (
        "A plan is the names you declare before their edits exist, with the baseline (HEAD when "
        "declared). `declare` adds names; save each piece of work with `ugh_save` `as` one of them. "
        "No arguments reports each declared name with the saves since the baseline that carry it "
        "(none = missing), then the steps since the baseline that carry no declared name. `done` "
        "clears the plan.",
        _schema({"declare": {"type": "array", "items": {"type": "string"}},
                 "done": {"type": "boolean"}}, []),
        tool_plan,
    ),
}


# Tools that change the ideal, mapped to the verb the editor previews. An agent
# working through these is the one case where something moves in the graph with
# nobody watching a terminal, so the editor is told before it happens.
_ANNOUNCED = {"ugh_revert": "revert", "ugh_restore": "restore"}


def _announce(repo_path: str, name: str, arguments: dict, state: str) -> None:
    """Leave a note saying what is about to happen, for any editor watching.

    Best-effort and silent: a tool call must never fail because a UI hint could
    not be written. The file is a single small JSON object rather than an
    append-only log because only the current action is ever interesting -- an
    editor that missed one has missed it.
    """
    verb = _ANNOUNCED.get(name)
    if verb is None:
        return
    try:
        import time

        path = Path(repo_path) / ".sgt" / "local"
        path.mkdir(parents=True, exist_ok=True)
        (path / "pending_action.json").write_text(
            json.dumps(
                {
                    "verb": verb,
                    "ref": str(arguments.get("ref") or ""),
                    "emit": bool(arguments.get("emit")),
                    "state": state,
                    "ts": int(time.time() * 1000),
                    "source": "agent",
                }
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def call_tool(repo_path: str, name: str, arguments: dict | None) -> dict:
    """Dispatch a single tool call to its handler. Raises KeyError on an unknown tool."""
    if name not in TOOLS:
        raise KeyError(name)
    _, _, handler = TOOLS[name]
    args = arguments or {}
    _announce(repo_path, name, args, "running")
    try:
        result = handler(repo_path, args)
    except Exception:
        _announce(repo_path, name, args, "failed")
        raise
    _announce(repo_path, name, args, "done")
    return result


def _tool_defs() -> list[dict]:
    return [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------
def _ok(mid, result) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def handle_request(repo_path: str, msg: dict) -> dict | None:
    """Handle one JSON-RPC message. Returns the response, or None for notifications."""
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        params = msg.get("params") or {}
        return _ok(mid, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method in ("notifications/initialized", "initialized"):
        return None  # notification, no response
    if method == "tools/list":
        return _ok(mid, {"tools": _tool_defs()})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        try:
            data = call_tool(repo_path, name, params.get("arguments"))
            is_error = isinstance(data, dict) and ("error" in data or data.get("ok") is False)
        except KeyError:
            data, is_error = {"error": f"unknown tool: {name}"}, True
        except Exception as ex:  # noqa: BLE001 - report any handler failure as a tool error
            data, is_error = {"error": f"{type(ex).__name__}: {ex}"}, True
        return _ok(mid, {"content": [{"type": "text", "text": json.dumps(data, separators=(",", ":"))}],
                         "isError": is_error})

    if mid is None:
        return None  # unknown notification — ignore
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(repo_path: str = ".", stdin=None, stdout=None) -> None:
    """Run the stdio server loop until EOF (newline-delimited JSON-RPC)."""
    inp = stdin or sys.stdin
    out = stdout or sys.stdout
    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue  # skip malformed frames rather than crash the session
        resp = handle_request(repo_path, msg)
        if resp is not None:
            out.write(json.dumps(resp) + "\n")
            out.flush()
