"""`ugh show <address>`: the steps at an address with their names and whether they are out, the
saves they came from with the ask behind each and what the check observed on each, and what taking
them out (or bringing them back) would take. `ugh find <words>`: the addresses whose names, save subjects, definition names or
captured asks contain the words. Both are read-only and offline."""

from __future__ import annotations

from ._common import _emit_json, _fail, observed_text


def register(subs, parent) -> None:
    p = subs.add_parser("show", parents=[parent], help="the steps at an address and what taking them out would take")
    p.add_argument("address", nargs="+")
    p.set_defaults(func=_cmd_show)

    f = subs.add_parser("find", parents=[parent], help="addresses matching some words")
    f.add_argument("query", nargs="+")
    f.add_argument("--limit", type=int, default=10)
    f.set_defaults(func=_cmd_find)


def _cmd_show(args) -> int:
    from ugh.api import show_view
    from ugh.core.lens import get

    from .ideal_edit import _print_plan_json

    token = " ".join(args.address)
    get(".")
    view = show_view(".", token)
    if args.as_json:
        _emit_json(view)
        return 0 if view["ok"] else 2
    if not view["ok"]:
        _fail(view["message"])
        if view["hits"]:
            print("  nearest `ugh find` hits:")
            for hit in view["hits"]:
                print(f"    {hit}")
        return 2
    n = len(view["steps"])
    print(f"{token}: {n} step{'s' * (n != 1)} in {len(view['saves'])} save(s)")
    for save in view["saves"]:
        print(f"  {save['sha'][:7]} {save['subject']}")
        for st in view["steps"]:
            if st["address"].endswith("@" + save["sha"][:7]):
                print(f"      {st['kind']:<7} {st['address']}" + (f"  [{st['name']}]" if st["name"] else "")
                      + ("  (out)" if st["out"] else ""))
        if save["ask"]:
            print(f'      asked “{save["ask"]["gist"]}” — {save["ask"]["source"]}')
        if view["plan"]["observed"] is not None:
            print(f"      {observed_text(save['observed'])}")
    _print_plan_json(view["plan"])
    return 0


def _cmd_find(args) -> int:
    from ugh.api import find_view
    from ugh.core.lens import get

    query = " ".join(args.query)
    get(".")
    view = find_view(".", query, limit=args.limit)
    if args.as_json:
        return _emit_json(view)
    if not view["hits"]:
        return _fail(f"nothing matched {query!r}")
    width = max(len(h["address"]) for h in view["hits"])
    for hit in view["hits"]:
        text = "" if hit["text"] == hit["address"] else f": {_clip(hit['text'], 60)}"
        print(f"  {hit['address']:<{width}}  {hit['matched']}{text}")
    return 0


def _clip(text: str, width: int) -> str:
    """One line of `text`, cut to `width` on a word boundary."""
    text = " ".join((text or "").split())
    if len(text) <= width:
        return text
    cut = text[:width - 1]
    space = cut.rfind(" ")
    return (cut[:space] if space > width // 2 else cut).rstrip(" ,.;:") + "…"
