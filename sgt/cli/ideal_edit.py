"""Exact ideal-edit verbs (plan U8, flipped onto the kernel in U10): `revert` (`I \\ ↑X`) and
`restore` (`I ∪ ↓X`), with `--emit` previews and chain-fork surfacing (AE2). `revert
--keep-dependents` (plan U11) instead drafts a continuation hollow per dependent. `after`
(U21) declares/retracts a declared order edge (`a <= b`) over the OR-Set."""

from __future__ import annotations

from ._common import _emit_json, _fail
from .rewrite import _print_draft, _print_repair_result


def register(subs, parent) -> None:
    r = subs.add_parser("revert", parents=[parent])
    r.add_argument("--emit", action="store_true")
    r.add_argument("--keep-dependents", action="store_true", dest="keep_dependents")
    r.add_argument("--repair", action="store_true")
    r.add_argument("--backend", default="api", choices=["api"])
    r.add_argument("--intent")
    r.add_argument("--session")
    r.add_argument("ref", nargs="*")
    r.set_defaults(func=_cmd_revert)

    s = subs.add_parser("restore", parents=[parent])
    s.add_argument("--emit", action="store_true")
    s.add_argument("ref", nargs="*")
    s.set_defaults(func=_cmd_restore)

    af = subs.add_parser("after", parents=[parent])
    af.add_argument("--retract", action="store_true")
    af.add_argument("a")
    af.add_argument("b")
    af.set_defaults(func=_cmd_after)


def _cmd_after(args) -> int:
    return _after(".", args.a, args.b, args.retract, args.as_json)


def _after(repo: str, a: str, b: str, retract: bool, as_json: bool) -> int:
    """`sgt after <a> <b>` declares the order edge `a <= b` (OR-Set add with a fresh tag);
    `sgt after --retract <a> <b>` tombstones every locally-observed tag for that edge (a concurrent
    add elsewhere survives). Both resolve `a`/`b` through the ideal the same way the other edit
    verbs resolve a target (op-id, prefix, or `file::name` frontier tip)."""
    from sgt.core import verbs
    from sgt.core.lens import get, retract_after

    get(repo)  # mine-on-contact before resolving targets (R9)
    preview = verbs.plan_after(repo, a, b)
    if not preview.ok:
        view = {"ok": False, "verb": "after", "message": preview.message}
        return _emit_json(view) if as_json else _fail(preview.message)
    assert preview.declared_edge is not None
    a_id, b_id = preview.declared_edge
    if retract:
        tags = retract_after(repo, a_id, b_id)
        view = {"ok": True, "verb": "after", "retracted": True,
                "edge": [a_id, b_id], "tombstoned_tags": sorted(tags)}
        msg = f"retract {a_id[:8]} ≤ {b_id[:8]} ({len(tags)} tag(s) tombstoned)"
    else:
        verbs.apply(repo, preview)
        view = {"ok": True, "verb": "after", "retracted": False, "edge": [a_id, b_id]}
        msg = f"declare {a_id[:8]} ≤ {b_id[:8]}"
    if as_json:
        return _emit_json(view)
    print(f"✓ {msg}")
    return 0


def _cmd_revert(args) -> int:
    if args.session:
        return _revert_session(".", args.session, args.emit, args.as_json)
    if args.keep_dependents:
        return _revert_keep_dependents(".", args.ref, args.intent, args.repair, args.as_json)
    return _kernel_edit_verb(".", "revert", args.ref, args.emit, args.as_json)


def _cmd_restore(args) -> int:
    return _kernel_edit_verb(".", "restore", args.ref, args.emit, args.as_json)


def _emit_verb_result(repo: str, preview, emit: bool, as_json: bool) -> int:
    """Shared tail for the ideal-edit verbs: `--emit` renders the preview projection; otherwise
    apply the edit (when the preview is ok) and render the plain result view. Identical on both
    the revert/restore and the `--session` paths."""
    from sgt.core import verbs

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

    return _emit_verb_result(repo, preview, emit, as_json)


def _revert_session(repo: str, name: str, emit: bool, as_json: bool) -> int:
    """`sgt revert --session <name>` (plan U31, S7): addressing by provenance -- resolves a
    session name to the op-set it landed (`sgt.core.session.ops_by_session`, reading structured
    attribution rather than the session record, so it still works long after the session itself is
    gone) and previews/applies the exact same grouped `I \\ (∪ upset_in(x))` edit `revert <feature>`
    already runs, through the identical `verbs.apply` path."""
    from sgt.core import verbs
    from sgt.core.lens import get

    get(repo)  # mine-on-contact before resolving the session's ops (R9)
    preview = verbs.plan_revert_session(repo, name)

    return _emit_verb_result(repo, preview, emit, as_json)


def _revert_keep_dependents(
    repo: str, ref_tokens: list[str], intent: str | None, do_repair: bool, as_json: bool,
) -> int:
    """`revert <ref> --keep-dependents` (plan U11, R14): removes the target's up-set but drafts
    a continuation hollow per direct reference-dependent, so its symbol stays live. `--repair`
    (plan U6) hands the draft straight to the LLM-backed repair loop instead of printing it -- the
    one-command happy path, symmetric with how `--keep-dependents` already routes to `rewrite`."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    if not ref_tokens:
        print("usage: sgt revert <ref> --keep-dependents [--repair]")
        return 2
    get(repo)
    draft = rewrite.revert_keep_dependents(repo, " ".join(ref_tokens), intent=intent)
    if not do_repair or not draft.ok:
        return _print_draft(draft, as_json)

    from sgt.repair.api_backend import ApiBackend
    from sgt.repair.loop import repair

    result = repair(repo, draft, ApiBackend(repo))
    return _print_repair_result(result, as_json)


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
