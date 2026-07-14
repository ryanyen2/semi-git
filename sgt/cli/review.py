"""`sgt review-queue` (plan U31, S7): the trust queue's dequeue mechanism.

`sgt review-queue list [--json]` renders `sgt.api.trust_view` -- every op with session/agent
attribution or drift status that isn't yet reviewed, grouped by provenance key. `sgt review-queue
ack <op-set|--session <name>> [--note "..."]` writes a committed review record covering that
op-set (`sgt.core.review.ack`), dequeuing it from future `trust_view` calls. This is the one new
mutation this unit adds -- acting on a group is still the existing verb surface (`sgt revert
--session`, `feature move`).
"""

from __future__ import annotations

from ._common import _emit_json, _fail_json

_USAGE = ('usage: sgt review-queue list [--json] | '
          'sgt review-queue ack <op-id>... [--session <name>] [--note "..."] [--json]')


def register(subs, parent) -> None:
    rq = subs.add_parser("review-queue", parents=[parent])
    rq.add_argument("sub", nargs="?")
    rq.add_argument("op_ids", nargs="*")
    rq.add_argument("--session")
    rq.add_argument("--note")
    rq.set_defaults(func=_cmd_review_queue)


def _cmd_review_queue(args) -> int:
    return _review_queue(".", args.sub, args.op_ids, args.session, args.note, args.as_json)


def _review_queue(repo: str, sub: str | None, op_ids: list[str], session: str | None,
                   note: str | None, as_json: bool) -> int:
    from sgt.core.lens import get

    if sub not in ("list", "ack"):
        print(_USAGE)
        return 2
    get(repo)  # mine-on-contact before reading/writing the queue (R9)
    if sub == "list":
        return _list(repo, as_json)
    return _ack(repo, op_ids, session, note, as_json)


def _list(repo: str, as_json: bool) -> int:
    from sgt.api import trust_view

    view = trust_view(repo)
    if as_json:
        return _emit_json(view)
    if not view["groups"]:
        print("trust queue is empty")
        return 0
    for g in view["groups"]:
        print(f"{g['provenance']}: {len(g['op_ids'])} op(s)")
        for op in g["ops"]:
            drift = " [drift]" if op["drift"] else ""
            print(f"    {op['op_id'][:12]} {op['kind']}{drift}")
    print(f"{view['total_ops']} op(s) awaiting review")
    return 0


def _ack(repo: str, op_ids: list[str], session: str | None, note: str | None, as_json: bool) -> int:
    from sgt.core import review, session as session_mod

    if session:
        target = session_mod.ops_by_session(repo, session)
        scope = f"session:{session}"
    else:
        target = set(op_ids)
        scope = f"op-set:{len(target)} ops"
    if not target:
        message = f"no op carries session {session!r} attribution" if session else "no op-ids given"
        return _fail_json(message, as_json)

    try:
        r = review.ack(repo, target, scope=scope, note=note)
    except ValueError as e:
        return _fail_json(str(e), as_json)
    view = {"ok": True, "id": r.id, "op_ids": list(r.op_ids), "scope": r.scope, "note": r.note}
    if as_json:
        return _emit_json(view)
    print(f"✓ review {r.id}: acked {len(r.op_ids)} op(s) ({r.scope})")
    return 0
