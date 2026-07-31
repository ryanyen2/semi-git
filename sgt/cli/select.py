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


def _cmd_select(args) -> int:
    return _select(".", args.feature, args.as_json)


def _cmd_why(args) -> int:
    return _why(".", args.op, args.for_feature, args.as_json)


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
              "-- consider `sgt split` or `sgt identity split`")
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

    # Intent-ledger M1: the recorded "why" -- the user's own reasoning, reflected from what the
    # workflow captured. An honest "no recorded reason" beats inventing one.
    rationale = view.get("rationale", [])
    if rationale:
        print("  why (recorded):")
        for r in rationale:
            badge = "confirmed" if r["confirmed"] else "inferred"
            if r["superseded"]:
                badge += ", overturned"
            ev = f" [{r['evidence']} turn(s)]" if r["evidence"] else ""
            print(f"    - {r['reason'] or '(unknown)'}  ({r['actor']}, {badge}){ev}")
    else:
        print("  why (recorded): no recorded reason")
    return 0
