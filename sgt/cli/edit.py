"""`sgt edit <selection>` (plan U4, R5/KTD5): change a feature/symbol in place.

The selection is resolved through the one universal resolver (U1, `sgt.lens.select.resolve`);
`edit` then chain-extends the resolved target with a single hollow and mechanically re-points its
dependents -- the LLM is invoked *only* when a dependent genuinely breaks the oracle (`--repair`).

Flow (reusing the existing `fulfill`/`commit` rewrite spine): `sgt edit <sel>` drafts the edit
hollow; the user edits the file and runs `sgt fulfill <draft-id> --from-tree` then `sgt commit`
(the mechanical repoints happen inside `fulfill`, no model call). If `commit`'s oracle goes red,
`sgt edit <sel> --repair` drafts continuation hollows for *all* blast dependents (KTD5's bounded-
safety caveat -- a whole-suite verdict can't be pinned to one) and fills them through the LLM
repair loop behind the same oracle gate.
"""

from __future__ import annotations

from ._common import _fail_json
from .rewrite import _print_draft, _print_repair_result


def register(subs, parent) -> None:
    e = subs.add_parser("edit", parents=[parent])
    e.add_argument("--repair", action="store_true")
    e.add_argument("--intent")
    e.add_argument("selection", nargs="*")
    e.set_defaults(func=_cmd_edit)


def _cmd_edit(args) -> int:
    return _edit(".", args.selection, args.intent, args.repair, args.as_json)


def _edit(repo: str, selection_tokens: list[str], intent: str | None, do_repair: bool, as_json: bool) -> int:
    from sgt.core import rewrite
    from sgt.core.lens import get
    from sgt.lens import select

    if not selection_tokens:
        print("usage: sgt advanced edit <selection> [--repair] [--intent \"...\"]")
        return 2
    get(repo)  # mine-on-contact before resolving/planning the edit (R9)

    spec = " ".join(selection_tokens)
    resolved = select.resolve(repo, spec)
    if not resolved.ok:
        return _fail_json(resolved.message, as_json)
    if len(resolved.direct_ops) != 1:
        return _fail_json(
            f"edit targets a single symbol; {spec!r} resolved to {len(resolved.direct_ops)} ops "
            "-- narrow the selection",
            as_json,
        )
    target = next(iter(resolved.direct_ops))

    if do_repair:
        draft = rewrite.edit_repair_op(repo, target, intent=intent)
        if not draft.ok:
            return _print_draft(draft, as_json)
        # Abandon the red happy-path stage (the edit op stays in the store, carried as a required_id)
        # so the repair loop stages a clean edit-plus-reworked-dependents candidate.
        if rewrite.staged_candidate(repo) is not None:
            rewrite.unstage(repo)
        from sgt.repair.api_backend import ApiBackend
        from sgt.repair.loop import repair

        result = repair(repo, draft, ApiBackend(repo), max_oracle_rounds=1)
        return _print_repair_result(result, as_json)

    draft = rewrite.edit_op(repo, target, intent=intent)
    return _print_draft(draft, as_json)
