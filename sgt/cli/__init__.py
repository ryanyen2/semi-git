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
working tree without committing; `sgt land` is the only verb that commits one, gated on a passing
oracle verdict (R14).

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

from . import feature, ideal_edit, init, inspect, loop, migrate, oracle, rewrite, sync

_VERBS = {
    "init", "revert", "restore", "log", "state", "diff", "oracle", "fsck", "mcp", "help",
    "merge-op", "split-op", "transplant", "identity", "fulfill", "land",
    "map", "blame", "status", "merge", "split", "rename", "move",
    "plan", "checkpoint", "drift", "sync", "push", "forks", "history", "preview",
    "after", "migrate",
}

# Tree-mutating git subcommands warrant an advisory when reached via `sgt git` -- they change the
# working tree behind sgt's back (C8; refusal is deferred to the porcelain plan, this is advisory).
_GIT_TREE_MUTATING = frozenset({
    "checkout", "switch", "restore", "pull", "reset", "merge", "rebase", "revert",
    "stash", "cherry-pick", "am", "apply",
})

_FAMILIES = (init, inspect, ideal_edit, feature, loop, sync, oracle, rewrite, migrate)


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
    # `--json` switches the read verbs to the canonical machine-readable projection (sgt.api),
    # which the VSCode extension and TUI consume. Shared by every subparser via `parents=`.
    parent.add_argument("--json", action="store_true", dest="as_json")
    parser = _Parser(prog="sgt")
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

    if argv[0] == "help" or argv[0] not in _VERBS:
        return _help()

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except _CLIExit as e:
        return e.code
    return args.func(args)


def _git_passthrough(args: list[str]) -> int:
    """`sgt git <args...>`: forward verbatim to real git (inherit stdio, propagate exit code).
    The args are untouched -- no `--json` stripping, no flag rewriting. A tree-mutating subcommand
    gets an advisory to stderr first (C8); the command runs regardless."""
    if args and args[0] in _GIT_TREE_MUTATING:
        print("note: this bypasses sgt's own tracking for this change; "
              "run `sgt sync`/`sgt log` after to reconcile", file=sys.stderr)
    return subprocess.run(["git", *args]).returncode


def _help() -> int:
    print(
        "sgt — semantic operation-ideal version control (kernel)\n\n"
        "  sgt init [path] [--horizon <ref>]   bind git + the kernel op store; mine existing\n"
        "                              history, or (with --horizon) only from that commit on (R10)\n"
        "  sgt revert [--emit] <ref>   remove an op and everything built on it (I \\ upset X)\n"
        "  sgt revert <ref> --keep-dependents   same, but drafts a continuation hollow per dependent\n"
        "  sgt restore [--emit] <ref>  re-add an op and its prerequisites (I ∪ downset X)\n"
        "  sgt fsck [--json]           verify the op store's content-address integrity\n"
        "  sgt log [--json]            the mined operation DAG\n"
        "  sgt state [--json]          the current ref's ideal: frontier, coverage, oracle verdict\n"
        "  sgt diff [--json] <a> <b>   semantic diff between two refs' ideals, grouped by symbol\n"
        '  sgt oracle run [--tier N]   run configured build/test tiers against the current ideal\n'
        '  sgt oracle override ...     record a human verdict (--status pass|fail --reason "...")\n'
        '  sgt merge-op <a> <b>        draft a hollow reconciling a chain fork (AE2\'s refusal)\n'
        "  sgt split-op <op-id>        draft an intermediate cut of a two-concern op\n"
        "  sgt transplant <op>... --onto <ref>   draft hollows backported onto another chain (AE3)\n"
        "  sgt identity split|join <a> <b>       correct the matcher itself, not a chain\n"
        "  sgt fulfill <draft-id> --from-tree     supply a drafted hollow's image; stages, no commit\n"
        '  sgt land [--message ...] [--override pass|fail --reason "..."]   commit what\'s staged\n'
        "  sgt land <branch> [--json]  advance a shared branch record by CAS, gated oracle-green (LAW-G)\n"
        "  sgt map [--json]            (re)build + print the hierarchical feature tree\n"
        "  sgt blame [--json] <file>   per-symbol feature attribution for a file's live entities\n"
        "  sgt status [--json]        files/symbols/features, coverage, oracle status, drift\n"
        "  sgt merge <survivor> <absorbed>        union two features under the survivor id\n"
        "  sgt split <feature> [--apply]          preview (then confirm) a two-way feature split\n"
        '  sgt rename <feature> "<label>"         override a feature\'s label, durably\n'
        "  sgt move <op>... --to <feature>        retag ops (+ their symbols) onto another feature\n"
        "  sgt revert <feature>        revert an entire feature's op-set (grouped ∪ upset X)\n"
        "  sgt history [--json]        mined commits in order + every op's kind/feature/commit-index\n"
        "  sgt preview <verb> <args>   side-effect-free preview of merge/split/rename/move/revert\n"
        '  sgt plan intake "<text>"    decompose a plan into predicted hollow ops (off-chain)\n'
        "  sgt plan abandon <session>  drop a session's pending hollows and its record\n"
        "  sgt plan status [--json]    active sessions and their steps' match status\n"
        "  sgt checkpoint [--json]     preview step<->op footprint-overlap groups, and drift\n"
        "  sgt checkpoint --confirm-hollow <id>... --confirm-op <id>...   apply one named group\n"
        "  sgt drift [--json]          ops mined that no active plan predicted\n"
        "  sgt sync [remote] [branch]  fetch + merge a teammate's work; union ops, reconcile\n"
        "                              pins/declared-edges/tree, surface any chain fork\n"
        "  sgt push [remote] [branch]  non-forcing git push; a rejection routes you to `sgt sync`\n"
        "  sgt forks [--json]          list open same-symbol forks + their `sgt merge-op` remedies\n"
        "  sgt git <args...>           pass through to real git (advises on tree-mutating verbs)\n"
        "  sgt mcp [path]              run the MCP stdio server for coding-agent clients\n"
        "  <ref> is an op-id, an op-id prefix, a `file::name` symbol (its frontier tip), or a\n"
        "  feature id/label (`revert` only)\n"
        "  (read verbs take --json for the machine-readable sgt.api projection)\n"
    )
    return 0
