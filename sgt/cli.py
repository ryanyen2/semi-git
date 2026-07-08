"""The `sgt` command surface — the operation-ideal kernel (plan U7/U8/U9/U11, flipped in U10).

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

The feature-lens verbs (`merge`/`split`/`rename`/`move`, `sgt map`) and the agentic-loop verbs
(`plan`/`checkpoint`/drift review) land in later units (P3/P4) — they have no kernel backing yet,
so they are not registered here rather than half-working against a deleted subsystem.
"""

from __future__ import annotations

import sys

_VERBS = {
    "init", "revert", "restore", "log", "state", "diff", "oracle", "fsck", "mcp", "help",
    "merge-op", "split-op", "transplant", "identity", "fulfill", "land",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _help()

    cmd = argv[0]
    if cmd not in _VERBS:
        return _help()
    rest = argv[1:]

    repo = "."
    # `--json` switches the read verbs to the canonical machine-readable projection (sgt.api),
    # which the VSCode extension and TUI consume. Stripped here so verb parsing is unaffected.
    as_json = "--json" in rest
    rest = [a for a in rest if a != "--json"]

    if cmd == "help":
        return _help()

    if cmd == "init":
        path = rest[0] if rest else "."
        from sgt.core.lens import init as kernel_init

        kernel_init(path)
        print(f"✓ initialized sgt kernel in {path} (.sgt/ + git)")
        return 0

    if cmd == "mcp":
        from sgt.mcp import serve

        serve(rest[0] if rest else repo)  # stdio MCP server for coding-agent clients
        return 0

    if cmd == "fsck":
        return _fsck(repo, as_json)

    if cmd == "log":
        return _log(repo, as_json)

    if cmd == "state":
        return _state(repo, as_json)

    if cmd == "diff":
        if len(rest) < 2:
            print("usage: sgt diff [--json] <ref-a> <ref-b>")
            return 2
        return _diff(repo, rest[0], rest[1], as_json)

    if cmd == "oracle":
        return _oracle(repo, rest, as_json)

    if cmd == "revert" and "--keep-dependents" in rest:
        rest = [a for a in rest if a != "--keep-dependents"]
        return _revert_keep_dependents(repo, rest, as_json)

    if cmd in ("revert", "restore"):
        return _kernel_edit_verb(repo, cmd, rest, as_json)

    if cmd == "merge-op":
        return _merge_op(repo, rest, as_json)

    if cmd == "split-op":
        return _split_op(repo, rest, as_json)

    if cmd == "transplant":
        return _transplant(repo, rest, as_json)

    if cmd == "identity":
        return _identity(repo, rest, as_json)

    if cmd == "fulfill":
        return _fulfill(repo, rest, as_json)

    if cmd == "land":
        return _land(repo, rest, as_json)

    return _help()


def _opt_value(args: list[str], flag: str) -> str | None:
    """Return the value following ``flag`` (e.g. ``--intent "..."``), or None if absent."""
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def _strip_opt(args: list[str], flag: str) -> tuple[str | None, list[str]]:
    """Like ``_opt_value``, but also returns ``args`` with ``flag`` and its value removed --
    for verbs (U11's) whose remaining positional args matter after the flag is consumed."""
    if flag not in args:
        return None, args
    i = args.index(flag)
    value = args[i + 1] if i + 1 < len(args) else None
    return value, args[:i] + args[i + 2 :]


def _emit_json(payload) -> int:
    import json

    print(json.dumps(payload, indent=2))
    return 1 if isinstance(payload, dict) and "error" in payload else 0


def _fsck(repo: str, as_json: bool = False) -> int:
    """Verify the kernel op store's integrity (plan U3): every ``.sgt/ops/<id>`` file's content
    hashes to its own filename. Repair (re-mining) is a follow-up step, not this verb's job."""
    from sgt.core.store import fsck as run_fsck

    report = run_fsck(repo)
    if as_json:
        return _emit_json(
            {
                "ok": report.ok,
                "checked": report.checked,
                "bad_hash": list(report.bad_hash),
                "corrupt": list(report.corrupt),
            }
        )
    icon = "✓" if report.ok else "✗"
    print(f"{icon} fsck — {report.checked} op(s) checked")
    for name in report.bad_hash:
        print(f"    bad hash: {name}")
    for name in report.corrupt:
        print(f"    corrupt: {name}")
    return 0 if report.ok else 1


def _log(repo: str, as_json: bool = False) -> int:
    """The kernel op DAG (plan U7). Mine-on-contact first, then project via `sgt.api.oplog_view`."""
    from sgt.api import oplog_view
    from sgt.core.lens import get

    get(repo)  # sync foreign commits into the store before inspecting it
    view = oplog_view(repo)
    if as_json:
        return _emit_json(view)
    if not view["ops"]:
        print("(no ops — nothing mined yet; commit some work then run `sgt log`)")
        return 0
    print(f"{view['count']} op(s):")
    for op in view["ops"]:
        syms = ", ".join(f["symbol"] for f in op["footprint"])
        print(f"  {op['id'][:12]} [{op['kind']}]: {syms}")
    return 0


def _state(repo: str, as_json: bool = False) -> int:
    """The current ref's ideal (plan U7): frontier, coverage, entity-granularity fraction."""
    from sgt.api import state_view
    from sgt.core.lens import get

    get(repo)  # mine-on-contact so the ideal reflects current reality
    view = state_view(repo)
    if as_json:
        return _emit_json(view)
    pct = view["coverage_fraction"] * 100
    print(f"{len(view['frontier'])} symbol(s) at the frontier; "
          f"{len(view['covered_paths'])} path(s) covered, "
          f"{len(view['entity_paths'])} at entity granularity ({pct:.0f}%)")
    for path in view["covered_paths"]:
        mark = "entity" if path in set(view["entity_paths"]) else "whole-file"
        print(f"  {path}  ({mark})")
    if view["oracle_configured"]:
        from sgt.core.oracle import overall_status

        print(f"  oracle: {overall_status(view['oracle_verdict'])}")
    return 0


def _diff(repo: str, ref_a: str, ref_b: str, as_json: bool = False) -> int:
    """Ideal-vs-ideal semantic diff (plan U7): the symmetric difference grouped by symbol."""
    from sgt.api import ideal_diff_view
    from sgt.core.lens import get

    get(repo)  # sync the current ref; ref_a/ref_b use whatever the store already holds
    view = ideal_diff_view(repo, ref_a, ref_b)
    if as_json:
        return _emit_json(view)
    print(f"{view['count']} differing op(s) between {ref_a} (a) and {ref_b} (b):")
    for sym, sides in view["by_symbol"].items():
        print(f"  {sym}")
        for oid in sides["only_in_a"]:
            print(f"    a: {oid[:12]}")
        for oid in sides["only_in_b"]:
            print(f"    b: {oid[:12]}")
    return 0


def _oracle(repo: str, rest: list[str], as_json: bool = False) -> int:
    """Async tiered build/test verdicts attached to the current ideal (plan U9, R13).
    `sgt oracle run [--tier NAME]` executes configured tiers in declared order, stopping at the
    first failure; `sgt oracle override --status pass|fail --reason "..." [--by NAME]` records a
    human verdict that supersedes them. Materialization itself never calls this -- a verdict is
    "pending" until this verb is run explicitly."""
    from sgt.core import oracle
    from sgt.core.lens import get

    usage = ('usage: sgt oracle run [--json] [--tier NAME] | '
             'sgt oracle override --status pass|fail --reason "..." [--by NAME]')
    if not rest or rest[0] not in ("run", "override"):
        print(usage)
        return 2

    get(repo)  # mine-on-contact so the verdict is keyed to the current ideal
    sub, opts = rest[0], rest[1:]

    if sub == "run":
        tier = _opt_value(opts, "--tier")
        try:
            result = oracle.run(repo, tier=tier)
        except ValueError as e:
            print(f"✗ {e}")
            return 2
        if not result["configured"]:
            print("⚠ no oracle configured (.sgt/oracle.json not found) — proceeding without a verdict")
            return 0
        if as_json:
            return _emit_json(result)
        for name, tr in result["tiers"].items():
            icon = "✓" if tr["status"] == "pass" else "✗"
            print(f"{icon} [{name}] exit {tr['exit_code']}")
        return 0

    status = _opt_value(opts, "--status")
    reason = _opt_value(opts, "--reason")
    by = _opt_value(opts, "--by")
    if status not in ("pass", "fail") or reason is None:
        print(usage)
        return 2
    record = oracle.override(repo, status, reason, by)
    if as_json:
        return _emit_json(record)
    print(f"✓ override recorded: {status} ({reason})")
    return 0


def _kernel_edit_verb(repo: str, cmd: str, rest: list[str], as_json: bool) -> int:
    """revert/restore (plan U8, flipped onto the kernel in U10): exact ideal edits (`I \\ ↑X` /
    `I ∪ ↓X`) with `--emit` previews and chain-fork surfacing (AE2). `--emit` is side-effect-free
    (`api.verb_preview_view`); otherwise the verb applies via `sgt.core.verbs`."""
    from sgt.core import verbs
    from sgt.core.lens import get

    emit = "--emit" in rest
    rest = [a for a in rest if a != "--emit"]
    if not rest:
        print(f"usage: sgt {cmd} [--emit] [--json] <ref>")
        return 2
    target = " ".join(rest)
    get(repo)  # mine-on-contact before planning/applying the edit (R9)

    if emit:
        from sgt.api import verb_preview_view

        view = verb_preview_view(repo, cmd, target)
        return _emit_json(view) if as_json else _print_verb_view(view)

    verb = verbs.revert if cmd == "revert" else verbs.restore
    preview = verb(repo, target)  # apply
    view = {
        "ok": preview.ok, "verb": preview.verb, "target": preview.target,
        "removed": sorted(preview.removed), "added": sorted(preview.added),
        "affected_symbols": list(preview.affected_symbols), "forked": preview.forked,
        "message": preview.message,
    }
    return _emit_json(view) if as_json else _print_verb_view(view)


def _print_draft(draft, as_json: bool) -> int:
    """Shared printer for the U11 draft-producing verbs (`merge-op`/`split-op`/`transplant`/
    `revert --keep-dependents`) -- each returns a `sgt.core.rewrite.RewriteDraft`."""
    if as_json:
        return _emit_json({
            "ok": draft.ok, "verb": draft.verb, "target": draft.target,
            "draft_id": draft.draft_id, "hollow_ids": list(draft.hollow_ids),
            "message": draft.message,
        })
    icon = "✓" if draft.ok else "✗"
    print(f"{icon} [{draft.verb}] {draft.target}" + (f" — {draft.message}" if draft.message else ""))
    if not draft.ok:
        return 1
    if draft.draft_id:
        print(f"    draft: {draft.draft_id}")
        for hid in draft.hollow_ids:
            print(f"    hollow: {hid[:12]}")
        print(f"    edit the working tree, then: sgt fulfill {draft.draft_id} --from-tree")
    return 0


def _merge_op(repo: str, rest: list[str], as_json: bool) -> int:
    """`merge-op <tip_a> <tip_b>` (plan U11, R14): drafts a hollow reconciling a chain fork --
    U8's cherry-pick refuses on exactly this shape (AE2)."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    intent, rest = _strip_opt(rest, "--intent")
    if len(rest) < 2:
        print('usage: sgt merge-op <tip_a> <tip_b> [--intent "..."]')
        return 2
    get(repo)
    draft = rewrite.merge_op(repo, rest[0], rest[1], intent=intent)
    return _print_draft(draft, as_json)


def _split_op(repo: str, rest: list[str], as_json: bool) -> int:
    """`split-op <op-id>` (plan U11, R14): drafts an intermediate cut of a two-concern op."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    intent, rest = _strip_opt(rest, "--intent")
    if not rest:
        print('usage: sgt split-op <op-id> [--intent "..."]')
        return 2
    get(repo)
    draft = rewrite.split_op(repo, rest[0], intent=intent)
    return _print_draft(draft, as_json)


def _transplant(repo: str, rest: list[str], as_json: bool) -> int:
    """`transplant <op-id>... --onto <ref>` (plan U11, R14, AE3): drafts hollows with the
    destination ref's own chain tip as ``before_version``."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    onto, rest = _strip_opt(rest, "--onto")
    intent, rest = _strip_opt(rest, "--intent")
    if not rest or not onto:
        print('usage: sgt transplant <op-id>... --onto <ref> [--intent "..."]')
        return 2
    get(repo)
    draft = rewrite.transplant(repo, rest, onto, intent=intent)
    return _print_draft(draft, as_json)


def _revert_keep_dependents(repo: str, rest: list[str], as_json: bool) -> int:
    """`revert <ref> --keep-dependents` (plan U11, R14): removes the target's up-set but drafts
    a continuation hollow per direct reference-dependent, so its symbol stays live."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    intent, rest = _strip_opt(rest, "--intent")
    if not rest:
        print("usage: sgt revert <ref> --keep-dependents")
        return 2
    get(repo)
    draft = rewrite.revert_keep_dependents(repo, " ".join(rest), intent=intent)
    return _print_draft(draft, as_json)


def _identity(repo: str, rest: list[str], as_json: bool) -> int:
    """`identity split <a> <b>` / `identity join <a> <b>` (plan U11, R14): corrects the tiered
    matcher itself, not a chain -- writes a committed `.sgt/identity_constraints.json`."""
    from sgt.core import rewrite

    usage = "usage: sgt identity split <a> <b> | sgt identity join <a> <b>"
    if len(rest) < 3 or rest[0] not in ("split", "join"):
        print(usage)
        return 2
    sub, a, b = rest[0], rest[1], rest[2]
    data = (rewrite.identity_split if sub == "split" else rewrite.identity_join)(repo, a, b)
    if as_json:
        return _emit_json(data)
    print(f"✓ identity {sub}: {a} / {b}")
    return 0


def _fulfill(repo: str, rest: list[str], as_json: bool) -> int:
    """`fulfill <draft-id> --from-tree` (plan U11): supplies a drafted hollow's image from the
    working tree, validates + folds + writes the candidate — no commit; run `sgt land` next."""
    from sgt.core import rewrite

    from_tree = "--from-tree" in rest
    rest = [a for a in rest if a != "--from-tree"]
    if not rest:
        print("usage: sgt fulfill <draft-id> --from-tree")
        return 2
    try:
        candidate = rewrite.fulfill(repo, rest[0], from_tree=from_tree)
    except rewrite.RewriteError as e:
        print(f"✗ {e}")
        return 1
    if as_json:
        return _emit_json({"ok": True, "op_ids": sorted(candidate.op_ids)})
    print(f"✓ staged {len(candidate.op_ids)} op(s) to the working tree (uncommitted) — "
          "run `sgt oracle run` then `sgt land`")
    return 0


def _land(repo: str, rest: list[str], as_json: bool) -> int:
    """`land [--message "..."] [--override pass|fail --reason "..." [--by NAME]]` (plan U11,
    R14): commits the last-staged rewrite candidate, refusing unless its oracle verdict is
    "pass" (or the supplied override resolves to one)."""
    from sgt.core import rewrite

    message = _opt_value(rest, "--message")
    status = _opt_value(rest, "--override")
    reason = _opt_value(rest, "--reason")
    by = _opt_value(rest, "--by")
    override = (status, reason or "", by) if status else None
    try:
        sha = rewrite.land(repo, message=message, override=override)
    except rewrite.RewriteError as e:
        print(f"✗ {e}")
        return 1
    if as_json:
        return _emit_json({"ok": True, "sha": sha})
    print(f"✓ landed {sha[:12]}")
    return 0


def _print_verb_view(view: dict) -> int:
    icon = "✓" if view["ok"] else "✗"
    print(f"{icon} [{view['verb']}] {view['target']}" + (f" — {view['message']}" if view["message"] else ""))
    if not view["ok"]:
        return 1
    if view["removed"]:
        print(f"    removed {len(view['removed'])} op(s): " + ", ".join(o[:12] for o in view["removed"]))
    if view["added"]:
        print(f"    added {len(view['added'])} op(s): " + ", ".join(o[:12] for o in view["added"]))
    if view["affected_symbols"]:
        print(f"    affected: {', '.join(view['affected_symbols'])}")
    return 0


def _help() -> int:
    print(
        "sgt — semantic operation-ideal version control (kernel)\n\n"
        "  sgt init [path]             bind git + the kernel op store; mine existing history\n"
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
        "  sgt mcp [path]              run the MCP stdio server for coding-agent clients\n"
        "  <ref> is an op-id, an op-id prefix, or a `file::name` symbol (its frontier tip)\n"
        "  (read verbs take --json for the machine-readable sgt.api projection)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
