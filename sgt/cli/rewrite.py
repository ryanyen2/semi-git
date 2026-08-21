"""Rewrite verbs (plan U11, R14): where the ideal algebra can't express an edit exactly, these
draft hollow ops for an agent/human to fulfill. `merge-op`/`split-op`/`transplant` draft hollows;
`identity split`/`join` correct the matcher itself; `fulfill` supplies a drafted hollow's image
from the working tree (stages, no commit); `commit` commits the staged candidate, gated on a
passing oracle verdict. (`sgt land <branch>` is a separate verb -- U23's shared-branch CAS
advance -- kept apart from `commit` precisely so "land" only ever means one thing.)"""

from __future__ import annotations

from ._common import _emit_json, _fail_json


def register(subs, parent) -> None:
    mo = subs.add_parser("merge-op", parents=[parent])
    mo.add_argument("--intent")
    mo.add_argument("tips", nargs="*")
    mo.set_defaults(func=_cmd_merge_op)

    so = subs.add_parser("split-op", parents=[parent])
    so.add_argument("--intent")
    so.add_argument("op", nargs="?")
    so.set_defaults(func=_cmd_split_op)

    tp = subs.add_parser("transplant", parents=[parent])
    tp.add_argument("--onto")
    tp.add_argument("--intent")
    tp.add_argument("ops", nargs="*")
    tp.set_defaults(func=_cmd_transplant)

    idp = subs.add_parser("identity", parents=[parent])
    idp.add_argument("rest", nargs="*")
    idp.set_defaults(func=_cmd_identity)

    fp = subs.add_parser("fulfill", parents=[parent])
    fp.add_argument("--from-tree", action="store_true", dest="from_tree")
    fp.add_argument("draft", nargs="?")
    fp.set_defaults(func=_cmd_fulfill)

    cp = subs.add_parser("commit", parents=[parent])
    cp.add_argument("--message")
    cp.add_argument("--override")
    cp.add_argument("--reason")
    cp.add_argument("--by")
    cp.set_defaults(func=_cmd_commit)

    lp = subs.add_parser("land", parents=[parent])
    lp.add_argument("branch")  # the U23 SYNC-2 shared-branch CAS advance -- always required
    lp.set_defaults(func=_cmd_land)

    up = subs.add_parser("unstage", parents=[parent])
    up.set_defaults(func=_cmd_unstage)

    rp = subs.add_parser("repair", parents=[parent])
    rp.add_argument("draft", nargs="?")
    rp.set_defaults(func=_cmd_repair)


def _cmd_merge_op(args) -> int:
    return _merge_op(".", args.tips, args.intent, args.as_json)


def _cmd_split_op(args) -> int:
    return _split_op(".", args.op, args.intent, args.as_json)


def _cmd_transplant(args) -> int:
    return _transplant(".", args.ops, args.onto, args.intent, args.as_json)


def _cmd_identity(args) -> int:
    return _identity(".", args.rest, args.as_json)


def _cmd_fulfill(args) -> int:
    return _fulfill(".", args.draft, args.from_tree, args.as_json)


def _cmd_unstage(args) -> int:
    return _unstage(".", args.as_json)


def _cmd_repair(args) -> int:
    return _repair(".", args.draft, args.as_json)


def _cmd_commit(args) -> int:
    return _commit(".", args.message, args.override, args.reason, args.by, args.as_json)


def _cmd_land(args) -> int:
    from .sync import _land_branch

    return _land_branch(".", args.branch, args.as_json)


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
        print(f"    edit the working tree, then: sgt advanced fulfill {draft.draft_id} --from-tree")
    return 0


def _print_repair_result(result, as_json: bool) -> int:
    """Shared printer for the semantic repair loop (plan U5/U6): `revert --keep-dependents
    --repair` and `sgt repair <draft-id>` both return a `sgt.repair.loop.RepairResult`."""
    if as_json:
        return _emit_json({
            "ok": result.ok, "sha": result.sha, "attempts": result.attempts,
            "oracle_rounds": result.oracle_rounds, "message": result.message,
        })
    icon = "✓" if result.ok else "✗"
    print(f"{icon} repair" + (f" — {result.message}" if result.message else ""))
    if result.ok:
        print(f"    landed {result.sha[:12]} ({result.attempts} attempt(s), "
              f"{result.oracle_rounds} oracle round(s))")
    if result.cost_line:
        print(f"    {result.cost_line}")
    return 0 if result.ok else 1


def _repair(repo: str, draft_id: str | None, as_json: bool) -> int:
    """`repair <draft-id>` (plan U5/U6): fulfills every hollow in an already-drafted rewrite via
    the pluggable LLM backend, then lands it through the same oracle-gated path a human
    `fulfill`/`land` pair would use."""
    from sgt.core import rewrite
    from sgt.repair.api_backend import ApiBackend
    from sgt.repair.loop import repair

    if not draft_id:
        print("usage: sgt advanced repair <draft-id>")
        return 2
    draft = rewrite.resolve_draft(repo, draft_id)
    if draft is None:
        print(f"✗ no draft {draft_id!r} -- see merge-op/split-op/transplant/revert --keep-dependents")
        return 1
    result = repair(repo, draft, ApiBackend(repo))
    return _print_repair_result(result, as_json)


def _merge_op(repo: str, tips: list[str], intent: str | None, as_json: bool) -> int:
    """`merge-op <tip_a> <tip_b>` (plan U11, R14): drafts a hollow reconciling a chain fork --
    U8's cherry-pick refuses on exactly this shape (AE2)."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    if len(tips) < 2:
        print('usage: sgt advanced merge-op <tip_a> <tip_b> [--intent "..."]')
        return 2
    get(repo)
    draft = rewrite.merge_op(repo, tips[0], tips[1], intent=intent)
    return _print_draft(draft, as_json)


def _split_op(repo: str, op: str | None, intent: str | None, as_json: bool) -> int:
    """`split-op <op-id>` (plan U11, R14): drafts an intermediate cut of a two-concern op."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    if not op:
        print('usage: sgt advanced split-op <op-id> [--intent "..."]')
        return 2
    get(repo)
    draft = rewrite.split_op(repo, op, intent=intent)
    return _print_draft(draft, as_json)


def _transplant(repo: str, ops: list[str], onto: str | None, intent: str | None, as_json: bool) -> int:
    """`transplant <op-id>... --onto <ref>` (plan U11, R14, AE3): drafts hollows with the
    destination ref's own chain tip as ``before_version``."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    if not ops or not onto:
        print('usage: sgt advanced transplant <op-id>... --onto <ref> [--intent "..."]')
        return 2
    get(repo)
    draft = rewrite.transplant(repo, ops, onto, intent=intent)
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


def _fulfill(repo: str, draft_id: str | None, from_tree: bool, as_json: bool) -> int:
    """`fulfill <draft-id> --from-tree` (plan U11): supplies a drafted hollow's image from the
    working tree, validates + folds + writes the candidate — no commit; run `sgt commit` next."""
    from sgt.core import rewrite

    if not draft_id:
        print("usage: sgt advanced fulfill <draft-id> --from-tree")
        return 2
    from sgt.core.lens import DirtyWorkingTreeError

    try:
        candidate = rewrite.fulfill(repo, draft_id, from_tree=from_tree)
    except rewrite.RewriteError as e:
        print(f"✗ {e}")
        return 1
    except DirtyWorkingTreeError as e:
        # A refusal, not a crash: this verb writes the candidate over the working tree, so the
        # guard fires on exactly the case where someone has unsaved work. The message already
        # names the files and the remedy (`sgt/core/rewrite.py::stage`).
        return _fail_json(str(e), as_json)
    if as_json:
        return _emit_json({"ok": True, "op_ids": sorted(candidate.op_ids)})
    print(f"✓ staged {len(candidate.op_ids)} op(s) to the working tree (uncommitted) — "
          "run `sgt advanced oracle run` then `sgt advanced commit`")
    return 0


def _unstage(repo: str, as_json: bool) -> int:
    """`sgt advanced unstage` (plan U6): abandon the staged rewrite candidate — restore the committed ideal
    to the working tree and drop `staged.json`, so `switch`/`save`/other edits work again."""
    from sgt.core import rewrite

    try:
        restored = rewrite.unstage(repo)
    except rewrite.RewriteError as e:
        print(f"✗ {e}")
        return 1
    if as_json:
        return _emit_json({"ok": True, "op_ids": sorted(restored.op_ids)})
    print(f"✓ abandoned staged candidate; restored the committed ideal "
          f"({len(restored.op_ids)} op(s)) to the working tree")
    return 0


def _commit(repo: str, message: str | None, status: str | None,
            reason: str | None, by: str | None, as_json: bool) -> int:
    """`commit [--message "..."] [--override pass|fail --reason "..." [--by NAME]]` (plan U11,
    R14): commits the last-staged rewrite candidate, refusing unless its oracle verdict is
    "pass" (or the supplied override resolves to one)."""
    from sgt.core import rewrite

    override = (status, reason or "", by) if status else None
    try:
        sha = rewrite.land(repo, message=message, override=override)
    except rewrite.RewriteError as e:
        print(f"✗ {e}")
        return 1
    if as_json:
        return _emit_json({"ok": True, "sha": sha})
    print(f"✓ committed {sha[:12]}")
    return 0
