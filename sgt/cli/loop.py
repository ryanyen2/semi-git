"""The agentic loop: `plan intake`/`abandon`/`status` decompose a stated plan into predicted
hollow ops (off-chain, R18). Step<->op matching (the former `checkpoint`) and plan-drift (the
former `drift`) are folded into `sgt save` (U12/R10): a save auto-confirms unambiguous single-step
matches and reports the rest -- see `sgt.cli.porcelain`. Every verb mines the working tree on
contact first (R9)."""

from __future__ import annotations

from ._common import _add_view_flags, _emit_json, _fail

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
    usage = ('usage: sgt plan intake "<text>" | sgt plan done <session> | '
             'sgt plan abandon <session> | sgt plan status [--json]')
    if not rest or rest[0] not in ("intake", "done", "abandon", "status"):
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

    if sub == "done":
        if not opts:
            print(usage)
            return 2
        from sgt.loop import plan as plan_mod

        ok = plan_mod.mark_done(repo, opts[0])
        if as_json:
            return _emit_json({"ok": ok})
        return 0 if ok else _fail(f"no such session: {opts[0]}")

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
            stalled = "  (stalled)" if s.get("derived_status") == "stalled" else ""
            print(f"  {s['session_id']}  {glyphs}{stalled}")
            for st in s["steps"]:
                print(f"    [{_STATUS_ICON.get(st['status'], '?')}] {st['title']}")
    else:
        for s in view["sessions"]:
            stalled = "  (stalled)" if s.get("derived_status") == "stalled" else ""
            print(f"  {s['session_id']}  {s['matched_count']}/{s['step_count']} step(s) matched{stalled}")
    return 0
