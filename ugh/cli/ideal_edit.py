"""`revert` and `restore`: resolve the address, then ask the planner (`ugh.core.planner`) what
taking it out, or bringing it back, would take."""

from __future__ import annotations

import argparse

from ._common import _emit_json, _fail_json


def _dirty_refusal(exc, as_json: bool) -> int:
    """Render a `DirtyWorkingTreeError` from a materializing verb as a clean refusal, not a raw
    traceback with a half-written `.sgt` (F4/F5). The guard's message already names the offending
    files; append the *actual*, executable remedy -- absorb the edits with `ugh save`, or commit /
    `git restore` them, then re-run. (`ugh` never overwrites uncommitted work; there is no `ugh put`
    verb, and `log --refresh` does not clear a dirty tree.)"""
    msg = str(exc)
    if "overwrite uncommitted changes" in msg:
        msg += (" -- record them with `ugh save`, or commit / `git restore` those files, then "
                "re-run (ugh won't overwrite uncommitted work)")
    return _fail_json(msg, as_json)


def register(subs, parent) -> None:
    r = subs.add_parser("revert", parents=[parent])
    # `--emit` (machine dry-run: VS Code/MCP) and `--yes` (non-tty apply) are hidden: on a tty the
    # preview and its `[y/N]` are the default confirm step.
    r.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--whole", action="store_true",
                   help="take out the removal set: the target and everything that stands on it")
    r.add_argument("--out", default=None, metavar="DIR",
                   help="with --emit: write the previewed tree onto DIR as raw bytes")
    r.add_argument("ref", nargs="*")
    r.set_defaults(func=_cmd_revert)

    s = subs.add_parser("restore", parents=[parent])
    s.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--out", default=None, metavar="DIR",
                   help="with --emit: write the previewed tree onto DIR as raw bytes")
    s.add_argument("ref", nargs="*")
    s.set_defaults(func=_cmd_restore)


def _cmd_revert(args) -> int:
    return _kernel_edit_verb(".", "revert", args.ref, args.emit, args.as_json, args.yes,
                             whole=args.whole, out=args.out)


def _cmd_restore(args) -> int:
    return _kernel_edit_verb(".", "restore", args.ref, args.emit, args.as_json, args.yes,
                             out=args.out)


def _kernel_edit_verb(
    repo: str, cmd: str, ref_tokens: list[str], emit: bool, as_json: bool, yes: bool = False,
    *, whole: bool = False, out: str | None = None,
) -> int:
    """revert/restore: resolve the address to steps, plan, then preview or apply."""
    from ugh.address import AddressError, resolve
    from ugh.core.lens import get

    if not ref_tokens:
        print(f"usage: ugh {cmd} [--json] <address>")
        return 2
    target = " ".join(ref_tokens)
    from ._common import busy
    with busy(f"working out what `{target}` covers…"):
        get(repo)  # mine-on-contact before planning/applying the edit (R9)
    try:
        steps = resolve(repo, target)
    except AddressError as e:
        return address_refusal(cmd, target, e, as_json)
    return _answer(repo, cmd, steps, target, emit, as_json, yes, whole=whole, out=out)


def address_refusal(cmd: str, target: str, e, as_json: bool) -> int:
    """An address that names nothing or two things: the reason and the nearest `find` hits. Exit 2."""
    if as_json:
        _emit_json({"ok": False, "verb": cmd, "target": target, "message": str(e), "hits": e.hits})
        return 2
    print(f"✗ {e}")
    if e.hits:
        print("  nearest `ugh find` hits:")
        for hit in e.hits:
            print(f"    {hit}")
    return 2


def _answer(repo: str, cmd: str, steps, label: str, emit: bool, as_json: bool, yes: bool,
            *, whole: bool = False, out: str | None = None) -> int:
    """Plan, then print the preview (`--emit`), ask, or apply."""
    import sys

    from ugh.core import planner
    from ugh.core.lens import DirtyWorkingTreeError

    p = planner.plan(repo, steps, verb=cmd, target=label, whole=whole,
                     removal_set=cmd == "revert" and not (yes or as_json and not emit))
    view = planner.to_json(p)
    if emit:
        if out is not None and p.ok:
            from ugh.api import verb_result_out_view

            view["out"] = verb_result_out_view(repo, p, out)
        if as_json:
            _emit_json(view)
        else:
            _print_plan_json(view)
        return 0 if p.ok else 1
    if not p.ok:
        return _fail_json(p.message, as_json)
    if not p.files:
        if as_json:
            _emit_json(view)
        else:
            _print_plan_json(view)
        return 0
    if not as_json:
        _print_plan_json(view)
        if not yes:
            if not sys.stdin.isatty():
                print("\n  not applied — this was the preview. re-run with --yes to apply.")
                return 2
            try:
                reply = input(f"\napply this {cmd}? [y/N] ").strip().lower()
            except EOFError:
                reply = ""
            if reply not in ("y", "yes"):
                print("  skipped — nothing changed.")
                return 1
    try:
        done = planner.apply(repo, p)
    except DirtyWorkingTreeError as e:
        return _dirty_refusal(e, as_json)
    applied = bool(done.check != "not_run" and done.check["passed"] and done.files)
    result = planner.to_json(done, applied=applied)
    if as_json:
        _emit_json(result)
        return 0 if applied else 1
    if applied:
        print(f"  ✓ {cmd} applied — {len(done.undone)} step(s) undone, {len(done.held)} held, "
              f"{_judged(done.check)}. (`ugh undo` reverses this.)")
        _print_observed(result)
        if done.held != p.held:
            _print_held(result)
        return 0
    print(f"  ✗ nothing applied — {_judged(done.check)}; the tree is as it was.")
    _print_held(result)
    return 1


def _print_observed(view: dict) -> None:
    """After an apply: the output's change, whether it is the reverse of what the saves taken out
    changed in it (the only "gone, as the check sees it"), and the
    undone steps the check never ran."""
    seen = view["observed"]
    if seen is None:
        return
    gone, place = ("gone", "gone from") if view["verb"] == "revert" else ("back", "back in")
    d = seen["delta"]
    if d is not None:
        own = seen["saves_delta"]
        claim = {True: f" — {gone}, as the check sees it",
                 False: " — not the reverse of what those saves changed in it", None: ""}[seen["reverses"]]
        if own is not None and not own["added"] + own["removed"]:
            claim = " — those saves never changed it, so the check cannot confirm anything"
        print(f"    output: {d['added']} line(s) added, {d['removed']} removed{claim}")
    never = [row["address"] for row in view["undone"] if row["ran"] is False]
    if never:
        print(f"    {place} the code; the check cannot confirm its behaviour: {', '.join(never)}")
    if "not_recorded" in seen:
        print(f"    observation not recorded: {seen['not_recorded']}")


def _judged(check) -> str:
    if check == "not_run":
        return "check not run"
    what = "the project's check" if check["judge"] == "check" else "the parser"
    return f"{what} {'passes' if check['passed'] else 'fails'}"


def _save_text(row: dict) -> str:
    return f'{row["save"]} "{row["subject"]}"' if row["subject"] else row["save"]


def _print_held(view: dict) -> None:
    for row in view["held"]:
        holders = ", ".join(dict.fromkeys(h["address"] for h in row["holders"]))
        print(f"    {row['address']} — {row['reason'].replace('_', ' ')}"
              + (f": {holders}" if holders else ""))


def _print_plan_json(view: dict) -> None:
    """A plan (`planner.to_json`), plain: what goes, what is held and why; the removal set; where
    the held part is and which saves must keep working; the files and the check; the steps HEAD's
    observation shows the check never ran, or that HEAD was not observed."""
    print(f"  {view['verb']} {view['target']}")
    if view["message"]:
        print(f"  {view['message']}")
    if not (view["undone"] or view["held"]):
        return
    saves = sorted({_save_text(row) for row in view["undone"]})
    print(f"  {'goes' if view['verb'] == 'revert' else 'comes back'}: {len(view['undone'])} step(s)"
          + (f" ({', '.join(saves)})" if saves else ""))
    if view["held"]:
        print(f"  held: {len(view['held'])} step(s)")
        _print_held(view)
    rs = view["removal_set"]
    if rs and rs["steps"] > len(view["undone"]):
        rows = ", ".join(f'{s["sha"]} ({s["steps"]})' for s in rs["saves"])
        print(f"  --whole takes {rs['steps']} step(s): {rows}")
    if view["held"]:
        keep = sorted({_save_text(h) for row in view["held"] for h in row["holders"]})
        print(f"  by hand: {', '.join(sorted({row['address'] for row in view['held']}))}"
              + (f"; keep working: {', '.join(keep)}" if keep else ""))
    files = sorted(view["files"])
    print(f"  files: {', '.join(files)}" if files else "  no file changes")
    print("  check: not run yet" if view["check"] == "not_run" else f"  check: {_judged(view['check'])}")
    never = [row["address"] for row in [*view["undone"], *view["held"]] if row["ran"] is False]
    if never:
        print(f"  the check never ran: {', '.join(never)}")
    if view["observed"] is not None and view["observed"]["head"] is None:
        print("  HEAD: not observed")
