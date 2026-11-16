"""`ugh feature rename` (plan U13, R16): an instant, reversible, content-untouched relabel of a
feature in the tree and its pins."""

from __future__ import annotations

from ._common import _emit_json, _fail


def _fail_preview(preview, as_json: bool) -> int:
    """A failed feature-verb preview, rendered per `--json`. The `"message"` envelope is the
    feature family's own failure shape (distinct from `_common._fail_json`'s `"error"` key)."""
    return _emit_json({"ok": False, "message": preview.message}) if as_json else _fail(preview.message)


def _confirm(repo: str, verb: str, preview) -> bool:
    """The consequence gate for `feature rename`. Returns False only when the user explicitly
    declined.

    On an interactive tty this is the consequence pane, or -- when `textual` isn't installed -- the
    printed summary plus `[y/N]` (`confirm_summary`). Previously the no-`textual` case returned True
    and applied immediately with nothing shown and nothing asked, which made an *optional*
    dependency the difference between a previewed re-cut and a silent one.

    Off a tty (a script, CI, an editor shelling out) it still returns True immediately: that
    machine contract is deliberate and unchanged -- there is nobody there to answer a prompt."""
    import sys

    from ugh.api import _project_feature_preview

    from ._common import confirm_summary

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return True
    return confirm_summary(_project_feature_preview(repo, verb, preview), f"{verb}?")


def register(subs, parent) -> None:
    fp = subs.add_parser("feature", parents=[parent])
    fsubs = fp.add_subparsers(dest="feature_verb")
    fp.set_defaults(func=lambda args: fp.print_help() or 0)

    rn = fsubs.add_parser("rename", parents=[parent])
    rn.add_argument("feature")
    rn.add_argument("label", nargs="+")
    rn.set_defaults(func=_cmd_rename)


def _cmd_rename(args) -> int:
    return _feature_rename(".", args.feature, " ".join(args.label), args.as_json)


def _feature_rename(repo: str, feature_id: str, new_label: str, as_json: bool = False) -> int:
    """`ugh rename <feature> "<label>"` (plan U13, R16): overrides the feature's label, durably
    (`.sgt/pins/pins.json`'s `labels`), so it survives the next `ugh map` re-cluster."""
    from ugh.lens import verbs as lens_verbs

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
