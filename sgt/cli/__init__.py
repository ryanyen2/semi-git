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
hollow ops for an agent/human to fulfill (`sgt fulfill <draft-id> --from-tree`) and stage to the
working tree without committing; `sgt commit` is the only verb that commits one, gated on a
passing oracle verdict (R14). `sgt land <branch>` is a distinct verb (U23's shared-branch CAS
advance) -- deliberately not named `commit` and not overloaded onto a bare `sgt land`, so "land"
only ever means the branch advance.

`sgt map` (re)builds the hierarchical feature tree over the op store and prints it; `blame`/
`status` are its read views. `merge`/`split`/`rename`/`move` are metadata-only feature verbs --
instant, reversible, content-untouched (R16) -- and `revert <feature>` bridges into the ideal
algebra: it resolves a feature id/label to its op-set and runs the same exact edit a single-op
`revert` would, grouped by feature.

`plan`/`checkpoint`/`drift` are the agentic loop (plan U14): `plan intake` decomposes a stated
plan into predicted hollow ops (off-chain, R18 -- never touching the ideal algebra); `checkpoint`
previews footprint-overlap matches between pending steps and ops mined since, and (given
`--confirm-hollow`/`--confirm-op`) applies exactly the named group; `drift` lists ops no active
plan predicted.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import (
    edit, feature, ideal_edit, init, inspect, intent, loop, migrate, oracle, porcelain, propose,
    review, rewrite, select, session, sync, tiers,
)

# The daily spine + the frequently-reached verbs kept at the top level, two groupings, and the
# unchanged collaboration/setup verbs (KTD2/R2, re-triaged). This is the ONLY top-level surface;
# every *rare/maintenance* verb is re-homed under `feature`/`advanced` and is reachable only there
# -- a hard rename, no alias layer. `advanced` is for maintenance/rare verbs only: the verbs a user
# runs daily (navigation, inspection, the agentic loop, the rewrite pipeline) stay one word away.
_VERBS = {
    # spine
    "save", "status", "log", "undo", "revert", "restore", "edit",
    # navigation + inspection (daily)
    "switch", "diff", "map", "blame",
    # agentic loop (daily)
    "plan", "checkpoint", "drift",
    # rewrite pipeline (daily)
    "commit", "fulfill",
    # groupings
    "feature", "advanced",
    # collaboration + setup (unchanged behavior + name)
    "sync", "land", "push", "propose", "session", "init", "mcp",
}

# Where each re-homed verb's registration lands (default: top-level, i.e. spine/collaboration/setup).
# The verb NAME and its args/handler are untouched -- only the parent subparser changes -- so the
# family modules register unchanged and each verb keeps its exact behavior at its new path.
_ROUTING = {
    "rename": "feature", "select": "feature", "why": "feature",
    "merge": "regroup", "split": "regroup", "move": "regroup",
    "state": "advanced", "oracle": "advanced", "fsck": "advanced",
    "reindex": "advanced", "history": "advanced",
    "compose": "advanced", "fold": "advanced", "preview": "advanced",
    "forks": "advanced", "after": "advanced",
    "tiers": "advanced", "migrate": "advanced", "intent": "advanced",
    "review-queue": "advanced", "identity": "advanced",
    "merge-op": "advanced", "split-op": "advanced", "transplant": "advanced",
    "unstage": "advanced", "repair": "advanced",
}

# Former top-level verb -> its new invocation path (KTD2's hard rename), derived from `_ROUTING` so
# the two can never drift: `main` uses it to point a user who typed a removed-but-known verb at its
# new home instead of a bare `_help()`. `regroup`-tier verbs nest one level deeper under `feature`.
_TIER_PATH = {"advanced": "advanced", "feature": "feature", "regroup": "feature regroup"}
_REMOVED = {verb: f"{_TIER_PATH[tier]} {verb}" for verb, tier in _ROUTING.items()}

_FAMILIES = (init, inspect, ideal_edit, feature, loop, sync, oracle, rewrite, migrate, propose,
             porcelain, tiers, select, session, review, intent, edit)


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

    if argv[0] == "help":
        return _help()

    if argv[0] not in _VERBS:
        # A removed-but-known old verb points at its new home (KTD2's hard rename); a genuinely
        # unknown token still falls to `_help()`.
        remedy = _REMOVED.get(argv[0])
        if remedy is not None:
            sys.stderr.write(f"sgt: `{argv[0]}` moved to `sgt {remedy}`\n")
            return 2
        return _help()

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _CLIExit as e:
        return e.code
    return args.func(args)


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
        '  sgt save [-m "<msg>"]       mine the working tree + commit a witness for it\n'
        "  sgt status [--json]         files/symbols/features, coverage, oracle status, drift\n"
        "  sgt log [--json]            the mined operation DAG\n"
        "  sgt undo                    invert the last mutating operation (the unified op log)\n"
        "  sgt revert <sel> [--emit]   remove a selection and everything built on it (I \\ upset X)\n"
        "  sgt restore <sel> [--emit]  re-add a selection and its prerequisites (I ∪ downset X)\n"
        "  sgt edit <sel> [--repair]   change a symbol/feature in place; dependents repoint\n"
        "\n"
        "  navigation + inspection:\n"
        "  sgt switch <branch>         move HEAD to a branch's committed tree (mines both ends)\n"
        "  sgt diff <ref_a> <ref_b>    semantic diff: the symmetric difference of two ideals\n"
        "  sgt map [--rebuild]         (re)build + print the hierarchical feature tree\n"
        "  sgt blame <path>            per-symbol semantic blame from the op DAG\n"
        "\n"
        "  agentic loop:\n"
        "  sgt plan <cmd>              intake/abandon/status a stated plan's predicted hollow ops\n"
        "  sgt checkpoint [--json]     preview + confirm plan-step <-> mined-op matches\n"
        "  sgt drift [--json]          ops no active plan predicted\n"
        "\n"
        "  rewrite pipeline:\n"
        "  sgt fulfill <draft> --from-tree   fill a drafted hollow op from the working tree\n"
        "  sgt commit                  commit a staged rewrite, gated oracle-green\n"
        "\n"
        "  groupings:\n"
        "  sgt feature <cmd>           author/re-cut features: regroup (merge/split/move),\n"
        "                              rename, select, why\n"
        "  sgt advanced <cmd>          maintenance/rare verbs: fsck, reindex, state, oracle,\n"
        "                              after, fold, preview, tiers, identity, migrate,\n"
        "                              history, compose, forks, intent, review-queue,\n"
        "                              unstage, repair, merge-op, split-op, transplant\n"
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
