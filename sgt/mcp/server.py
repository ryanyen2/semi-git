"""A dependency-free MCP stdio server exposing the operation-ideal kernel (plan U7/U8/U9, flipped
onto MCP in U10).

The protocol is JSON-RPC 2.0 over newline-delimited stdin/stdout (the MCP stdio transport).
We implement only the small surface a tool server needs — ``initialize``, ``tools/list``,
``tools/call`` — with no third-party dependency, matching the project's minimal footprint and
keeping the dispatch (``handle_request``) a pure function that is unit-tested without a process.

Tool surface — kernel parity with the CLI's registered verbs:

The tool names mirror their CLI paths after the spine re-triage (KTD2): the daily verbs (read/write
kernel verbs, the semantic diff, and the agentic loop) keep their bare ``sgt_`` names, while only
the genuinely rare/maintenance tools carry the ``sgt_advanced_`` prefix of their re-homed CLI verbs.

* **read** (no API key, no writes): ``sgt_log`` (the mined op DAG), ``sgt_grid`` (the lane×commit
  timeline join — features × commits, ghost cells, fidelity marks), ``sgt_status`` (the current
  ref's ideal: frontier, coverage, oracle verdict), ``sgt_diff`` (semantic diff between two refs'
  ideals), ``sgt_advanced_fsck`` (op-store integrity).
* **write**: ``sgt_init`` (bind git + the kernel store, mine existing history), ``sgt_revert`` /
  ``sgt_restore`` (exact ideal edits, `I \\ ↑X` / `I ∪ ↓X`, with an `emit` dry-run preview),
  ``sgt_advanced_oracle_run`` (execute configured build/test tiers against the current ideal).
* **agentic loop** (plan U14): ``sgt_plan_intake`` (decompose a plan into predicted hollow
  ops), ``sgt_checkpoint`` (the pure step<->op footprint-overlap preview, or -- given
  ``confirm`` -- the explicit, one-group-at-a-time write that resolves it), ``sgt_drift``
  (ops no active plan predicted), ``sgt_plan_done`` (close a finished session so it leaves the
  active surface -- a fully-matched plan closes itself on the last confirm).

Every write tool mines the working tree on contact first (R9), so it reflects whatever the agent
just edited. The feature-lens verbs (merge/split/rename/move) have no MCP surface yet -- CLI-only
for now.
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
    kwargs = {"full": bool(args.get("full", False))}
    # Default to a tight window over MCP -- an agent rarely needs 100 ops of context at once, and
    # the payload is pure context cost. The CLI keeps its own larger default; this caps only here.
    kwargs["limit"] = int(args["limit"]) if args.get("limit") is not None else 30
    if args.get("offset") is not None:
        kwargs["offset"] = int(args["offset"])
    return oplog_view(repo_path, **kwargs)


def tool_grid(repo_path: str, args: dict) -> dict:
    from sgt.api import grid_view
    from sgt.core.lens import get

    get(repo_path)  # mine-on-contact before projecting the grid
    return grid_view(repo_path)


def tool_state(repo_path: str, args: dict) -> dict:
    from sgt.api import state_view
    from sgt.core.lens import get

    get(repo_path)
    return state_view(repo_path, full=bool(args.get("full", False)))


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


def tool_plan_intake(repo_path: str, args: dict) -> dict:
    """Decompose a plan into predicted hollow ops (plan U14). Mines the working tree first (R9)
    so `baseline_op_ids` reflects current reality."""
    from sgt.core.lens import get
    from sgt.loop import plan as plan_mod

    plan_text = (args.get("plan_text") or "").strip()
    if not plan_text:
        return {"error": "missing 'plan_text'"}
    session_id = (args.get("session_id") or "").strip() or None
    claude_session_id = (args.get("claude_session_id") or "").strip() or None
    get(repo_path)
    session = plan_mod.intake(repo_path, plan_text, session_id=session_id,
                              claude_session_id=claude_session_id)
    # `predicted_footprint` is echoed so the agent can SEE prediction quality: an all-empty
    # column means checkpoint's footprint-overlap will never match these steps (testbed
    # 2026-07-31: an intake whose predictions all came back [] silently guaranteed zero matches
    # downstream, and the agent had no way to notice).
    return {
        "session_id": session.session_id, "status": session.status, "step_count": len(session.steps),
        "steps": [
            {"title": s["title"], "predicted_feature": s["predicted_feature"],
             "predicted_footprint": s["predicted_footprint"], "rationale": s["rationale"]}
            for s in session.steps
        ],
    }


def tool_checkpoint(repo_path: str, args: dict) -> dict:
    """The pure step<->op footprint-overlap preview (plan U14); given `confirm` (a list of
    `{hollow_ids, op_ids}` groups), applies exactly those groups via `confirm_match` first --
    still "never auto-resolved" since nothing is confirmed unless a group is explicitly named."""
    from sgt.api import plan_view
    from sgt.core.lens import get
    from sgt.loop import plan as plan_mod
    from sgt.loop.match import confirm_match

    get(repo_path)
    # Intent-ledger M1: an optional `note` -- the user's latest instruction/correction, relayed at
    # checkpoint time. Alignment-timing evidence (channel `note`, agent paraphrase), never the
    # authoritative user voice (that's the verbatim `UserPromptSubmit` hook). Guarded: capture
    # must never break a checkpoint.
    note = (args.get("note") or "").strip()
    if note:
        try:
            from sgt.intent.turns import record_turn
            from sgt.loop import plan as _plan
            sid = args.get("session_id") or next(iter(sorted(_plan.active_sessions(repo_path))), None)
            if sid:
                record_turn(repo_path, key=sid, key_kind="plan", actor="agent", channel="note",
                            text=note)
        except Exception:  # noqa: BLE001
            pass
    confirm = args.get("confirm")
    if confirm:
        sessions = plan_mod.active_sessions(repo_path)
        for group in confirm:
            hollow_ids = group.get("hollow_ids") or []
            op_ids = group.get("op_ids") or []
            session_id = next(
                (sid for sid, rec in sessions.items()
                 if any(s["hollow_id"] in hollow_ids for s in rec["steps"])),
                None,
            )
            if session_id is None:
                return {"error": f"no active session owns hollow(s) {hollow_ids}"}
            confirm_match(repo_path, session_id, hollow_ids, op_ids)
    # Compact by default: each candidate group keeps its `hollow_ids`/`op_ids` -- everything a
    # follow-up `confirm` call needs -- and drops the per-op file/line span fold, the bulky part.
    # Pass `detail=true` for the spans when the agent wants to eyeball the actual changes.
    detail = bool(args.get("detail", False))
    return plan_view(repo_path, full=detail)["checkpoint"]


def tool_recall(repo_path: str, args: dict) -> dict:
    """Agent recall (intent-ledger M1, design §4.4): the recorded "why" for the symbols an agent
    is about to touch + stated-but-unlanded intents. Local-tier read; no mining needed beyond
    contact so stale stores still answer."""
    from sgt.core.lens import get as _get
    from sgt.intent.rationale import recall

    _get(repo_path)
    return recall(repo_path, list(args.get("symbols") or []))


def tool_drift(repo_path: str, args: dict) -> dict:
    """Every op not predicted by any active plan session (plan U14)."""
    from sgt.api import drift_view
    from sgt.core.lens import get

    get(repo_path)
    return drift_view(repo_path, full=bool(args.get("full", False)))


def tool_save(repo_path: str, args: dict) -> dict:
    """`sgt save`: record the agent's edits as a real save.

    Without this an agent could read the graph and edit code but not record either, so a human had
    to relay every save by hand -- the exact back-and-forth between editor, terminal and agent that
    the graph exists to remove. A save is additive and `sgt undo` reverses it, which is what makes
    it safe to hand over."""
    from sgt.cli.porcelain import save

    return save(repo_path,
                message=(args.get("message") or "").strip() or None,
                as_label=(args.get("as_feature") or "").strip() or None)


def tool_now(repo_path: str, args: dict) -> dict:
    """`sgt now`: what the developer asked for, what is unsaved, what needs them, what is next.

    Useful to the *agent* as well as the human: `working_on` carries the user's own prompt, so an
    agent picking work back up can read what was actually asked rather than infer it from a diff."""
    from sgt.api import now_view
    from sgt.core.lens import get

    get(repo_path)
    return now_view(repo_path)


def tool_show(repo_path: str, args: dict) -> dict:
    """`sgt show <spec> [<path>]`: a file as it was at a past frontier, read-only.

    Shares `api.show_view` with the CLI verb rather than resolving it again here. It was rebuilt at
    first and had drifted before it shipped -- the CLI matched an exact repo-relative path *or* a
    suffix and told "no such file" apart from "ambiguous"; this one did suffixes only and collapsed
    both errors."""
    from sgt.api import show_view
    from sgt.core.lens import get

    spec = (args.get("at") or "").strip()
    if not spec:
        return {"error": "missing 'at' (a commit index, `op:<id>,...`, or a ref)"}
    get(repo_path)
    return show_view(repo_path, spec, (args.get("path") or "").strip() or None)


def tool_plan_done(repo_path: str, args: dict) -> dict:
    """Close a finished plan session (plan U14). A fully-matched session already completes on its
    own via `sgt_checkpoint`'s confirm; this is the explicit close for a plan whose remaining steps
    were done differently than predicted and will never match, so it stops showing as active."""
    from sgt.loop import plan as plan_mod

    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return {"error": "missing 'session_id'"}
    # Ownership was stated in the skill ("only confirm your own") and enforced by nothing: any agent
    # holding another's session id could close its plan out from under it, mid-build. When the
    # caller identifies itself, a mismatch is refused instead of silently honored.
    caller = (args.get("claude_session_id") or "").strip()
    if caller:
        owner = (plan_mod.active_sessions(repo_path).get(session_id) or {}).get("claude_session_id")
        if owner and owner != caller:
            return {"error": f"plan {session_id} belongs to another agent (session {owner}); "
                             f"leave it alone, or omit claude_session_id to close it deliberately"}
    ok = plan_mod.mark_done(repo_path, session_id)
    return {"ok": ok} if ok else {"error": f"no such session: {session_id}"}


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
        "Read the mined operation DAG. Compact by default: {count, kinds, truncated, ops} where "
        "each op is {id, kind, symbols, intent}, windowed by limit/offset. Pass full=true for "
        "each op's before->after footprint, witnessing-commit provenance, and attribution, unpaged.",
        _schema(
            {"full": {"type": "boolean", "description": "the full per-op payload instead of the compact default"},
             "limit": {"type": "integer", "description": "compact mode only: window size (default 30)"},
             "offset": {"type": "integer", "description": "compact mode only: window start (default 0)"}},
            [],
        ),
        tool_log,
    ),
    "sgt_grid": (
        "The lane×commit grid: the canonical join every timeline surface renders. "
        "Returns {commits, cells, features, ghosts, partial_commits} -- one cell per (feature, "
        "commit) that carries ops, the commit axis, active-plan ghost cells, and per-commit "
        "mining-fidelity marks. A complete projection (not paged): a grid needs every cell.",
        _schema({}, []),
        tool_grid,
    ),
    "sgt_status": (
        "The current ref's ideal: covered paths, entity-granularity coverage fraction, and the "
        "async oracle's verdict (if `.sgt/oracle.json` is configured). Compact by default "
        "(frontier_count/entity_path_count instead of the full per-symbol frontier map and "
        "entity_paths list); pass full=true to restore them.",
        _schema({"full": {"type": "boolean", "description": "restore the full frontier map and entity_paths list"}}, []),
        tool_state,
    ),
    "sgt_diff": (
        "Semantic diff between two refs' ideals: the symmetric difference of their op sets, "
        "grouped by symbol.",
        _schema({"ref_a": {"type": "string"}, "ref_b": {"type": "string"}}, ["ref_a", "ref_b"]),
        tool_diff,
    ),
    "sgt_save": (
        "Record your edits as a save. Pass `message` -- your own words about what this work was; "
        "they become the save's subject and the recorded intent, and a feature born from this work "
        "is named from them rather than by a model. Additive and reversible (`sgt undo`).",
        _schema({"message": {"type": "string", "description": "what this work was, in your words"},
                 "as_feature": {"type": "string", "description": "name the feature this work lands in (optional)"}},
                []),
        tool_save,
    ),
    "sgt_now": (
        "Where the work stands: what the user asked for (`working_on`, their own prompt verbatim), "
        "what is unsaved, what needs them, what was recently done, and the single next action. Call "
        "this when picking work back up -- it says what was actually asked, rather than leaving you "
        "to infer it from a diff.",
        _schema({}, []),
        tool_now,
    ),
    "sgt_show": (
        "Read a file as it was at a past point, or list what existed there. `at` is a commit index "
        "(`12`), an op set (`op:<id>,...`), or a ref. Read-only: nothing is checked out and the "
        "working tree is untouched.",
        _schema({"at": {"type": "string"}, "path": {"type": "string", "description": "omit to list files"}},
                ["at"]),
        tool_show,
    ),
    "sgt_advanced_fsck": (
        "Verify the op store's content-address integrity.",
        _schema({}, []),
        tool_fsck,
    ),
    "sgt_revert": (
        "Remove a symbol-level edit and everything built on top of it from the current state. "
        "`ref` is an op-id, an op-id prefix, or a `file::name` symbol (resolves to its latest edit). "
        "Pass emit=true for a dry-run preview -- writes nothing.",
        _schema({"ref": {"type": "string"}, "emit": {"type": "boolean", "description": "dry-run preview only"}}, ["ref"]),
        tool_revert,
    ),
    "sgt_restore": (
        "Bring a symbol-level edit back, along with everything it needs -- revert's inverse. "
        "`ref` is an op-id, an op-id prefix, or a `file::name` symbol. Pass emit=true for a "
        "dry-run preview -- writes nothing.",
        _schema({"ref": {"type": "string"}, "emit": {"type": "boolean", "description": "dry-run preview only"}}, ["ref"]),
        tool_restore,
    ),
    "sgt_advanced_oracle_run": (
        "Run configured build/test tiers (declared in `.sgt/oracle.json`) against the current "
        "ideal, in declared order, stopping at the first failure. Omit 'tier' to run the full "
        "pipeline; pass it to re-run just that one.",
        _schema({"tier": {"type": "string", "description": "run just this one tier (optional)"}}, []),
        tool_oracle_run,
    ),
    "sgt_plan_intake": (
        "Decompose a plan (an agent's or human's stated intent before doing the work) into "
        "predicted hollow ops -- one per step, off-chain, never touching the ideal algebra. "
        "Grounds `predicted_feature` in the repo's own feature tree (`sgt log --tree`) when one exists.",
        _schema(
            {"plan_text": {"type": "string"},
             "session_id": {"type": "string", "description": "explicit id (optional; defaults to a fresh one)"},
             "claude_session_id": {"type": "string", "description": "your Claude Code session id (read $CLAUDE_CODE_SESSION_ID via Bash -- the per-session UUID; do NOT use $CLAUDE_CODE_BRIDGE_SESSION_ID, which can carry a parent session's id in nested runs); stored so an interrupted plan can be resumed directly with `claude --resume <uuid>` and so hook-captured prompts join to this plan's commits"}},
            ["plan_text"],
        ),
        tool_plan_intake,
    ),
    "sgt_checkpoint": (
        "Preview candidate step<->op groups (footprint-overlap between pending plan steps and "
        "ops mined since each session's own baseline) plus drift op-ids. Pass `confirm` -- a "
        "list of `{hollow_ids, op_ids}` groups -- to apply exactly those groups; omit it for a "
        "pure, read-only preview. Compact by default; pass detail=true for each match's file/line "
        "spans.",
        _schema(
            {"detail": {"type": "boolean", "description": "include each match's file/line spans (bulkier)"},
             "confirm": {
                "type": "array",
                "items": _schema(
                    {"hollow_ids": {"type": "array", "items": {"type": "string"}},
                     "op_ids": {"type": "array", "items": {"type": "string"}}},
                    ["hollow_ids", "op_ids"],
                ),
            },
             "session_id": {"type": "string", "description": "which plan session a `note` belongs to (optional; defaults to the single active one)"},
             "note": {"type": "string", "description": "the user's latest instruction/correction in this conversation, verbatim -- recorded as intent evidence so `sgt why` can answer later"}},
            [],
        ),
        tool_checkpoint,
    ),
    "sgt_recall": (
        "Before editing, recall why the code you are about to touch is the way it is: recorded "
        "rationale (from past plans/conversations) overlapping the given symbols, plus intents "
        "that were stated but never landed. Read-only, local, cheap -- call it at plan time.",
        _schema({"symbols": {"type": "array", "items": {"type": "string"},
                             "description": "symbols you plan to touch, as `repo/relative/path.py::name` (bare `file.py::name` also matches -- symbol names are joined exactly, file paths by suffix; empty = all recorded rationale)"}}, []),
        tool_recall,
    ),
    "sgt_drift": (
        "Every op not predicted by any active plan session. Compact by default: {count, op_ids, "
        "kinds}, no spans. Pass full=true for each entry's footprint and current file/line spans.",
        _schema({"full": {"type": "boolean", "description": "restore per-op footprint and file/line spans"}}, []),
        tool_drift,
    ),
    "sgt_plan_done": (
        "Close a finished plan session so it stops showing as active. A fully-matched plan "
        "completes automatically when its last step is confirmed; call this for a plan whose "
        "remaining steps were built differently than predicted and will never match. The record is "
        "kept as completed history (its work stays attributable); use `sgt plan abandon` to delete "
        "an unwanted plan entirely.",
        _schema({"session_id": {"type": "string"},
                 "claude_session_id": {"type": "string", "description": "your own $CLAUDE_CODE_SESSION_ID; pass it so closing a plan another agent owns is refused rather than silently honored"}},
                ["session_id"]),
        tool_plan_done,
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
