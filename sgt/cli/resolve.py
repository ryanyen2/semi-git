"""`sgt resolve <symbol>` (plan U13/R11): one guided verb for resolving a same-symbol fork.

A `sync`/`land` that can't fold two divergent edits of one symbol records the fork in committed
`.sgt/forks.json` with a `sgt resolve <symbol>` remedy. This is that verb: it wraps the three-step
spine -- `merge_op` (draft a reconciliation hollow) -> edit the tree -> `fulfill` -> `land`
(oracle-gated) -- into one command, so a user never stitches the pieces together by hand
(`sgt advanced merge-op <a> <b>` remains the low-level escape hatch):

    sgt resolve <symbol>          # draft the reconciliation; then edit the file to merge both sides
    sgt resolve <symbol> --apply  # fulfill from the edited tree and land it (gated on the oracle)

Nothing new in the kernel: `resolve` only sequences existing `sgt.core.rewrite` verbs and reads the
same `.sgt/forks.json` the remedy string already names.
"""

from __future__ import annotations

from ._common import _emit_json


def register(subs, parent) -> None:
    p = subs.add_parser("resolve", parents=[parent])
    p.add_argument("symbol")
    p.add_argument("--apply", action="store_true",
                   help="fulfill the drafted reconciliation from the edited tree and land it")
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
    from sgt.api import forks_view
    from sgt.core import rewrite
    from sgt.core.store import Store

    fork = next((f for f in forks_view(repo)["forks"] if f["symbol"] == symbol), None)
    if fork is None:
        msg = f"no open fork for {symbol!r} — run `sgt advanced forks` to list the open forks"
        return _emit_json({"ok": False, "error": msg}) if as_json else _err(msg)
    tip_a, tip_b = fork["tips"]

    import sys

    if not apply:
        draft = rewrite.merge_op(repo, tip_a, tip_b)
        if not draft.ok:
            return _emit_json({"ok": False, "error": draft.message}) if as_json else _err(draft.message)
        if as_json:
            return _emit_json({"ok": True, "draft_id": draft.draft_id, "symbol": symbol, "file": fork["file"]})
        # Interactive tty: show a side-by-side diff of the two tips and let the user pick a side (or
        # edit) in one step, then fall straight into the fulfill→land spine below -- no second command.
        # A non-tty draft keeps the two-step contract (print the `--apply` hint and stop).
        if sys.stdin.isatty() and sys.stdout.isatty():
            picked = _interactive_pick(repo, symbol, fork["file"])
            if picked == "quit":
                print(f"  draft kept — edit {fork['file']} then: sgt resolve {symbol} --apply")
                return 0
            return _apply_resolution(repo, symbol, as_json, override=override, reason=reason, by=by)
        print(f"✓ drafted a reconciliation of {symbol}")
        print(f"  edit {fork['file']} to merge both versions, then: sgt resolve {symbol} --apply")
        return 0

    # --apply: the confirm step. On an interactive tty show the three-step remedy feedforward first
    # (fulfill your merge → run the oracle → land, closing the fork) and let the user back out;
    # --json and a non-tty apply immediately (the machine/CI contract), no new args.
    if not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        from sgt.api import resolve_apply_preview_view

        from ._common import confirm_collab

        pview = resolve_apply_preview_view(repo, symbol)
        if not confirm_collab(pview, f"resolve {symbol}?"):
            print("  aborted — nothing resolved.")
            return 1

    return _apply_resolution(repo, symbol, as_json, override=override, reason=reason, by=by)


def _interactive_pick(repo: str, symbol: str, file: str) -> str:
    """Render the two tips side by side and prompt for a resolution. `l`/`r` write the chosen tip's
    content for the forked file to the working tree; `e` opens `$EDITOR` on it for a manual merge;
    `q` keeps the draft untouched. Returns the choice ("left"/"right"/"edit"/"quit")."""
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from sgt.api import fork_detail_view
    from sgt.tui.fork_diff import side_by_side

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


def _apply_resolution(repo: str, symbol: str, as_json: bool, *, override: str | None = None,
                      reason: str | None = None, by: str | None = None) -> int:
    """The fulfill→oracle→land spine shared by `--apply` and the interactive pick: find the
    reconciliation drafted for this symbol, fulfill it from the edited tree, and land it
    (`rewrite.land` refuses unless the oracle passes; landing closes the fork record)."""
    from sgt.core import oracle, rewrite
    from sgt.core.store import Store

    store = Store(repo)
    draft_id = next(
        (did for did, rec in rewrite.pending_drafts(repo).items()
         if any((h := store.get_hollow(hid)) is not None and symbol in h.footprint
                for hid in rec["hollow_ids"])),
        None,
    )
    if draft_id is None:
        msg = (f"no drafted resolution for {symbol!r} yet — run `sgt resolve {symbol}` first, "
               f"edit the file to merge both versions, then re-run with --apply")
        return _emit_json({"ok": False, "error": msg}) if as_json else _err(msg)

    # A manual `--override pass` lets the guided verb land when no oracle is configured (the common
    # case; the land gate's own error names this remedy). Otherwise run the configured tests here.
    override_tuple = (override, reason or "", by) if override else None
    try:
        candidate = rewrite.fulfill(repo, draft_id, from_tree=True)
        if override_tuple is None:
            oracle.run(repo, ideal=candidate)  # the "run the tests" step of the guided flow, on the fresh candidate
        sha = rewrite.land(repo, message=f"resolve fork: {symbol}", override=override_tuple)
    except rewrite.RewriteError as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _err(str(e))
    if as_json:
        return _emit_json({"ok": True, "symbol": symbol, "commit": sha})
    print(f"✓ resolved {symbol} and landed the reconciliation ({sha[:12]}) — the fork is closed")
    return 0


def _err(msg: str) -> int:
    print(f"✗ {msg}")
    return 1
