"""`sgt advanced suggestions` (plan U7): the clustering / merge suggestion queue.

`sgt advanced suggestions list [--json]` renders `sgt.api.suggestion_view` -- every open suggestion
(a `merge`/`split` a clustering-critic proposes, or a `conflict` a sync recorded, U6). Accepting a
suggestion is the existing feature-verb surface (`sgt feature regroup merge`/`split`, `sgt feature
regroup move`); `sgt advanced suggestions dismiss <id>...` drops one from the queue. This module
never mutates the feature tree -- it only lists and dismisses, per U7's "clustering proposes, the
user disposes" boundary.
"""

from __future__ import annotations

from ._common import _emit_json

_USAGE = ("usage: sgt advanced suggestions list [--json] | "
          "sgt advanced suggestions dismiss <id>... [--json]")

# The feature verb that accepts each suggestion kind (shown as a hint, never run here).
_ACCEPT_HINT = {
    "merge": "sgt feature regroup merge <survivor> <absorbed>",
    "split": "sgt feature regroup split <feature>",
    "conflict": "sgt feature regroup move <op>... --to <feature>",
}


def register(subs, parent) -> None:
    sp = subs.add_parser("suggestions", parents=[parent])
    sp.add_argument("sub", nargs="?")
    sp.add_argument("ids", nargs="*")
    sp.set_defaults(func=_cmd_suggestions)


def _cmd_suggestions(args) -> int:
    return _suggestions(".", args.sub, args.ids, args.as_json)


def _suggestions(repo: str, sub: str | None, ids: list[str], as_json: bool) -> int:
    if sub not in ("list", "dismiss"):
        print(_USAGE)
        return 2
    if sub == "list":
        from sgt.api import suggestion_view

        view = suggestion_view(repo)
        if as_json:
            return _emit_json(view)
        if not view["suggestions"]:
            print("(no open suggestions)")
            return 0
        print(f"{view['count']} open suggestion(s):")
        for s in view["suggestions"]:
            feats = ", ".join(s["features"]) or "—"
            print(f"  {s['id']}  [{s['kind']}]  {feats}"
                  + (f"  — {s['rationale']}" if s["rationale"] else ""))
            print(f"    accept: {_ACCEPT_HINT.get(s['kind'], '(feature verb)')}"
                  f"  ·  dismiss: sgt advanced suggestions dismiss {s['id']}")
        return 0

    # dismiss
    from sgt.core import suggest

    dismissed = sum(1 for i in ids if suggest.dismiss(repo, i))
    if as_json:
        return _emit_json({"ok": True, "dismissed": dismissed})
    print(f"✓ dismissed {dismissed} suggestion(s)")
    return 0
