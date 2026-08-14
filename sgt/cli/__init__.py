"""The `sgt` command surface — the operation-ideal kernel (plan U7/U8/U9/U11, flipped in U10),
plus the feature lens (U13). Restructured in U18 into an argparse subcommand package: `main()`
wires one subparser per verb from the verb-family modules below, and `sgt git <args...>` forwards
verbatim to real git (advisory-only for tree-mutating subcommands, C8) before argparse is entered.

History is a mined, content-addressed op DAG; a codebase state is an order ideal of that DAG.
`revert`/`restore` are exact ideal edits (`I \\ ↑X` / `I ∪ ↓X`) with `--emit` previews and
chain-fork surfacing (AE2). `log`/`state`/`diff` inspect the DAG, the current ideal, and
ideal-vs-ideal semantic diffs. `oracle` attaches async tiered build/test verdicts. `fsck` verifies
the op store's integrity. Every verb mines the working tree on contact before acting (R9).

Where the ideal algebra can't express an edit exactly, U11's rewrite verbs (`merge-op`,
`split-op`, `transplant`, `revert --keep-dependents`, `identity split`/`identity join`) draft
hollow ops for an agent/human to fulfill (`sgt advanced fulfill <draft-id> --from-tree`) and stage
to the working tree without committing; `sgt advanced commit` is the only verb that commits one,
gated on a passing oracle verdict (R14). `sgt land <branch>` is a distinct verb (U23's shared-branch
CAS advance) -- deliberately not named `commit` and not overloaded onto a bare `sgt land`, so "land"
only ever means the branch advance.

`sgt log` is the one inspection surface (U14): the lane×commit grid, with `--tree` (the hierarchical
feature tree, formerly `sgt map`; `--rebuild` reclusters), `--rail` (episode rail, formerly `sgt
episodes`), `--summary` (scalars, formerly `sgt status`), and `--ops` (the raw op DAG). `sgt advanced
blame` attributes a file's symbols. `merge`/`split`/`rename`/`move` are metadata-only feature verbs
-- instant, reversible, content-untouched (R16) -- and `revert <feature>` bridges into the ideal
algebra: it resolves a feature id/label to its op-set and runs the same exact edit a single-op
`revert` would, grouped by feature.

`plan` is the agentic loop's off-chain half (plan U14): `plan intake` decomposes a stated plan into
predicted hollow ops (R18 -- never touching the ideal algebra). Step<->op matching and plan-drift
(the former `checkpoint`/`drift` verbs) are folded into `sgt save` (U12/R10): a save auto-confirms
each unambiguous single-step match and reports the rest; `sgt save --resolve-plan` settles an
n:m/multi-step match (`--confirm-hollow`/`--confirm-op` names one group). The working-tree sense of
"drift" keeps its name in `sgt log --summary`/`sgt advanced fsck --tree`.
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys

from . import (
    edit, feature, ideal_edit, init, inspect, intent, loop, migrate, oracle, porcelain, propose,
    resolve, review, rewrite, select, session, show, suggestions, sync, tiers,
)

# The daily spine + the frequently-reached verbs kept at the top level, two groupings, and the
# unchanged collaboration/setup verbs (KTD2/R2, re-triaged). This is the ONLY top-level surface;
# every *rare/maintenance* verb is re-homed under `feature`/`advanced` and is reachable only there
# -- a hard rename, no alias layer. `advanced` is for maintenance/rare verbs only: the verbs a user
# runs daily (navigation, inspection, the agentic loop, the rewrite pipeline) stay one word away.
_VERBS = {
    # spine (U14: the grid is the only inspection surface -- status/map/graph/episodes are `log`
    # modes, no longer verbs; blame/edit/commit/fulfill demoted under `advanced`)
    "save", "log", "undo", "revert", "restore", "resolve", "show",
    # navigation + inspection (daily). `intent` stays top-level: its subcommands (list/show/build)
    # don't map to a `log` mode and it was deliberately re-promoted (c4f9966/KTD8). `now` is the
    # state-of-actions orient (what's in flight / needs you / next), a fast assembler distinct from
    # `log`'s history grid.
    # `status` is a thin alias of `log --summary` (one handler, so they cannot drift): U14 removed
    # the spelling, which made the first command every git user types answer with the help text.
    # `why` is top-level because "why is this code like this" is a question about a *selector*, not
    # about features: `sgt why <sha>` answers with the prompt and words that produced a commit, and
    # nothing in that reading is feature-scoped (the feature-closure form is the `--for` flag). It
    # sat under `feature why`, so the natural spelling printed the help text instead of answering.
    # `show` is the read half of visiting a past state: sgt could always reconstruct `code(I)` at an
    # arbitrary frontier, and only the workbench's playhead ever displayed it. Reading an old version
    # of a file is a daily question, so it answers at the top level, like `git show <rev>:<path>`.
    "switch", "diff", "intent", "now", "status", "why", "show",
    # agentic loop (daily) -- checkpoint/drift folded into `save` (U12)
    "plan",
    # groupings
    "feature", "advanced",
    # collaboration + setup (unchanged behavior + name)
    "sync", "land", "push", "propose", "session", "init", "mcp",
}

# Where each re-homed verb's registration lands (default: top-level, i.e. spine/collaboration/setup).
# The verb NAME and its args/handler are untouched -- only the parent subparser changes -- so the
# family modules register unchanged and each verb keeps its exact behavior at its new path.
_ROUTING = {
    "rename": "feature", "select": "feature",
    "merge": "regroup", "split": "regroup", "move": "regroup",
    "state": "advanced", "oracle": "advanced", "fsck": "advanced",
    "resync": "advanced", "history": "advanced", "ops": "advanced",
    "compose": "advanced", "fold": "advanced", "preview": "advanced",
    "forks": "advanced", "after": "advanced",
    "tiers": "advanced", "migrate": "advanced",
    "review-queue": "advanced", "identity": "advanced", "suggestions": "advanced",
    "merge-op": "advanced", "split-op": "advanced", "transplant": "advanced",
    "unstage": "advanced", "repair": "advanced",
    # U14: demoted from the daily spine -- `blame` is a narrow single-symbol lookup, `edit` the
    # opt-in oracle-gated ceremony (ordinary edits go through plain `save`), and `commit`/`fulfill`
    # join the rewrite pipeline verbs (merge-op/split-op/transplant) already here.
    "blame": "advanced", "edit": "advanced", "commit": "advanced", "fulfill": "advanced",
}

_FAMILIES = (init, inspect, ideal_edit, feature, loop, sync, oracle, rewrite, migrate, propose,
             porcelain, tiers, select, session, review, intent, edit, resolve, show, suggestions)

# Verbs that were *renamed* rather than re-homed: `log` render modes (U14) and the two loop verbs
# folded into `save` (U12). `_ROUTING` covers the re-homed ones, so this table only holds the names
# whose replacement isn't `sgt <grouping> <same-name>`. Both feed `_unknown_verb`: the KTD2 rename
# was deliberately alias-free, which means a user arriving from older output or docs types a verb
# that no longer exists -- the one thing we owe them is the command that replaced it.
_RENAMED = {
    # `status` is deliberately absent: it is a real top-level verb again (a thin alias onto
    # `log --summary`'s handler), so an entry here would be unreachable -- the `_VERBS` gate passes
    # it through before `_unknown_verb` is ever consulted -- and would read as a claim that it moved.
    "map": "sgt log --map",
    "graph": "sgt log --map",
    "episodes": "sgt log --rail",
    "tree": "sgt log --tree",
    "checkpoint": "sgt save",       # U12: folded into the save beat
    "drift": "sgt log --summary",   # working-tree drift kept its name as a summary scalar
    "put": "sgt save",              # never existed as a verb (stale in core/sync/land.py docstring)
}


class _Router:
    """Routes each family's ``add_parser(name, ...)`` to the destination subparsers action named by
    ``_ROUTING`` (default: the top-level ``subs``). This is the whole mechanism of KTD2's re-homing:
    a family module still calls ``subs.add_parser("fsck", ...)`` exactly as before, but the parser
    it gets back is created under the ``advanced`` grouping instead of the top level, so the verb's
    args and handler are unchanged -- only its path moves."""

    def __init__(self, dests: dict):
        self._dests = dests  # tier key -> subparsers action

    def add_parser(self, name: str, **kwargs):
        return self._dests[_ROUTING.get(name, "top")].add_parser(name, **kwargs)


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


def _grouping_help(parser: argparse.ArgumentParser):
    """A grouping verb invoked bare (`sgt feature`, `sgt advanced`, `sgt feature regroup`) prints
    its own subhelp and exits cleanly, rather than erroring on a missing subcommand."""

    def _f(args) -> int:
        parser.print_help()
        return 0

    return _f


def _build_parser() -> _Parser:
    parent = argparse.ArgumentParser(add_help=False)
    # `--json` switches the read verbs to the canonical machine-readable projection (sgt.api),
    # which the VSCode extension and TUI consume. Shared by every subparser via `parents=`.
    parent.add_argument("--json", action="store_true", dest="as_json")
    parser = _Parser(prog="sgt")
    subs = parser.add_subparsers(dest="verb")

    # Two grouping verbs host the re-homed subcommands (KTD2), mirroring `tiers.py`'s nested
    # `add_subparsers`. `feature regroup` is a third nesting level for the old merge/split/move.
    feature_p = subs.add_parser("feature", parents=[parent])
    feature_subs = feature_p.add_subparsers(dest="feature_verb")
    feature_p.set_defaults(func=_grouping_help(feature_p))
    regroup_p = feature_subs.add_parser("regroup", parents=[parent])
    regroup_subs = regroup_p.add_subparsers(dest="regroup_verb")
    regroup_p.set_defaults(func=_grouping_help(regroup_p))

    advanced_p = subs.add_parser("advanced", parents=[parent])
    advanced_subs = advanced_p.add_subparsers(dest="advanced_verb")
    advanced_p.set_defaults(func=_grouping_help(advanced_p))

    router = _Router({"top": subs, "feature": feature_subs,
                      "regroup": regroup_subs, "advanced": advanced_subs})
    for family in _FAMILIES:
        family.register(router, parent)
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
    the most common way to reach this is running sgt somewhere that isn't one -- which used to end
    in a `GitError` traceback out of `rev-parse`, the least useful thing to show someone in their
    first minute with the tool. Anything else still prints its type and message rather than 30
    lines of stack; `SGT_TRACEBACK=1` brings the stack back for debugging."""
    import os
    import sys
    import traceback

    if os.environ.get("SGT_TRACEBACK"):
        traceback.print_exc()
        return 1
    text = str(exc)
    if "not a git repository" in text:
        print("✗ this isn't a git repository. cd into your project first, or run `git init` "
              "then `sgt init` to start tracking one.", file=sys.stderr)
        return 1
    print(f"✗ {type(exc).__name__}: {text}", file=sys.stderr)
    print("  if this looks like a bug, re-run with SGT_TRACEBACK=1 and send the output.",
          file=sys.stderr)
    return 1


def _version() -> int:
    """`sgt --version`. The first thing anyone runs after installing, to check that it worked."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        print(version("semi-git"))
    except PackageNotFoundError:
        # Running straight from a source checkout that was never installed.
        print("semi-git (not installed; running from source)")
    return 0


def _runnable(name: str) -> str:
    """The command a user can actually type for `name` today: its `log`-mode/fold replacement, its
    re-homed path, or the bare top-level verb."""
    if name in _RENAMED:
        return _RENAMED[name]
    group = _ROUTING.get(name)
    if group == "regroup":
        return f"sgt feature regroup {name}"
    if group is not None:
        return f"sgt {group} {name}"
    return f"sgt {name}"


def _unknown_verb(token: str) -> int:
    """An unrecognized first token. Never prints the full help and exits 0: a caller (a script, an
    agent, a user reading a stale README) that types a verb which no longer exists would read that
    success as "it ran and did nothing to do", which is the same silent-no-op failure class the
    workbench fixed. Exit 2 always, and -- because KTD2's re-homing shipped without an alias layer --
    name the command that replaced it whenever we can compute one."""
    if token.startswith("-"):
        sys.stderr.write(
            f"sgt: `{token}` is a flag, not a verb — flags go after the verb (e.g. `sgt log --json`).\n"
            "  run `sgt help` for the verb surface.\n")
        return 2

    if token in _RENAMED or token in _ROUTING:
        sys.stderr.write(f"sgt: `sgt {token}` no longer exists — it moved.\n"
                         f"  run: {_runnable(token)}\n")
        return 2

    known = sorted(_VERBS | set(_ROUTING) | set(_RENAMED))
    close = difflib.get_close_matches(token, known, n=3, cutoff=0.6)
    sys.stderr.write(f"sgt: unknown verb `{token}`.\n")
    if close:
        sys.stderr.write("  did you mean:\n")
        for name in close:
            sys.stderr.write(f"    {_runnable(name)}\n")
    sys.stderr.write("  run `sgt help` for the verb surface.\n")
    return 2


def _git_passthrough(args: list[str]) -> int:
    """`sgt git <args...>`: forward to real git (inherit stdio, propagate exit code). A tree-mutating
    subcommand (D2's refusal table in `porcelain`) is *refused* -- it would rewrite the tree behind
    sgt's tracking -- with the native sgt verb named, unless `--force` is present, in which case the
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
        "sgt — semantic operation-ideal version control\n\n"
        "  the daily spine (a selection — symbol / glob / NL / feature / set — is the argument):\n"
        '  sgt save [-m "<msg>"] [--as "<feature>"]   record your edits + show which feature(s) they landed in\n'
        "  sgt now [--json]            where am I, what next — in flight / needs you / recently done\n"
        "  sgt log [--json]            what you did, newest first — the one inspection surface:\n"
        "                              --map (feature lanes over time) · --tree (feature tree) ·\n"
        "                              --summary (what needs attention) · --refresh (reflect new edits)\n"
        "  sgt show <sel>              what is this? — any id sgt printed: what it covers, what\n"
        "                              would go with it, and what you can do next\n"
        "  sgt undo                    invert the last mutating operation (the unified op log)\n"
        "  sgt revert <sel>            remove a selection and everything built on it\n"
        "  sgt restore <sel>           bring a selection back, along with what it needs\n"
        "  sgt resolve <symbol>        guided fork resolution when two versions of one symbol compete\n"
        "\n"
        "  navigation + inspection:\n"
        "  sgt switch <branch>         move HEAD to a branch's committed tree (mines both ends)\n"
        "  sgt diff <ref_a> <ref_b>    semantic diff: the symmetric difference of two ideals\n"
        "  sgt intent <cmd>            per-feature checkpoints (intent segments): list/show/build;\n"
        "                              rewind one with `sgt revert <feature>@<n>`\n"
        "\n"
        "  agentic loop:\n"
        "  sgt plan <cmd>              intake/abandon/status a stated plan's predicted hollow ops\n"
        "  sgt save --resolve-plan     settle an ambiguous plan-step match a save couldn't auto-confirm\n"
        "\n"
        "  groupings:\n"
        "  sgt feature <cmd>           author/re-cut features: regroup (merge/split/move),\n"
        "                              rename, select   (`why` is the top-level `sgt why <sel>`)\n"
        "  sgt advanced <cmd>          maintenance/rare verbs: blame, edit, fulfill, commit, fsck,\n"
        "                              resync (recover after a git history rewrite), ops, state,\n"
        "                              oracle, after, fold, preview, tiers, identity, migrate,\n"
        "                              history, compose, forks, review-queue, unstage, repair,\n"
        "                              merge-op, split-op, transplant\n"
        "\n"
        "  collaboration + setup:\n"
        "  sgt sync [remote] [branch]  fetch + merge a teammate's work; union ops, reconcile, fork\n"
        "  sgt land <branch> [--json]  advance a shared branch record by CAS, gated oracle-green\n"
        "  sgt push [remote] [branch]  non-forcing git push; a rejection routes you to `sgt sync`\n"
        "  sgt propose <cmd>           a base+Δ review object: create/status/land/render/publish\n"
        "  sgt session <cmd>           named scratch-tree lifecycle: start/status/land/gc\n"
        "  sgt init [path] [--horizon <ref>]   bind git + the kernel op store; mine existing history\n"
        "  sgt mcp [path]              run the MCP stdio server for coding-agent clients\n"
        "  sgt git <args...>           pass through to real git (refuses tree-mutating verbs)\n"
        "\n"
        "  <sel> resolves an op-id / prefix, a `file::name` symbol, a glob, a feature id/label, or\n"
        "  an NL phrase; read verbs take --json for the machine-readable sgt.api projection.\n"
    )
    return 0
