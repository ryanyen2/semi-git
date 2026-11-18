"""The `ugh` command surface: an argparse subcommand package. `main()` wires one subparser per verb
from the verb-family modules below, and `ugh git <args...>` forwards verbatim to real git (refusing
tree-mutating subcommands) before argparse is entered.

History is mined from git into steps (one save's change to one definition). Every verb that takes
an operand takes an address (`ugh.address`); `revert`/`restore` take steps out and bring them back
through the planner. `log` lists the saves with their names, `status` says what needs attention, and
`check` verifies the op store. Every verb mines the working tree on contact before acting (R9).
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys

from . import ideal_edit, init, inspect, intent, name, plan, porcelain, resolve, show, sync

# `intent` is the hidden entry point the editor hooks call (`intent record` / `intent activity`).
_VERBS = {
    "save", "name", "rename", "log", "status", "show", "find", "undo", "revert", "restore",
    "resolve", "plan", "check", "sync", "push", "init", "mcp", "intent",
}

_FAMILIES = (init, inspect, ideal_edit, name, plan, sync, porcelain, intent, resolve, show)


class _CLIExit(Exception):
    """Raised in place of argparse's ``SystemExit`` so ``main`` returns an int (and tests that
    call ``main`` directly see a return code, not an exception)."""

    def __init__(self, code: int):
        self.code = code


class _Parser(argparse.ArgumentParser):
    def exit(self, status: int = 0, message: str | None = None):
        if message:
            sys.stderr.write(message)
        raise _CLIExit(status)


def _build_parser() -> _Parser:
    parent = argparse.ArgumentParser(add_help=False)
    # `--json` switches the read verbs to the canonical machine-readable projection (ugh.api),
    # which the VSCode extension consumes. Shared by every subparser via `parents=`.
    parent.add_argument("--json", action="store_true", dest="as_json")
    parser = _Parser(prog="ugh")
    subs = parser.add_subparsers(dest="verb")
    for family in _FAMILIES:
        family.register(subs, parent)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _help()

    if argv[0] == "git":
        return _git_passthrough(argv[1:])

    if argv[0] in ("help", "-h", "--help"):
        return _help()

    if argv[0] in ("--version", "-V"):
        return _version()

    if argv[0] not in _VERBS:
        return _unknown_verb(argv[0])

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _CLIExit as e:
        return e.code
    try:
        return args.func(args)
    except AssertionError:
        # A fired assert is a broken invariant in our own code, not a condition a user can act
        # on, so the traceback is the useful output and swallowing it would hide a real bug.
        raise
    except Exception as e:  # noqa: BLE001 -- the outermost boundary; nothing above it prints
        return _fatal(e)


def _fatal(exc: Exception) -> int:
    """Turn an escaped exception into a sentence. Every verb here reads or writes a git repo, so
    the most common way to reach this is running ugh somewhere that isn't one -- which used to end
    in a `GitError` traceback out of `rev-parse`, the least useful thing to show someone in their
    first minute with the tool. Anything else still prints its type and message rather than 30
    lines of stack; `UGH_TRACEBACK=1` brings the stack back for debugging."""
    import os
    import sys
    import traceback

    if os.environ.get("UGH_TRACEBACK"):
        traceback.print_exc()
        return 1
    text = str(exc)
    if "not a git repository" in text:
        print("✗ this isn't a git repository. cd into your project first, or run `git init` "
              "then `ugh init` to start tracking one.", file=sys.stderr)
        return 1
    print(f"✗ {type(exc).__name__}: {text}", file=sys.stderr)
    print("  if this looks like a bug, re-run with UGH_TRACEBACK=1 and send the output.",
          file=sys.stderr)
    return 1


def _version() -> int:
    """`ugh --version`. The first thing anyone runs after installing, to check that it worked."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        print(version("ugh"))
    except PackageNotFoundError:
        # Running straight from a source checkout that was never installed.
        print("ugh (not installed; running from source)")
    return 0


def _unknown_verb(token: str) -> int:
    """An unrecognized first token. Never prints the full help and exits 0: a caller (a script, an
    agent, a user reading a stale README) that types a verb which does not exist would read that
    success as "it ran and did nothing to do". Exit 2 always, with the closest real verbs."""
    if token.startswith("-"):
        sys.stderr.write(
            f"ugh: `{token}` is a flag, not a verb — flags go after the verb (e.g. `ugh log --json`).\n"
            "  run `ugh help` for the verb surface.\n")
        return 2

    close = difflib.get_close_matches(token, sorted(_VERBS), n=3, cutoff=0.6)
    sys.stderr.write(f"ugh: unknown verb `{token}`.\n")
    if close:
        sys.stderr.write("  did you mean:\n")
        for name in close:
            sys.stderr.write(f"    ugh {name}\n")
    sys.stderr.write("  run `ugh help` for the verb surface.\n")
    return 2


def _git_passthrough(args: list[str]) -> int:
    """`ugh git <args...>`: forward to real git (inherit stdio, propagate exit code). A tree-mutating
    subcommand (D2's refusal table in `porcelain`) is *refused* -- it would rewrite the tree behind
    ugh's tracking -- with the native ugh verb named, unless `--force` is present, in which case the
    override token is consumed and the plain git command runs (the `gitbind` out-of-band detector
    re-mines on next contact). Everything else forwards verbatim: no `--json` stripping, no flag
    rewriting."""
    if args:
        remedy = porcelain.git_remedy(args[0])
        if remedy is not None:
            if "--force" not in args:
                sys.stderr.write(porcelain.refusal_message(args[0], remedy))
                return 1
            args = [a for a in args if a != "--force"]  # override consumed, not passed to git
    return subprocess.run(["git", *args]).returncode


def _help() -> int:
    print(
        "ugh — steps you can name, take out and bring back\n\n"
        "  the daily spine (an <address> names steps — see below):\n"
        '  ugh save [-m "<msg>"] [--as "<label>"]   commit your edits as a save; --as names its steps\n'
        "  ugh name <address> <label>  give a label to the steps at an address\n"
        "  ugh rename <label> <new>    relabel every step with a label (an existing label absorbs them)\n"
        "  ugh log                     the saves, newest first, with the names on their steps\n"
        "  ugh status                  what needs attention: two-version choices, unsaved edits, the plan\n"
        "  ugh show <address>          its steps, the ask behind each save, what taking it out takes\n"
        "  ugh find <words>            addresses whose names, subjects, definitions or asks match\n"
        "  ugh undo                    invert the last mutating operation\n"
        "  ugh revert <address>        take steps out of the code, holding what others stand on\n"
        "  ugh restore <address>       bring steps that are out back\n"
        "  ugh resolve <address> [--theirs|--ours|--apply]   the two versions a sync left; settle them\n"
        "  ugh plan [<label>...] [--done]   declare names before their edits exist; report on them\n"
        "\n"
        "  collaboration + setup:\n"
        "  ugh sync [remote] [branch]  fetch + merge a teammate's work unit by unit; merge names\n"
        "  ugh push [remote] [branch]  non-forcing git push; a rejection routes you to `ugh sync`\n"
        "  ugh check [--tree] [--repair]   verify the op store; --repair re-derives after a history rewrite\n"
        "  ugh init [path] [--horizon <ref>]   bind git + the kernel op store; mine existing history\n"
        "  ugh mcp [path]              run the MCP stdio server for coding-agent clients\n"
        "  ugh git <args...>           pass through to real git (refuses tree-mutating verbs)\n"
        "\n"
        "  an <address> is a <label>, <label>@<rev>, <path>, <path>::<def>, <path>::<def>@<rev>,\n"
        "  a <rev>, or <rev>..<rev>; read verbs take --json for the machine-readable ugh.api shape.\n"
    )
    return 0
