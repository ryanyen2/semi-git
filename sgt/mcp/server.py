"""A dependency-free MCP stdio server exposing the semi-git semantic tree.

The protocol is JSON-RPC 2.0 over newline-delimited stdin/stdout (the MCP stdio transport).
We implement only the small surface a tool server needs — ``initialize``, ``tools/list``,
``tools/call`` — with no third-party dependency, matching the project's minimal footprint and
keeping the dispatch (``handle_request``) a pure function that is unit-tested without a process.

Tool surface (the agent-facing semantic API) — full parity with the CLI's mutating verbs:

* **read** (no API key): ``sgt_graph``, ``sgt_show``, ``sgt_status``, ``sgt_conflicts`` — pull
  the semantic tree and any open conflicts (with full witnesses) into the agent's context.
* **write/reconcile**: ``sgt_init`` (bootstrap a workspace), ``sgt_plan`` (decompose into PLANNED
  nodes), ``sgt_checkpoint`` — *the* tool: after the agent edits files, distill the drift into the
  log under the agent's **declared** intent (captured live, not reverse-guessed; ``fulfills`` lands
  it on a PLANNED node), ``sgt_revert`` / ``sgt_switch`` (graph ops), and ``sgt_reconcile`` (re-gate
  held quarantines). A held checkpoint returns its witness so the agent can act on it.

Every call opens the project fresh from disk, so it reflects whatever the agent just edited.
``sgt_checkpoint`` uses the deterministic distiller (the agent supplies intent), so it needs no
API key — the loop works fully offline.
"""

from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "semi-git", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# Tool handlers — each takes (repo_path, arguments) and returns a JSON-able dict.
# ---------------------------------------------------------------------------
def _open(repo_path: str):
    from sgt.project import Project

    return Project.open(repo_path)


# The read tools delegate to the canonical JSON projection in ``sgt.api`` so the MCP surface
# and the CLI's ``--json`` mode return the identical schema and never drift.
def tool_graph(repo_path: str, args: dict) -> dict:
    from sgt.api import graph_view

    return graph_view(_open(repo_path))


def tool_show(repo_path: str, args: dict) -> dict:
    from sgt.api import show_view

    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    return show_view(_open(repo_path), ref)


def tool_status(repo_path: str, args: dict) -> dict:
    from sgt.api import status_view

    return status_view(_open(repo_path))


def tool_conflicts(repo_path: str, args: dict) -> dict:
    from sgt.api import conflicts_view

    return conflicts_view(_open(repo_path))


def tool_blame(repo_path: str, args: dict) -> dict:
    """Per-file semantic blame: which feature node owns each line of a materialized file."""
    from sgt.api import blame_view

    file = (args.get("file") or "").strip()
    if not file:
        return {"error": "missing 'file'"}
    return blame_view(_open(repo_path), file)


def tool_map(repo_path: str, args: dict) -> dict:
    """The deterministic code-entity map: entities + containment/calls/imports, transitive-reduced."""
    from sgt.api import entity_graph_view

    return entity_graph_view(_open(repo_path))


def tool_checkpoint(repo_path: str, args: dict) -> dict:
    """Reconcile the agent's on-disk edits into the log under a declared intent.

    The intent the agent passes labels the new/extended nodes directly — better than the
    reverse-guessing distiller — and statement-level body edits still split into their own fix
    nodes so a later cross-agent merge stays statement-granular. Pass ``fulfills`` (a node ref)
    to land the whole change under a PLANNED node and flip it ACTIVE.
    """
    from sgt.agents.distill import fallback_cluster
    from sgt.agents.resolve import resolve_ref
    from sgt.orchestrate.sync import run_sync

    project = _open(repo_path)
    intent = (args.get("intent") or "").strip()
    if not intent:
        return {"error": "missing 'intent' — declare what this change accomplishes"}

    fulfills_id = None
    if (fulfills := (args.get("fulfills") or "").strip()):
        r = resolve_ref(project.graph, fulfills)
        if r.node_id is None:
            return {"error": f"could not resolve fulfills {fulfills!r} ({r.kind})", "matches": r.matches}
        fulfills_id = r.node_id

    def clusterer(effects, proj):
        clusters = fallback_cluster(effects, proj)
        for c in clusters:
            c.intent = intent  # the agent's declared intent overrides the structural label
        return clusters

    rep = run_sync(project, repo_path=repo_path, clusterer=clusterer, confirm=lambda c: True,
                   fulfills=fulfills_id, intent=intent)
    return {
        "ok": rep.ok,
        "message": rep.message,
        "landed": rep.landed,
        "fulfilled": rep.fulfilled,
        "extended": rep.extended,
        "quarantined": rep.quarantined,
        # Superseded (zombie) quarantines GC'd automatically — a fixed re-checkpoint that lands
        # clean cleans up the prior hold here, so no manual revert + replan is needed.
        "swept": rep.swept,
        # The witness for each held node — *why* it did not commute and against what — so the
        # agent can act (revise the code and re-checkpoint, or revert the rival) without guessing.
        "witnesses": {q: project.witnesses.get(q, {}) for q in rep.quarantined},
        "notes": rep.notes,
    }


def _orchestrator(repo_path: str):
    from sgt.orchestrate.loop import Orchestrator

    # Graph-only: no coding backend. revert/switch/reconcile need no key; plan uses the
    # graph-reasoning LLM (the planner) only.
    return Orchestrator(_open(repo_path), repo_path=repo_path)


def _report(rep) -> dict:
    return {"ok": rep.ok, "action": rep.action, "node_id": rep.node_id, "message": rep.message,
            "landed": list(rep.landed), "held": list(rep.held),
            "quarantined": list(rep.quarantined)}


def tool_plan(repo_path: str, args: dict) -> dict:
    """Decompose an intent into reviewable PLANNED nodes (no code authored)."""
    intent = (args.get("intent") or "").strip()
    if not intent:
        return {"error": "missing 'intent' — describe what to plan"}
    return _report(_orchestrator(repo_path).plan(intent))


def tool_reconcile(repo_path: str, args: dict) -> dict:
    """Re-gate held quarantines on demand; resolve any that now commute.

    With no ``ref``, retries every pending quarantine; with one, just that node. This closes
    the quarantine loop from MCP — the agent can observe holds via ``sgt_conflicts`` and clear
    them here once a rival was reverted/suspended (revise-the-code retries go through
    ``sgt_checkpoint`` with ``fulfills`` instead).
    """
    ref = (args.get("ref") or "").strip() or None
    return _report(_orchestrator(repo_path).reconcile(ref))


def tool_init(repo_path: str, args: dict) -> dict:
    """Bind a fresh ``.sgt`` store + git to a repo so the agent can bootstrap a workspace."""
    from sgt.project import Project

    path = (args.get("path") or "").strip() or repo_path
    Project.init(path)
    return {"ok": True, "message": f"initialized sgt workspace at {path}"}


def tool_revert(repo_path: str, args: dict) -> dict:
    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    return _report(_orchestrator(repo_path).revert(ref, emit=bool(args.get("emit", False))))


def tool_switch(repo_path: str, args: dict) -> dict:
    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    on = bool(args.get("on", True))
    return _report(_orchestrator(repo_path).switch(ref, on, emit=bool(args.get("emit", False))))


# ---------------------------------------------------------------------------
# Tool registry (name -> (description, inputSchema, handler))
# ---------------------------------------------------------------------------
def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS: dict[str, tuple[str, dict, Any]] = {
    "sgt_graph": (
        "Read the semantic DAG: every node's id, kind, status, intent, dependencies, and any "
        "open conflict. Call this before editing to see what features exist.",
        _schema({}, []),
        tool_graph,
    ),
    "sgt_show": (
        "Inspect one node by a fuzzy ref (id, name, or intent substring): its effects, "
        "dependencies, dependents, and conflict witness.",
        _schema({"ref": {"type": "string", "description": "node id, name, or intent substring"}}, ["ref"]),
        tool_show,
    ),
    "sgt_status": (
        "Summarize the project: node count, managed files, effect count, and whether the "
        "working tree has un-checkpointed drift.",
        _schema({}, []),
        tool_status,
    ),
    "sgt_conflicts": (
        "List open conflicts: each held node, the node(s) it lost to, and the reason — so you "
        "can decide how to resolve them.",
        _schema({}, []),
        tool_conflicts,
    ),
    "sgt_blame": (
        "Semantic blame for one file: the line spans of the materialized file mapped to the "
        "feature node that owns each — the per-feature analogue of `git blame`.",
        _schema({"file": {"type": "string", "description": "repo-relative path of a managed file"}}, ["file"]),
        tool_blame,
    ),
    "sgt_map": (
        "The deterministic code-entity map of the whole repo: functions/classes/methods as "
        "entities connected by containment + calls/imports, each tagged with its owning feature "
        "(node_id, null if unattributed) plus the transitive-reduced edge set for layout.",
        _schema({}, []),
        tool_map,
    ),
    "sgt_plan": (
        "Decompose an intent into reviewable PLANNED nodes (the semantic outline) without "
        "writing any code. Each node carries its declared provides/needs and dependency edges; "
        "implement them with your own tools, then land each via sgt_checkpoint.",
        _schema({"intent": {"type": "string", "description": "what to plan / decompose"}}, ["intent"]),
        tool_plan,
    ),
    "sgt_checkpoint": (
        "Reconcile your on-disk edits into the semantic log under a declared intent. Call this "
        "after finishing a logical unit of work. Body edits land at statement granularity so a "
        "later merge with another agent's edits stays conflict-aware. Pass 'fulfills' (a node "
        "ref) to land the change under a PLANNED node and flip it ACTIVE.\n"
        "Distillable code: top-level def/class, imports, and single-name bindings (`X = ...`, "
        "`X: T = ...`) all round-trip. Arbitrary module-level executable statements (tuple-unpack, "
        "bare expressions, `if __name__` blocks) are NOT captured and will be lost on "
        "rematerialize — keep that logic inside a function. If a checkpoint is held "
        "(invariant_violated), just fix the code on disk and checkpoint again with the same "
        "`fulfills`: the superseded hold is reclaimed automatically (no revert/replan needed).",
        _schema({"intent": {"type": "string", "description": "what this change accomplishes"},
                 "fulfills": {"type": "string", "description": "PLANNED node ref this change implements (optional)"}},
                ["intent"]),
        tool_checkpoint,
    ),
    "sgt_revert": (
        "Remove a feature by dependency closure (blocked if the tree has un-checkpointed drift; "
        "checkpoint first). Pass emit=true for a dry-run: preview the semantic delta without "
        "writing the tree.",
        _schema({"ref": {"type": "string"}, "emit": {"type": "boolean", "description": "dry-run preview only"}}, ["ref"]),
        tool_revert,
    ),
    "sgt_switch": (
        "Suspend or restore a feature (on=false suspends, on=true restores). Pass emit=true for "
        "a dry-run preview.",
        _schema({"ref": {"type": "string"},
                 "on": {"type": "boolean", "default": True,
                        "description": "true restores the feature (default), false suspends it"},
                 "emit": {"type": "boolean", "description": "dry-run preview only"}}, ["ref"]),
        tool_switch,
    ),
    "sgt_reconcile": (
        "Re-gate held quarantines and resolve any that now commute (e.g. after a rival was "
        "reverted/suspended). Omit 'ref' to retry all pending; pass one to target a single node.",
        _schema({"ref": {"type": "string", "description": "a single quarantined node ref (optional)"}}, []),
        tool_reconcile,
    ),
    "sgt_init": (
        "Bind a fresh .sgt store + git to a repo so you can start versioning semantics. "
        "Idempotent-ish: run once per repo before planning or checkpointing.",
        _schema({"path": {"type": "string", "description": "repo path (defaults to the server's root)"}}, []),
        tool_init,
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
