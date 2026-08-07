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
    p = subs.add_parser("show", parents=[parent])
    p.add_argument("sel", nargs="+", help="a feature id/label, a checkpoint, an op id, or a symbol")
    p.set_defaults(func=_cmd_show)


def _cmd_show(args) -> int:
    return _show(".", " ".join(args.sel), args.as_json)


def _show(repo: str, target: str, as_json: bool) -> int:
    from sgt.api import show_view

    view = show_view(repo, target)
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
    if view["feature"]:
        print(f'  in feature   {view["feature"]["handle"]}  "{view["feature"]["label"]}"')
    # Suppressed when the list would only restate the header (a single-symbol selection *is* its
    # one symbol) -- repeating it reads as two separate facts.
    if view["symbols"] and view["symbols"] != [view["handle"]]:
        shown = ", ".join(view["symbols"])
        more = view["symbol_count"] - len(view["symbols"])
        print(f"  symbols      {shown}" + (f" (+{more} more)" if more > 0 else ""))
    elided = view.get("save_count", len(view["saves"])) - len(view["saves"])
    for i, save in enumerate(view["saves"]):
        head = "saves        " if i == 0 else "             "
        print(f'  {head}{save["sha"]}  {save["subject"]}')
    if elided > 0:
        # Say what isn't shown. Stopping silently at the cap reads as "that was all of them".
        print(f"               (+{elided} older save(s))")

    _print_consequences(view["consequences"])
    _print_next(view["next"])


def _extent(view: dict) -> str:
    """The one-line size/shape/age summary. Assembled from only the parts that are non-empty so a
    single-op selection doesn't read as `1 edits · 0 symbols in 0 files`."""
    parts = [f'{view["op_count"]} edit' + ("s" if view["op_count"] != 1 else "")]
    # A single-symbol selection's "1 symbol in 1 file" is already in the header; only a *group*
    # (feature/checkpoint) needs its breadth spelled out.
    if view["symbol_count"] > 1 or view["kind"] in ("feature", "checkpoint"):
        parts.append(f'{view["symbol_count"]} symbol' + ("s" if view["symbol_count"] != 1 else "")
                     + f' in {len(view["files"])} file' + ("s" if len(view["files"]) != 1 else ""))
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
    removes, dependents = cons["removes"], cons["dependents"]
    tail = f" — {dependents} of them work built on top" if dependents else ""
    print(f"  reverting this removes {removes} edit" + ("s" if removes != 1 else "") + tail)
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
