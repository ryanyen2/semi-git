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
   (`get` + witness commit + `record_ideal`); `undo` inverts the last recorded ideal edit by
   popping the ideal-edit journal (`lens.undo_ideal`). Everything git does that sgt does not wrap
   stays one keystroke away behind `sgt git …`; a verb is only wrapped where the sgt-native version
   is *semantically different*, never merely renamed (design doc §1 non-goal).
"""

from __future__ import annotations

from ._common import _emit_json, _fail

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
    "cherry-pick": "sgt restore <ref>  (or `sgt transplant <op>... --onto <ref>`)",
    "stash": "sgt save  (a dirty tree is just ops not yet landed)",
    "am": "git apply, then `sgt save` to record the change as ops",
    "apply": "apply, then `sgt save` to record the change as ops",
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
    sv.set_defaults(func=_cmd_save)

    subs.add_parser("undo", parents=[parent]).set_defaults(func=_cmd_undo)


def _cmd_switch(args) -> int:
    return _switch(".", args.branch, args.as_json)


def _cmd_save(args) -> int:
    return _save(".", args.message, args.as_json)


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
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _fail(str(e))

    n = len(ideal.op_ids)
    if as_json:
        return _emit_json({"ok": True, "branch": branch, "ops": n})
    print(f"✓ switch {branch}: {n} op(s) in the ideal")
    return 0


def _save(repo: str, message: str | None, as_json: bool) -> int:
    """`sgt save [-m]` (D3): the put-path sugar -- mine the working tree (R9), then materialize a
    witness commit for the resulting ideal and record it. "Nothing to save" is decided by the
    ideal, not git's dirty flag: with no uncommitted ops the mined ideal equals the recorded one
    (a `.sgt/` untracked in an unconfigured repo would otherwise always read as dirty)."""
    from sgt.core.lens import (
        DirtyWorkingTreeError, current_ideal, get, put, record_ideal,
    )
    from sgt.store.gitbind import GitError

    ideal = get(repo)  # mine the working tree (R9)
    if ideal.op_ids == current_ideal(repo).op_ids:
        msg = "nothing to save -- no uncommitted ops"
        if as_json:
            return _emit_json({"ok": True, "saved": False, "message": msg})
        print(f"✓ {msg}")
        return 0

    try:
        sha = put(repo, ideal, message=message or "sgt save")
    except (DirtyWorkingTreeError, GitError, ValueError) as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _fail(str(e))
    record_ideal(repo, ideal, sha)

    n = len(ideal.op_ids)
    if as_json:
        return _emit_json({"ok": True, "saved": True, "commit": sha, "ops": n})
    print(f"✓ save {sha[:12]}: {n} op(s)")
    return 0


def _undo(repo: str, as_json: bool) -> int:
    """`sgt undo` (D3): invert the last recorded ideal edit. Pops the ref's ideal-edit journal and
    restores that prior ideal exactly (set arithmetic makes it exact), materialized as a fresh
    witness commit (history is append-only -- undo is a forward edit, never a rewind)."""
    from sgt.core.lens import DirtyWorkingTreeError, undo_ideal
    from sgt.store.gitbind import GitError

    try:
        result = undo_ideal(repo)
    except (DirtyWorkingTreeError, GitError, ValueError) as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _fail(str(e))

    if result is None:
        msg = "nothing to undo -- no recorded ideal edits"
        if as_json:
            return _emit_json({"ok": True, "undone": False, "message": msg})
        print(f"✓ {msg}")
        return 0

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
