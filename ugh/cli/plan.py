"""`ugh plan`: names declared before their edits exist. `ugh plan <label>...` declares them, `ugh
plan` reports each with the saves since the baseline that carry it and the steps that carry no
declared name, and `ugh plan --done` clears the plan."""

from __future__ import annotations

from ._common import _emit_json


def register(subs, parent) -> None:
    p = subs.add_parser("plan", parents=[parent])
    p.add_argument("names", nargs="*", metavar="LABEL", help="names to declare")
    p.add_argument("--done", action="store_true", help="clear the plan")
    p.set_defaults(func=_cmd_plan)


def _cmd_plan(args) -> int:
    from ugh import names
    from ugh.api import plan_view
    from ugh.core.lens import get

    if args.done:
        names.plan_done(".")
        if args.as_json:
            return _emit_json({"ok": True, "plan": None})
        print("✓ plan cleared")
        return 0
    if args.names:
        names.declare(".", args.names)
    get(".")
    view = plan_view(".")
    if args.as_json:
        return _emit_json({"ok": True, "plan": view})
    for line in plan_lines(view) if view else ["(no plan — `ugh plan <label>...` declares one)"]:
        print(line)
    return 0


def plan_lines(view: dict) -> list[str]:
    """Each declared name, built (with its saves) or missing, then the steps carrying none."""
    lines = [f"plan since {view['baseline'][:7]}:" if view["baseline"] else "plan:"]
    for row in view["names"]:
        shas = ", ".join(s["sha"][:7] for s in row["saves"])
        lines.append(f"  {row['name']}: " + (f"built in {shas}" if shas else "missing"))
    if view["other"]:
        lines.append(f"  {len(view['other'])} step(s) carry no declared name:")
        lines += [f"    {st['address']}" + (f"  [{st['name']}]" if st["name"] else "")
                  for st in view["other"]]
    return lines
