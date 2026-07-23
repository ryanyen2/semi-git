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

`plan` is the agentic loop's off-chain half (plan U14): `plan intake` decomposes a stated plan into
predicted hollow ops (R18 -- never touching the ideal algebra). Step<->op matching and plan-drift
(the former `checkpoint`/`drift` verbs) are folded into `sgt save` (U12/R10): a save auto-confirms
each unambiguous single-step match and reports the rest; `sgt save --resolve-plan` settles an
n:m/multi-step match (`--confirm-hollow`/`--confirm-op` names one group). The working-tree sense of
"drift" keeps its name in `status`/`fsck --tree`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import (
    edit, feature, ideal_edit, init, inspect, intent, loop, migrate, oracle, porcelain, propose,
    resolve, review, rewrite, select, session, suggestions, sync, tiers,
)

# The daily spine + the frequently-reached verbs kept at the top level, two groupings, and the
# unchanged collaboration/setup verbs (KTD2/R2, re-triaged). This is the ONLY top-level surface;
# every *rare/maintenance* verb is re-homed under `feature`/`advanced` and is reachable only there
# -- a hard rename, no alias layer. `advanced` is for maintenance/rare verbs only: the verbs a user
# runs daily (navigation, inspection, the agentic loop, the rewrite pipeline) stay one word away.
_VERBS = {
    # spine (U14: the grid is the only inspection surface -- status/map/graph/episodes are `log`
    # modes, no longer verbs; blame/edit/commit/fulfill demoted under `advanced`)
    "save", "log", "undo", "revert", "restore", "resolve",
    # navigation + inspection (daily). `intent` stays top-level: its subcommands (list/show/build)
    # don't map to a `log` mode and it was deliberately re-promoted (c4f9966/KTD8).
    "switch", "diff", "intent",
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
    "rename": "feature", "select": "feature", "why": "feature",
    "merge": "regroup", "split": "regroup", "move": "regroup",
    "state": "advanced", "oracle": "advanced", "fsck": "advanced",
    "reindex": "advanced", "history": "advanced",
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

# Former top-level verb -> its new invocation path (KTD2's hard rename), derived from `_ROUTING` so
# the two can never drift: `main` uses it to point a user who typed a removed-but-known verb at its
# new home instead of a bare `_help()`. `regroup`-tier verbs nest one level deeper under `feature`.
_TIER_PATH = {"advanced": "advanced", "feature": "feature", "regroup": "feature regroup"}
_REMOVED = {verb: f"{_TIER_PATH[tier]} {verb}" for verb, tier in _ROUTING.items()}

# Verbs folded *into another verb* (not re-homed under a tier): typed by muscle memory, they point
# at their new home rather than silently falling to `_help()`. `checkpoint`/`drift` folded into
# `save` (U12/R10): a save auto-confirms unambiguous plan-step matches and reports unmatched ops.
# `map`/`graph`/`episodes`/`status` are re-projections of one (feature × commit) dataset, so U14
# folds them onto `sgt log`'s render modes (the grid is the only inspection surface, KTD8/KTD9).
_FOLDED = {
    "checkpoint": "save  (matches auto-confirm; `sgt save --resolve-plan` settles an ambiguous one)",
    "drift": "save  (unmatched ops show in `sgt save`; `sgt log --summary` for working-tree drift)",
    "map": "log --tree",
    "graph": "log",
    "episodes": "log --rail",
    "status": "log --summary",
}

_FAMILIES = (init, inspect, ideal_edit, feature, loop, sync, oracle, rewrite, migrate, propose,
             porcelain, tiers, select, session, review, intent, edit, resolve, suggestions)


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
        folded = _FOLDED.get(argv[0])
        if folded is not None:
            sys.stderr.write(f"sgt: `{argv[0]}` folded into `sgt {folded}`\n")
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
        '  sgt save [-m "<msg>"]       mine the working tree + commit a witness (auto-matches plan steps)\n'
        "  sgt log [--json]            the lane×commit grid — the one inspection surface:\n"
        "                              --tree (feature tree) · --rail (episode rail) · --summary\n"
        "                              (status scalars) · --ops (raw op DAG) · --rebuild (recluster)\n"
        "  sgt undo                    invert the last mutating operation (the unified op log)\n"
        "  sgt revert <sel> [--emit]   remove a selection and everything built on it (I \\ upset X)\n"
        "  sgt restore <sel> [--emit]  re-add a selection and its prerequisites (I ∪ downset X)\n"
        "  sgt resolve <symbol>        guided same-symbol fork resolution (merge-op → fulfill → land)\n"
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
        "                              rename, select, why\n"
        "  sgt advanced <cmd>          maintenance/rare verbs: blame, edit, fulfill, commit, fsck,\n"
        "                              reindex, state, oracle, after, fold, preview, tiers,\n"
        "                              identity, migrate, history, compose, forks, review-queue,\n"
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
