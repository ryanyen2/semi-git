"""`ugh name <address> <label>` gives a label to the steps at an address (naming, merging into an
existing name, splitting and moving are all this), and `ugh rename <label> <new label>` relabels
every step carrying a label. Metadata only: no code changes, and `ugh undo` reverses either."""

from __future__ import annotations

from ._common import _emit_json, _fail_json


def register(subs, parent) -> None:
    n = subs.add_parser("name", parents=[parent])
    n.add_argument("address")
    n.add_argument("label", nargs="+")
    n.set_defaults(func=_cmd_name)

    r = subs.add_parser("rename", parents=[parent])
    r.add_argument("old")
    r.add_argument("new", nargs="+")
    r.set_defaults(func=_cmd_rename)


def _cmd_name(args) -> int:
    from ugh import names
    from ugh.address import AddressError, Record, resolve
    from ugh.core.lens import get

    from .ideal_edit import address_refusal

    label = " ".join(args.label)
    get(".")
    rec = Record(".")
    try:
        op_ids = resolve(".", args.address, rec)
    except AddressError as e:
        return address_refusal("name", args.address, e, args.as_json)
    ids = [o for o in op_ids if o in rec.units]
    if not ids:
        return _fail_json(f"{args.address!r} has no steps to name", args.as_json)
    names.name(".", ids, label)
    rec.names = names.load(".")
    steps = rec.steps(ids)
    if args.as_json:
        return _emit_json({"ok": True, "name": label, "steps": steps})
    print(f'✓ named {len(steps)} step(s) "{label}"')
    for st in steps:
        print(f"    {st['address']}")
    return 0


def _cmd_rename(args) -> int:
    from ugh import names

    new = " ".join(args.new)
    if args.old not in names.load(".").values():
        return _fail_json(f"no name {args.old!r} — `ugh log` shows the names in use", args.as_json)
    merged = new in names.load(".").values()
    moved = names.rename(".", args.old, new)
    if args.as_json:
        return _emit_json({"ok": True, "old": args.old, "new": new, "steps": moved, "merged": merged})
    print(f'✓ renamed "{args.old}" → "{new}" ({moved} step(s))'
          + (f'; "{new}" already existed, so the two are now one' if merged else ""))
    return 0
