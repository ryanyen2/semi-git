"""Porcelain completion (plan 002, U26; design doc §1 / D2, D3).

Two things live here, both small and both *data*:

1. **The `sgt git` refusal table (D2).** `REFUSALS` maps each tree-mutating git subcommand to the
   sgt verb that does the job natively. `sgt git <sub> …` for such a subcommand is refused (never
   run) with that remedy named, because a raw `git checkout`/`reset`/`merge` rewrites the working
   tree behind sgt's back and the op store drifts. Appending `--force` overrides the refusal (the
   token is consumed, git runs the plain command, and the `gitbind` out-of-band detector re-mines
   on next contact -- the net that catches raw `git` too). This replaces U18's advisory-only
   posture; the routing table is one data table, not conditionals scattered through the passthrough.

2. **The daily-loop verbs (D3): `switch`, `save`, `undo`.** Exactly three, each composed from
   existing lens machinery (no new kernel call): `switch` materializes a named ideal (an existing
   branch's committed tree) via `get` + git checkout + `get`; `save` is the put-path sugar
   (`get` + witness commit + `record_ideal`); `undo` inverts the last mutating operation by popping
   the unified operation log (`oplog.undo`), whatever its kind. Everything git does that sgt does not wrap
   stays one keystroke away behind `sgt git …`; a verb is only wrapped where the sgt-native version
   is *semantically different*, never merely renamed (design doc §1 non-goal).
"""

from __future__ import annotations

from ._common import _emit_json, _fail, _fail_json

# D2 routing table (design doc §1): git subcommand -> the sgt verb that owns that job. A subcommand
# here is refused by `sgt git`; anything absent passes through untouched (read/inspect verbs, and
# plumbing/config like `remote add`/`config`/`tag`/`bisect`/`fsck`). `pull`/`merge` route to `sgt
# sync` (integration is op-set union + fork-surfacing, not a text merge); `reset` to `sgt undo`;
# `revert` to `sgt revert`; `stash` dissolves into `sgt save` (a dirty tree is just ops not yet
# landed). `rebase` has no sgt analogue -- history is a mined DAG -- so it routes to `sgt sync`.
REFUSALS: dict[str, str] = {
    "checkout": "sgt switch <branch>  (or `sgt restore <path>` to restore files)",
    "switch": "sgt switch <branch>",
    "restore": "sgt restore <ref>",
    "pull": "sgt sync [remote] [branch]",
    "merge": "sgt sync [remote] [branch]",
    "reset": "sgt undo  (or `sgt revert <ref>` to drop an op)",
    "rebase": "sgt sync  (history is a mined op DAG; sgt has no rebase)",
    "revert": "sgt revert <ref>",
    "cherry-pick": "sgt restore <ref>  (or `sgt advanced transplant <op>... --onto <ref>`)",
    "stash": "sgt save  (a dirty tree is just ops not yet landed)",
    "am": "git apply, then `sgt save` to record the change as ops",
}


def git_remedy(subcommand: str) -> str | None:
    """The sgt remedy for a refused git subcommand, or None if `sgt git <subcommand>` passes
    through. The single decision point the passthrough consults (D2 as data)."""
    return REFUSALS.get(subcommand)


def refusal_message(subcommand: str, remedy: str) -> str:
    """The stderr refusal text: what sgt refused, the named remedy, and the `--force` escape."""
    return (
        f"sgt: `git {subcommand}` rewrites the working tree behind sgt's tracking -- use `{remedy}`\n"
        f"     (append --force to run git anyway; sgt re-mines on next contact)\n"
    )


def register(subs, parent) -> None:
    sw = subs.add_parser("switch", parents=[parent])
    sw.add_argument("branch")
    sw.set_defaults(func=_cmd_switch)

    sv = subs.add_parser("save", parents=[parent])
    sv.add_argument("-m", "--message", dest="message")
    sv.add_argument("--resolve-plan", action="store_true", dest="resolve_plan",
                    help="settle plan-step matches this save couldn't auto-confirm (n:m / "
                         "multi-step); runs standalone on a clean tree to resolve a prior save's "
                         "leftover ambiguity. Pass --confirm-hollow/--confirm-op to confirm a group.")
    sv.add_argument("--confirm-hollow", action="append", dest="confirm_hollow", default=[],
                    help="(with --resolve-plan) the plan step hollow(s) to confirm against --confirm-op")
    sv.add_argument("--confirm-op", action="append", dest="confirm_op", default=[])
    sv.set_defaults(func=_cmd_save)

    subs.add_parser("undo", parents=[parent]).set_defaults(func=_cmd_undo)


def _cmd_switch(args) -> int:
    return _switch(".", args.branch, args.as_json)


def _cmd_save(args) -> int:
    return _save(".", args.message, args.as_json, resolve_plan=args.resolve_plan,
                 confirm_hollow=args.confirm_hollow, confirm_op=args.confirm_op)


def _cmd_undo(args) -> int:
    return _undo(".", args.as_json)


def _switch(repo: str, branch: str, as_json: bool) -> int:
    """`sgt switch <branch>` (D3): materialize a named ideal -- an existing branch's committed
    tree. Mines the current ref first so nothing is lost (R9), moves HEAD to `branch` (git writes
    that branch's tree, which *is* `code(ideal)`), then mines the new ref so the op store reflects
    it. sgt owns the write path; a raw `git switch` is what D2 refuses in favor of this."""
    from sgt.core.lens import DirtyWorkingTreeError, get
    from sgt.store.gitbind import GitBinding, GitError

    gb = GitBinding(repo)
    try:
        get(repo)  # absorb current reality before leaving this ref (R9)
        gb.checkout_branch(branch)  # move HEAD + materialize the branch's committed tree
        ideal = get(repo)  # reconcile the now-current ref's ideal
    except (GitError, DirtyWorkingTreeError, ValueError) as e:
        return _fail_json(str(e), as_json)

    n = len(ideal.op_ids)
    if as_json:
        return _emit_json({"ok": True, "branch": branch, "ops": n})
    print(f"✓ switch {branch}: {n} op(s) in the ideal")
    return 0


def _save(repo: str, message: str | None, as_json: bool, *, resolve_plan: bool = False,
          confirm_hollow: list[str] = (), confirm_op: list[str] = ()) -> int:
    """`sgt save [-m]` (D3): the put-path sugar -- mine the working tree (R9), then materialize a
    witness commit for the resulting ideal and record it. "Nothing to save" is decided by the
    ideal, not git's dirty flag: with no uncommitted ops the mined ideal equals the recorded one
    (a `.sgt/` untracked in an unconfigured repo would otherwise always read as dirty).

    Plan-matching is folded in (U12/R10): after a save, every *unambiguous* single-step match
    between an active plan session's pending steps and the just-mined ops auto-confirms -- no
    separate `sgt checkpoint`. An n:m / multi-step match instead waits for `sgt save --resolve-plan`
    (which also runs standalone on a clean tree to settle a prior save's leftover ambiguity, and,
    given `--confirm-hollow`/`--confirm-op`, confirms one named group). Ops matching no active plan
    (the old `sgt drift`) surface in this output; the working-tree sense of "drift" keeps its name
    in `status`/`fsck --tree`."""
    from sgt.core.lens import (
        DirtyWorkingTreeError, current_ideal, get, put, record_ideal,
    )
    from sgt.store.gitbind import GitError

    ideal = get(repo)  # mine the working tree (R9)

    # `save --resolve-plan --confirm-hollow ... --confirm-op ...` settles one ambiguous group by
    # name -- exactly what the removed `checkpoint --confirm-*` did, now reached through `save`.
    if resolve_plan and (confirm_hollow or confirm_op):
        return _confirm_plan_match(repo, list(confirm_hollow), list(confirm_op), as_json)

    nothing_new = ideal.op_ids == current_ideal(repo).op_ids
    saved, sha, n = False, None, len(ideal.op_ids)
    if not nothing_new:
        try:
            sha = put(repo, ideal, message=message or "sgt save")
        except (DirtyWorkingTreeError, GitError, ValueError) as e:
            return _fail_json(str(e), as_json)
        record_ideal(repo, ideal, sha)
        saved = True
    elif not resolve_plan:
        msg = "nothing to save -- no uncommitted ops"
        if as_json:
            return _emit_json({"ok": True, "saved": False, "message": msg})
        print(f"✓ {msg}")
        return 0

    plan = _fold_plan_matches(repo)
    return _render_save(as_json, saved, sha, n, plan, resolve_plan)


def _fold_plan_matches(repo: str) -> dict | None:
    """After a save (U12/R10), auto-confirm every unambiguous single-step plan match -- one pending
    step matched to its op(s) -- and leave n:m / multi-step groups for `save --resolve-plan`.
    Returns the fold summary, or `None` when no active plan session produced any match/drift (the
    common case), so `save`'s output and JSON are byte-unchanged then."""
    from sgt.loop.match import compute_checkpoint, confirm_match

    result = compute_checkpoint(repo)
    if not result.matches and not result.drift_op_ids:
        return None
    auto, ambiguous = [], []
    for g in result.matches:
        entry = {"session_id": g.session_id, "hollow_ids": list(g.hollow_ids), "op_ids": list(g.op_ids)}
        if len(g.hollow_ids) == 1:  # one pending step, unambiguous which -> confirm it now
            confirm_match(repo, g.session_id, list(g.hollow_ids), list(g.op_ids))
            auto.append(entry)
        else:  # multiple steps tangled in one op cluster -> needs `save --resolve-plan`
            ambiguous.append(entry)
    return {"auto_confirmed": auto, "ambiguous": ambiguous, "drift_op_ids": list(result.drift_op_ids)}


def _confirm_plan_match(repo: str, hollow_ids: list[str], op_ids: list[str], as_json: bool) -> int:
    """`save --resolve-plan --confirm-hollow ... --confirm-op ...`: confirm one named step<->op
    group, the explicit path the removed `checkpoint --confirm-*` verb owned (`confirm_match` is
    unchanged)."""
    from sgt.loop import plan as plan_mod
    from sgt.loop.match import confirm_match

    sessions = plan_mod.active_sessions(repo)
    session_id = next(
        (sid for sid, rec in sessions.items() if any(s["hollow_id"] in hollow_ids for s in rec["steps"])),
        None,
    )
    if session_id is None:
        return _emit_json({"error": "no session"}) if as_json else _fail(
            f"no active session owns hollow(s) {hollow_ids}")
    confirm_match(repo, session_id, hollow_ids, op_ids)
    if as_json:
        return _emit_json({"ok": True, "session_id": session_id, "hollow_ids": hollow_ids, "op_ids": op_ids})
    print(f"✓ confirmed {len(hollow_ids)} hollow(s) matched to {len(op_ids)} op(s) in session {session_id}")
    return 0


def _render_save(as_json: bool, saved: bool, sha: str | None, n: int,
                 plan: dict | None, resolve_plan: bool) -> int:
    if as_json:
        out: dict = {"ok": True, "saved": saved}
        if saved:
            out["commit"], out["ops"] = sha, n
        if plan is not None:
            out["plan"] = plan
        return _emit_json(out)

    if saved:
        print(f"✓ save {sha[:12]}: {n} op(s)")
    if plan is not None:
        for e in plan["auto_confirmed"]:
            steps = ", ".join(h[:12] for h in e["hollow_ids"])
            print(f"  ✓ plan step {steps} fulfilled by {len(e['op_ids'])} op(s)")
        for e in plan["ambiguous"]:  # show the group so the user can name it to --resolve-plan
            print(f"  ⚠ ambiguous: {len(e['hollow_ids'])} step(s) <-> {len(e['op_ids'])} op(s) "
                  "-- run `sgt save --resolve-plan`")
            if resolve_plan:
                print(f"      hollow: {', '.join(h[:12] for h in e['hollow_ids'])}")
                print(f"      op:     {', '.join(o[:12] for o in e['op_ids'])}")
        if plan["drift_op_ids"]:
            print(f"  drift: {', '.join(o[:12] for o in plan['drift_op_ids'])}")
    elif resolve_plan and not saved:
        print("✓ no plan matches to resolve")
    return 0


def _undo(repo: str, as_json: bool) -> int:
    """`sgt undo` (D3, R7): invert the last mutating operation. Walks the *unified* operation log
    (U8/KTD6) reverse-chronologically -- popping the tail event and applying its inverse, whatever
    its kind: an ideal edit re-materializes its prior ideal, a feature reorg restores its snapshot,
    a shared-out `land`/`propose` is refused. History is append-only, so an undo is a forward edit,
    never a ref rewind."""
    from sgt.core import oplog
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.store.gitbind import GitError

    try:
        outcome = oplog.undo(repo)
    except (DirtyWorkingTreeError, GitError, ValueError) as e:
        return _fail_json(str(e), as_json)

    if outcome.status == "empty":
        # Byte-identical to the pre-U8 message (a golden CLI snapshot pins it).
        msg = "nothing to undo -- no recorded ideal edits"
        if as_json:
            return _emit_json({"ok": True, "undone": False, "message": msg})
        print(f"✓ {msg}")
        return 0

    if outcome.status == "refused":
        return _fail_json(outcome.message, as_json)

    if outcome.status == "ideal_edit":
        result = outcome.ideal
        if as_json:
            return _emit_json({
                "ok": True, "undone": True, "commit": result.witness_sha,
                "restored_ops": len(result.ideal.op_ids),
                "removed": sorted(result.removed), "added": sorted(result.added),
            })
        print(f"✓ undo {result.witness_sha[:12]}: restored {len(result.ideal.op_ids)} op(s)")
        if result.removed:
            print(f"    dropped {len(result.removed)} op(s): "
                  + ", ".join(o[:12] for o in sorted(result.removed)))
        if result.added:
            print(f"    re-added {len(result.added)} op(s): "
                  + ", ".join(o[:12] for o in sorted(result.added)))
        return 0

    # A metadata-snapshot kind (feature reorg / declared edge).
    if as_json:
        return _emit_json({"ok": True, "undone": True, "kind": outcome.kind, "message": outcome.message})
    print(f"✓ undo: {outcome.message}")
    return 0
