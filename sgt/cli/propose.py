"""`sgt propose` (plan U24, C10): the review object end-to-end.

`sgt propose create [--base REF] [--title "..."] [--description "..."]` captures the current ideal's
Δ over `REF`'s committed ideal as a committed, immutable review object (rejecting a Δ that forks the
base); `sgt propose status <id>` reports staleness by re-union (current / clean-reunion / fork);
`sgt propose land <id>` advances the base branch by the U23 CAS (refusing a stale-forked proposal);
`sgt propose render <id> --github` emits a suggested branch name and a PR body in plain markdown a
reviewer without sgt can act on. `--json` gives the canonical `sgt.api` projection throughout.
"""

from __future__ import annotations

from ._common import _emit_json, _fail

_USAGE = ('usage: sgt propose create [--base REF] [--title "..."] [--description "..."] [--json] | '
          'sgt propose status <id> [--json] | sgt propose land <id> [--json] | '
          'sgt propose render <id> --github [--json]')


def register(subs, parent) -> None:
    p = subs.add_parser("propose", parents=[parent])
    p.add_argument("sub", nargs="?")
    p.add_argument("id", nargs="?")
    p.add_argument("--base", default="main")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--github", action="store_true")
    p.set_defaults(func=_cmd_propose)


def _cmd_propose(args) -> int:
    return _propose(".", args.sub, args.id, args.base, args.title, args.description,
                    args.github, args.as_json)


def _propose(repo: str, sub: str | None, pid: str | None, base: str, title: str | None,
             description: str | None, github: bool, as_json: bool) -> int:
    from sgt.core import propose

    if sub not in ("create", "status", "land", "render"):
        print(_USAGE)
        return 2

    if sub == "create":
        return _create(repo, propose, base, title, description, as_json)

    if pid is None:
        print(_USAGE)
        return 2
    if sub == "status":
        return _status(repo, pid, as_json)
    if sub == "render":
        return _render(repo, propose, pid, github, as_json)
    return _land(repo, propose, pid, as_json)


def _create(repo, propose, base, title, description, as_json) -> int:
    from sgt.api import proposal_view

    try:
        p = propose.create(repo, base_ref=base, title=title, description=description)
    except ValueError as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _fail(str(e))
    if as_json:
        return _emit_json(proposal_view(repo, p.id))
    print(f"✓ proposal {p.id} (base {p.base_ref}, +{len(p.delta_ids)} op(s), "
          f"{len(p.feature_delta)} feature(s))")
    return 0


def _status(repo, pid, as_json) -> int:
    from sgt.api import proposal_view

    view = proposal_view(repo, pid)
    if "error" in view:
        return _emit_json(view) if as_json else _fail(view["error"])
    if as_json:
        return _emit_json(view)
    st = view["status"]
    print(f"proposal {pid}: {st['state']}")
    if st.get("note"):
        print(f"  {st['note']}")
    for f in st["forks"]:
        print(f"  ⚠ {f['symbol']}: {f['remedy']}")
    return 0


def _render(repo, propose, pid, github, as_json) -> int:
    from sgt.api import proposal_view

    if not github:
        print("usage: sgt propose render <id> --github [--json]")
        return 2
    view = proposal_view(repo, pid)
    if "error" in view:
        return _emit_json(view) if as_json else _fail(view["error"])
    rendered = propose.render_github(view)
    if as_json:
        return _emit_json(rendered)
    print(f"branch: {rendered['branch']}")
    print(f"title:  {rendered['pr_title']}\n")
    print(rendered["pr_body"])
    return 0


def _land(repo, propose, pid, as_json) -> int:
    from sgt.api import land_view
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.core.sync import MinerVersionMismatch
    from sgt.store.gitbind import GitError

    try:
        report = propose.land(repo, pid)
    except (DirtyWorkingTreeError, GitError, ValueError, MinerVersionMismatch) as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _fail(str(e))

    view = land_view(report)
    if as_json:
        return _emit_json({"ok": report.landed, **view})
    if report.landed:
        print(f"✓ propose land {report.branch}: {report.land_sha[:12]} (+{report.ops_added} op(s))")
        return 0
    print(f"✗ propose land {report.branch}: {report.blocked_reason}")
    for sym, a, b in report.forks:
        print(f"    {sym}: sgt merge-op {a[:8]} {b[:8]}")
    return 1
