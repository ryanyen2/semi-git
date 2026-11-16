"""`ugh resolve <symbol>` (plan U13/R11): close a same-symbol fork.

A `sync` that can't fold two divergent edits of one symbol records the fork in committed
`.sgt/forks.json` with a `ugh resolve <symbol>` remedy. Edit the file to merge both versions, then:

    ugh resolve <symbol> --apply  # commit the edited version (gated on the oracle)

On a tty, a bare `ugh resolve <symbol>` shows both tips side by side and applies the side picked.
"""

from __future__ import annotations

from ._common import _emit_json


def register(subs, parent) -> None:
    p = subs.add_parser("resolve", parents=[parent])
    p.add_argument("symbol")
    p.add_argument("--apply", action="store_true",
                   help="commit the edited tree's version of the symbol, closing the fork")
    p.add_argument("--override", help="land with a manual oracle verdict (pass|fail) when no "
                                      "test runner is configured; the error names this")
    p.add_argument("--reason")
    p.add_argument("--by")
    p.set_defaults(func=_cmd_resolve)


def _cmd_resolve(args) -> int:
    return _resolve(".", args.symbol, args.apply, args.as_json,
                    override=args.override, reason=args.reason, by=args.by)


def _resolve(repo: str, symbol: str, apply: bool, as_json: bool,
             *, override: str | None = None, reason: str | None = None, by: str | None = None) -> int:
    import sys

    from ugh.api import forks_view

    fork = next((f for f in forks_view(repo)["forks"] if f["symbol"] == symbol), None)
    if fork is None:
        msg = f"no open fork for {symbol!r} — `ugh status` lists the open forks"
        return _emit_json({"ok": False, "error": msg}) if as_json else _err(msg)

    if not apply:
        if as_json:
            return _emit_json({"ok": True, "symbol": symbol, "file": fork["file"]})
        if sys.stdin.isatty() and sys.stdout.isatty():
            if _interactive_pick(repo, symbol, fork["file"]) == "quit":
                print(f"  nothing resolved — edit {fork['file']} then: ugh resolve {symbol} --apply")
                return 0
            return _apply_resolution(repo, symbol, fork, as_json, override=override, reason=reason, by=by)
        print(f"  edit {fork['file']} to merge both versions, then: ugh resolve {symbol} --apply")
        return 0

    if not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        from ugh.api import resolve_apply_preview_view

        from ._common import confirm_collab

        if not confirm_collab(resolve_apply_preview_view(repo, symbol), f"resolve {symbol}?"):
            print("  aborted — nothing resolved.")
            return 1

    return _apply_resolution(repo, symbol, fork, as_json, override=override, reason=reason, by=by)


def _interactive_pick(repo: str, symbol: str, file: str) -> str:
    """Render the two tips side by side and prompt for a resolution. `l`/`r` write the chosen tip's
    content for the forked file to the working tree; `e` opens `$EDITOR` on it for a manual merge;
    `q` resolves nothing. Returns the choice ("left"/"right"/"edit"/"quit")."""
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from ugh.api import fork_detail_view
    from ugh.tui.fork_diff import side_by_side

    detail = fork_detail_view(repo, symbol)
    tips = detail.get("tips", [])
    if len(tips) != 2:
        return "quit"
    color = sys.stdout.isatty()
    width = shutil.get_terminal_size((120, 40)).columns
    files_a = {k: v for k, v in tips[0]["files"].items() if k == file} or tips[0]["files"]
    files_b = {k: v for k, v in tips[1]["files"].items() if k == file} or tips[1]["files"]
    print(f"── fork on {symbol} ──  (◀ left = tip A   ▶ right = tip B)")
    for line in side_by_side(files_a, files_b, width=width, color=color):
        print(line)
    choice = (input("pick [l]eft / [r]ight / [e]dit / [q]uit: ").strip().lower() or "q")[0]

    target = Path(repo) / file
    if choice == "l":
        target.write_text(tips[0]["files"].get(file, ""), encoding="utf-8")
        return "left"
    if choice == "r":
        target.write_text(tips[1]["files"].get(file, ""), encoding="utf-8")
        return "right"
    if choice == "e":
        subprocess.call([os.environ.get("EDITOR", "vi"), str(target)])
        return "edit"
    return "quit"


def _apply_resolution(repo: str, symbol: str, fork: dict, as_json: bool, *,
                      override: str | None = None, reason: str | None = None,
                      by: str | None = None) -> int:
    from ugh.core import rewrite

    tip_a, tip_b = fork["tips"]
    override_tuple = (override, reason or "", by) if override else None
    try:
        sha = rewrite.resolve_fork(repo, tip_a, tip_b, message=f"resolve fork: {symbol}",
                                   override=override_tuple)
    except rewrite.ResolveError as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _err(str(e))
    if as_json:
        return _emit_json({"ok": True, "symbol": symbol, "commit": sha})
    print(f"✓ resolved {symbol} and landed the reconciliation ({sha[:12]}) — the fork is closed")
    return 0


def _err(msg: str) -> int:
    print(f"✗ {msg}")
    return 1
