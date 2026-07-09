"""The `sgt` command surface — the operation-ideal kernel (plan U7/U8/U9/U11, flipped in U10),
plus the feature lens (U13).

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
`revert` would, grouped by feature. The agentic-loop verbs (`plan`/`checkpoint`/drift review)
land in a later unit (P4) — not registered here rather than half-working against a deleted
subsystem.
"""

from __future__ import annotations

import sys

_VERBS = {
    "init", "revert", "restore", "log", "state", "diff", "oracle", "fsck", "mcp", "help",
    "merge-op", "split-op", "transplant", "identity", "fulfill", "land",
    "map", "blame", "status", "merge", "split", "rename", "move",
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

    if cmd == "map":
        return _map(repo, as_json)

    if cmd == "blame":
        if not rest:
            print("usage: sgt blame [--json] <file>")
            return 2
        return _blame(repo, rest[0], as_json)

    if cmd == "status":
        return _status(repo, as_json)

    if cmd == "merge":
        if len(rest) < 2:
            print("usage: sgt merge [--json] <survivor-feature> <absorbed-feature>")
            return 2
        return _feature_merge(repo, rest[0], rest[1], as_json)

    if cmd == "split":
        return _feature_split(repo, rest, as_json)

    if cmd == "rename":
        if len(rest) < 2:
            print('usage: sgt rename [--json] <feature> "<new label>"')
            return 2
        return _feature_rename(repo, rest[0], " ".join(rest[1:]), as_json)

    if cmd == "move":
        return _feature_move(repo, rest, as_json)

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


def _print_map_tree(view: dict) -> None:
    """Indented `label (id) · N op(s)` tree, DFS from `roots` via each node's `children`."""
    by_id = {n["id"]: n for n in view["nodes"]}

    def visit(nid: str, depth: int) -> None:
        n = by_id[nid]
        print(f"{'  ' * depth}{n['label']} ({n['id']}) · {n['op_count']} op(s)")
        for child in n["children"]:
            visit(child, depth + 1)

    for root in view["roots"]:
        visit(root, 0)
    print(f"{view['feature_count']} feature(s)")


def _map(repo: str, as_json: bool = False) -> int:
    """`sgt map` (plan U13): (re)build the feature tree from the live op store -- clustering,
    Greene identity, pins, labeling -- then print the kernel-backed projection (`api.map_view`)."""
    from sgt.api import map_view
    from sgt.core.lens import get
    from sgt.lens.map import build_map

    get(repo)  # mine-on-contact so the map reflects current reality (R9)
    build_map(repo)
    view = map_view(repo)
    if as_json:
        return _emit_json(view)
    _print_map_tree(view)
    return 0


def _blame(repo: str, file: str, as_json: bool = False) -> int:
    """`sgt blame <file>` (plan U13): per-symbol feature attribution (`sym -> max-op-in-I ->
    feature`) for the file's live entities."""
    from sgt.api import blame_view
    from sgt.core.lens import get

    get(repo)
    view = blame_view(repo, file)
    if as_json:
        return _emit_json(view)
    if view.get("error"):
        print(f"✗ {view['error']}")
        return 1
    for span in view["spans"]:
        print(f"  {span['start_line']:>5}-{span['end_line']:<5}  {span['symbol']}"
              f"  [{span['label']} ({span['feature_id']})]")
    return 0


def _status(repo: str, as_json: bool = False) -> int:
    """`sgt status` (plan U13): file/symbol/feature counts, coverage, oracle status, drift."""
    from sgt.api import status_view
    from sgt.core.lens import get

    get(repo)
    view = status_view(repo)
    if as_json:
        return _emit_json(view)
    print(f"{view['files']} file(s), {view['symbols']} symbol(s), {view['features']} feature(s), "
          f"{view['coverage_fraction'] * 100:.0f}% entity coverage")
    print(f"  oracle: {view['oracle']['status']}")
    if view["drift"]["any"]:
        print(f"  ⚠ drift: {', '.join(view['drift']['paths'])}")
    else:
        print("  ✓ in sync")
    return 0


def _feature_merge(repo: str, survivor_id: str, absorbed_id: str, as_json: bool = False) -> int:
    """`sgt merge <survivor> <absorbed>` (plan U13, R16): union two features' op-sets under the
    survivor id -- a metadata-only tree.json patch + pin, content-untouched."""
    from sgt.lens import verbs as lens_verbs

    preview = lens_verbs.plan_merge(repo, survivor_id, absorbed_id)
    if not preview.ok:
        return _emit_json({"ok": False, "message": preview.message}) if as_json else _fail(preview.message)
    lens_verbs.apply_merge(repo, preview)
    if as_json:
        return _emit_json({
            "ok": True, "survivor": preview.survivor_id, "absorbed": preview.absorbed_id,
            "op_count": preview.op_count, "member_count": preview.member_count,
        })
    print(f"✓ merged {preview.absorbed_id} into {preview.survivor_id} "
          f"({preview.op_count} op(s), {preview.member_count} member(s))")
    return 0


def _feature_rename(repo: str, feature_id: str, new_label: str, as_json: bool = False) -> int:
    """`sgt rename <feature> "<label>"` (plan U13, R16): overrides the feature's label, durably
    (`.sgt/pins/pins.json`'s `labels`), so it survives the next `sgt map` re-cluster."""
    from sgt.lens import verbs as lens_verbs

    preview = lens_verbs.plan_rename(repo, feature_id, new_label)
    if not preview.ok:
        return _emit_json({"ok": False, "message": preview.message}) if as_json else _fail(preview.message)
    lens_verbs.apply_rename(repo, preview)
    if as_json:
        return _emit_json({
            "ok": True, "feature": preview.feature_id,
            "old_label": preview.old_label, "new_label": preview.new_label,
        })
    print(f"✓ renamed {preview.feature_id}: {preview.old_label!r} → {preview.new_label!r}")
    return 0


def _feature_move(repo: str, rest: list[str], as_json: bool = False) -> int:
    """`sgt move <op>... --to <feature>` (plan U13, R16): retags ops (and their footprint
    symbols) onto another feature."""
    from sgt.lens import verbs as lens_verbs

    target, rest = _strip_opt(rest, "--to")
    if not rest or not target:
        print("usage: sgt move [--json] <op>... --to <feature>")
        return 2
    preview = lens_verbs.plan_move(repo, rest, target)
    if not preview.ok:
        return _emit_json({"ok": False, "message": preview.message}) if as_json else _fail(preview.message)
    lens_verbs.apply_move(repo, preview)
    if as_json:
        return _emit_json({"ok": True, "op_ids": list(preview.op_ids), "target": preview.target_id})
    print(f"✓ moved {len(preview.op_ids)} op(s) to {preview.target_id}")
    return 0


def _feature_split(repo: str, rest: list[str], as_json: bool = False) -> int:
    """`sgt split <feature> [--apply]` (plan U13, R16): proposes a clusterer cut of the feature's
    members into two; mutates nothing until `--apply` confirms it."""
    from sgt.lens import verbs as lens_verbs

    do_apply = "--apply" in rest
    rest = [a for a in rest if a != "--apply"]
    if not rest:
        print("usage: sgt split [--json] <feature> [--apply]")
        return 2
    preview = lens_verbs.plan_split(repo, rest[0])
    if not preview.ok:
        return _emit_json({"ok": False, "message": preview.message}) if as_json else _fail(preview.message)

    if not do_apply:
        if as_json:
            return _emit_json({
                "ok": True, "feature": preview.feature_id, "applied": False,
                "groups": [list(g) for g in preview.groups],
            })
        for i, group in enumerate(preview.groups):
            print(f"  group {i}: {', '.join(group)}")
        print("  (preview only — pass --apply to split)")
        return 0

    result = lens_verbs.apply_split(repo, preview, confirm=True)
    new_id = next(
        nid for nid, nd in result["nodes"].items()
        if not nd["children"] and tuple(sorted(nd["members"])) == preview.groups[1]
    )
    if as_json:
        return _emit_json({"ok": True, "feature": preview.feature_id, "new_feature": new_id, "applied": True})
    print(f"✓ split {preview.feature_id} → {preview.feature_id} + {new_id}")
    return 0


def _fail(message: str) -> int:
    print(f"✗ {message}")
    return 1


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
    `I ∪ ↓X`) with `--emit` previews and chain-fork surfacing (AE2). `revert`'s target
    additionally accepts a feature id/label (plan U13): when it doesn't resolve as an op-id or
    symbol, `sgt.lens.verbs.resolve_feature` is tried next, routing to the feature-grouped
    `plan_revert_feature` preview -- applied through the exact same `sgt.core.verbs.apply` path
    as a single-op revert, since both produce the same `VerbPreview` shape."""
    from sgt.core import verbs
    from sgt.core.lens import get

    emit = "--emit" in rest
    rest = [a for a in rest if a != "--emit"]
    if not rest:
        print(f"usage: sgt {cmd} [--emit] [--json] <ref>")
        return 2
    target = " ".join(rest)
    get(repo)  # mine-on-contact before planning/applying the edit (R9)

    if cmd == "revert":
        preview = verbs.plan_revert(repo, target)
        if not preview.ok:
            from sgt.lens import verbs as lens_verbs

            if lens_verbs.resolve_feature(repo, target) is not None:
                preview = lens_verbs.plan_revert_feature(repo, target)
    else:
        preview = verbs.plan_restore(repo, target)

    if emit:
        from sgt.api import _project_verb_preview

        view = _project_verb_preview(repo, preview)
        return _emit_json(view) if as_json else _print_verb_view(view)

    if preview.ok:
        verbs.apply(repo, preview)
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
        "  sgt map [--json]            (re)build + print the hierarchical feature tree\n"
        "  sgt blame [--json] <file>   per-symbol feature attribution for a file's live entities\n"
        "  sgt status [--json]        files/symbols/features, coverage, oracle status, drift\n"
        "  sgt merge <survivor> <absorbed>        union two features under the survivor id\n"
        "  sgt split <feature> [--apply]          preview (then confirm) a two-way feature split\n"
        '  sgt rename <feature> "<label>"         override a feature\'s label, durably\n'
        "  sgt move <op>... --to <feature>        retag ops (+ their symbols) onto another feature\n"
        "  sgt revert <feature>        revert an entire feature's op-set (grouped ∪ upset X)\n"
        "  sgt mcp [path]              run the MCP stdio server for coding-agent clients\n"
        "  <ref> is an op-id, an op-id prefix, a `file::name` symbol (its frontier tip), or a\n"
        "  feature id/label (`revert` only)\n"
        "  (read verbs take --json for the machine-readable sgt.api projection)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
