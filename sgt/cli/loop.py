"""The agentic loop (plan U14): `plan intake`/`abandon`/`status` decompose a stated plan into
predicted hollow ops (off-chain, R18); `checkpoint` previews footprint-overlap matches between
pending steps and ops mined since (and applies a named group); `drift` lists ops no active plan
predicted. Every verb mines the working tree on contact first (R9)."""

from __future__ import annotations

from ._common import _add_view_flags, _emit_json, _fail

_STATUS_ICON = {"pending": "○", "matched": "●"}


def register(subs, parent) -> None:
    pl = subs.add_parser("plan", parents=[parent])
    pl.add_argument("rest", nargs="*")
    _add_view_flags(pl)  # only meaningful for `plan status`
    pl.set_defaults(func=_cmd_plan)

    cp = subs.add_parser("checkpoint", parents=[parent])
    cp.add_argument("--confirm-hollow", action="append", dest="confirm_hollow", default=[])
    cp.add_argument("--confirm-op", action="append", dest="confirm_op", default=[])
    cp.set_defaults(func=_cmd_checkpoint)

    dp = subs.add_parser("drift", parents=[parent])
    _add_view_flags(dp)
    dp.set_defaults(func=_cmd_drift)


def _cmd_plan(args) -> int:
    return _plan(".", args.rest, args.as_json, args.full)


def _cmd_checkpoint(args) -> int:
    return _checkpoint(".", args.confirm_hollow, args.confirm_op, args.as_json)


def _cmd_drift(args) -> int:
    return _drift(".", args.as_json, args.full)


def _plan(repo: str, rest: list[str], as_json: bool, full: bool = False) -> int:
    """`sgt plan intake "<text>"` / `sgt plan abandon <session>` / `sgt plan status [--json]
    [--full]` (plan U14): plan-session intake, abandonment, and the read view over active
    sessions. `status`'s default is compact (per-session step/matched counts, no per-step
    detail); `--full` restores each step's title/status/spans."""
    usage = 'usage: sgt plan intake "<text>" | sgt plan abandon <session> | sgt plan status [--json]'
    if not rest or rest[0] not in ("intake", "abandon", "status"):
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

        session = plan_mod.intake(repo, " ".join(opts))
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

    if sub == "abandon":
        if not opts:
            print(usage)
            return 2
        from sgt.loop import plan as plan_mod

        ok = plan_mod.abandon(repo, opts[0])
        if as_json:
            return _emit_json({"ok": ok})
        return 0 if ok else _fail(f"no such session: {opts[0]}")

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
            print(f"  {s['session_id']}  {glyphs}")
            for st in s["steps"]:
                print(f"    [{_STATUS_ICON.get(st['status'], '?')}] {st['title']}")
    else:
        for s in view["sessions"]:
            print(f"  {s['session_id']}  {s['matched_count']}/{s['step_count']} step(s) matched")
    return 0


def _checkpoint(repo: str, hollow_ids: list[str], op_ids: list[str], as_json: bool) -> int:
    """`sgt checkpoint [--json]` (preview) / `sgt checkpoint --confirm-hollow <id>...
    --confirm-op <id>...` (plan U14): the pure footprint-overlap preview between pending plan
    steps and unpredicted real ops, and the explicit, one-group-at-a-time confirmation."""
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so the preview reflects current reality (R9)

    if hollow_ids or op_ids:
        from sgt.loop import plan as plan_mod
        from sgt.loop.match import confirm_match

        sessions = plan_mod.active_sessions(repo)
        session_id = next(
            (sid for sid, rec in sessions.items() if any(s["hollow_id"] in hollow_ids for s in rec["steps"])),
            None,
        )
        if session_id is None:
            return _emit_json({"error": "no session"}) if as_json else _fail(f"no active session owns hollow(s) {hollow_ids}")
        confirm_match(repo, session_id, hollow_ids, op_ids)
        if as_json:
            return _emit_json({"ok": True, "session_id": session_id, "hollow_ids": hollow_ids, "op_ids": op_ids})
        print(f"✓ confirmed {len(hollow_ids)} hollow(s) matched to {len(op_ids)} op(s) in session {session_id}")
        return 0

    from sgt.api import plan_view

    view = plan_view(repo)["checkpoint"]
    if as_json:
        return _emit_json(view)
    if not view["matches"] and not view["drift_op_ids"]:
        print("(nothing to checkpoint)")
        return 0
    for group in view["matches"]:
        print(f"  session {group['session_id']}: {len(group['hollow_ids'])} step(s) <-> {len(group['op_ids'])} op(s)")
        print(f"    hollow: {', '.join(h[:12] for h in group['hollow_ids'])}")
        print(f"    op:     {', '.join(o[:12] for o in group['op_ids'])}")
    if view["drift_op_ids"]:
        print(f"  drift: {', '.join(o[:12] for o in view['drift_op_ids'])}")
    return 0


def _drift(repo: str, as_json: bool, full: bool = False) -> int:
    """`sgt drift [--json] [--full]` (plan U14): every op not predicted by any active plan
    session. Compact by default (`{count, op_ids, kinds}`, no spans); `--full` restores each
    entry's footprint and current file/line spans."""
    from sgt.api import drift_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so drift reflects current reality (R9)
    view = drift_view(repo, full=full)
    if as_json:
        return _emit_json(view)
    if full:
        if not view["entries"]:
            print("(no drift — every recent op was predicted by an active plan)")
            return 0
        for e in view["entries"]:
            print(f"  {e['op_id'][:12]} [{e['kind']}]: {', '.join(e['footprint'])}")
        return 0
    if not view["op_ids"]:
        print("(no drift — every recent op was predicted by an active plan)")
        return 0
    for op_id in view["op_ids"]:
        print(f"  {op_id[:12]}")
    return 0
