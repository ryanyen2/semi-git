"""`sgt resolve <symbol>` (plan U13/R11): one guided verb for resolving a same-symbol fork.

A `sync`/`land` that can't fold two divergent edits of one symbol records the fork in committed
`.sgt/forks.json` with a `sgt merge-op <a> <b>` remedy. This wraps that remedy's three-step spine
-- `merge_op` (draft a reconciliation hollow) -> edit the tree -> `fulfill` -> `land` (oracle-gated)
-- into one verb, so a user never stitches the pieces together by hand:

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
    p.set_defaults(func=_cmd_resolve)


def _cmd_resolve(args) -> int:
    return _resolve(".", args.symbol, args.apply, args.as_json)


def _resolve(repo: str, symbol: str, apply: bool, as_json: bool) -> int:
    from sgt.api import forks_view
    from sgt.core import rewrite
    from sgt.core.store import Store

    fork = next((f for f in forks_view(repo)["forks"] if f["symbol"] == symbol), None)
    if fork is None:
        msg = f"no open fork for {symbol!r} — run `sgt forks` to list the open forks"
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

    # --apply: find the reconciliation drafted for this symbol, fulfill it from the edited tree,
    # and land it (rewrite.land refuses unless the oracle passes; landing closes the fork record).
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

    try:
        candidate = rewrite.fulfill(repo, draft_id, from_tree=True)
        oracle.run(repo, ideal=candidate)  # the "run the tests" step of the guided flow, on the fresh candidate
        sha = rewrite.land(repo, message=f"resolve fork: {symbol}")
    except rewrite.RewriteError as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _err(str(e))
    if as_json:
        return _emit_json({"ok": True, "symbol": symbol, "commit": sha})
    print(f"✓ resolved {symbol} and landed the reconciliation ({sha[:12]}) — the fork is closed")
    return 0


def _err(msg: str) -> int:
    print(f"✗ {msg}")
    return 1
