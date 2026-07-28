"""Exact ideal-edit verbs (plan U8, flipped onto the kernel in U10): `revert` (`I \\ ↑X`) and
`restore` (`I ∪ ↓X`), with `--emit` previews and chain-fork surfacing (AE2). `revert
--keep-dependents` (plan U11) instead drafts a continuation hollow per dependent. `after`
(U21) declares/retracts a declared order edge (`a <= b`) over the OR-Set."""

from __future__ import annotations

import argparse

from ._common import _emit_json, _fail, _fail_json
from .rewrite import _print_draft, _print_repair_result


def register(subs, parent) -> None:
    r = subs.add_parser("revert", parents=[parent])
    # `--emit` (machine dry-run: VS Code/MCP) and `--yes` (non-tty apply) stay functional but hidden
    # -- on a tty the consequence pane is the default confirm step, so a human never types them.
    r.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--keep-dependents", action="store_true", dest="keep_dependents")
    r.add_argument("--keep", help="comma-separated dependent op-ids to keep (from the --emit "
                                  "frontier); implies --keep-dependents. Empty = keep none. "
                                  "With --to, names other LANES to preserve instead.")
    r.add_argument("--to", type=int, metavar="COMMIT", dest="to",
                   help="scrub <lane> back to its state as of commit <COMMIT> (a grid column "
                        "index from `sgt log`); drops that lane's ops after it and their up-set.")
    r.add_argument("--repair", action="store_true")
    r.add_argument("--intent")
    r.add_argument("--session")
    r.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("ref", nargs="*")
    r.set_defaults(func=_cmd_revert)

    s = subs.add_parser("restore", parents=[parent])
    s.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
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
        return _revert_session(".", args.session, args.emit, args.as_json, args.yes)
    if args.to is not None:
        # `sgt revert <lane> --to <commit>` (plan U11): the timeline-scrub edit. In this mode
        # `--keep a,b` reinterprets its tokens as other LANE refs to preserve (not op-ids), since
        # truncation removes a lane's post-<commit> up-set rather than toggling named dependents.
        keep = tuple(tok for tok in (t.strip() for t in (args.keep or "").split(",")) if tok)
        return _revert_lane_to_commit(".", args.ref, args.to, keep, args.emit, args.as_json, args.yes)
    if args.keep_dependents or args.keep is not None:
        # `--keep a,b` (from the --emit frontier) keeps exactly those toggleable dependents; a bare
        # `--keep-dependents` keeps them all (keep=None); `--keep ""` keeps none (a plain revert).
        keep = None if args.keep is None else frozenset(
            tok for tok in (t.strip() for t in args.keep.split(",")) if tok
        )
        return _revert_keep_dependents(".", args.ref, args.intent, args.repair, args.as_json, keep=keep)
    return _kernel_edit_verb(".", "revert", args.ref, args.emit, args.as_json, args.yes)


def _cmd_restore(args) -> int:
    return _kernel_edit_verb(".", "restore", args.ref, args.emit, args.as_json, args.yes)


def _emit_verb_result(repo: str, preview, emit: bool, as_json: bool, extra: dict | None = None,
                      *, yes: bool = False, focus_fid: str | None = None) -> int:
    """Shared tail for the ideal-edit verbs: `--emit` renders the preview projection; otherwise
    apply the edit (when the preview is ok) and render the plain result view. Identical on both
    the revert/restore and the `--session` paths. `extra` (e.g. the intent overlay's `tier`) is
    merged into `view` unconditionally in both branches, before either the JSON or plain-text
    emission call -- so it's visible on both output paths, not JSON only.

    Feedforward (KTD): a plain-text (non-`--json`) apply first *draws* where the edit lands -- the
    target feature's checkpoints with the removed/restored slice marked, plus the other features
    the dependency blast hits (`render_verb_preview_lines`) -- then gates on `[y/N]`. `--yes` skips
    the prompt; a non-tty stdin without `--yes` prints the preview and refuses to apply (exit 2),
    mirroring the did-you-mean guard. `--json` is unchanged (applies immediately -- the machine
    contract VS Code/TUI depend on)."""
    from sgt.core import verbs

    if emit:
        from sgt.api import _project_verb_preview

        view = _project_verb_preview(repo, preview)
        if extra:
            view = {**view, **extra}
        return _emit_json(view) if as_json else _print_verb_view(view)

    # Plain-text apply: the confirm step. On an interactive tty this *is* the consequence focus
    # pane (a zoomed region of `sgt log`: what breaks, adjust with space, apply with enter); with
    # `--yes`, a non-tty, or textual absent it degrades to the feedforward-graph + `[y/N]` prompt.
    if preview.ok and not as_json:
        import sys

        from sgt.api import _project_verb_preview, grid_view, map_view, segments_view

        from ._common import maybe_confirm

        pview = _project_verb_preview(repo, preview)
        mv, gv, sv = map_view(repo), grid_view(repo), segments_view(repo)

        if not yes:
            decision = maybe_confirm(pview, mv, gv, sv, focus_fid=focus_fid)
            if decision is not None:  # interactive tty + textual: the pane is the confirm step
                if not decision.apply:
                    print("  skipped — nothing changed.")
                    return 1
                return _apply_decision(repo, preview, decision.kept, as_json)

        # Degrade path (--yes, non-tty, or no textual): draw the feedforward, gate on [y/N].
        from sgt.tui.graph import render_verb_preview_lines

        color = sys.stdout.isatty()
        for line in render_verb_preview_lines(mv, gv, sv, pview, focus_fid=focus_fid, color=color):
            print(line)
        if not yes:
            if not sys.stdin.isatty():
                print("\n  not applied (no tty). re-run with --yes to apply, or --emit for a no-op preview.")
                return 2
            try:
                reply = input(f"\napply this {preview.verb}? [y/N] ").strip().lower()
            except EOFError:
                reply = ""
            if reply not in ("y", "yes"):
                print("  skipped — nothing changed.")
                return 1
        verbs.apply(repo, preview)
        print(f"  ✓ {preview.verb} applied — {len(preview.removed)} op(s) removed, "
              f"{len(preview.added)} added.")
        return 0

    if preview.ok:
        verbs.apply(repo, preview)
    view = {
        "ok": preview.ok, "verb": preview.verb, "target": preview.target,
        "removed": sorted(preview.removed), "added": sorted(preview.added),
        "affected_symbols": list(preview.affected_symbols), "forked": preview.forked,
        "message": preview.message,
    }
    if extra:
        view = {**view, **extra}
    return _emit_json(view) if as_json else _print_verb_view(view)


def _apply_decision(repo: str, preview, kept: frozenset[str], as_json: bool) -> int:
    """Apply a consequence-pane Decision. A kept-set on a `revert` is exactly the existing
    `--keep <ids>` continuation-hollow path -- each kept dependent stays live as a draft instead
    of cascading out with the up-set; everything else is the plain exact edit through
    `verbs.apply`. Empty kept-set = a bare apply (identical to the old `[y/N]` yes path)."""
    from sgt.core import verbs

    if kept and preview.verb == "revert":
        return _revert_keep_dependents(repo, [preview.target], None, False, as_json, keep=kept)
    verbs.apply(repo, preview)
    print(f"  ✓ {preview.verb} applied — {len(preview.removed)} op(s) removed, "
          f"{len(preview.added)} added.")
    return 0


def _revert_lane_to_commit(
    repo: str, ref_tokens: list[str], commit_index: int, keep: tuple[str, ...],
    emit: bool, as_json: bool, yes: bool = False,
) -> int:
    """`sgt revert <lane> --to <commit>` (plan U11): resolve <lane> to a feature, drop exactly its
    ops after commit <commit> (plus their up-set), and preserve any lane named in `--keep`. Routed
    through the same `_emit_verb_result` tail as every other ideal edit, so the `--emit` preview
    carries the U4 coupling rows and apply goes through `sgt.core.verbs.apply`."""
    from sgt.core.lens import get
    from sgt.lens import verbs as lens_verbs

    if not ref_tokens:
        print("usage: sgt revert <lane> --to <commit> [--keep <lane>,...] [--json]")
        return 2
    get(repo)  # mine-on-contact before planning the edit (R9)
    preview = lens_verbs.plan_revert_lane_to_commit(repo, " ".join(ref_tokens), commit_index, keep=keep)
    return _emit_verb_result(repo, preview, emit, as_json, yes=yes)


def _kernel_edit_verb(
    repo: str, cmd: str, ref_tokens: list[str], emit: bool, as_json: bool, yes: bool = False,
) -> int:
    """revert/restore (plan U8, flipped onto the kernel in U10): exact ideal edits (`I \\ ↑X` /
    `I ∪ ↓X`) with `--emit` previews and chain-fork surfacing (AE2). Both verbs' targets
    additionally accept a feature id/label (plan U13): when it doesn't resolve as an op-id or
    symbol, `sgt.lens.verbs.resolve_feature` is tried next, routing to the feature-grouped
    `plan_revert_feature`/`plan_restore_feature` preview -- applied through the exact same
    `sgt.core.verbs.apply` path as a single-op revert/restore, since both produce the same
    `VerbPreview` shape.

    Once every deterministic rung above is exhausted (single-op plan refused and `resolve_feature`
    found no feature either), the target falls to the NL rung (`_resolve_via_intent`, plan
    U8/U13's fallback ladder's last step)."""
    from sgt.core import verbs
    from sgt.core.lens import get

    if not ref_tokens:
        print(f"usage: sgt {cmd} [--json] <ref>")
        return 2
    target = " ".join(ref_tokens)
    get(repo)  # mine-on-contact before planning/applying the edit (R9)

    # A `<feature>@<n>` or `<feature>:<slug>` checkpoint (the intent-segment rewind unit): resolve
    # it to its deterministic op-set and run the exact same op-set revert `sgt intent revert` uses
    # (KTD6). Tried first for `revert` because `@`/`:` name a checkpoint unambiguously; a non-match
    # returns None and falls through. restore has no op-set counterpart, so it never enters here.
    if cmd == "revert" and ("@" in target or ":" in target):
        from sgt.intent.segment import resolve_checkpoint

        resolved = resolve_checkpoint(repo, target)
        if resolved is not None:
            op_ids, label = resolved
            preview = verbs.plan_revert_op_set(repo, target, op_ids)
            # The feedforward focus is the feature the checkpoint's ops belong to (all ops in one
            # segment share a feature); pick any target op and read its leaf feature.
            from sgt.lens.tree import load as load_tree

            op_leaf = (load_tree(repo) or {}).get("op_leaf", {})
            focus_fid = next((op_leaf[o] for o in op_ids if o in op_leaf), None)
            return _emit_verb_result(repo, preview, emit, as_json, extra={"checkpoint": label},
                                     yes=yes, focus_fid=focus_fid)

    import re

    from sgt.lens import verbs as lens_verbs

    plan_single = verbs.plan_revert if cmd == "revert" else verbs.plan_restore
    plan_feature = lens_verbs.plan_revert_feature if cmd == "revert" else lens_verbs.plan_restore_feature
    # A bare-hex / `f-` handle (the copy token the graph prints) *is* a founding op id, so `plan_single`
    # would target that one op. But the handle names the whole feature -- resolve it as a feature first,
    # the feature scope winning over the op it shadows. Symbols (`a.py::foo`) and `@n`/`:slug` never
    # match this shape (handled earlier / carry `::@:`), so single-op-by-symbol is unchanged.
    handle_shaped = re.fullmatch(r"(f-)?[0-9a-f]{3,}", target) is not None
    focus_fid = None
    if handle_shaped:
        resolved_feature = lens_verbs.resolve_feature(repo, target)
        if resolved_feature is not None:
            focus_fid = resolved_feature[1]  # (op_set, feature_id, label): feature scope wins over the op it shadows
            preview = plan_feature(repo, target)
        else:
            # No feature claims this hex handle -- but a *full* op id (the copy token `log --ops`
            # prints) is all-hex too, so it lands here while genuinely naming an op. Try the single-op
            # plan before giving up. Only a hex string that is *neither* feature nor op is a
            # typo'd/stale handle; answer that deterministically (no NL rung a bare hex never deserves).
            preview = plan_single(repo, target)
            if not preview.ok:
                return _no_feature_match(repo, cmd, target, as_json)
    else:
        preview = plan_single(repo, target)
        if not preview.ok:
            resolved_feature = lens_verbs.resolve_feature(repo, target)
            if resolved_feature is not None:
                focus_fid = resolved_feature[1]
                preview = plan_feature(repo, target)
            else:
                return _resolve_via_intent(repo, cmd, target, as_json, yes)

    if focus_fid is None and preview.ok:
        # Single-op target: focus the feedforward on the feature that owns the most touched ops.
        from collections import Counter

        from sgt.lens.tree import load as load_tree

        op_leaf = (load_tree(repo) or {}).get("op_leaf", {})
        touched = preview.removed if cmd == "revert" else preview.added
        tally = Counter(op_leaf[o] for o in touched if o in op_leaf)
        focus_fid = tally.most_common(1)[0][0] if tally else None

    return _emit_verb_result(repo, preview, emit, as_json, yes=yes, focus_fid=focus_fid)


def _no_feature_match(repo: str, cmd: str, target: str, as_json: bool) -> int:
    """A handle-shaped target (`f-`/bare-hex) that resolved to no *unique* feature -- a typo, a stale
    id, or a too-short (ambiguous) prefix. Answer deterministically and instantly (no LLM): list the
    leaf features whose handle the prefix matches, or point at `sgt log` when none do. Always exit 2."""
    from sgt.lens.tree import load as load_tree

    nodes = (load_tree(repo) or {}).get("nodes", {})
    bare = target[2:] if target.startswith("f-") else target

    def body(nid: str) -> str:
        return nid[2:] if nid.startswith("f-") else nid

    hits = sorted(
        (nid for nid, nd in nodes.items() if not nd["children"] and body(nid).startswith(bare)),
        key=lambda nid: nodes[nid].get("label", nid),
    )
    if as_json:
        import json

        cands = [{"ref": body(nid)[:8], "label": nodes[nid].get("label", nid)} for nid in hits]
        print(json.dumps({"ok": False, "verb": cmd, "target": target, "candidates": cands}, indent=2))
        return 2
    if not hits:
        print(f"? [{cmd}] no feature matches handle {target!r} -- run `sgt log` to see the handles.")
        return 2
    print(f"? [{cmd}] {target!r} is an ambiguous handle; did you mean:")
    for nid in hits[:8]:
        print(f"  sgt {cmd} {body(nid)[:8]}   {nodes[nid].get('label', nid)}")
    return 2


def _plan_for(verb: str, repo: str, ref: str, kind: str = ""):
    """The one piece of verb-specific glue `resolve_intent`'s candidates need: re-plan a
    candidate ref through the same pure `plan_*` the deterministic rungs already used, so its
    preview is truthful (and a hallucinated/no-longer-live ref reports `ok=False`).

    A `feature`-kind candidate is routed through `plan_revert_feature`/`plan_restore_feature` --
    the same feature-grouped plan the deterministic feature rung uses (mirroring
    `_kernel_edit_verb`'s ladder) -- since the prompt invites feature ids and a plain single-op
    `plan_revert`/`plan_restore` can't resolve one (it would drop the very target the LLM found)."""
    from sgt.core import verbs

    plan_single = verbs.plan_revert if verb == "revert" else verbs.plan_restore
    if kind == "feature":
        from sgt.lens import verbs as lens_verbs

        plan_feature = lens_verbs.plan_revert_feature if verb == "revert" else lens_verbs.plan_restore_feature
        return plan_feature(repo, ref)
    return plan_single(repo, ref)


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


def _revert_session(repo: str, name: str, emit: bool, as_json: bool, yes: bool = False) -> int:
    """`sgt revert --session <name>` (plan U31, S7): addressing by provenance -- resolves a
    session name to the op-set it landed (`sgt.core.session.ops_by_session`, reading structured
    attribution rather than the session record, so it still works long after the session itself is
    gone) and previews/applies the exact same grouped `I \\ (∪ upset_in(x))` edit `revert <feature>`
    already runs, through the identical `verbs.apply` path."""
    from sgt.core import verbs
    from sgt.core.lens import get

    get(repo)  # mine-on-contact before resolving the session's ops (R9)
    preview = verbs.plan_revert_session(repo, name)

    return _emit_verb_result(repo, preview, emit, as_json, yes=yes)


def _revert_keep_dependents(
    repo: str, ref_tokens: list[str], intent: str | None, do_repair: bool, as_json: bool,
    keep: frozenset[str] | None = None,
) -> int:
    """`revert <ref> --keep-dependents` (plan U11, R14): removes the target's up-set but drafts
    a continuation hollow per direct reference-dependent, so its symbol stays live. `keep` (from
    `--keep`, plan U3/R4) narrows that to a caller-chosen frontier of dependent op-ids; `None`
    keeps them all. `--repair` (plan U6) hands the draft straight to the LLM-backed repair loop
    instead of printing it -- the one-command happy path, symmetric with how `--keep-dependents`
    already routes to `rewrite`."""
    from sgt.core import rewrite
    from sgt.core.lens import get

    if not ref_tokens:
        print("usage: sgt revert <ref> --keep-dependents [--keep <id>,<id>] [--repair]")
        return 2
    get(repo)
    draft = rewrite.revert_keep_dependents(repo, " ".join(ref_tokens), intent=intent, keep=keep)
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
    def _op_ids(ids: list, limit: int = 8) -> str:
        head = ", ".join(o[:12] for o in ids[:limit])
        return head + (f" +{len(ids) - limit} more" if len(ids) > limit else "")

    if view["removed"]:
        print(f"    removed {len(view['removed'])} op(s): " + _op_ids(view["removed"]))
    if view["added"]:
        print(f"    added {len(view['added'])} op(s): " + _op_ids(view["added"]))
    # The dependent frontier: what reverting *lands on*. blast = a direct dependent that must be
    # re-drafted (a hollow to fulfill); carry = a transitive dependent that repoints mechanically;
    # foundation = an upstream prerequisite a revert cannot drop. This is the "where does it lead".
    frontier = view.get("frontier") or []
    if frontier:
        buckets: dict[str, int] = {}
        for row in frontier:
            buckets[row.get("bucket", "?")] = buckets.get(row.get("bucket", "?"), 0) + 1
        parts = []
        if buckets.get("blast"):
            parts.append(f"{buckets['blast']} to re-draft (blast)")
        if buckets.get("carry"):
            parts.append(f"{buckets['carry']} auto-repoint (carry)")
        if buckets.get("foundation"):
            parts.append(f"{buckets['foundation']} prerequisite(s) locked (foundation)")
        if parts:
            print("    dependents: " + ", ".join(parts))
    affected = view.get("affected") or []
    if affected:
        rows = ", ".join(
            f"{a['feature_id'][:12]} ({a['direction']} {a['op_count']})" for a in affected[:6]
        )
        more = f" +{len(affected) - 6}" if len(affected) > 6 else ""
        print(f"    features touched: {rows}{more}")
    elif view["affected_symbols"]:
        print(f"    affected: {', '.join(view['affected_symbols'])}")
    _print_verb_diff(view.get("files") or {})
    return 0


def _print_verb_diff(files: dict, max_lines: int = 60) -> None:
    """Show the actual resulting change (the state you land in), computed by the backend as a
    before/after fold per changed path. A capped unified diff -- the honest answer to 'what does
    reverting this do to my code', not just an op-count."""
    if not files:
        return
    import difflib

    print(f"    ── resulting change ({len(files)} file(s)) ──")
    shown = 0
    for path in sorted(files):
        pair = files[path]
        before = (pair.get("before") or "").splitlines()
        after = (pair.get("after") or "").splitlines()
        diff = list(difflib.unified_diff(before, after, lineterm="", n=2))[2:]  # drop the ---/+++ header
        if not diff:
            continue
        print(f"    {path}")
        for line in diff:
            if shown >= max_lines:
                print("    … (diff truncated; use --json for the full before/after)")
                return
            print(f"      {line}")
            shown += 1
