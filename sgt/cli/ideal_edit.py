"""Exact ideal-edit verbs (plan U8, flipped onto the kernel in U10): `revert` (`I \\ ↑X`) and
`restore` (`I ∪ ↓X`), with `--emit` previews and chain-fork surfacing (AE2). `revert
--keep-dependents` (plan U11) instead drafts a continuation hollow per dependent. `after`
(U21) declares/retracts a declared order edge (`a <= b`) over the OR-Set."""

from __future__ import annotations

from ._common import _emit_json, _fail, _fail_json
from .rewrite import _print_draft, _print_repair_result


def register(subs, parent) -> None:
    r = subs.add_parser("revert", parents=[parent])
    r.add_argument("--emit", action="store_true")
    r.add_argument("--keep-dependents", action="store_true", dest="keep_dependents")
    r.add_argument("--repair", action="store_true")
    r.add_argument("--backend", default="api", choices=["api"])
    r.add_argument("--intent")
    r.add_argument("--session")
    r.add_argument("--yes", action="store_true")
    r.add_argument("ref", nargs="*")
    r.set_defaults(func=_cmd_revert)

    s = subs.add_parser("restore", parents=[parent])
    s.add_argument("--emit", action="store_true")
    s.add_argument("--yes", action="store_true")
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
    return _kernel_edit_verb(".", "revert", args.ref, args.emit, args.as_json, args.yes)


def _cmd_restore(args) -> int:
    return _kernel_edit_verb(".", "restore", args.ref, args.emit, args.as_json, args.yes)


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


def _kernel_edit_verb(
    repo: str, cmd: str, ref_tokens: list[str], emit: bool, as_json: bool, yes: bool = False,
) -> int:
    """revert/restore (plan U8, flipped onto the kernel in U10): exact ideal edits (`I \\ ↑X` /
    `I ∪ ↓X`) with `--emit` previews and chain-fork surfacing (AE2). `revert`'s target
    additionally accepts a feature id/label (plan U13): when it doesn't resolve as an op-id or
    symbol, `sgt.lens.verbs.resolve_feature` is tried next, routing to the feature-grouped
    `plan_revert_feature` preview -- applied through the exact same `sgt.core.verbs.apply` path
    as a single-op revert, since both produce the same `VerbPreview` shape.

    Once every deterministic rung above is exhausted (`revert`: `plan_revert` refused and
    `resolve_feature` found no feature either; `restore`: `plan_restore` refused -- it has no
    feature-label rung), the target falls to the NL rung (`_resolve_via_intent`, plan U8/U13's
    fallback ladder's last step)."""
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
                return _resolve_via_intent(repo, cmd, target, as_json, yes)
    else:
        preview = verbs.plan_restore(repo, target)
        if not preview.ok:
            return _resolve_via_intent(repo, cmd, target, as_json, yes)

    return _emit_verb_result(repo, preview, emit, as_json)


def _plan_for(verb: str, repo: str, ref: str, kind: str = ""):
    """The one piece of verb-specific glue `resolve_intent`'s candidates need: re-plan a
    candidate ref through the same pure `plan_*` the deterministic rungs already used, so its
    preview is truthful (and a hallucinated/no-longer-live ref reports `ok=False`).

    A `feature`-kind candidate is routed through `plan_revert_feature` -- the same feature-grouped
    plan the deterministic feature rung uses -- since the prompt invites feature ids and a plain
    single-op `plan_revert` can't resolve one (it would drop the very target the LLM found).
    Restore has no feature plan (mirroring `_kernel_edit_verb`'s ladder), so a feature candidate
    there falls through `plan_restore` and is dropped as unresolvable."""
    from sgt.core import verbs

    if verb == "revert":
        if kind == "feature":
            from sgt.lens import verbs as lens_verbs

            return lens_verbs.plan_revert_feature(repo, ref)
        return verbs.plan_revert(repo, ref)
    return verbs.plan_restore(repo, ref)


def _resolve_via_intent(repo: str, cmd: str, target: str, as_json: bool, yes: bool) -> int:
    """The NL rung (plan B2/B3): an LLM (`sgt.intent.resolve.resolve_intent`) proposes candidate
    refs for `target`; each is re-planned via `_plan_for` for a truthful preview, dropping any
    that isn't `ok`. Default UX is did-you-mean -- print the survivors and the exact re-invoke
    command, exit 2, apply nothing. `--yes` applies the top survivor directly. No key, no
    network, or zero surviving candidates all report a clear message and exit 1 -- never a
    crash, never a guess."""
    from sgt.intent.resolve import resolve_intent

    resolution = resolve_intent(repo, target, verb=cmd)
    if resolution is None:
        return _fail_json(
            f"could not resolve {target!r} to a ref; set OPENAI_API_KEY to enable "
            "natural-language targets",
            as_json,
        )

    if not resolution.candidates:
        return _fail_json(
            f"nothing in this codebase's tracked history plausibly matches {target!r}", as_json,
        )

    survivors = []
    seen_effects: set[tuple] = set()
    for cand in resolution.candidates:
        preview = _plan_for(cmd, repo, cand.ref, cand.kind)
        # Drop refs that don't re-plan, and refs whose edit is a no-op (e.g. a `restore` of an
        # already-live symbol, or a `revert` the LLM proposed for something not actually in the
        # ideal): a candidate the user can't tell apart from doing nothing isn't a real choice.
        if not preview.ok or not (preview.removed or preview.added):
            continue
        # Collapse candidates that re-plan to the *same* edit (e.g. an op-id and its `file::symbol`
        # both resolving to one op) -- the higher-ranked phrasing wins, so the user sees one entry
        # per distinct outcome rather than the same revert spelled several ways.
        effect = (frozenset(preview.removed), frozenset(preview.added))
        if effect in seen_effects:
            continue
        seen_effects.add(effect)
        survivors.append((cand, preview))
    if not survivors:
        return _fail_json(f"no live candidate for {target!r} survived re-planning", as_json)

    if yes:
        from sgt.core import verbs

        _, top_preview = survivors[0]
        verbs.apply(repo, top_preview)
        view = {
            "ok": True, "verb": cmd, "target": top_preview.target,
            "removed": sorted(top_preview.removed), "added": sorted(top_preview.added),
            "affected_symbols": list(top_preview.affected_symbols), "forked": top_preview.forked,
            "message": f"resolved {target!r} -> {top_preview.target!r}",
        }
        return _emit_json(view) if as_json else _print_verb_view(view)

    candidates_view = [
        {
            "ref": preview.target, "kind": cand.kind, "rationale": cand.rationale,
            "removed": len(preview.removed), "added": len(preview.added),
            "reinvoke": f"sgt {cmd} {preview.target}",
        }
        for cand, preview in survivors
    ]
    if as_json:
        import json

        print(json.dumps({"ok": False, "verb": cmd, "target": target, "candidates": candidates_view}, indent=2))
        return 2

    print(f"? [{cmd}] {target!r} did not resolve; did you mean:")
    for i, c in enumerate(candidates_view, 1):
        print(f"  {i}. {c['ref']} ({c['kind']}) — {c['rationale']}")
        print(f"     would remove {c['removed']} op(s), add {c['added']} op(s)")
        print(f"     re-invoke: {c['reinvoke']}")
    return 2


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
