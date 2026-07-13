"""`sgt session` (plan U30, D5): named scratch-tree lifecycle for agentic sessions.

`session start <name> [--base <branch>]` materializes a `git worktree` off `base` (default: the
current branch) onto a fresh `sgt-session/<name>` branch. `session status [<name>] [--watch]`
reports active sessions and any footprint overlap between them -- the early-fork warning, a
report, never a lock; `--watch` polls it every couple of seconds until interrupted, no daemon.
`session land <name>` advances the target branch by the U23 CAS land and stamps `session=<name>`
onto the landed ops' attribution. `session gc [--force]` reaps sessions whose owning pid has
died (or, with `--force`, every session) and removes their scratch trees.
"""

from __future__ import annotations

import time

from ._common import _emit_json, _fail

_USAGE = ("usage: sgt session start <name> [--base <branch>] | "
          "sgt session status [<name>] [--watch] [--json] | "
          "sgt session land <name> [--json] | sgt session gc [--force] [--json]")


def register(subs, parent) -> None:
    p = subs.add_parser("session", parents=[parent])
    p.add_argument("sub", nargs="?")
    p.add_argument("name", nargs="?")
    p.add_argument("--base")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cmd_session)


def _cmd_session(args) -> int:
    return _session(".", args.sub, args.name, args.base, args.watch, args.force, args.as_json)


def _session(repo, sub, name, base, watch, force, as_json) -> int:
    from sgt.core import session as session_mod

    if sub not in ("start", "status", "land", "gc"):
        print(_USAGE)
        return 2
    if sub == "start":
        if name is None:
            print(_USAGE)
            return 2
        return _start(repo, session_mod, name, base, as_json)
    if sub == "status":
        return _status(repo, session_mod, name, watch, as_json)
    if sub == "land":
        if name is None:
            print(_USAGE)
            return 2
        return _land(repo, session_mod, name, as_json)
    return _gc(repo, session_mod, force, as_json)


def _start(repo, session_mod, name, base, as_json) -> int:
    try:
        session = session_mod.start(repo, name, base=base)
    except session_mod.SessionError as e:
        return _fail(str(e)) if not as_json else _emit_json({"error": str(e)})
    if as_json:
        return _emit_json({
            "name": session.name, "branch": session.branch, "scratch": session.scratch,
            "target_branch": session.target_branch, "base_ref": session.base_ref,
        })
    print(f"session {session.name!r} started: {session.scratch} (branch {session.branch}, "
          f"base {session.target_branch}@{session.base_ref[:8]})")
    return 0


def _render_status(view: dict) -> None:
    if not view["sessions"]:
        print("no active sessions")
    for s in view["sessions"]:
        liveness = "alive" if s["alive"] else "DEAD (gc will reap)"
        print(f"  {s['name']}: {s['new_op_count']} new op(s), pid {s['owner_pid']} ({liveness}), "
              f"-> {s['target_branch']}")
    for pair in view["overlaps"]:
        print(f"  ⚠ {pair['a']} and {pair['b']} both touch: {', '.join(pair['symbols'])}")


def _status(repo, session_mod, name, watch, as_json) -> int:
    from sgt.api import sessions_view

    if name is not None and name not in {s.name for s in session_mod.list_sessions(repo)}:
        return _fail(f"no such session {name!r}")

    def _view() -> dict:
        view = sessions_view(repo)
        if name is not None:
            view = {**view, "sessions": [s for s in view["sessions"] if s["name"] == name]}
        return view

    if not watch:
        view = _view()
        if as_json:
            return _emit_json(view)
        _render_status(view)
        return 0

    try:
        while True:
            _render_status(_view())
            time.sleep(2)
    except KeyboardInterrupt:
        return 0


def _land(repo, session_mod, name, as_json) -> int:
    from sgt.api import land_view

    try:
        report = session_mod.land(repo, name)
    except session_mod.SessionError as e:
        return _fail(str(e)) if not as_json else _emit_json({"error": str(e)})
    view = land_view(report)
    if as_json:
        return _emit_json(view)
    if not report.landed:
        return _fail(f"session land refused: {report.blocked_reason}")
    print(f"session {name!r} landed onto {report.branch}: {report.land_sha[:8]} "
          f"(+{report.ops_added} op(s))")
    return 0


def _gc(repo, session_mod, force, as_json) -> int:
    reaped = session_mod.gc(repo, force=force)
    if as_json:
        return _emit_json({"reaped": list(reaped)})
    if not reaped:
        print("nothing to reap")
    for name in reaped:
        print(f"reaped session {name!r}")
    return 0
