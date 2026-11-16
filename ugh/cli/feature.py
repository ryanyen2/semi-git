"""`ugh feature rename` (plan U13, R16): an instant, reversible, content-untouched relabel of a
feature in the tree and its pins."""

from __future__ import annotations

from ._common import _emit_json, _fail


def _fail_preview(preview, as_json: bool) -> int:
    """A failed feature-verb preview, rendered per `--json`. The `"message"` envelope is the
    feature family's own failure shape (distinct from `_common._fail_json`'s `"error"` key)."""
    return _emit_json({"ok": False, "message": preview.message}) if as_json else _fail(preview.message)


def _confirm(verb: str, preview) -> bool:
    """The `[y/N]` gate for `feature rename` on a tty. Off a tty (a script, CI, an editor shelling
    out) it returns True: there is nobody there to answer a prompt."""
    import sys

    from ._common import confirm

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return True
    return confirm([f"  {verb}: {preview.old_label!r} → {preview.new_label!r}",
                    "    (metadata only — no code changes, reversible)"], f"{verb}?")


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
    if not as_json and not _confirm("rename", preview):
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
