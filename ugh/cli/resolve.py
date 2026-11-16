"""`ugh resolve <symbol>` (plan U13/R11): close a same-symbol fork.

A `sync` that can't fold two divergent edits of one symbol records the fork in committed
`.sgt/forks.json` with a `ugh resolve <symbol>` remedy. Edit the file to merge both versions, then:

    ugh resolve <symbol> --apply  # commit the edited version (gated on the oracle)

A bare `ugh resolve <symbol>` prints both versions of the symbol.
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
        _print_tips(repo, symbol, fork["file"])
        print(f"\n  edit {fork['file']} to merge both versions, then: ugh resolve {symbol} --apply")
        return 0

    if not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        from ugh.api import resolve_apply_preview_view

        from ._common import confirm

        pview = resolve_apply_preview_view(repo, symbol)
        oracle = ("runs the oracle before landing" if pview["oracle_configured"]
                  else "no oracle configured — lands unverified")
        if not confirm([f"  {pview['so_what']}", f"    {oracle}"], f"resolve {symbol}?"):
            print("  aborted — nothing resolved.")
            return 1

    return _apply_resolution(repo, symbol, fork, as_json, override=override, reason=reason, by=by)


def _print_tips(repo: str, symbol: str, file: str) -> None:
    """Each tip's text of `symbol`, or of the whole file when the symbol is not found in it."""
    from ugh.api import _side_entity_spans, fork_detail_view

    for name, tip in zip("AB", fork_detail_view(repo, symbol).get("tips", [])):
        text = tip["files"].get(file, "")
        span = next((s for s in _side_entity_spans(file, text.encode("utf-8"))
                     if s["symbol"] == symbol), None)
        if span is not None:
            text = "\n".join(text.splitlines()[span["start_line"] - 1:span["end_line"]])
        print(f"version {name} ({tip['op_id'][:8]}):")
        print(text.rstrip("\n"))


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
