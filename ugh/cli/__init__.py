"""The `ugh` command surface: an argparse subcommand package. `main()` wires one subparser per verb
from the verb-family modules below, and `ugh git <args...>` forwards verbatim to real git (refusing
tree-mutating subcommands) before argparse is entered.

History is a mined, content-addressed op DAG; a codebase state is an order ideal of that DAG.
`revert`/`restore` are exact ideal edits (`I \\ ↑X` / `I ∪ ↓X`) with `--emit` previews.
`log` is the one inspection surface: the feature map, with `--rail`, `--tree`, `--summary` and
`--ops` modes. `check` verifies the op store's integrity. Every verb mines the working tree on
contact before acting (R9).
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys

from . import feature, ideal_edit, init, inspect, intent, loop, porcelain, resolve, select, show, sync

# `intent` is the hidden entry point the editor hooks call (`intent record` / `intent activity`).
_VERBS = {
    "save", "log", "status", "undo", "revert", "restore", "resolve", "show", "find", "plan",
    "feature", "check", "sync", "push", "init", "mcp", "intent",
}

_FAMILIES = (init, inspect, ideal_edit, feature, loop, sync, porcelain, select, intent, resolve,
             show)


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
    # which the VSCode extension and TUI consume. Shared by every subparser via `parents=`.
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
        "ugh — semantic operation-ideal version control\n\n"
        "  the daily spine (a selection — symbol / glob / NL / feature / set — is the argument):\n"
        '  ugh save [-m "<msg>"] [--as "<feature>"]   record your edits + show which feature they landed in\n'
        "  ugh log [--json]            the feature map: one lane per feature, edits over time —\n"
        "                              --rail (saves, newest first) · --tree (feature tree) ·\n"
        "                              --summary (what needs attention) · --refresh (reflect new edits)\n"
        "  ugh show <sel>              what is this? — any id ugh printed: what it covers, what\n"
        "                              would go with it, and what you can do next\n"
        "  ugh find <words>            find a feature, save or symbol you can describe but not name\n"
        "  ugh undo                    invert the last mutating operation (the unified op log)\n"
        "  ugh revert <sel>            remove a selection and everything built on it\n"
        "  ugh restore <sel>           bring a selection back, along with what it needs\n"
        "  ugh resolve <symbol>        guided fork resolution when two versions of one symbol compete\n"
        "  ugh feature rename <feature> <label>   name a feature in your own words\n"
        "\n"
        "  agentic loop:\n"
        "  ugh plan <cmd>              intake/abandon/status a stated plan's predicted hollow ops\n"
        "  ugh save --resolve-plan     settle an ambiguous plan-step match a save couldn't auto-confirm\n"
        "\n"
        "  collaboration + setup:\n"
        "  ugh sync [remote] [branch]  fetch + merge a teammate's work; union ops, reconcile, fork\n"
        "  ugh push [remote] [branch]  non-forcing git push; a rejection routes you to `ugh sync`\n"
        "  ugh check [--tree] [--repair]   verify the op store; --repair re-derives after a history rewrite\n"
        "  ugh init [path] [--horizon <ref>]   bind git + the kernel op store; mine existing history\n"
        "  ugh mcp [path]              run the MCP stdio server for coding-agent clients\n"
        "  ugh git <args...>           pass through to real git (refuses tree-mutating verbs)\n"
        "\n"
        "  <sel> resolves an op-id / prefix, a `file::name` symbol, a glob, a feature id/label, or\n"
        "  an NL phrase; read verbs take --json for the machine-readable ugh.api projection.\n"
    )
    return 0
