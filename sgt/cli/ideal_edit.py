"""Exact ideal-edit verbs (plan U8, flipped onto the kernel in U10): `revert` (`I \\ ↑X`) and
`restore` (`I ∪ ↓X`), with `--emit` previews and chain-fork surfacing (AE2). `revert
--keep-dependents` (plan U11) instead drafts a continuation hollow per dependent."""

from __future__ import annotations

from ._common import _emit_json
from .rewrite import _print_draft


def register(subs, parent) -> None:
    r = subs.add_parser("revert", parents=[parent])
    r.add_argument("--emit", action="store_true")
    r.add_argument("--keep-dependents", action="store_true", dest="keep_dependents")
    r.add_argument("--intent")
    r.add_argument("ref", nargs="*")
    r.set_defaults(func=_cmd_revert)

    s = subs.add_parser("restore", parents=[parent])
    s.add_argument("--emit", action="store_true")
    s.add_argument("ref", nargs="*")
    s.set_defaults(func=_cmd_restore)


def _cmd_revert(args) -> int:
    if args.keep_dependents:
        return _revert_keep_dependents(".", args.ref, args.intent, args.as_json)
    return _kernel_edit_verb(".", "revert", args.ref, args.emit, args.as_json)


def _cmd_restore(args) -> int:
    return _kernel_edit_verb(".", "restore", args.ref, args.emit, args.as_json)


def _kernel_edit_verb(repo: str, cmd: str, ref_tokens: list[str], emit: bool, as_json: bool) -> int:
    """revert/restore (plan U8, flipped onto the kernel in U10): exact ideal edits (`I \\ ↑X` /
    `I ∪ ↓X`) with `--emit` previews and chain-fork surfacing (AE2). `revert`'s target
    additionally accepts a feature id/label (plan U13): when it doesn't resolve as an op-id or
    symbol, `sgt.lens.verbs.resolve_feature` is tried next, routing to the feature-grouped
    `plan_revert_feature` preview -- applied through the exact same `sgt.core.verbs.apply` path
    as a single-op revert, since both produce the same `VerbPreview` shape."""
    from sgt.core import verbs
    from sgt.core.lens import get

    if not ref_tokens:
        print(f"usage: sgt {cmd} [--emit] [--json] <ref>")
        return 2
    target = " ".join(ref_tokens)
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


def _revert_keep_dependents(repo: str, ref_tokens: list[str], intent: str | None, as_json: bool) -> int:
    """`revert <ref> --keep-dependents` (plan U11, R14): removes the target's up-set but drafts
    a continuation hollow per direct reference-dependent, so its symbol stays live."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    if not ref_tokens:
        print("usage: sgt revert <ref> --keep-dependents")
        return 2
    get(repo)
    draft = rewrite.revert_keep_dependents(repo, " ".join(ref_tokens), intent=intent)
    return _print_draft(draft, as_json)


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
