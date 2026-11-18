"""Porcelain completion (plan 002, U26; design doc §1 / D2, D3).

Two things live here, both small and both *data*:

1. **The `ugh git` refusal table (D2).** `REFUSALS` maps each tree-mutating git subcommand to the
   ugh verb that does the job natively. `ugh git <sub> …` for such a subcommand is refused (never
   run) with that remedy named, because a raw `git checkout`/`reset`/`merge` rewrites the working
   tree behind ugh's back and the op store drifts. Appending `--force` overrides the refusal (the
   token is consumed, git runs the plain command, and the `gitbind` out-of-band detector re-mines
   on next contact -- the net that catches raw `git` too). This replaces U18's advisory-only
   posture; the routing table is one data table, not conditionals scattered through the passthrough.

2. **The daily-loop verbs (D3): `save`, `undo`.** `save` is the put-path sugar (`get` + a commit
   that is mined like any other); `undo` reverses the last mutating operation by popping the
   operation log (`oplog.undo`). Everything git does that ugh does not wrap stays one keystroke away behind
   `ugh git …`; a verb is only wrapped where the ugh-native version is *semantically different*,
   never merely renamed (design doc §1 non-goal).
"""

from __future__ import annotations

import argparse

from ._common import _emit_json, _fail, _fail_json, fork_lines

# D2 routing table (design doc §1): git subcommand -> the ugh verb that owns that job. A subcommand
# here is refused by `ugh git`; anything absent passes through untouched (read/inspect verbs, and
# plumbing/config like `remote add`/`config`/`tag`/`bisect`/`fsck`). `pull`/`merge` route to `ugh
# sync` (integration is op-set union + fork-surfacing, not a text merge); `reset` to `ugh undo`;
# `revert` to `ugh revert`; `stash` dissolves into `ugh save` (a dirty tree is just ops not yet
# landed). `rebase` has no ugh analogue -- history is a mined DAG -- so it routes to `ugh sync`.
REFUSALS: dict[str, str] = {
    "restore": "ugh restore <ref>",
    "pull": "ugh sync [remote] [branch]",
    "merge": "ugh sync [remote] [branch]",
    "reset": "ugh undo  (or `ugh revert <ref>` to drop an op)",
    "rebase": "ugh sync  (history is a mined op DAG; ugh has no rebase)",
    "revert": "ugh revert <ref>",
    "cherry-pick": "ugh restore <ref>",
    "stash": "ugh save  (a dirty tree is just ops not yet landed)",
    "am": "git apply, then `ugh save` to record the change as ops",
}


def git_remedy(subcommand: str) -> str | None:
    """The ugh remedy for a refused git subcommand, or None if `ugh git <subcommand>` passes
    through. The single decision point the passthrough consults (D2 as data)."""
    return REFUSALS.get(subcommand)


def refusal_message(subcommand: str, remedy: str) -> str:
    """The stderr refusal text: what ugh refused, the named remedy, and the `--force` escape."""
    return (
        f"ugh: `git {subcommand}` rewrites the working tree behind ugh's tracking -- use `{remedy}`\n"
        f"     (append --force to run git anyway; ugh re-mines on next contact)\n"
    )


def register(subs, parent) -> None:
    sv = subs.add_parser("save", parents=[parent])
    sv.add_argument("-m", "--message", dest="message")
    sv.add_argument("--as", dest="as_label", metavar="LABEL",
                    help="name this save's steps")
    sv.set_defaults(func=_cmd_save)

    uv = subs.add_parser("undo", parents=[parent])
    # The same hidden machine dry-run `revert`/`restore` carry: report what the next undo would do
    # and apply nothing. A tty gets this report for free as `undo`'s confirm step; before this flag
    # a non-tty caller (the extension, MCP) had no way to ask the question, only to take the action.
    uv.add_argument("--emit", action="store_true", help=argparse.SUPPRESS)
    uv.set_defaults(func=_cmd_undo)


def _cmd_save(args) -> int:
    out = save(".", message=args.message, as_label=args.as_label)
    if args.as_json or not out["ok"]:
        return _emit_json(out) if args.as_json else _fail(out["error"])
    _render_save(out, args.message, args.as_label)
    return 0


def _cmd_undo(args) -> int:
    return _undo(".", args.as_json, emit=args.emit)


def save(repo: str, *, message: str | None = None, as_label: str | None = None) -> dict:
    """Record the working tree's edits as a save, name its steps when `as_label` is given, and
    return what the save did: `{ok, saved, commit, steps, open_fork_count}` or `{ok: False, error}`.

    "Nothing to save" is decided by the ideal, not git's dirty flag: with no uncommitted ops the
    mined ideal equals the recorded one."""
    import time

    from ugh import names
    from ugh.address import Record
    from ugh.api import forks_view
    from ugh.core import opindex, oplog
    from ugh.core.lens import DirtyWorkingTreeError, current_ideal, get, merge_in_progress, put, sync_status
    from ugh.intent.manifest import load_manifests, record_manifest
    from ugh.store.gitbind import GitBinding, GitError

    from ._common import busy

    # An in-progress git merge/cherry-pick/revert leaves conflict markers in the tree: refuse before
    # mining so they never become ops and the paused operation is never finalized blind.
    gb = GitBinding(repo)
    verb = merge_in_progress(gb)
    if verb is not None:
        return {"ok": False, "error": f"in-progress git {verb} -- finish or abort the git {verb} first "
                                      f"(git {verb} --continue / git {verb} --abort); ugh save won't "
                                      "commit conflict markers"}
    with busy("examining the working tree…"):
        ideal = get(repo)  # mine the working tree (R9)
    prev_ids = current_ideal(repo).op_ids
    if ideal.op_ids == prev_ids:
        # Only claim "nothing to save" once the tree was actually examined: while history is still
        # being mined, the dirty pass is skipped and a real edit could go unmined.
        if not sync_status(repo)["complete"]:
            return {"ok": False, "error": "can't tell yet whether there's anything to save -- this "
                    "repo's history is still being mined, and until that finishes ugh does not examine "
                    "the working tree. Re-run `ugh save` (each run mines another chunk) until it "
                    "reports a result"}
        return {"ok": True, "saved": False, "message": "nothing to save -- no uncommitted ops"}
    prev_head = gb.head()
    with busy("recording the save…"):
        try:
            sha = put(repo, ideal, message=message or "ugh save")
        except (DirtyWorkingTreeError, GitError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        oplog.append(repo, {"kind": "commit", "sha": sha, "verb": "save", "target": message or ""})
    if message:  # the user's own words about this save, keyed by its commit
        from ugh.intent.turns import record_turn

        record_turn(repo, key=sha, key_kind="sha", actor="human", channel="cli", text=message)
    # Close the capture window: the turns and activity since the previous save, with this save's
    # ops, become one manifest, which `show` reads to give the ask behind the save.
    new_op_ids = ideal.op_ids - prev_ids
    prev_ts = gb.commit_times().get(prev_head) if prev_head and not load_manifests(repo) else None
    record_manifest(repo, sha=sha, ops=[op for op in opindex.index_ops(repo) if op.id in new_op_ids],
                    end=time.time(), prev_save_ts=prev_ts)
    rec = Record(repo)
    steps = rec.ctx.of_commit.get(sha, [])
    if as_label:
        names.name(repo, rec.ops(steps), as_label)
        rec.names = names.load(repo)
    return {"ok": True, "saved": True, "commit": sha, "steps": rec.steps(steps),
            "open_fork_count": forks_view(repo)["open"]}


def _render_save(out: dict, message: str | None, as_label: str | None) -> None:
    from ugh.api import forks_view

    if not out["saved"]:
        print(f"✓ {out['message']}")
        if as_label:
            print("  --as names a save's steps; nothing was saved, so nothing to name.")
        return
    title = f' "{message}"' if message else ""
    print(f"✓ save {out['commit'][:7]}{title}")
    for st in out["steps"]:
        print(f"    {st['kind']:<7} {st['address']}" + (f"  [{st['name']}]" if st["name"] else ""))
    # Reversing the save takes its edits back off the working copy, not only out of the record.
    print("  ⤺ undo this save, and take its edits back off the working copy:  ugh undo")
    if out["open_fork_count"]:
        for line in fork_lines(forks_view(".")["forks"]):
            print(line)


def _undo_preview_lines(pv: dict) -> list[str]:
    """One undo preview rendered for a human: what is being reversed and the steps that go."""
    lines = [f"undo: {pv['message']}"]
    if pv["undone"] or pv["held"]:
        lines.append(f"  {pv['undone']} step(s) go" + (f", {pv['held']} held" if pv["held"] else ""))
    if pv["steps"]:
        lines.append(f"  {', '.join(pv['steps'][:6])}" + (" …" if len(pv["steps"]) > 6 else ""))
    return lines


def _undo(repo: str, as_json: bool, *, emit: bool = False) -> int:
    """`ugh undo`: reverse the last mutating operation. A commit ugh made (a save, a take-out, a
    bring-back) is reversed by taking out the ops mined from it, which is a new commit; a `name` or
    `rename` restores the names it changed. A tty sees the preview and confirms first."""
    from ugh.core import oplog
    from ugh.core.lens import DirtyWorkingTreeError
    from ugh.store.gitbind import GitError

    import sys as _sys

    if emit:
        pv = oplog.preview(repo)
        if as_json:
            return _emit_json({"applied": False, **pv})
        for line in _undo_preview_lines(pv):
            print(line)
        return 0

    if not as_json and _sys.stdin.isatty() and _sys.stdout.isatty():
        pv = oplog.preview(repo)
        if pv["kind"] is not None:
            if not pv["ok"]:
                return _fail(pv["message"])
            for line in _undo_preview_lines(pv):
                print(line)
            try:
                reply = input("apply this undo? [y/N] ").strip().lower()
            except EOFError:
                reply = ""
            if reply not in ("y", "yes"):
                print("  aborted — nothing undone.")
                return 1

    try:
        outcome = oplog.undo(repo)
    except (DirtyWorkingTreeError, GitError, ValueError) as e:
        return _fail_json(str(e), as_json)

    if outcome.status == "empty":
        msg = "nothing to undo -- no recorded ideal edits"
        if as_json:
            return _emit_json({"ok": True, "undone": False, "message": msg})
        print(f"✓ {msg}")
        return 0

    if outcome.status in ("commit", "refused"):
        from ugh.core.planner import to_json

        if as_json:
            view = to_json(outcome.plan, applied=outcome.status == "commit")
            return _emit_json({**view, "ok": outcome.status == "commit", "message": outcome.message})
        if outcome.status == "refused":
            return _fail(outcome.message)
        print(f"✓ undo: {outcome.message} — {len(outcome.plan.undone)} step(s) undone"
              + (f", {len(outcome.plan.held)} held" if outcome.plan.held else ""))
        return 0

    # A names snapshot (`name`, `rename`).
    if as_json:
        return _emit_json({"ok": True, "undone": True, "kind": outcome.kind, "message": outcome.message})
    print(f"✓ undo: {outcome.message}")
    return 0
