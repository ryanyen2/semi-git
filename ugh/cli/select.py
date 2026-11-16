"""`ugh find "<phrase>"`: rank features, saves and symbols against a description. Report-only."""

from __future__ import annotations

from ._common import _emit_json, _fail


def register(subs, parent) -> None:
    f = subs.add_parser("find", parents=[parent])
    f.add_argument("query", nargs="+")
    f.add_argument("--limit", type=int, default=8)
    f.add_argument("--refresh", action="store_true", help="rebuild the index first")
    f.set_defaults(func=_cmd_find)


def _cmd_find(args) -> int:
    return _find(".", " ".join(args.query), args.limit, args.refresh, args.as_json)


def _find(repo: str, query: str, limit: int, refresh: bool, as_json: bool) -> int:
    """`ugh find "<phrase>" [--json]`: rank features, saves and symbols against a
    description. Report-only."""
    from ugh.lens.search import search

    view = search(repo, query, k=limit, refresh=refresh)
    if as_json:
        return _emit_json(view)
    if not view["hits"]:
        return _fail(view.get("message") or f"nothing matched {query!r}")

    kind_width = max(len(h["kind"]) for h in view["hits"])
    for hit in view["hits"]:
        print(f"  {hit['score']:.2f}  {hit['kind']:<{kind_width}}  {_clip(hit['label'], 64)}")
        # A symbol's id IS its label, so a second copy of it says nothing. Only
        # a feature or a save has a handle worth printing under its name.
        handle = "" if hit["id"] == hit["label"] else _handle(hit["id"])
        detail = _clip(hit["detail"], 70)
        if handle or detail:
            print(f"        {handle}{'  ' if handle and detail else ''}{detail}")
    if view["mode"] == "lexical":
        # Say so. A word-overlap answer and a meaning answer look identical in a
        # list, and only one of them is worth trusting when it returns nothing.
        print("\n  (matched on words, not meaning — no working key for this repo)")
    print("\n  next:")
    print(f"    ugh show {_handle(view['hits'][0]['id'])}   what it is, and what would come with it")
    return 0


# A feature id is 66 characters and nothing prints it whole; the feature tree
# shows `f-` plus twelve. Everything else -- a save
# sha, a symbol's path-qualified name -- is already short and is a thing the
# reader is meant to type, so it goes through unchanged.
#
# This used to be `id[:16]` on the listing and `id[:12]` on the `next:` line,
# which made `bikecount/metrics.py::hourly_averages` print as
# `bikecount/metric` and suggested `ugh show bikecount/me` -- a command that
# cannot resolve, printed by the tool as the thing to run next. A handle is
# either whole or it is not a handle.
_HASHED = ("f-", "t-", "op-", "theme-")


def _handle(ident: str) -> str:
    if ident.startswith(_HASHED) and len(ident) > 14 and "/" not in ident:
        head = ident.split("-", 1)[0]
        return f"{head}-{ident.split('-', 1)[1][:12]}"
    return f'"{ident}"' if " " in ident else ident


def _clip(text: str, width: int) -> str:
    """Cut to `width`, on a word boundary, marked with an ellipsis.

    Mid-word cuts read as corruption rather than as brevity: a description that
    ended "separated int" had a reader checking whether the sentence itself was
    broken."""
    text = (text or "").strip()
    if len(text) <= width:
        return text
    cut = text[:width - 1]
    space = cut.rfind(" ")
    if space > width // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + "…"
