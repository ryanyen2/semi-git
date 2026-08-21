"""`sgt select` / `sgt why` (plan U29): closure-explanation UX. `select <feature>...` reports the
closure a feature-tree selection induces -- direct ops, files, and anything the closure pulls in
from other features, each group with its exact requires/chain path plus a hub-symbol diagnosis
when the pull crosses a feature boundary. `why <op>` explains one op's feature attribution, or
(with `--for <feature>`) the chain that pulled it into that feature's closure.

Explanation-only by design (U25's BET-C measurement gate came back RED on silent branch
materialization -- see `sgt.lens.select`'s module docstring): neither verb mutates anything.
"""

from __future__ import annotations

from ._common import _emit_json, _fail


def register(subs, parent) -> None:
    s = subs.add_parser("select", parents=[parent])
    s.add_argument("feature", nargs="+")
    s.set_defaults(func=_cmd_select)

    w = subs.add_parser("why", parents=[parent])
    w.add_argument("op")
    w.add_argument("--for", dest="for_feature")
    w.set_defaults(func=_cmd_why)

    # Top level rather than under `advanced`, which is for maintenance and rare
    # verbs. This is neither: it is the verb for the most ordinary situation
    # there is, arriving somewhere new and not knowing what anything is called.
    f = subs.add_parser("find", parents=[parent])
    f.add_argument("query", nargs="+")
    f.add_argument("--limit", type=int, default=8)
    f.add_argument("--refresh", action="store_true", help="rebuild the index first")
    f.set_defaults(func=_cmd_find)


def _cmd_select(args) -> int:
    return _select(".", args.feature, args.as_json)


def _cmd_why(args) -> int:
    return _why(".", args.op, args.for_feature, args.as_json)


def _cmd_find(args) -> int:
    return _find(".", " ".join(args.query), args.limit, args.refresh, args.as_json)


def _find(repo: str, query: str, limit: int, refresh: bool, as_json: bool) -> int:
    """`sgt find "<phrase>" [--json]`: rank features, saves and symbols against a
    description. Report-only, like everything else in this module."""
    from sgt.lens.search import search

    view = search(repo, query, k=limit, refresh=refresh)
    if as_json:
        return _emit_json(view)
    if not view["hits"]:
        return _fail(view.get("message") or f"nothing matched {query!r}")

    kind_width = max(len(h["kind"]) for h in view["hits"])
    for hit in view["hits"]:
        print(f"  {hit['score']:.2f}  {hit['kind']:<{kind_width}}  {hit['label'][:64]}")
        print(f"        {hit['id'][:16]}  {hit['detail'][:70]}")
    if view["mode"] == "lexical":
        # Say so. A word-overlap answer and a meaning answer look identical in a
        # list, and only one of them is worth trusting when it returns nothing.
        print("\n  (matched on words, not meaning — no working key for this repo)")
    print("\n  next:")
    print(f"    sgt show {view['hits'][0]['id'][:12]}      what it is, and what would come with it")
    return 0


def _select(repo: str, features: list[str], as_json: bool) -> int:
    from sgt.api import selection_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact (R9)
    view = selection_view(repo, features)
    if as_json:
        return _emit_json(view)
    if not view["ok"]:
        return _fail(view["message"])

    print(f"{', '.join(view['feature_ids'])}: {view['direct_op_count']} direct op(s), "
          f"{view['closure_op_count']} in closure, {len(view['files'])} file(s)")
    for group in view["pulled"]:
        label = group["feature_id"] or "(unattributed)"
        print(f"  + {group['op_count']} op(s) pulled in from {label}")
        for hop in group["chain"]:
            arrow = f" --{hop['via']}--> " if hop["via"] else ""
            print(f"    {arrow}{hop['op_id']}")
    if view["hub"]:
        print(f"  ⚠ hub: {view['hub']['symbol']} pulled in {view['hub']['pulled_op_count']} op(s) "
              "-- consider `sgt feature regroup split` or `sgt advanced identity split`")
    return 0


def _why(repo: str, op_ref: str, for_feature: str | None, as_json: bool) -> int:
    from sgt.api import why_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact (R9)
    view = why_view(repo, op_ref, for_feature)
    if as_json:
        return _emit_json(view)
    if not view["ok"]:
        return _fail(view["message"])

    # A commit sha resolves to the whole commit's aligned words (`sgt why <sha>`), not one op's
    # attribution -- a distinct read with its own render.
    if view.get("kind") == "commit":
        return _render_commit_why(view)

    if for_feature is None:
        print(f"{view['op_id']}: attributed to {view['feature_id'] or '(none)'}")
        for vote in view["votes"]:
            print(f"  {vote['count']} vote(s) -> {vote['feature_id']}")
    else:
        print(f"{view['op_id']} (own feature: {view['feature_id'] or '(none)'}) "
              f"in {view['for_feature']}'s closure:")
        for hop in view["chain"]:
            arrow = f" --{hop['via']}--> " if hop["via"] else ""
            print(f"  {arrow}{hop['op_id']}")

    _print_rationale(view.get("rationale", []))
    return 0


def _render_commit_why(view: dict) -> int:
    """`sgt why <sha>`: the aligned words for one commit -- its subject, the user's captured words,
    the `claude --resume` handle(s) for the chat it came from, and any recorded reasons. A pure
    read; the words are only ever those truly keyed to this commit, so it cannot misattribute."""
    print(f"{view['sha'][:8]}: {view['subject'] or '(no subject)'}  ·  {view['op_count']} op(s)")
    words = view.get("words")
    print(f'  words: "{words}"' if words else "  words: (none captured)")
    for sid in view.get("claude_session_ids", []):
        print(f"  resume: claude --resume {sid}")
    _print_rationale(view.get("rationale", []))
    return 0


def _print_rationale(rationale: list[dict]) -> None:
    """The recorded "why" section shared by the op- and commit-scoped `why` renders (intent-ledger
    M1): the user's own reasoning reflected from what the workflow captured, each badged
    inferred/confirmed (and `overturned` when superseded). An honest "no recorded reason" beats
    inventing one."""
    if rationale:
        print("  why (recorded):")
        for r in rationale:
            badge = "confirmed" if r["confirmed"] else "inferred"
            if r.get("superseded"):
                badge += ", overturned"
            ev = f" [{r['evidence']} turn(s)]" if r["evidence"] else ""
            print(f"    - {r['reason'] or '(unknown)'}  ({r['actor']}, {badge}){ev}")
    else:
        print("  why (recorded): no recorded reason")
