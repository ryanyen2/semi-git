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
    feature, ideal_edit, init, inspect, loop, migrate, oracle, porcelain, propose, review, rewrite,
    select, session, sync, tiers,
)

_VERBS = {
    "init", "revert", "restore", "log", "state", "diff", "oracle", "fsck", "mcp", "help",
    "merge-op", "split-op", "transplant", "identity", "fulfill", "commit", "land", "unstage",
    "repair", "map", "blame", "status", "merge", "split", "rename", "move",
    "plan", "checkpoint", "drift", "sync", "push", "forks", "history", "preview", "compose", "fold",
    "after", "migrate", "propose", "switch", "save", "undo", "tiers", "select", "why", "session",
    "review-queue",
}

_FAMILIES = (init, inspect, ideal_edit, feature, loop, sync, oracle, rewrite, migrate, propose,
             porcelain, tiers, select, session, review)


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
        "sgt — semantic operation-ideal version control (kernel)\n\n"
        "  sgt init [path] [--horizon <ref>]   bind git + the kernel op store; mine existing\n"
        "                              history, or (with --horizon) only from that commit on (R10)\n"
        "  sgt revert [--emit] <ref>   remove an op and everything built on it (I \\ upset X)\n"
        "  sgt revert <ref> --keep-dependents [--repair]   same, but drafts a continuation hollow\n"
        "                              per dependent (--repair hands it straight to the LLM repair loop)\n"
        "  sgt revert --session <name> [--emit]   revert every op a session's attribution covers\n"
        "  sgt restore [--emit] <ref>  re-add an op and its prerequisites (I ∪ downset X)\n"
        "  sgt after <a> <b> [--retract]   declare (or retract) the order edge a ≤ b\n"
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
        "  sgt repair <draft-id> [--backend api]   fulfill an already-drafted hollow via the LLM\n"
        "                              repair loop, then land it through the same oracle gate\n"
        '  sgt commit [--message ...] [--override pass|fail --reason "..."]   commit what\'s staged\n'
        "  sgt land <branch> [--json]  advance a shared branch record by CAS, gated oracle-green (LAW-G)\n"
        "  sgt unstage                 abandon the staged candidate; restore the committed ideal\n"
        "  sgt switch <branch>         materialize a branch's ideal (the sgt-native `git switch`)\n"
        '  sgt save [-m "<msg>"]       mine the working tree + commit a witness for it\n'
        "  sgt undo                    invert the last ideal edit (revert/restore/save/…)\n"
        "  sgt map [--json]            (re)build + print the hierarchical feature tree\n"
        "  sgt blame [--json] <file>   per-symbol feature attribution for a file's live entities\n"
        "  sgt status [--json]        files/symbols/features, coverage, oracle status, drift\n"
        "  sgt select <feature>...    explain the closure a feature selection induces (files, ops,\n"
        "                              and the requires-chain that pulled in anything cross-feature)\n"
        "  sgt why <op> [--for <feature>]   explain one op's feature attribution, or the chain\n"
        "                              that pulled it into a given feature's closure\n"
        "  sgt merge <survivor> <absorbed>        union two features under the survivor id\n"
        "  sgt split <feature> [--apply]          preview (then confirm) a two-way feature split\n"
        '  sgt rename <feature> "<label>"         override a feature\'s label, durably\n'
        "  sgt move <op>... --to <feature>        retag ops (+ their symbols) onto another feature\n"
        "  sgt revert <feature>        revert an entire feature's op-set (grouped ∪ upset X)\n"
        "  sgt history [--json]        mined commits in order + every op's kind/feature/commit-index\n"
        "  sgt preview <verb> <args>   side-effect-free preview of merge/split/rename/move/revert\n"
        "  sgt compose [--json]        one aggregate read: map/history/status/forks/plan/drift/\n"
        "                              sessions/trust + the oracle verdict + open proposals\n"
        "  sgt fold --at <spec> [--json]   side-effect-free code(I) + oracle verdict at an arbitrary\n"
        "                              frontier (a commit-index, `op:<id>,...`, or a ref); no checkout\n"
        '  sgt plan intake "<text>"    decompose a plan into predicted hollow ops (off-chain)\n'
        "  sgt plan abandon <session>  drop a session's pending hollows and its record\n"
        "  sgt plan status [--json]    active sessions and their steps' match status\n"
        "  sgt checkpoint [--json]     preview step<->op footprint-overlap groups, and drift\n"
        "  sgt checkpoint --confirm-hollow <id>... --confirm-op <id>...   apply one named group\n"
        "  sgt drift [--json]          ops mined that no active plan predicted\n"
        "  sgt sync [remote] [branch]  fetch + merge a teammate's work; union ops, reconcile\n"
        "                              pins/declared-edges/tree, surface any chain fork\n"
        "  sgt push [remote] [branch]  non-forcing git push; a rejection routes you to `sgt sync`\n"
        "  sgt forks [<symbol>] [--json]   list open same-symbol forks + their `sgt merge-op`\n"
        "                              remedies; given a symbol, show that fork's two tips' files\n"
        '  sgt propose create [--base REF] [--title "..."]   a base+Δ review object over REF (default main)\n'
        "  sgt propose status <id>     staleness by re-union: current / clean-reunion / fork\n"
        "  sgt propose land <id> [--subset <feature>...]   advance the base by CAS, all Δ or named features\n"
        "  sgt propose render <id> --github   emit a suggested branch + a GitHub PR body (plain markdown)\n"
        "  sgt propose publish <id> [--remote origin]   push the rendered branch + create/update a GitHub PR\n"
        "  sgt tiers [--json]          the three-tier file boundary's effective config + coverage\n"
        "  sgt tiers set <pattern> <entity|opaque|ignored>   add an override (`.sgt/tiers.json`)\n"
        "  sgt session start <name> [--base <branch>]   a git-worktree scratch tree on its own branch\n"
        "  sgt session status [<name>] [--watch]   active sessions + early-fork footprint overlaps\n"
        "  sgt session land <name>     CAS-land the session's ops onto its target branch (U23)\n"
        "  sgt session gc [--force]    reap sessions whose owning process has died\n"
        "  sgt review-queue list [--json]   ops with session/agent/drift provenance, not yet reviewed\n"
        '  sgt review-queue ack <op-id>... [--session <name>] [--note "..."]   mark an op-set reviewed\n'
        "  sgt git <args...>           pass through to real git (refuses tree-mutating verbs; --force overrides)\n"
        "  sgt mcp [path]              run the MCP stdio server for coding-agent clients\n"
        "  <ref> is an op-id, an op-id prefix, a `file::name` symbol (its frontier tip), or a\n"
        "  feature id/label (`revert` only)\n"
        "  (read verbs take --json for the machine-readable sgt.api projection)\n"
        "\n"
        "  advanced/maintenance (not part of the daily loop):\n"
        "  sgt migrate [feature-ids|ops-v3] [--apply]   dry-run-by-default op-store schema\n"
        "                              migration; a one-time repair step, not a repeat verb\n"
    )
    return 0
