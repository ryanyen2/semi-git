"""Exact ideal-edit verbs (plan U8, flipped onto the kernel in U10): `revert` (`I \\ ↑X`) and
`restore` (`I ∪ ↓X`), with `--emit` previews and chain-fork surfacing (AE2). `revert
--keep-dependents` (plan U11) instead drafts a continuation hollow per dependent. `after`
(U21) declares/retracts a declared order edge (`a <= b`) over the OR-Set."""

from __future__ import annotations

import argparse
from pathlib import Path

from sgt.select import resolve as select_resolve

from ._common import _emit_json, _fail, _fail_json
from .rewrite import _print_draft, _print_repair_result


def _theme_id_for_label(repo: str, target: str) -> str | None:
    """The theme whose label is exactly `target`, case- and punctuation-insensitively; `None` if
    no theme or more than one matches.

    `sgt intent list` prints themes by label, so the label is what a person has in front of them
    and the id (`theme-4a6670e474a2`) is what they would have to copy. Without this, typing the
    printed name fell past the theme rung to the fuzzy one and matched a *different* object: on
    the study testbed `sgt revert "Event-Day Handling"` proposed the feature "Event Day Tracking",
    which is a neighbouring two-commit feature rather than the five-feature theme. Suggesting the
    wrong grouping under the right name is worse than not resolving, because the preview that
    follows is about something else and reads as if it were about what was asked for.

    Matching folds case and strips non-alphanumerics, so the hyphen in "Event-Day Handling" is not
    the difference between resolving and not. Ambiguity declines to guess, like every other rung."""
    from sgt import state

    try:
        themes = state.load_json(repo, "intent_themes", default={}) or {}
    except Exception:  # noqa: BLE001 -- a read feeding resolution; never fail the verb
        return None
    squash = lambda s: "".join(ch for ch in str(s).casefold() if ch.isalnum())  # noqa: E731
    needle = squash(target)
    if not needle:
        return None
    hits = [tid for tid, body in themes.items() if squash((body or {}).get("label", "")) == needle]
    return hits[0] if len(hits) == 1 else None


def _resolve_theme(repo: str, target: str) -> tuple[frozenset[str], str] | None:
    """A theme id -> the ops its member commits landed, plus a human label.

    Deliberately the same lookup `sgt intent revert` uses (`sgt.intent.group.resolve_group`), so
    the two spellings can never disagree about what a theme contains."""
    from sgt import state
    from sgt.intent import group as intent_group

    try:
        themes = state.load_json(repo, "intent_themes", default={}) or {}
        resolved = intent_group.resolve_group(target, themes, intent_group.atoms(repo))
    except Exception:  # noqa: BLE001 -- an unresolvable theme falls through to the other rungs
        return None
    if resolved is None:
        return None
    _kind, members = resolved
    op_ids = frozenset(oid for a in members for oid in getattr(a, "op_ids", ()) or ())
    if not op_ids:
        return None
    label = (themes.get(target) or {}).get("label") or target
    return op_ids, label


def _oracle_after_apply(repo: str, verb: str) -> list[str]:
    """Run the project's own checks after a destructive edit and say if they now fail.

    The counts a preview gives are about the op set, and an op set can shrink by one symbol while
    the program stops running. The case that prompted this: reverting one checkpoint reported
    "removes 1 edit across 1 symbol · 1 file" and left `NameError: name 'events' is not defined`,
    because the import it took with it was still wanted by a function it never mentioned. Nothing
    in the preview was wrong; it was answering a narrower question than the one being asked.

    So ask the wider one, with the thing that already knows the answer. The oracle is configured
    per project (`.sgt/oracle.json`) and is normally run on demand. Quiet when it passes, quiet
    when no oracle is configured, and never fatal: the edit is already applied and this only
    reports.

    Only the *first* configured tier runs. Tiers are declared cheapest first, so tier one is the
    parse-or-smoke check that answers "does this still start", which is the failure this exists to
    catch. Running the whole pipeline would put a full test suite on the end of every revert, and a
    verb that becomes slow is a verb people stop previewing with.
    """
    import sys

    # Only on an interactive terminal. This exists to stop a person walking away from a green
    # tick, and it costs a real build every time it runs. On the automated paths -- the test
    # suite, an agent driving the MCP server, a script -- nobody is reading the sentence and the
    # cost lands on every revert. The suite went from minutes to a thirty-minute CI timeout before
    # this gate: hundreds of reverts, each paying for a build nobody read.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return []

    from sgt.config import load_oracle_config

    try:
        cfg = load_oracle_config(repo)
        if cfg is None or not cfg.tiers:
            return []
        from sgt.core import oracle

        verdict = oracle.run(repo, tier=cfg.tiers[0].name)
    except Exception:  # noqa: BLE001 -- a check that cannot run must never fail the edit
        return []

    if not isinstance(verdict, dict) or not verdict.get("configured", True):
        return []
    # `run` reports one record per tier, stopping at the first failure.
    failed = [name for name, rec in (verdict.get("tiers") or {}).items()
              if isinstance(rec, dict) and rec.get("status") not in (None, "pass", "green")]
    if not failed:
        return []
    failing = failed[0]
    return [
        f"  ⚠ {failing} now fails after this {verb}. The edit did what it said; something it "
        f"did not name depends on what went.",
        "     `sgt undo` puts it back, or fix the break and `sgt save`.",
    ]


def _dirty_refusal(exc, as_json: bool) -> int:
    """Render a `DirtyWorkingTreeError` from a materializing verb as a clean refusal, not a raw
    traceback with a half-written `.sgt` (F4/F5). The guard's message already names the offending
    files; append the *actual*, executable remedy -- absorb the edits with `sgt save`, or commit /
    `git restore` them, then re-run. (`sgt` never overwrites uncommitted work; there is no `sgt put`
    verb, and `log --refresh` does not clear a dirty tree.)"""
    msg = str(exc)
    if "overwrite uncommitted changes" in msg:
        msg += (" -- record them with `sgt save`, or commit / `git restore` those files, then "
                "re-run (sgt won't overwrite uncommitted work)")
    return _fail_json(msg, as_json)


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
    r.add_argument("--take-dependents", action="store_true", dest="take_dependents",
                   help="the old blanket removal: also remove everything that builds on the "
                        "target, later work included. The default instead subtracts the target "
                        "from shared code at its tip and keeps later work.")
    r.add_argument("--repair", action="store_true")
    r.add_argument("--intent")
    r.add_argument("--session")
    r.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    r.add_argument("--out", default=None, metavar="DIR",
                   help="with --emit: materialize the previewed result onto DIR as raw "
                        "bytes -- the render overlay's counterfactual sync")
    r.add_argument("ref", nargs="*")
    r.set_defaults(func=_cmd_revert)

    s = subs.add_parser("restore", parents=[parent])
    s.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)
    s.add_argument("--out", default=None, metavar="DIR",
                   help="with --emit: materialize the previewed result onto DIR as raw "
                        "bytes -- the render overlay's counterfactual sync")
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
        if not args.yes and not args.as_json:
            # Every other revert shape previews and applies only on `--yes`, and that is what the
            # tutorial teaches. This one used to mutate on sight: it writes continuation hollows
            # into the store and registers a draft before printing anything. A pilot participant
            # added the flag while still deciding whether to use it, got a `✓` and a draft id, and
            # was then handed a `sgt fulfill` command that rewrote six files. A flag must not be
            # able to turn a preview into an action.
            #
            # `--json` keeps applying immediately, like every other verb here: it is the machine
            # contract VS Code and the MCP server depend on, and a caller passing it is not a
            # person who mistook a flag for a dry run.
            print("  `--keep-dependents` drafts a continuation hollow per kept dependent and "
                  "records them in sgt's state.")
            print("  It does not edit your files, but it is a mutation and it cannot be previewed "
                  "in this shape.")
            print(f"\n  not applied. to go ahead:  sgt revert {' '.join(args.ref)} "
                  f"--keep-dependents --yes")
            print("  to see what a plain removal would do instead, drop the flag:  "
                  f"sgt revert {' '.join(args.ref)}")
            return 2
        return _revert_keep_dependents(".", args.ref, args.intent, args.repair, args.as_json, keep=keep)
    return _kernel_edit_verb(".", "revert", args.ref, args.emit, args.as_json, args.yes,
                             take_dependents=args.take_dependents, out=args.out)


def _cmd_restore(args) -> int:
    return _kernel_edit_verb(".", "restore", args.ref, args.emit, args.as_json, args.yes,
                             out=args.out)


def _emit_verb_result(repo: str, preview, emit: bool, as_json: bool, extra: dict | None = None,
                      *, yes: bool = False, focus_fid: str | None = None,
                      out: str | None = None) -> int:
    """Shared tail for the ideal-edit verbs: `--emit` renders the preview projection; otherwise
    apply the edit (when the preview is ok) and render the plain result view. Identical on both
    the revert/restore and the `--session` paths. `extra` (e.g. the intent overlay's `tier`) is
    merged into `view` unconditionally in both branches, before either the JSON or plain-text
    emission call -- so it's visible on both output paths, not JSON only.

    Feedforward (KTD): a plain-text (non-`--json`) apply first *draws* where the edit lands inline
    in the normal `sgt log` flow -- the target feature's checkpoints with the removed/restored slice
    marked, plus the other features the dependency blast hits (`render_verb_preview_lines`) -- then
    gates on `[y/N]`. `--yes` skips the prompt; a non-tty stdin without `--yes` prints the preview
    and refuses to apply (exit 2), mirroring the did-you-mean guard. `--json` is unchanged (applies
    immediately -- the machine contract VS Code/TUI depend on)."""
    from sgt.core import verbs
    from sgt.core.lens import DirtyWorkingTreeError

    if emit:
        from sgt.api import _project_verb_preview

        view = _project_verb_preview(repo, preview)
        if extra:
            view = {**view, **extra}
        # F124. `--emit --json` is the dry run and plain `--json` applies, which is the contract the
        # extension and the tests here depend on -- but the two emitted the same keys, so a machine
        # caller could not tell the preview it asked for from the mutation it caused.
        view = {"applied": False, **view}
        # F129. The projection carries the consequence *summary* (`so_what`, `fallout`, `carry_count`)
        # and none of the subtraction report, which only the apply view hand-built -- so the two
        # warnings a developer is supposed to read *before* deciding arrived only in the result of the
        # mutation, and a dry run named neither. Same four keys as the apply view below, off the same
        # preview object, so the two formats of the preview and the result all agree.
        view = {**view, **_subtraction_fields(preview)}
        # `--emit --out <dir>` materializes the counterfactual this preview describes. It has to
        # go through the preview object rather than `fold --at op:<result_op_ids>`, because a safe
        # revert's forward-subtraction ops live only on the preview until `apply` stores them and
        # the fold would (correctly) refuse the id set as ungrounded. Guarded on `ok`: a refused
        # preview has `after_ids == before_ids`, so writing it would silently materialize the
        # *current* state and read as a successful counterfactual.
        if out is not None and preview.ok:
            from sgt.api import verb_result_out_view

            view = {**view, "out": verb_result_out_view(repo, preview, out)}
        if as_json:
            return _emit_json(view)
        rc = _print_verb_view(view)
        for line in _subtraction_report(preview):
            print(line)
        return rc

    # Plain-text apply: the confirm step draws the feedforward inline -- the same before/after
    # `sgt log` region the edit lands in (`render_verb_preview_lines`) -- in the normal terminal
    # flow, then gates on `[y/N]`. `--yes` skips the prompt; a non-tty stdin without `--yes` prints
    # the preview and refuses (exit 2), mirroring the did-you-mean guard.
    if preview.ok and not as_json:
        import sys

        from sgt.api import _project_verb_preview, grid_view, map_view, segments_view
        from sgt.tui.graph import render_verb_preview_lines

        pview = _project_verb_preview(repo, preview)
        mv, gv, sv = map_view(repo), grid_view(repo), segments_view(repo)

        color = sys.stdout.isatty()
        for line in render_verb_preview_lines(mv, gv, sv, pview, focus_fid=focus_fid, color=color):
            print(line)
        for line in _subtraction_report(preview):
            print(line)
        # Computed before apply, on purpose: apply journals this restore's own
        # entry, and the gap walk would find that entry instead of the revert.
        gap_lines = _restore_gap_report(repo, preview) if preview.verb == "restore" else []
        for line in gap_lines:
            print(line)
        if not yes:
            if not sys.stdin.isatty():
                print("\n  not applied — this was the preview. re-run with --yes to apply.")
                return 2
            try:
                reply = input(f"\napply this {preview.verb}? [y/N] ").strip().lower()
            except EOFError:
                reply = ""
            if reply not in ("y", "yes"):
                print("  skipped — nothing changed.")
                return 1
        try:
            verbs.apply(repo, preview)
        except DirtyWorkingTreeError as e:
            return _dirty_refusal(e, as_json)
        if _changed_nothing(preview):
            print(f"  · {preview.verb} changed nothing — no edit left the ideal and no file moved. "
                  f"(nothing was recorded, so there is nothing to reverse.)")
        else:
            print(f"  ✓ {preview.verb} applied — {_applied_magnitude(preview)}. "
                  f"(`sgt undo` reverses this.)")
        # Same rule as `gap_lines` below, which already had it: repeat the report
        # after the apply only when a confirm prompt scrolled the first copy away.
        # Under `--yes` nothing intervenes, so the three consequence lines printed
        # twice with the ✓ sandwiched between them -- which reads as two rounds of
        # damage rather than one report shown twice.
        if not yes:
            for line in _subtraction_report(preview):
                print(line)
        for line in _oracle_after_apply(repo, preview.verb):
            print(line)
        # Repeated after the apply only when a confirm scrolled the preview away. Under `--yes` there
        # is no prompt between the two prints, so the same warning landed twice, two lines apart, and
        # a warning about work that stays gone reads as two separate problems when it repeats.
        if not yes:
            for line in gap_lines:
                print(line)
        return 0

    gap = _restore_gap(repo, preview) if preview.ok and preview.verb == "restore" else None
    applied = False
    if preview.ok:
        try:
            verbs.apply(repo, preview)
        except DirtyWorkingTreeError as e:
            return _dirty_refusal(e, as_json)
        applied = True
    view = {
        "applied": applied,
        "ok": preview.ok, "verb": preview.verb, "target": preview.target,
        "removed": sorted(preview.removed), "added": sorted(preview.added),
        "affected_symbols": list(preview.affected_symbols), "forked": preview.forked,
        "message": preview.message,
        **_subtraction_fields(preview),
    }
    if gap:
        # Machine consumers (the extension, MCP) get the same warning the
        # terminal prints: what the earlier revert removed that this restore
        # leaves removed. Omitted when there is no gap.
        view["restore_gap"] = gap
    if extra:
        view = {**view, **extra}
    if not as_json:
        return _print_verb_view(view)
    # `_emit_json` keys its exit status off an `error` field this view does not carry, so a refusal
    # rendered here exited 0 while saying `"ok": false` -- a machine caller reading the exit code saw
    # a refusal as a success. Report the refusal in the status too.
    _emit_json(view)
    return 0 if preview.ok else 1


def _restore_gap(repo: str, preview) -> dict | None:
    """What this restore did NOT bring back, when it follows a revert.

    Revert and restore are not inverses, though every natural reading of the two
    words says they are. Revert removes a target *and everything built on it* --
    which can include ops that belong to other features -- while restore brings
    back the target and what it needs, and nothing else. The gap is real work
    that stays gone: reverting "Enrollment Drop" removed `enrollment.drop` as a
    dependent from the *other* drop feature, and restoring "Enrollment Drop"
    then printed a bare ✓ while the function every kept test calls was still
    missing. This is that gap, computed from the journal the undo stack already
    keeps (a revert's entry holds its before/after sets), plus the one command
    that actually is the revert's inverse.
    """
    from sgt.core import oplog

    try:
        key = oplog._ref_key(Path(repo))
        events = oplog.load(repo).get(key, []) if key else []
    except Exception:
        return None

    # Which revert is this restore reversing? Recency is not the answer. Revert `bar`, revert
    # `baz`, restore `bar`, and the newest entry carrying a delta is the `baz` revert, so the
    # report named work this restore never claimed and pointed at `sgt undo` -- which would have
    # thrown away the restore that had just worked. `plan_restore` answers this exactly, from the
    # `verb` and `target_ops` the entry now carries, so ask it rather than guessing again here.
    # Entries written before those keys existed match nothing, and fall back to the walk.
    candidates = None
    if getattr(preview, "target_ops", None):
        from sgt.core import verbs as _verbs

        try:
            found = _verbs._matching_revert_event(Path(repo), frozenset(preview.target_ops))
        except Exception:
            found = None
        if found is not None:
            candidates = [found[0]]

    for event in candidates if candidates is not None else reversed(events):
        prior = set(event.get("ideal") or ())
        result = set(event.get("result") or ())
        removed = prior - result
        introduced = result - prior
        if not removed and not introduced:
            continue

        after = set(preview.after_ids)
        # Two ways a revert takes something out. Dropping the op outright:
        # caught by the op still being absent. Splicing it out of shared code:
        # the revert *introduces* a subtraction op, and the symbol is still
        # subtracted exactly when that op is still the tip of its chain after
        # the restore -- which is why the first version of this check, a bare
        # removed-minus-after diff, reported nothing on the very repro that
        # motivated it.
        still_gone = removed - after
        surviving_splices = introduced & after
        if surviving_splices:
            from sgt.core import opindex, order

            ops = opindex.index_ops(Path(repo))
            tips = set(order.frontier(frozenset(after), ops).values())
            surviving_splices &= tips
        still = frozenset(still_gone | surviving_splices)
        if not still:
            return None
        try:
            from sgt.core import opindex as _oi

            by_id = {op.id: op for op in _oi.index_ops(Path(repo))}
            # Splice footprints name layout entities (`file::__anchor__::name`);
            # collapse the infix so the report says `file::name`, the spelling
            # every other line in this tool uses.
            names = set()
            for oid in still:
                op = by_id.get(oid)
                for sym in op.footprint if op else ():
                    for infix in ("::__anchor__::", "::__residue__::"):
                        sym = sym.replace(infix, "::")
                    # `\x00HEAD\x00` (`mine._RESIDUE_HEAD`) is the gap before a file's first
                    # entity, not an entity. It survives the infix collapse above because it
                    # carries no `__`, and printing it puts a raw null byte on the terminal and
                    # into the MCP payload.
                    if "__" not in sym and "\x00" not in sym:
                        names.add(sym)
            symbols = sorted(names)
        except Exception:
            symbols = []
        return {"still_removed_op_count": len(still), "still_removed_symbols": symbols}
    return None


def _restore_gap_report(repo: str, preview) -> list[str]:
    gap = _restore_gap(repo, preview)
    if not gap:
        return []
    symbols = gap["still_removed_symbols"]
    shown = ", ".join(symbols[:6]) + (f" +{len(symbols) - 6} more" if len(symbols) > 6 else "")
    return [
        f"  ⚠ the earlier revert also removed {gap['still_removed_op_count']} op(s) this restore"
        f" does not bring back{': ' + shown if shown else ''}",
        "    `sgt undo` reverses that revert whole, if that is what you meant.",
    ]

def _changed_nothing(preview) -> bool:
    """F33. A revert whose removal is entirely held by later work removes no op, adds none, and
    splices nothing -- so `verbs.apply` appends no journal event. The apply line used to promise
    "`sgt undo` reverses this" anyway, and undo, finding no event of its own, pops the *previous*
    save and silently drops that edit. Don't offer an undo for an edit that was never recorded."""
    return not preview.removed and not preview.added and not [
        s for s in preview.affected_symbols if "::__" not in s
    ]


def _applied_magnitude(preview) -> str:
    """What the applied edit actually changed. A revert realized as a forward subtraction removes no
    whole op (`sgt.core.subtract` splices instead), so the op counts read "0 edits removed, 5
    added" for a revert that rewrote a function and dropped a test -- the number said no-op while
    the files moved. `restore`'s "N added" is the real magnitude of a restore, so it is untouched."""
    from sgt.tui.graph import plural
    removed, added = len(preview.removed), len(preview.added)
    if preview.verb == "revert" and not removed:
        syms = [s for s in preview.affected_symbols if "::__" not in s]
        return f"{len(syms)} symbol(s) changed, no whole edit removed"
    return f"{plural(removed, 'edit')} removed, {added} added"


def _subtraction_fields(preview) -> dict:
    """The four subtraction-report keys, read off the preview. One accessor because the `--emit` view
    and the apply view are built in different places and drifted apart for eight months (F129): the
    preview carried none of these and the result of the mutation carried all four."""
    return {
        "subtracted_symbols": list(getattr(preview, "subtracted_symbols", ())),
        "pruned_symbols": list(getattr(preview, "pruned_symbols", ())),
        "kept_conflicts": list(getattr(preview, "kept_conflicts", ())),
        "broken_references": list(getattr(preview, "broken_references", ())),
    }


def _readable_symbols(symbols) -> list[str]:
    """Footprint symbol names as a person should read them.

    A footprint names layout entities as well as code: `file::__anchor__::name` for a splice
    position, and `file::__residue__::\\x00HEAD\\x00` for the gap before a file's first entity.
    Neither is a thing anyone wrote or can act on, and the residue head prints its null bytes
    straight to the terminal -- the consequence report showed
    `bikecount/pages/monthly.py::__residue__:: HEAD ` in the middle of the line that tells a
    person what a revert is about to touch. Anchors collapse to the entity they position; residue
    entries are dropped, because there is no entity behind them to name.

    The same collapse `_still_removed` does for its own report; both call here now, so the two
    lists cannot disagree about how a symbol is spelled."""
    out: set[str] = set()
    for sym in symbols or ():
        sym = str(sym)
        if "::__residue__::" in sym:
            continue
        sym = sym.replace("::__anchor__::", "::")
        if "__" in sym or "\x00" in sym:
            continue
        out.add(sym)
    return sorted(out)


def _subtraction_report(preview) -> list[str]:
    """The safe-revert consequence report, printed with the preview AND after apply: what was
    spliced out of shared code, what was bottomed, and -- most important -- what was deliberately
    left alone and still needs a human: conflicting symbols kept byte-identical, and surviving
    code that still names something removed."""
    lines: list[str] = []
    subtracted = _readable_symbols(getattr(preview, "subtracted_symbols", ()))
    pruned = _readable_symbols(getattr(preview, "pruned_symbols", ()))
    kept = _readable_symbols(getattr(preview, "kept_conflicts", ()))
    broken = _readable_symbols(getattr(preview, "broken_references", ()))
    if subtracted:
        lines.append(f"  subtracted from shared code (later work kept): {', '.join(subtracted)}")
    if pruned:
        lines.append(f"  removed going forward: {', '.join(pruned)}")
    if kept:
        lines.append(f"  ⚠ kept unchanged (the removal overlaps later edits — needs your edit): "
                     f"{', '.join(kept)}")
    if broken:
        lines.append(f"  ⚠ still references removed code (fix or revert separately): "
                     f"{', '.join(broken)}")
    return lines


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
    take_dependents: bool = False, out: str | None = None,
) -> int:
    """revert/restore (plan U8, flipped onto the kernel in U10): exact ideal edits (`I \\ ↑X` /
    `I ∪ ↓X`) with `--emit` previews and chain-fork surfacing (AE2). Both verbs' targets
    additionally accept a feature id/label (plan U13): when it doesn't resolve as an op-id or
    symbol, `sgt.lens.verbs.resolve_feature` is tried next, routing to the feature-grouped
    `plan_revert_feature`/`plan_restore_feature` preview -- applied through the exact same
    `sgt.core.verbs.apply` path as a single-op revert/restore, since both produce the same
    `VerbPreview` shape.

    Once every symbol/feature rung above is exhausted (single-op plan refused and `resolve_feature`
    found no feature either), the target falls to two NL rungs in order: first the deterministic
    ledger rung (`_resolve_via_ledger` -- match the phrase against captured intent reasons, offline),
    then the LLM rung (`_resolve_via_intent`, plan U8/U13's fallback ladder's last step)."""
    from sgt.core import verbs
    from sgt.core.lens import get

    if not ref_tokens:
        print(f"usage: sgt {cmd} [--json] <ref>")
        return 2
    target = " ".join(ref_tokens)
    from ._common import busy
    with busy(f"working out what `{target}` covers…"):
        get(repo)  # mine-on-contact before planning/applying the edit (R9)

    # A `<feature>@<n>` or `<feature>:<slug>` checkpoint (the intent-segment rewind unit): resolve
    # it to its deterministic op-set and run the exact same op-set edit `sgt intent revert` uses
    # (KTD6). Tried first because `@`/`:` name a checkpoint unambiguously; a non-match returns None
    # and falls through. Both verbs enter here: `@n` is the rewind unit the map and the checkpoint
    # detail tell users to type, so a rewind whose inverse could not be addressed the same way was a
    # one-way door -- `sgt restore <feature>@<n>` used to fall past every deterministic rung to the
    # NL one and exit `could not resolve ... set OPENAI_API_KEY`.
    # A theme id names an intent that runs across several features -- exactly the shape a job that
    # was done over three afternoons has. `sgt intent list` prints these, `sgt intent revert` has
    # always been able to remove one, and plain `sgt revert` could not, so the tool showed people a
    # grouping it then refused to act on and the answer was a second verb they had to already know.
    # Same resolution, same removal path, one verb.
    # By id, or by the label `sgt intent list` prints -- see `_theme_id_for_label` for why the
    # label has to resolve here rather than fall through to the fuzzy rung.
    theme_target = target if target.startswith("theme-") else _theme_id_for_label(repo, target)
    if theme_target is not None:
        from sgt.intent import group as intent_group

        themed = _resolve_theme(repo, theme_target)
        if themed is not None:
            op_ids, label = themed
            preview = (verbs.plan_revert_op_set(repo, label, op_ids,
                                                take_dependents=take_dependents)
                       if cmd == "revert" else
                       verbs.plan_restore_op_set(repo, label, op_ids))
            # `yes` is keyword-only; passing it positionally lands it in `extra` and the apply
            # silently degrades to a preview that says "re-run with --yes" when --yes was given.
            return _emit_verb_result(repo, preview, emit, as_json, yes=yes, out=out)

    if select_resolve.is_checkpoint_shaped(target):
        from sgt.intent.segment import resolve_checkpoint

        resolved = resolve_checkpoint(repo, target)
        if resolved is None:
            # The feature part resolved and only the selector missed -- an index past the last
            # chapter, or an unknown/ambiguous slug. `resolve_checkpoint` cannot say so in its
            # return type, so this used to fall through every remaining rung to the NL one and
            # answer `set OPENAI_API_KEY`: F94's defect again, on the handle the practice sheet
            # types verbatim. `checkpoint_miss` returns None for every spec this cannot improve
            # on, so an unknown *feature* still reaches `_no_feature_match` below.
            from sgt.intent.segment import checkpoint_miss

            miss = checkpoint_miss(repo, target)
            if miss is not None:
                return _no_checkpoint_match(cmd, target, miss, as_json)
        if resolved is not None:
            op_ids, label = resolved
            preview = (verbs.plan_revert_op_set(repo, target, op_ids,
                                                take_dependents=take_dependents)
                       if cmd == "revert" else
                       verbs.plan_restore_op_set(repo, target, op_ids))
            # The feedforward focus is the feature the checkpoint's ops belong to (all ops in one
            # segment share a feature); pick any target op and read its leaf feature.
            from sgt.lens.tree import load as load_tree

            op_leaf = (load_tree(repo) or {}).get("op_leaf", {})
            focus_fid = next((op_leaf[o] for o in op_ids if o in op_leaf), None)
            return _emit_verb_result(repo, preview, emit, as_json, extra={"checkpoint": label},
                                     yes=yes, focus_fid=focus_fid, out=out)

    from functools import partial

    from sgt.lens import verbs as lens_verbs

    if cmd == "revert":
        plan_single = partial(verbs.plan_revert, take_dependents=take_dependents)
        plan_feature = partial(lens_verbs.plan_revert_feature, take_dependents=take_dependents)
    else:
        plan_single = verbs.plan_restore
        plan_feature = lens_verbs.plan_restore_feature
    # A bare-hex / `f-` handle (the copy token the graph prints) *is* a founding op id, so `plan_single`
    # would target that one op. But the handle names the whole feature -- resolve it as a feature first,
    # the feature scope winning over the op it shadows. Symbols (`a.py::foo`) and `@n`/`:slug` never
    # match this shape (handled earlier / carry `::@:`), so single-op-by-symbol is unchanged.
    # The shape predicates live in `sgt.select.resolve` so `sgt show <x>` classifies `<x>` exactly as
    # this ladder does -- a token that reads as a feature here must not read as an op there.
    handle_shaped = select_resolve.is_handle_shaped(target)
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
                if cmd == "restore":
                    # The id a `revert` just printed names an op the *reduced* ideal no longer
                    # holds, so plan_restore can't see it -- explain the block instead of
                    # "no feature matches".
                    explained = _explain_restore_block(repo, target, as_json)
                    if explained is not None:
                        return explained
                if not _names_a_stored_op(repo, target):
                    return _no_feature_match(repo, cmd, target, as_json)
                # F39's collateral defect: the hex names an op the store *is* holding and this verb
                # cannot apply it -- `_explain_restore_block` returns None for every reason other
                # than a competing live sibling, and a `revert` refusal never had a rung here at
                # all. `_no_feature_match` would deny the op exists and send the reader looking for
                # a feature; fall through to the planner's own reason instead.
    else:
        preview = plan_single(repo, target)
        if not preview.ok:
            resolved_feature = lens_verbs.resolve_feature(repo, target)
            if resolved_feature is not None:
                focus_fid = resolved_feature[1]
                preview = plan_feature(repo, target)
            elif "::" not in target:
                ledgered = _resolve_via_ledger(repo, cmd, target, emit, as_json, yes)
                if ledgered is not None:
                    return ledgered
                return _resolve_via_intent(repo, cmd, target, as_json, yes)
            # F94, F91's defect one rung down: a `file::Symbol` target is a deterministic reference,
            # not prose, so the NL rung cannot improve on it and its refusal ("set OPENAI_API_KEY to
            # enable natural-language targets") names a remedy that would not help. Fall through with
            # the planner's own reason instead -- the same fall-through the handle-shaped branch
            # takes. This is the rung WP-V4's recoverability ladder uses, so every refusal it
            # recorded blamed a missing key where a true reason had already been computed.

    if cmd == "restore" and preview.ok and not preview.added and "::" in target:
        # "restores 0 op" is an honest set answer but a useless human one: the symbol is already
        # live. Say that, and surface any parked (ghost) versions -- the thing the user was
        # probably reaching for -- with the exact swap commands.
        return _restore_already_live(repo, target, as_json)

    if focus_fid is None and preview.ok:
        # Single-op target: focus the feedforward on the feature that owns the most touched ops.
        from collections import Counter

        from sgt.lens.tree import load as load_tree

        op_leaf = (load_tree(repo) or {}).get("op_leaf", {})
        touched = preview.removed if cmd == "revert" else preview.added
        tally = Counter(op_leaf[o] for o in touched if o in op_leaf)
        focus_fid = tally.most_common(1)[0][0] if tally else None

    return _emit_verb_result(repo, preview, emit, as_json, yes=yes, focus_fid=focus_fid, out=out)


def _save_named_by(repo: str, target: str) -> tuple[str, str, list[tuple[str, str]]] | None:
    """``(short sha, subject, [(handle, label)])`` when `target` names a *save* -- a commit in this
    branch's history -- listing the features whose ops that save recorded. ``None`` when it names no
    commit here, so the caller's handle ladder continues.

    `sgt save` prints the sha of the commit it just made, which makes `sgt revert <that sha>` the
    obvious next move after a save you regret. A bare hex is handle-shaped, so it lands in
    `_no_feature_match` and used to be answered with "no feature matches handle '<sha>' -- run `sgt
    log --map`": the map lists feature handles and never save shas, so the one pointer offered
    cannot resolve the one id typed, and a name sgt itself printed reads back as unknown."""
    from sgt.core import opindex
    from sgt.lens.tree import load as load_tree
    from sgt.store.gitbind import GitBinding

    gb = GitBinding(repo)
    if not gb.is_repo():
        return None
    full = gb.rev_parse(target)
    if full is None:
        return None
    rows = gb.history()
    subject = next((subj for sha, _parent, subj in rows if sha == full), None)
    if subject is None:
        return None  # resolves to an object, but not to a commit on this history
    tree = load_tree(repo) or {}
    op_leaf, nodes = tree.get("op_leaf", {}), tree.get("nodes", {})
    sha_of = opindex.earliest_commit_sha(gb, rows, opindex.index_ops(repo))
    fids = {op_leaf[oid] for oid, sha in sha_of.items() if sha == full and oid in op_leaf}
    feats = sorted(
        ((fid[2:] if fid.startswith("f-") else fid)[:8], nodes.get(fid, {}).get("label", fid))
        for fid in fids
    )
    return full[:7], subject.strip(), feats


def _no_checkpoint_match(
    cmd: str, target: str, miss: tuple[str, str, list[str]], as_json: bool,
) -> int:
    """A `<feature>@<n>`/`<feature>:<slug>` whose feature resolved and whose selector did not.
    Deterministic and instant, like `_no_feature_match`: name the feature, say how many checkpoints
    it actually has, and list them as commands that run. Always exit 2."""
    feat_part, label, seg_labels = miss
    n = len(seg_labels)
    message = (f"{label!r} has {n} checkpoint{'' if n == 1 else 's'}, "
               f"so {target!r} names none of them")
    refs = [(f"{feat_part}@{i}", lbl) for i, lbl in enumerate(seg_labels)]
    if as_json:
        import json

        print(json.dumps({"ok": False, "verb": cmd, "target": target, "message": message,
                          "candidates": [{"ref": r, "label": lbl} for r, lbl in refs]}, indent=2))
        return 2
    print(f"? [{cmd}] {message}; did you mean:")
    for ref, lbl in refs[:8]:
        print(f"  sgt {cmd} {ref}   {lbl}")
    if len(refs) > 8:
        print(f"  ... and {len(refs) - 8} more — run `sgt log --focus {feat_part}` for all of them.")
    return 2


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
    save = _save_named_by(repo, target) if not hits else None
    if save is not None:
        return _revert_a_save(cmd, target, save, as_json)
    if as_json:
        import json

        cands = [{"ref": body(nid)[:8], "label": nodes[nid].get("label", nid)} for nid in hits]
        # `message` carries the same explanation the human path prints. Every UI
        # consumer shows `view.message || <generic>`, so a refusal without one
        # surfaces as "Cannot revert X." with no reason -- a dead end in exactly
        # the surface that cannot fall through to a terminal's stdout.
        message = (
            f"{target!r} is an ambiguous handle; candidates listed"
            if hits
            else f"no feature matches handle {target!r} -- it may be stale; refresh the view"
        )
        print(json.dumps({"ok": False, "verb": cmd, "target": target, "message": message,
                          "candidates": cands}, indent=2))
        return 2
    if not hits:
        print(f"? [{cmd}] no feature matches handle {target!r} -- run `sgt log` to see the handles.")
        return 2
    print(f"? [{cmd}] {target!r} is an ambiguous handle; did you mean:")
    for nid in hits[:8]:
        print(f"  sgt {cmd} {body(nid)[:8]}   {nodes[nid].get('label', nid)}")
    return 2


def _revert_a_save(cmd: str, target: str, save: tuple[str, str, list[tuple[str, str]]],
                   as_json: bool) -> int:
    """The refusal for a save sha: say what the sha is, then name the moves that do work -- one
    `sgt <cmd> <handle>` per feature that save recorded, plus `sgt undo` (step the last save back)
    and `sgt why <sha>` (what it was for). Exit 2 like every other unresolved target."""
    sha, subject, feats = save
    if feats:
        # The message is self-contained: the extension shows `view.message` and nothing else, so
        # "the features are listed" would name a listing that surface does not render. `candidates`
        # still carries them all, for a consumer that can.
        named = "; ".join(f"{label} ({ref})" for ref, label in feats[:3])
        more = f"; and {len(feats) - 3} more" if len(feats) > 3 else ""
        message = (f"{target!r} is a save ({subject!r}), not a feature handle -- {cmd} takes one "
                   f"feature or symbol at a time. It recorded: {named}{more}. "
                   f"`sgt undo` steps the most recent save back.")
    else:
        message = (f"{target!r} is a save ({subject!r}), not a feature handle, and it recorded no "
                   f"feature ops -- try `sgt undo` to step the most recent save back")
    if as_json:
        import json

        print(json.dumps({"ok": False, "verb": cmd, "target": target, "message": message,
                          "candidates": [{"ref": ref, "label": label} for ref, label in feats]},
                         indent=2))
        return 2
    print(f"? [{cmd}] {target!r} is a save, not a feature handle: {subject!r}")
    if feats:
        print(f"  a save is a point in time; `{cmd}` takes what it changed, one feature at a time:")
        for ref, label in feats[:8]:
            print(f"    sgt {cmd} {ref}   {label}")
        if len(feats) > 8:
            print(f"    ... and {len(feats) - 8} more (`sgt log`)")
    else:
        print("  it recorded no feature ops.")
    print("  or:")
    print("    sgt undo            step the most recent save back")
    print(f"    sgt why {sha}     what that save was for")
    return 2


def _save_of(repo: str, op_ids) -> dict[str, tuple[str, str]]:
    """``op_id -> (short sha, subject)`` of the save that produced each op -- the anchor users
    actually remember. Reads the same earliest-witness rule every time-aware view uses."""
    from sgt.core import opindex
    from sgt.store.gitbind import GitBinding

    gb = GitBinding(repo)
    rows = gb.history()
    ops = [op for op in opindex.index_ops(repo) if op.id in set(op_ids)]
    sha_of = opindex.earliest_commit_sha(gb, rows, ops)
    subject = {sha: subj for sha, _parent, subj in rows}
    return {oid: (sha[:7], subject.get(sha, "")) for oid, sha in sha_of.items()}


def _live_and_ghosts(repo: str, symbol: str):
    """(live tip op-id or None, [out-of-ideal op-ids touching `symbol`, oldest-first])."""
    from sgt.core import lens, opindex, order

    ops = opindex.index_ops(repo)
    ideal = lens.current_ideal(repo)
    tip = order.frontier(ideal.op_ids, ops).get(symbol)
    ghosts = sorted(op.id for op in ops if symbol in op.footprint and op.id not in ideal.op_ids)
    return tip, ghosts


def _names_a_stored_op(repo: str, target: str) -> bool:
    """Does this hex handle name an op the store holds? One that does is not a typo or a stale
    handle, so a verb that cannot apply it owes the reader its own refusal rather than
    `_no_feature_match`'s denial that the id exists."""
    from sgt.core import opindex

    return any(op.id == target or op.id.startswith(target) for op in opindex.index_ops(repo))


def _explain_restore_block(repo: str, target: str, as_json: bool) -> int | None:
    """A hex restore target that names a real stored op the current ideal can't legally re-admit.
    `plan_restore` resolves against the reduced HEAD ideal, which parks a superseded or forked
    version -- so the very id a `revert` printed reads as "not found". Resolve it against the
    whole store instead and, when another version of its symbol is live, explain the one-live-
    version rule and the two ways out (swap / reconcile). Returns None when no stored op matches
    (the caller's ladder continues)."""
    from sgt.core import lens, opindex, order
    from sgt.core.op import _symbol_kind

    ops = opindex.index_ops(repo)
    ids = {op.id for op in ops}
    matches = sorted(oid for oid in ids if oid == target or oid.startswith(target))
    if not matches:
        return None
    if len(matches) > 1:
        msg = f"ambiguous op-id prefix {target!r}: {[m[:12] for m in matches[:5]]}"
        return _fail_json(msg, as_json)
    op_id = matches[0]
    ideal = lens.current_ideal(repo)
    if op_id in ideal.op_ids:
        if as_json:
            return _emit_json({"ok": True, "verb": "restore", "target": target,
                               "removed": [], "added": [], "message": "already live"})
        print(f"✓ {op_id[:8]} is already live — nothing to restore.")
        return 0

    op = next(o for o in ops if o.id == op_id)
    tips = order.frontier(ideal.op_ids, ops)
    blocked = []
    for sym in sorted(op.footprint):
        if _symbol_kind(sym) not in ("entity", "nested", "whole_file"):
            continue
        tip = tips.get(sym)
        if tip is not None and tip != op_id:
            blocked.append((sym, tip))
    if not blocked:
        return None  # parked for some other reason -- let the normal ladder report it

    saves = _save_of(repo, [op_id] + [tip for _s, tip in blocked])
    if as_json:
        # Shape-compatible with the verb-preview projection (`forked` is what a client keys the
        # refusal overlay on), plus the block detail no other field carries.
        return _emit_json({
            "ok": False, "verb": "restore", "target": target, "forked": True, "blocked": True,
            "removed": [], "added": [],
            "symbols": [{"symbol": s, "live_op": t, "ghost_op": op_id} for s, t in blocked],
            "message": "another version of the symbol is live; revert it first or resolve",
        })
    sym, tip = blocked[0]
    g_sha, g_subj = saves.get(op_id, ("?", ""))
    t_sha, t_subj = saves.get(tip, ("?", ""))
    print(f"✗ can't restore {op_id[:8]} — another version of {sym} is live")
    print(f"    one live version per symbol: {tip[:8]}"
          + (f" (save {t_sha} \"{t_subj}\")" if t_subj else "") + " is live;")
    print(f"    {op_id[:8]}" + (f" (save {g_sha} \"{g_subj}\")" if g_subj else "")
          + " waits behind it as a ghost.")
    print(f"      swap       sgt revert {tip[:8]}   then   sgt restore {op_id[:8]}")
    print(f"      reconcile  sgt resolve {sym}   (combine both versions)")
    for sym_extra, _tip in blocked[1:]:
        print(f"    (also blocked on {sym_extra})")
    return 2


def _restore_already_live(repo: str, symbol: str, as_json: bool) -> int:
    """`sgt restore <file::symbol>` when the symbol is already live: name that plainly, then list
    any parked versions with the swap commands -- the likely reason the user reached for restore."""
    tip, ghosts = _live_and_ghosts(repo, symbol)
    if as_json:
        return _emit_json({"ok": True, "verb": "restore", "target": symbol, "removed": [],
                           "added": [], "already_live": True, "parked_versions": ghosts})
    print(f"✓ {symbol} is already live — nothing to restore.")
    if ghosts and tip:
        from sgt.core import verbs

        saves = _save_of(repo, ghosts + [tip])
        print(f"  {len(ghosts)} parked version(s) of this symbol exist:")
        for g in ghosts[:4]:
            sha, subj = saves.get(g, ("?", ""))
            note = f"  from save {sha} \"{subj}\"" if subj else ""
            print(f"    {g[:8]}{note}")
            # Re-plan for a truthful hint: a ghost whose live tip is its own ancestor restores
            # directly (a chain extension); a competing sibling needs the swap.
            replan = verbs.plan_restore(repo, g)
            if replan.ok and replan.added:
                print(f"      bring it back:  sgt restore {g[:8]}")
            else:
                print(f"      swap it in:  sgt revert {tip[:8]}  then  sgt restore {g[:8]}")
        if len(ghosts) > 4:
            print(f"    +{len(ghosts) - 4} more")
    return 0


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


def _resolve_via_ledger(repo: str, cmd: str, target: str, emit: bool, as_json: bool, yes: bool) -> int | None:
    """The deterministic NL rung, tried *before* the LLM (plan U8): match the prose `target` against
    the intent ledger's captured `reason` texts (M1's topic tokenizer, typo-tolerant) and revert the
    best-matching record's subject op-set -- so "drop the retry logic" resolves offline once the
    ledger holds a reason mentioning retry, no `OPENAI_API_KEY` needed.

    Revert-only: a reason indexes the op-set that *landed* it, which is what `revert` removes; there
    is no op-set counterpart for `restore` (the same reason the `@`/`:` checkpoint branch is
    revert-only). Returns an exit code when it resolved and emitted, or `None` to fall through to the
    LLM rung -- for `restore`, an empty target phrase, no ledgered reason overlapping the phrase, an
    ambiguous tie between records, or a matched record whose op-set no longer re-plans cleanly."""
    if cmd != "revert":
        return None
    from sgt.core import verbs
    from sgt.intent import align, rationale

    phrase = set(align._content_words(target))
    if not phrase:
        return None
    recs = list(rationale.load_rationale(repo).values())
    dead = rationale._superseded_ids(recs)
    scored: list[tuple[int, float, dict]] = []
    for r in recs:  # the live, landed reasons -- mirror recall's filter (skip superseded/open/reasonless)
        if r["id"] in dead or r.get("open") or not r.get("reason"):
            continue
        score = align.topic_overlap(phrase, align._content_words(r["reason"]))
        if score:
            scored.append((score, r["ts"], r))
    if not scored:
        return None
    scored.sort(key=lambda sr: (-sr[0], -sr[1]))  # strongest overlap, then most recent
    best = scored[0][0]
    if len([s for s in scored if s[0] == best]) > 1:
        return None  # a tie is genuinely ambiguous -- let the LLM rung disambiguate rather than guess
    rec = scored[0][2]
    op_ids = frozenset(s["op"] for s in rec.get("subject", []))
    if not op_ids:
        return None  # an open/unlanded intent has no op-set to revert
    preview = verbs.plan_revert_op_set(repo, target, op_ids)
    if not preview.ok or not preview.removed:
        return None  # already reverted, or not in the ideal -- fall through rather than a no-op "success"

    from sgt.lens.tree import load as load_tree

    op_leaf = (load_tree(repo) or {}).get("op_leaf", {})
    focus_fid = next((op_leaf[o] for o in op_ids if o in op_leaf), None)
    return _emit_verb_result(repo, preview, emit, as_json,
                             extra={"resolved_from": rec["reason"]}, yes=yes, focus_fid=focus_fid)


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
        from sgt.core.lens import DirtyWorkingTreeError

        _, top_preview = survivors[0]
        try:
            verbs.apply(repo, top_preview)
        except DirtyWorkingTreeError as e:
            return _dirty_refusal(e, as_json)
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
            # Same reason as `_applied_magnitude`: a candidate whose revert is a subtraction removes
            # no whole op, and "would remove 0 op(s)" read as "this candidate does nothing".
            "cost": _applied_magnitude(preview),
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
        print(f"     would apply — {c['cost']}")
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
    from sgt.tui.graph import plural
    icon = "✓" if view["ok"] else "✗"
    print(f"{icon} [{view['verb']}] {view['target']}" + (f" — {view['message']}" if view["message"] else ""))
    if not view["ok"]:
        return 1
    # Human units: symbols, not op ids (the ids stay in --json; `sgt undo` is the recovery path,
    # and a blocked restore lists parked versions by symbol).
    syms = [s for s in view.get("affected_symbols", []) if "::__" not in s]
    sym_note = (": " + ", ".join(syms[:6]) + (f" +{len(syms) - 6} more" if len(syms) > 6 else "")) if syms else ""
    if view["removed"]:
        print(f"    removed {plural(len(view['removed']), 'edit')}{sym_note}")
    if view["added"]:
        print(f"    added {len(view['added'])} edit(s){sym_note}")
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
            parts.append(f"{plural(buckets['foundation'], 'prerequisite')} locked (foundation)")
        if parts:
            print("    dependents: " + ", ".join(parts))
    affected = view.get("affected") or []
    if affected:
        rows = ", ".join(
            f"{a['feature_id'][:12]} ({a['direction']} {a['op_count']})" for a in affected[:6]
        )
        more = f" +{len(affected) - 6}" if len(affected) > 6 else ""
        print(f"    features touched: {rows}{more}")
    _print_verb_diff(view.get("files") or {})
    return 0


def _print_verb_diff(files: dict, max_lines: int = 60) -> None:
    """Show the actual resulting change (the state you land in), computed by the backend as a
    before/after fold per changed path. A capped unified diff -- the honest answer to 'what does
    reverting this do to my code', not just an op-count."""
    from sgt.tui.graph import plural
    if not files:
        return
    import difflib

    print(f"    ── resulting change ({plural(len(files), 'file')}) ──")
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
