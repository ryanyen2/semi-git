"""The agentic loop: `plan intake`/`abandon`/`status` decompose a stated plan into predicted
hollow ops (off-chain, R18). Step<->op matching (the former `checkpoint`) and plan-drift (the
former `drift`) are folded into `sgt save` (U12/R10): a save auto-confirms unambiguous single-step
matches and reports the rest -- see `sgt.cli.porcelain`. Every verb mines the working tree on
contact first (R9).

`plan resume` orients you in an interrupted plan (which steps remain, how to reopen the
conversation) and `plan adopt` transfers a dead agent's plan to you -- the pair that makes the
ownership check on `done`/`abandon` safe, since a check with no transfer would turn every crashed
agent into a plan nobody may ever close."""

from __future__ import annotations

from ._common import _add_view_flags, _emit_json, _fail, _fail_json

_STATUS_ICON = {"pending": "○", "matched": "●"}


def register(subs, parent) -> None:
    pl = subs.add_parser("plan", parents=[parent])
    pl.add_argument("rest", nargs="*")
    # Only meaningful for `plan intake`: the drafting Claude Code session id, so a stalled plan can
    # be resumed directly with `claude --resume <id>`. Registered as a real optional so argparse
    # doesn't reject the leading `--` before it reaches `rest`.
    pl.add_argument("--claude-session", dest="claude_session", default=None)
    _add_view_flags(pl)  # only meaningful for `plan status`
    pl.set_defaults(func=_cmd_plan)


def _cmd_plan(args) -> int:
    return _plan(".", args.rest, args.as_json, args.full, args.claude_session)


def _plan(repo: str, rest: list[str], as_json: bool, full: bool = False,
          claude_session: str | None = None) -> int:
    """`sgt plan intake "<text>"` / `sgt plan abandon <session>` / `sgt plan status [--json]
    [--full]` (plan U14): plan-session intake, abandonment, and the read view over active
    sessions. `status`'s default is compact (per-session step/matched counts, no per-step
    detail); `--full` restores each step's title/status/spans."""
    usage = ('usage: sgt plan intake "<text>" | sgt plan status [--json] | '
             'sgt plan resume [<session>] | sgt plan adopt <session> | '
             'sgt plan done <session> | sgt plan abandon <session>')
    if not rest or rest[0] not in ("intake", "done", "abandon", "status", "adopt", "resume"):
        print(usage)
        return 2
    sub, opts = rest[0], rest[1:]
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so a session's baseline reflects current reality (R9)

    if sub == "intake":
        if not opts:
            print(usage)
            return 2
        from sgt.loop import plan as plan_mod

        session = plan_mod.intake(repo, " ".join(opts), claude_session_id=claude_session)
        if as_json:
            return _emit_json({
                "session_id": session.session_id, "step_count": len(session.steps),
                "steps": [{"title": s["title"], "predicted_feature": s["predicted_feature"]} for s in session.steps],
            })
        print(f"✓ intake: session {session.session_id} — {len(session.steps)} step(s)")
        for s in session.steps:
            suffix = f"  [{s['predicted_feature']}]" if s["predicted_feature"] else ""
            print(f"    {s['title']}{suffix}")
        return 0

    if sub in ("done", "abandon"):
        if not opts:
            print(usage)
            return 2
        return _close(repo, sub, opts[0], as_json, claude_session)

    if sub == "adopt":
        if not opts:
            print(usage)
            return 2
        return _adopt(repo, opts[0], as_json, claude_session)

    if sub == "resume":
        return _resume(repo, opts[0] if opts else None, as_json)

    return _status(repo, as_json, full)


def _close(repo: str, sub: str, session_id: str, as_json: bool, actor: str | None) -> int:
    """`plan done` / `plan abandon` -- the two ways a plan leaves the active surface. Both unlink the
    still-pending hollows, so both are ownership-checked: `--claude-session <id>` identifies the
    caller, and a mismatch is refused with the owner named rather than silently destroying another
    agent's in-flight plan."""
    from sgt.loop import plan as plan_mod

    close = plan_mod.mark_done if sub == "done" else plan_mod.abandon
    try:
        ok = close(repo, session_id, actor=actor)
    except plan_mod.PlanOwnershipError as e:
        if as_json:
            return _emit_json({"ok": False, "error": str(e), "owner": e.owner,
                               "session_id": e.session_id})
        return _fail(str(e))
    if as_json:
        return _emit_json({"ok": ok})
    if not ok:
        return _fail(f"no such session: {session_id}")
    if sub == "done":
        print(f"✓ plan session {session_id} closed (completed)")
    else:
        print(f"✓ abandoned plan session {session_id} -- its unfinished steps are recorded as "
              "open intents (sgt intent open)")
    return 0


def _adopt(repo: str, session_id: str, as_json: bool, actor: str | None) -> int:
    """`plan adopt <session>`: take over a plan another Claude session started (typically one whose
    agent died, leaving it stalled). Non-destructive -- the steps, their matches, and the pending
    hollows all survive, so work continues from where the previous agent stopped. This is the
    deliberate transfer that keeps the ownership check from turning every crashed agent into
    permanently stuck state."""
    from sgt.loop import plan as plan_mod

    ok, previous = plan_mod.adopt(repo, session_id, actor)
    if as_json:
        return _emit_json({"ok": ok, "session_id": session_id,
                           "previous_owner": previous, "owner": actor})
    if not ok:
        return _fail(f"no such session: {session_id}")
    was = f" (was {previous})" if previous else " (it had no owner)"
    print(f"✓ adopted plan session {session_id}{was}")
    print(f"  next: sgt plan resume {session_id}   see where it stands")
    return 0


def _resume(repo: str, session_id: str | None, as_json: bool) -> int:
    """`plan resume [<session>]`: where a plan stands and how to get back into it.

    The re-take itself is already non-destructive -- nothing needs to be reset to continue a stalled
    plan -- so what was actually missing was the *orientation*: which steps remain, and the
    `claude --resume <uuid>` handle for the conversation that was building it. With no argument it
    picks the stalled plan when there is exactly one, since that is the case a user hits after
    stepping away."""
    from sgt.api import plan_view

    sessions = plan_view(repo, full=True)["sessions"]
    if not sessions:
        if as_json:
            return _emit_json({"ok": False, "error": "no active plan sessions"})
        print("(no active plan sessions)")
        return 0

    if session_id is None:
        stalled = [s for s in sessions if s.get("derived_status") == "stalled"]
        pick = stalled if stalled else sessions
        if len(pick) != 1:
            if as_json:
                return _emit_json({"ok": False, "error": "several active plans -- name one",
                                   "session_ids": [s["session_id"] for s in pick]})
            print("several active plans — name one:")
            for s in pick:
                print(f"  sgt plan resume {s['session_id']}   {s['pending_count']} step(s) left")
            return 2
        session = pick[0]
    else:
        session = next((s for s in sessions
                        if s["session_id"] == session_id
                        or s["session_id"].startswith(session_id)), None)
        if session is None:
            return _fail_json(f"no such active session: {session_id}", as_json)

    if as_json:
        return _emit_json({"ok": True, **session})
    _print_resume(session)
    return 0


def _print_resume(session: dict) -> None:
    done = [s for s in session["steps"] if s["status"] == "matched"]
    pending = [s for s in session["steps"] if s["status"] == "pending"]
    state = session.get("derived_status", "active")
    print(f"plan {session['session_id']}  ({state}) — {len(done)}/{len(session['steps'])} done")
    for step in done:
        print(f"    [{_STATUS_ICON['matched']}] {step['title']}")
    for step in pending:
        # `covered` is the stall explanation: the predicted file saw edits but under other names, so
        # the exact matcher will never confirm it. Saying so is the difference between "this step is
        # undone" and "this step is done, just not where I predicted".
        why = ""
        if step.get("covered"):
            why = f"   (looks built: {step.get('coverage_reason', 'files already edited')})"
        print(f"    [{_STATUS_ICON['pending']}] {step['title']}{why}")

    print("\n  next:")
    chat = session.get("claude_session_id")
    if chat:
        print(f"    claude --resume {chat}        reopen the conversation that was building this")
    else:
        print("    claude --resume                 pick the conversation from the session list")
    if pending:
        print("    sgt save                        record the next step's edits; matching is automatic")
    if any(s.get("covered") for s in pending):
        print(f"    sgt plan done {session['session_id']}   if the rest already landed under other names")


def _status(repo: str, as_json: bool, full: bool) -> int:
    from sgt.api import plan_view

    view = plan_view(repo, full=full)
    if as_json:
        return _emit_json(view)
    if not view["sessions"]:
        print("(no active plan sessions)")
        return 0
    if full:
        for s in view["sessions"]:
            glyphs = "".join(_STATUS_ICON.get(st["status"], "?") for st in s["steps"])
            stalled = "  (stalled)" if s.get("derived_status") == "stalled" else ""
            print(f"  {s['session_id']}  {glyphs}{stalled}")
            for st in s["steps"]:
                print(f"    [{_STATUS_ICON.get(st['status'], '?')}] {st['title']}")
    else:
        for s in view["sessions"]:
            stalled = "  (stalled)" if s.get("derived_status") == "stalled" else ""
            print(f"  {s['session_id']}  {s['matched_count']}/{s['step_count']} step(s) matched{stalled}")
    return 0
