"""Metadata-only feature verbs (plan U13, R16): `merge`/`split`/`rename`/`move` -- instant,
reversible, content-untouched patches to the feature tree and its pins."""

from __future__ import annotations

from ._common import _emit_json, _fail


def _fail_preview(preview, as_json: bool) -> int:
    """A failed feature-verb preview, rendered per `--json`. The `"message"` envelope is the
    feature family's own failure shape (distinct from `_common._fail_json`'s `"error"` key)."""
    return _emit_json({"ok": False, "message": preview.message}) if as_json else _fail(preview.message)


def _confirm(repo: str, verb: str, preview) -> bool:
    """The consequence gate for a metadata verb (merge/rename/move/split). Returns False only when
    the user explicitly declined.

    On an interactive tty this is the consequence pane, or -- when `textual` isn't installed -- the
    printed summary plus `[y/N]` (`confirm_summary`). Previously the no-`textual` case returned True
    and applied immediately with nothing shown and nothing asked, which made an *optional*
    dependency the difference between a previewed re-cut and a silent one.

    Off a tty (a script, CI, an editor shelling out) it still returns True immediately: that
    machine contract is deliberate and unchanged -- there is nobody there to answer a prompt."""
    import sys

    from sgt.api import _project_feature_preview

    from ._common import confirm_summary

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return True
    return confirm_summary(_project_feature_preview(repo, verb, preview), f"{verb}?")


def register(subs, parent) -> None:
    m = subs.add_parser("merge", parents=[parent])
    m.add_argument("survivor")
    m.add_argument("absorbed")
    m.set_defaults(func=_cmd_merge)

    sp = subs.add_parser("split", parents=[parent])
    sp.add_argument("--apply", action="store_true")
    sp.add_argument("feature", nargs="?")
    sp.set_defaults(func=_cmd_split)

    rn = subs.add_parser("rename", parents=[parent])
    rn.add_argument("feature")
    rn.add_argument("label", nargs="+")
    rn.set_defaults(func=_cmd_rename)

    mv = subs.add_parser("move", parents=[parent])
    mv.add_argument("--to")
    mv.add_argument("ops", nargs="*")
    mv.set_defaults(func=_cmd_move)


def _cmd_merge(args) -> int:
    return _feature_merge(".", args.survivor, args.absorbed, args.as_json)


def _cmd_split(args) -> int:
    return _feature_split(".", args.feature, args.apply, args.as_json)


def _cmd_rename(args) -> int:
    return _feature_rename(".", args.feature, " ".join(args.label), args.as_json)


def _cmd_move(args) -> int:
    return _feature_move(".", args.ops, args.to, args.as_json)


def _feature_merge(repo: str, survivor_id: str, absorbed_id: str, as_json: bool = False) -> int:
    """`sgt merge <survivor> <absorbed>` (plan U13, R16): union two features' op-sets under the
    survivor id -- a metadata-only tree.json patch + pin, content-untouched."""
    from sgt.lens import verbs as lens_verbs

    preview = lens_verbs.plan_merge(repo, survivor_id, absorbed_id)
    if not preview.ok:
        return _fail_preview(preview, as_json)
    if not as_json and not _confirm(repo, "merge", preview):
        print("  skipped — nothing changed.")
        return 1
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
        return _fail_preview(preview, as_json)
    if not as_json and not _confirm(repo, "rename", preview):
        print("  skipped — nothing changed.")
        return 1
    lens_verbs.apply_rename(repo, preview)
    if as_json:
        return _emit_json({
            "ok": True, "feature": preview.feature_id,
            "old_label": preview.old_label, "new_label": preview.new_label,
        })
    print(f"✓ renamed {preview.feature_id}: {preview.old_label!r} → {preview.new_label!r}")
    return 0


def _feature_move(repo: str, ops: list[str], target: str | None, as_json: bool = False) -> int:
    """`sgt move <op>... --to <feature>` (plan U13, R16): retags ops (and their footprint
    symbols) onto another feature."""
    from sgt.lens import verbs as lens_verbs

    if not ops or not target:
        print("usage: sgt feature regroup move [--json] <op>... --to <feature>")
        return 2
    preview = lens_verbs.plan_move(repo, ops, target)
    if not preview.ok:
        return _fail_preview(preview, as_json)
    if not as_json and not _confirm(repo, "move", preview):
        print("  skipped — nothing changed.")
        return 1
    lens_verbs.apply_move(repo, preview)
    if as_json:
        return _emit_json({"ok": True, "op_ids": list(preview.op_ids), "target": preview.target_id})
    print(f"✓ moved {len(preview.op_ids)} op(s) to {preview.target_id}")
    return 0


def _feature_split(repo: str, feature: str | None, do_apply: bool, as_json: bool = False) -> int:
    """`sgt split <feature> [--apply]` (plan U13, R16): proposes a clusterer cut of the feature's
    members into two; mutates nothing until `--apply` confirms it."""
    from sgt.lens import verbs as lens_verbs

    if not feature:
        print("usage: sgt feature regroup split [--json] <feature> [--apply]")
        return 2
    preview = lens_verbs.plan_split(repo, feature)
    if not preview.ok:
        return _fail_preview(preview, as_json)

    if not do_apply and not as_json:
        # On an interactive tty the split preview *is* the consequence pane -- confirming it splits.
        # A `None` decision (non-tty / no textual) falls through to the printed preview-only path.
        from sgt.api import _project_feature_preview

        from ._common import maybe_confirm

        decision = maybe_confirm(_project_feature_preview(repo, "split", preview))
        if decision is not None:
            if not decision.apply:
                print("  skipped — nothing changed.")
                return 1
            do_apply = True

    if not do_apply:
        if as_json:
            # `new_id`/`moving_op_ids` are additive: the id the split will mint and the recorded
            # ops that follow the new group, so a graph surface can draw the cut at chunk grain
            # (which cars leave, and into which lane) instead of just naming symbols.
            return _emit_json({
                "ok": True, "feature": preview.feature_id, "applied": False,
                "groups": [list(g) for g in preview.groups],
                "new_id": preview.new_id,
                "moving_op_ids": list(preview.moving_op_ids),
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
