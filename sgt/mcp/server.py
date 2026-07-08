"""A dependency-free MCP stdio server exposing the operation-ideal kernel (plan U7/U8/U9, flipped
onto MCP in U10).

The protocol is JSON-RPC 2.0 over newline-delimited stdin/stdout (the MCP stdio transport).
We implement only the small surface a tool server needs — ``initialize``, ``tools/list``,
``tools/call`` — with no third-party dependency, matching the project's minimal footprint and
keeping the dispatch (``handle_request``) a pure function that is unit-tested without a process.

Tool surface — kernel parity with the CLI's registered verbs:

* **read** (no API key, no writes): ``sgt_log`` (the mined op DAG), ``sgt_state`` (the current
  ref's ideal: frontier, coverage, oracle verdict), ``sgt_diff`` (semantic diff between two refs'
  ideals), ``sgt_fsck`` (op-store integrity).
* **write**: ``sgt_init`` (bind git + the kernel store, mine existing history), ``sgt_revert`` /
  ``sgt_restore`` (exact ideal edits, `I \\ ↑X` / `I ∪ ↓X`, with an `emit` dry-run preview),
  ``sgt_oracle_run`` (execute configured build/test tiers against the current ideal).

Every write tool mines the working tree on contact first (R9), so it reflects whatever the agent
just edited. The feature-lens and agentic-loop tools (plan/checkpoint/merge/split/decisions) have
no kernel backing yet (P3/P4) and are not registered here.
"""

from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "semi-git", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Tool handlers — each takes (repo_path, arguments) and returns a JSON-able dict.
# The read tools delegate to the canonical JSON projection in ``sgt.api`` so the MCP surface
# and the CLI's ``--json`` mode return the identical schema and never drift.
# ---------------------------------------------------------------------------
def tool_init(repo_path: str, args: dict) -> dict:
    """Bind git + the kernel op store to a repo and mine its existing history. Idempotent."""
    from sgt.core.lens import init as kernel_init

    path = (args.get("path") or "").strip() or repo_path
    kernel_init(path)
    return {"ok": True, "message": f"initialized sgt kernel workspace at {path}"}


def tool_log(repo_path: str, args: dict) -> dict:
    from sgt.api import oplog_view
    from sgt.core.lens import get

    get(repo_path)  # mine-on-contact before inspecting the store
    return oplog_view(repo_path)


def tool_state(repo_path: str, args: dict) -> dict:
    from sgt.api import state_view
    from sgt.core.lens import get

    get(repo_path)
    return state_view(repo_path)


def tool_diff(repo_path: str, args: dict) -> dict:
    from sgt.api import ideal_diff_view
    from sgt.core.lens import get

    ref_a = (args.get("ref_a") or "").strip()
    ref_b = (args.get("ref_b") or "").strip()
    if not ref_a or not ref_b:
        return {"error": "missing 'ref_a'/'ref_b'"}
    get(repo_path)
    return ideal_diff_view(repo_path, ref_a, ref_b)


def tool_fsck(repo_path: str, args: dict) -> dict:
    from sgt.core.store import fsck as run_fsck

    report = run_fsck(repo_path)
    return {
        "ok": report.ok, "checked": report.checked,
        "bad_hash": list(report.bad_hash), "corrupt": list(report.corrupt),
    }


def _verb_result(preview) -> dict:
    return {
        "ok": preview.ok, "verb": preview.verb, "target": preview.target,
        "removed": sorted(preview.removed), "added": sorted(preview.added),
        "affected_symbols": list(preview.affected_symbols), "forked": preview.forked,
        "message": preview.message,
    }


def tool_revert(repo_path: str, args: dict) -> dict:
    """`I \\ ↑X`: remove an op and everything built on it. `emit=true` previews with no write."""
    from sgt.core import verbs
    from sgt.core.lens import get

    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    get(repo_path)  # mine-on-contact before planning/applying the edit (R9)
    return _verb_result(verbs.revert(repo_path, ref, emit=bool(args.get("emit", False))))


def tool_restore(repo_path: str, args: dict) -> dict:
    """`I ∪ ↓X`: revert's inverse. `emit=true` previews with no write."""
    from sgt.core import verbs
    from sgt.core.lens import get

    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    get(repo_path)
    return _verb_result(verbs.restore(repo_path, ref, emit=bool(args.get("emit", False))))


def tool_oracle_run(repo_path: str, args: dict) -> dict:
    """Run configured build/test tiers against the current ideal (plan U9, R13)."""
    from sgt.core import oracle
    from sgt.core.lens import get

    get(repo_path)
    tier = (args.get("tier") or "").strip() or None
    try:
        return oracle.run(repo_path, tier=tier)
    except ValueError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool registry (name -> (description, inputSchema, handler))
# ---------------------------------------------------------------------------
def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS: dict[str, tuple[str, dict, Any]] = {
    "sgt_init": (
        "Bind git + the kernel op store to a repo and mine its existing history. Idempotent.",
        _schema({"path": {"type": "string", "description": "repo path (defaults to the server's root)"}}, []),
        tool_init,
    ),
    "sgt_log": (
        "Read the mined operation DAG: every op's id, derived kind, footprint (each symbol's "
        "before->after version), witnessing-commit provenance, and intent if any.",
        _schema({}, []),
        tool_log,
    ),
    "sgt_state": (
        "The current ref's ideal: per-symbol frontier, covered paths, entity-granularity "
        "coverage fraction, and the async oracle's verdict (if `.sgt/oracle.json` is configured).",
        _schema({}, []),
        tool_state,
    ),
    "sgt_diff": (
        "Semantic diff between two refs' ideals: the symmetric difference of their op sets, "
        "grouped by symbol.",
        _schema({"ref_a": {"type": "string"}, "ref_b": {"type": "string"}}, ["ref_a", "ref_b"]),
        tool_diff,
    ),
    "sgt_fsck": (
        "Verify the op store's content-address integrity.",
        _schema({}, []),
        tool_fsck,
    ),
    "sgt_revert": (
        "Remove an op and everything built on it from the current ideal (I \\ upset X). `ref` is "
        "an op-id, an op-id prefix, or a `file::name` symbol (resolves to its frontier tip). "
        "Pass emit=true for a dry-run preview -- writes nothing.",
        _schema({"ref": {"type": "string"}, "emit": {"type": "boolean", "description": "dry-run preview only"}}, ["ref"]),
        tool_revert,
    ),
    "sgt_restore": (
        "Re-add an op and its prerequisites to the current ideal (I union downset X) -- revert's "
        "inverse. Pass emit=true for a dry-run preview -- writes nothing.",
        _schema({"ref": {"type": "string"}, "emit": {"type": "boolean", "description": "dry-run preview only"}}, ["ref"]),
        tool_restore,
    ),
    "sgt_oracle_run": (
        "Run configured build/test tiers (declared in `.sgt/oracle.json`) against the current "
        "ideal, in declared order, stopping at the first failure. Omit 'tier' to run the full "
        "pipeline; pass it to re-run just that one.",
        _schema({"tier": {"type": "string", "description": "run just this one tier (optional)"}}, []),
        tool_oracle_run,
    ),
}


def call_tool(repo_path: str, name: str, arguments: dict | None) -> dict:
    """Dispatch a single tool call to its handler. Raises KeyError on an unknown tool."""
    if name not in TOOLS:
        raise KeyError(name)
    _, _, handler = TOOLS[name]
    return handler(repo_path, arguments or {})


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
            is_error = isinstance(data, dict) and "error" in data
        except KeyError:
            data, is_error = {"error": f"unknown tool: {name}"}, True
        except Exception as ex:  # noqa: BLE001 - report any handler failure as a tool error
            data, is_error = {"error": f"{type(ex).__name__}: {ex}"}, True
        return _ok(mid, {"content": [{"type": "text", "text": json.dumps(data, indent=2)}],
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
