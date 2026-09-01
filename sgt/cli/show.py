"""`sgt show <sel>` -- "what is this?" for any id sgt printed (Phase 3 item 5).

Every id in sgt's output is a token the user is expected to type back somewhere: the `f-` handle on
a graph node, the bare hex off `--ops`, a `f-x:slug` checkpoint, a `file::name` symbol. But choosing
the verb that accepts a given token required already knowing what *kind* of thing it was --
`feature why` for an op, `intent show` for a feature, `advanced state` for the ideal -- which is
backwards when the token is exactly the thing you don't recognize.

This verb takes any of them and reads out identity, extent, consequence, and next steps. It is
read-only and offline (no LLM, no mining), because its job is to be the thing a cautious user runs
*before* a mutating verb: the revert offer is printed last, after the cost of taking it.

All projection lives in `api.show_view`; this module only renders.
"""

from __future__ import annotations

import time

from ._common import _emit_json, _fail
from .inspect import _fmt_age


def register(subs, parent) -> None:
    p = subs.add_parser("show", parents=[parent],
                        help="what is this? — or, with --at, what it was at a past point")
    p.add_argument("sel", nargs="*",
                   help="a feature id/label, a checkpoint, an op id, a symbol, a save id from "
                        "`sgt log`, or (with --at) a file")
    p.add_argument("--at", metavar="SPEC", default=None,
                   help="read it as it was at a past point: a commit index (`12`), an op set "
                        "(`op:<id>,...`), or a ref. Omit the selection to list what existed there")
    p.add_argument("--saves", metavar="N", type=int, default=5,
                   help="how many saves to list (default 5); the rest are counted, not shown")
    p.set_defaults(func=_cmd_show)


def _cmd_show(args) -> int:
    """One verb, two readings of "show me this", chosen by whether a point in time was named.

    They could have been two verbs -- and briefly were, when a `show` that reads a file at a past
    frontier and a `show` that explains an id landed from different directions. But a user does not
    hold two concepts here: they are pointing at something and asking to see it. `--at` is the same
    time modifier `sgt log --at` and `sgt advanced fold --at` already use, so "add `--at` to look at
    the past" is one idea that composes across the tool rather than a second verb to learn."""
    selection = " ".join(args.sel)
    if args.at is not None:
        from .inspect import _show_at

        return _show_at(".", args.at, selection or None, args.as_json)
    if not selection:
        print("usage: sgt show <sel>            what is this? (feature, checkpoint, op, symbol, save)\n"
              "       sgt show <file> --at 12   that file as it was at commit 12\n"
              "       sgt show --at 12          what existed at commit 12")
        return 2
    return _show(".", selection, args.as_json, args.saves)


def _show(repo: str, target: str, as_json: bool, save_limit: int = 5) -> int:
    from sgt.api import show_view

    view = show_view(repo, target, save_limit=max(1, save_limit))
    if as_json:
        return _emit_json(view)
    if not view["ok"]:
        _fail(view["message"])
        _print_next(view["next"])
        return 1
    _print_show(view)
    return 0


def _print_show(view: dict) -> None:
    label = f'  "{view["label"]}"' if view.get("label") else ""
    # The short handle in the header, matching the token the graph gutter shows and the token the
    # `next:` commands use -- so the whole block reads as being about one thing the user can retype.
    print(f'{view["kind"]} {view["handle"]}{label}')

    print(f"  {_extent(view)}")
    # A ◆ row is the one kind that carries a sentence saying what the work WAS, written when the row
    # was named. Everything else on this card is shape -- counts, symbols, saves -- and shape does
    # not tell a reader coming to unfamiliar history what they are looking at. Only this kind has it.
    if view.get("rationale"):
        print(f'  {view["rationale"]}')
    if view["feature"]:
        print(f'  in feature   {view["feature"]["handle"]}  "{view["feature"]["label"]}"')
    # Suppressed when the list would only restate the header (a single-symbol selection *is* its
    # one symbol) -- repeating it reads as two separate facts.
    if view["symbols"] and view["symbols"] != [view["handle"]]:
        shown = ", ".join(view["symbols"])
        more = view["symbol_count"] - len(view["symbols"])
        print(f"  symbols      {shown}" + (f" (+{more} more)" if more > 0 else ""))
    # Same rule as the symbols line above: a save's own provenance is itself, and printing it back
    # under `saves` reads as a second, different fact.
    saves = [s for s in view["saves"] if not (view["kind"] == "save" and s["sha"] == view["handle"])]
    elided = view.get("save_count", len(view["saves"])) - len(view["saves"])
    for i, save in enumerate(saves):
        head = "saves        " if i == 0 else "             "
        print(f'  {head}{save["sha"]}  {save["subject"]}')
    if elided > 0:
        # Say what isn't shown *and* how to see it. Stopping silently at the cap reads as "that was
        # all of them"; naming a count with no way to reach it is the same problem one step later --
        # nothing else lists a symbol's saves (`log --focus` lists a feature's checkpoints, `advanced
        # blame` a file's symbols), so without the flag those saves were unreachable from the CLI.
        print(f"               (+{elided} older save(s) — `--saves {view['save_count']}` for all)")

    _print_consequences(view["consequences"])
    _print_next(view["next"])


def _extent(view: dict) -> str:
    """The one-line size/shape/age summary. Assembled from only the parts that are non-empty so a
    single-op selection doesn't read as `1 edits · 0 symbols in 0 files`."""
    parts = [f'{view["op_count"]} edit' + ("s" if view["op_count"] != 1 else "")]
    # A single-symbol selection's "1 symbol in 1 file" is already in the header; only a *group*
    # (feature/checkpoint/save) needs its breadth spelled out.
    if view["symbol_count"] > 1 or view["kind"] in ("feature", "checkpoint", "save"):
        parts.append(f'{view["symbol_count"]} symbol' + ("s" if view["symbol_count"] != 1 else "")
                     + f' in {len(view["files"])} file' + ("s" if len(view["files"]) != 1 else ""))
    # A ◆ row's defining property is the spread itself, and it is what `sgt log` labels the row
    # with ("across 7 features"). Only present on that kind, so no other selection grows a clause.
    if view.get("across_features"):
        parts.append(f'across {view["across_features"]} features')
    last = view["span"]["last"]
    if last is not None:
        parts.append(f"last touched {_fmt_age(max(0.0, time.time() - last))}")
    return " · ".join(parts)


def _print_consequences(cons: dict) -> None:
    """Stated in prose, before any revert command appears in `next:` -- the point is that the cost is
    read first. A selection with nothing live says so instead of implying a revert would do
    something."""
    if not cons["live_op_count"]:
        print("  nothing here is currently live — a revert would be a no-op")
        if cons["message"]:
            print(f'  {cons["message"]}')
        return
    from sgt.api import revert_cost

    print(f"  reverting this {revert_cost(cons)}")
    if cons["forked"]:
        print("  ⚠ this selection is forked: two versions of a symbol compete")
    if not cons["ok"] and cons["message"]:
        print(f'  ⚠ {cons["message"]}')


def _print_next(steps: list[dict]) -> None:
    if not steps:
        return
    print("\n  next:")
    width = max(len(s["cmd"]) for s in steps)
    for step in steps:
        print(f'    {step["cmd"]:<{width}}   {step["why"]}')
