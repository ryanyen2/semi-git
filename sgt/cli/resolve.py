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

    if not apply:
        draft = rewrite.merge_op(repo, tip_a, tip_b)
        if not draft.ok:
            return _emit_json({"ok": False, "error": draft.message}) if as_json else _err(draft.message)
        if as_json:
            return _emit_json({"ok": True, "draft_id": draft.draft_id, "symbol": symbol, "file": fork["file"]})
        print(f"✓ drafted a reconciliation of {symbol}")
        print(f"  edit {fork['file']} to merge both versions, then: sgt resolve {symbol} --apply")
        return 0

    # --apply: the confirm step. On an interactive tty show the three-step remedy feedforward first
    # (fulfill your merge → run the oracle → land, closing the fork) and let the user back out;
    # --json and a non-tty apply immediately (the machine/CI contract), no new args.
    import sys

    if not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        from sgt.api import resolve_apply_preview_view

        from ._common import confirm_collab

        pview = resolve_apply_preview_view(repo, symbol)
        if not confirm_collab(pview, f"resolve {symbol}?"):
            print("  aborted — nothing resolved.")
            return 1

    # find the reconciliation drafted for this symbol, fulfill it from the edited tree, and land it
    # (rewrite.land refuses unless the oracle passes; landing closes the fork record).
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
    from sgt.core import oracle

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
