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
from pathlib import Path
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


def tool_state(repo_path: str, args: dict) -> dict:
    from sgt.api import state_view
    from sgt.core.lens import get

    get(repo_path)
    full = bool(args.get("full", False))
    view = state_view(repo_path, full=full)
    if not full:
        # Transport-level compaction, not a view change: the canonical projection keeps its
        # schema (CLI `--json` parity), but over MCP the full covered/derived path lists were
        # ~75% of the payload (~1200 tokens on this repo) and an orienting agent needs the
        # counts; `full=true` restores the lists.
        view["covered_path_count"] = len(view.pop("covered_paths"))
        view["derived_path_count"] = len(view.pop("derived_paths"))
    return view


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


def _verb_with_feature_fallback(repo_path: str, verb: str, ref: str, emit: bool) -> dict:
    """An op or symbol first, then the feature of that name.

    The CLI has resolved a feature id or label since U13; this tool did not, so
    an agent asked to take a feature out got "neither an op-id in the ideal nor a
    live symbol" for the id every other surface prints. There is no version of
    this tool being useful without it: features are the unit the graph is drawn
    in, and an agent that cannot name one falls back to editing files by hand,
    which is the behaviour the tool exists to replace.
    """
    from sgt.core import verbs as core_verbs
    from sgt.lens import verbs as lens_verbs

    single = core_verbs.revert if verb == "revert" else core_verbs.restore
    preview = single(repo_path, ref, emit=True)
    if not preview.ok and lens_verbs.resolve_feature(repo_path, ref) is not None:
        plan = (
            lens_verbs.plan_revert_feature if verb == "revert" else lens_verbs.plan_restore_feature
        )
        preview = plan(repo_path, ref)
    gap = None
    if verb == "restore" and preview.ok:
        # Computed before apply: apply journals this restore's own entry, and
        # the gap walk would find that instead of the revert it looks for.
        from sgt.cli.ideal_edit import _restore_gap

        gap = _restore_gap(repo_path, preview)
    if preview.ok and not emit:
        core_verbs.apply(repo_path, preview)
    result = _verb_result(preview)
    if verb == "restore" and preview.ok:
        # The agent is the consumer most likely to read "✓ restore" as "the
        # earlier revert is undone" and report success to a person. Say what
        # stays removed, in the same shape the CLI's JSON carries.
        if gap:
            result["restore_gap"] = gap
            result["message"] = (
                (result.get("message") or "")
                + f" note: {gap['still_removed_op_count']} op(s) the earlier revert removed stay"
                  " removed; `sgt undo` reverses that revert whole."
            ).strip()
    return result


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
            from sgt.intent.turns import record_turn
            record_turn(repo_path, key=key, key_kind=key_kind, actor="human", channel="agent",
                        text=prompt)
    except Exception:  # noqa: BLE001
        pass


def tool_revert(repo_path: str, args: dict) -> dict:
    """`I \\ ↑X`: remove an op and everything built on it. `emit=true` previews with no write."""
    from sgt.core.lens import get

    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    if not args.get("emit"):  # a preview is a read; only the applying call carries intent
        _carry_prompt(repo_path, args)
    get(repo_path)  # mine-on-contact before planning/applying the edit (R9)
    return _verb_with_feature_fallback(repo_path, "revert", ref, bool(args.get("emit", False)))


def tool_restore(repo_path: str, args: dict) -> dict:
    """`I ∪ ↓X`: revert's inverse. `emit=true` previews with no write."""
    from sgt.core.lens import get

    ref = (args.get("ref") or "").strip()
    if not ref:
        return {"error": "missing 'ref'"}
    if not args.get("emit"):
        _carry_prompt(repo_path, args)
    get(repo_path)
    return _verb_with_feature_fallback(repo_path, "restore", ref, bool(args.get("emit", False)))


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
        actor = (args.get("claude_session_id") or "").strip() or None
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
            # Ownership-checked like `plan done`/`abandon`: a confirm consumes the hollow and credits
            # the plan, so confirming into another agent's session both steals its pending step and
            # attributes work that session never did.
            try:
                confirm_match(repo_path, session_id, hollow_ids, op_ids, actor=actor)
            except plan_mod.PlanOwnershipError as e:
                return {"error": str(e), "owner": e.owner, "session_id": e.session_id}
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


def tool_find(repo_path: str, args: dict) -> dict:
    """Rank features, saves and symbols against a description (`sgt find`)."""
    from sgt.core.lens import get as _get
    from sgt.lens.search import search

    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "missing 'query'"}
    _get(repo_path)
    return search(repo_path, query, k=int(args.get("limit") or 8))


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

    # Carry the driving prompt (weave P1): with a chat key, record it BEFORE the save so it lands
    # inside the capture window this save is about to close; without one, record it after, keyed
    # by the new commit sha -- a key `_atom_prompt` already joins by directly, so the words still
    # reach every read surface, just not through the manifest.
    chat = (args.get("claude_session_id") or "").strip()
    if chat:
        _carry_prompt(repo_path, args)
    out = save(repo_path,
               message=(args.get("message") or "").strip() or None,
               as_label=(args.get("as_feature") or "").strip() or None)
    if not chat and out.get("saved") and out.get("commit"):
        _carry_prompt(repo_path, args, key=out["commit"], key_kind="sha")
        # The carry landed after the save's own reflection ran -- re-reflect so the sha-keyed
        # prompt becomes a save-wide rationale record too (idempotent; guarded like the carry).
        try:
            from sgt.intent.stint import reflect_save
            reflect_save(repo_path, out["commit"])
        except Exception:  # noqa: BLE001
            pass
    return out


def tool_now(repo_path: str, args: dict) -> dict:
    """`sgt now`: what the developer asked for, what is unsaved, what needs them, what is next.

    The tool to call *first* when picking up work in a repo you did not just set up -- it answers "is
    someone mid-something here?" before you edit over them. Useful to the *agent* as well as the
    human: `working_on` carries the user's own prompt, so an agent resuming reads what was actually
    asked rather than inferring it from a diff."""
    from sgt.api import now_view

    # No leading `get()`: `now_view`'s default `include_preview=True` already runs the one
    # mine-on-contact step (`save_preview_view` calls `get`), and a second sync per tool call
    # re-pays the dirty-tree fingerprint for nothing.
    return now_view(repo_path)


def tool_show(repo_path: str, args: dict) -> dict:
    """`sgt show`: two readings of "show me this", chosen by whether a point in time was named.

    Without `at`, `sel` is an id -- a feature handle, a checkpoint, an op id, a `file::name` symbol,
    a path, a save id -- and the answer is what it covers, how much a revert would remove (including
    the work built on top), and the commands that apply. With `at`, `sel` is a file and the answer is its
    content as it was at that frontier, or the list of what existed there when `sel` is omitted.

    One tool because a caller holds one intent (point at a thing, see it), and `at` is the same time
    modifier the rest of sgt uses. Both readings share their projection with the CLI verb rather than
    resolving anything here: the historical read was rebuilt separately once and had already drifted
    before it shipped -- the CLI matched an exact repo-relative path *or* a suffix and told "no such
    file" apart from "ambiguous", while the copy did suffixes only and collapsed both errors."""
    from sgt.api import show_at_view, show_view
    from sgt.core.lens import get

    spec = (args.get("at") or "").strip()
    sel = (args.get("sel") or args.get("path") or "").strip()
    if spec:
        get(repo_path)  # mine-on-contact so the fold reflects current reality (R9)
        return show_at_view(repo_path, spec, sel or None)
    if not sel:
        return {"error": "pass 'sel' (what is this?) or 'at' (what existed at a past point)"}
    return show_view(repo_path, sel)


def tool_plan_done(repo_path: str, args: dict) -> dict:
    """Close a finished plan session (plan U14). A fully-matched session already completes on its
    own via `sgt_checkpoint`'s confirm; this is the explicit close for a plan whose remaining steps
    were done differently than predicted and will never match, so it stops showing as active.

    Ownership-checked: closing unlinks the still-pending hollows, so an agent closing a *different*
    agent's plan would make that agent's remaining steps permanently unmatchable with nothing
    anywhere explaining why. Passing `claude_session_id` is what lets the check protect you too."""
    from sgt.loop import plan as plan_mod

    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return {"error": "missing 'session_id'"}
    actor = (args.get("claude_session_id") or "").strip() or None
    try:
        ok = plan_mod.mark_done(repo_path, session_id, actor=actor)
    except plan_mod.PlanOwnershipError as e:
        return {"error": str(e), "owner": e.owner, "session_id": e.session_id}
    return {"ok": ok} if ok else {"error": f"no such session: {session_id}"}


def tool_plan_adopt(repo_path: str, args: dict) -> dict:
    """Take over a plan session another Claude session started -- typically one left stalled when
    its agent stopped. Non-destructive: the steps, their confirmed matches, and the pending hollows
    all survive, so you continue from where the previous agent stopped rather than re-intaking (which
    would mint duplicate hollows for work already done)."""
    from sgt.loop import plan as plan_mod

    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return {"error": "missing 'session_id'"}
    actor = (args.get("claude_session_id") or "").strip() or None
    ok, previous = plan_mod.adopt(repo_path, session_id, actor)
    if not ok:
        return {"error": f"no such session: {session_id}"}
    return {"ok": True, "session_id": session_id, "previous_owner": previous, "owner": actor}


# ---------------------------------------------------------------------------
# Tool registry (name -> (description, inputSchema, handler))
# ---------------------------------------------------------------------------
def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


# The caller's own Claude Code session id, used by the plan tools to tell agents apart. Declared once
# because it now appears on several tools and its guidance (which env var, and why not the bridge
# one) must not drift between them.
# The capture-weave carry pair (P1, design doc 2026-09-01 §4a): an MCP client has no
# `UserPromptSubmit` hook, so a mutating verb is the only door the user's ask can arrive through.
_DRIVING_PROMPT_PROP = {
    "type": "string",
    "description": "the user's ask that drove this work, verbatim as you received it (optional "
                   "but valuable): it is recorded as capture evidence, so `sgt why` and the "
                   "checkpoint labels can answer with the user's own words instead of a guess. "
                   "Pass their words, not your paraphrase.",
}
_CHAT_KEY_PROP = {
    "type": "string",
    "description": "your Claude Code session id (read $CLAUDE_CODE_SESSION_ID via Bash -- the "
                   "per-session UUID; do NOT use $CLAUDE_CODE_BRIDGE_SESSION_ID, which can carry "
                   "a parent session's id in nested runs). Keys the carried prompt to your "
                   "conversation so the work it produced can be traced back -- and resumed -- "
                   "from the checkpoint it lands in.",
}

_OWN_SESSION_PROP = {
    "type": "string",
    "description": "your Claude Code session id (read $CLAUDE_CODE_SESSION_ID via Bash -- the "
                   "per-session UUID; do NOT use $CLAUDE_CODE_BRIDGE_SESSION_ID, which can carry a "
                   "parent session's id in nested runs). Identifies you as the plan's owner so "
                   "another agent cannot close your plan out from under you, and vice versa.",
}


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
    # `sgt_grid` is deliberately NOT exposed. `grid_view` is a *complete* projection by design --
    # a grid surface needs every cell to draw -- which is right for the TUI and the VS Code webview
    # and wrong for a language model, because a model never draws it. Measured on a 290-commit repo:
    # ~515 KB, about 129,000 tokens in a single tool result, growing linearly with history (1.5k
    # tokens at 10 commits, 6.5k at 60). One call would consume most of a context window and answer
    # no question that `sgt_log` (capped at 30) or `sgt_now` (flat ~470 tokens) doesn't answer more
    # cheaply. An agent that genuinely needs the raw join can shell out to `sgt log --json` and page
    # it itself. Please don't re-add it without a compact, paged shape.
    "sgt_status": (
        "The current ref's ideal: entity-granularity coverage fraction, covered/derived path "
        "counts, and the async oracle's verdict (if `.sgt/oracle.json` is configured). Compact "
        "by default (counts instead of the per-symbol frontier map and the covered/derived/"
        "entity path lists); pass full=true to restore the lists.",
        _schema({"full": {"type": "boolean", "description": "restore the frontier map and the covered/derived/entity path lists"}}, []),
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
                 "as_feature": {"type": "string", "description": "name the feature this work lands in (optional)"},
                 "prompt": _DRIVING_PROMPT_PROP,
                 "claude_session_id": _CHAT_KEY_PROP},
                []),
        tool_save,
    ),
    "sgt_now": (
        "Where the work stands: what the user asked for (`working_on`, their own prompt verbatim), "
        "what is unsaved, what needs a human (forks, stalled plans, a paused git merge), what was "
        "recently done, and the single next action. Call this FIRST when picking up work in a repo "
        "you did not just set up: it says what was actually asked rather than leaving you to infer "
        "it from a diff, and it tells you whether someone is mid-something before you edit over them.",
        _schema({}, []),
        tool_now,
    ),
    "sgt_show": (
        "Show me this thing. With `sel` alone it explains an id — a feature handle, a `feature@n` "
        "checkpoint, an op id, a `file::name` symbol, a path, or a save id (the commit sha `sgt "
        "log` prints in its id column) — reporting what it covers, how many "
        "edits a revert would remove (and how many of those are work built on top), and which "
        "commands apply. Use it before proposing a revert so you can state the consequence. Add "
        "`at` to read the past instead: `sel` is then a file and you get its content as it was at "
        "that point (a commit index like `12`, an op set `op:<id>,...`, or a ref), or the list of "
        "what existed there if you omit `sel`. Read-only either way — nothing is checked out, and "
        "the id reading never calls an LLM.",
        _schema({"sel": {"type": "string",
                         "description": "an id/label/symbol to explain, or (with `at`) a file to read"},
                 "at": {"type": "string",
                        "description": "read it as it was at this point: a commit index, `op:<id>,...`, or a ref"}},
                []),
        tool_show,
    ),
    "sgt_advanced_fsck": (
        "Verify the op store's content-address integrity.",
        _schema({}, []),
        tool_fsck,
    ),
    "sgt_find": (
        "Find something you can describe but cannot name: ranks features, saves and symbols "
        "against a phrase like 'the thing that formats dates'. Use this before revert/restore "
        "when you do not already hold an id. Report-only. The `mode` field says whether the "
        "answer came from meaning ('semantic') or word overlap ('lexical').",
        _schema({"query": {"type": "string", "description": "a description in plain words"},
                 "limit": {"type": "integer", "description": "how many hits (default 8)"}},
                ["query"]),
        tool_find,
    ),
    "sgt_revert": (
        "Remove a symbol-level edit and everything built on top of it from the current state. "
        "`ref` is an op-id, an op-id prefix, or a `file::name` symbol (resolves to its latest edit). "
        "Pass emit=true for a dry-run preview -- writes nothing.",
        _schema({"ref": {"type": "string"}, "emit": {"type": "boolean", "description": "dry-run preview only"},
                 "prompt": _DRIVING_PROMPT_PROP, "claude_session_id": _CHAT_KEY_PROP}, ["ref"]),
        tool_revert,
    ),
    "sgt_restore": (
        "Bring a symbol-level edit back, along with everything it needs -- revert's inverse. "
        "`ref` is an op-id, an op-id prefix, or a `file::name` symbol. Pass emit=true for a "
        "dry-run preview -- writes nothing.",
        _schema({"ref": {"type": "string"}, "emit": {"type": "boolean", "description": "dry-run preview only"},
                 "prompt": _DRIVING_PROMPT_PROP, "claude_session_id": _CHAT_KEY_PROP}, ["ref"]),
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
             "claude_session_id": _OWN_SESSION_PROP,
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
        "an unwanted plan entirely. Pass your own claude_session_id: closing another agent's plan "
        "is refused, because it would make that agent's remaining steps permanently unmatchable.",
        _schema({"session_id": {"type": "string"},
                 "claude_session_id": _OWN_SESSION_PROP}, ["session_id"]),
        tool_plan_done,
    ),
    "sgt_plan_adopt": (
        "Take over a plan session another Claude session started — use this when a plan shows as "
        "stalled and the agent that owned it is gone, so you can finish and close it. "
        "Non-destructive: its steps, confirmed matches, and pending predictions all survive, so "
        "continue from where that agent stopped instead of re-intaking the same plan (which would "
        "predict work that is already done).",
        _schema({"session_id": {"type": "string"},
                 "claude_session_id": _OWN_SESSION_PROP}, ["session_id"]),
        tool_plan_adopt,
    ),
}


# Tools that change the ideal, mapped to the verb the editor previews. An agent
# working through these is the one case where something moves in the graph with
# nobody watching a terminal, so the editor is told before it happens.
_ANNOUNCED = {"sgt_revert": "revert", "sgt_restore": "restore"}


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
