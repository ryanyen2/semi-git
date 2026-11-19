"""`save` and `undo`, and the table of git subcommands `ugh git` refuses.

`save` commits the working tree and mines the commit like any other; `undo` reverses the last
operation in the operation log (`oplog.undo`). `REFUSALS` maps each tree-mutating git subcommand to
the ugh verb that does its job; `--force` runs git anyway, and ugh mines the result on next contact.
"""

from __future__ import annotations

import argparse

from ._common import _emit_json, _fail, _fail_json, observed_text

REFUSALS: dict[str, str] = {
    "restore": "ugh restore <ref>",
    "pull": "ugh sync [remote] [branch]",
    "merge": "ugh sync [remote] [branch]",
    "reset": "ugh undo  (or `ugh revert <ref>` to take steps out)",
    "rebase": "ugh sync  (history is a mined op DAG; ugh has no rebase)",
    "revert": "ugh revert <ref>",
    "cherry-pick": "ugh restore <ref>",
    "stash": "ugh save  (a dirty tree is edits not yet saved)",
    "am": "git apply, then `ugh save` to record the change as ops",
}


def git_remedy(subcommand: str) -> str | None:
    """The ugh remedy for a refused git subcommand, or None if `ugh git <subcommand>` passes
    through."""
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
    # hidden machine dry-run, as on revert and restore: report the next undo, apply nothing
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
    """Commit the working tree as git would (`git add -A`), mine the commit, name its steps when
    `as_label` is given, and run the project's check to record what it observed on the commit:
    `{ok, saved, commit, steps, observed}` or `{ok: False, error}`. Each step says whether the check
    ran it. `observed` is None without a check, `{not_recorded}` when recording failed, else
    `observe.of_commit`. Refused during an in-progress git merge, cherry-pick or revert, and while a
    two-version choice is pending."""
    import time

    from ugh import names
    from ugh.address import Record
    from ugh.core import lens, observe, opindex, oplog, planner, sync
    from ugh.core.op import step_address
    from ugh.intent.manifest import load_manifests, record_manifest
    from ugh.store.gitbind import GitBinding, GitError

    from ._common import busy

    gb = GitBinding(repo)
    verb = lens.merge_in_progress(gb)
    if verb is not None:
        return {"ok": False, "error": f"in-progress git {verb} -- finish or abort the git {verb} first "
                                      f"(git {verb} --continue / git {verb} --abort); ugh save won't "
                                      "commit conflict markers"}
    pending = sync.choices(repo)
    if pending:
        named = "; ".join(f"{c.address} (ugh resolve {c.address})" for c in pending)
        return {"ok": False, "choices": [c.address for c in pending],
                "error": f"a sync left {len(pending)} definition(s) with two versions -- resolve each "
                         f"before saving: {named}"}
    if gb.is_clean():
        return {"ok": True, "saved": False, "message": "nothing to save -- the working tree matches HEAD"}
    prev_head = gb.head()
    with busy("recording the save…"):
        try:
            sha = lens.commit(repo, message or "ugh save")
        except GitError as e:
            return {"ok": False, "error": str(e)}
        oplog.append(repo, {"kind": "commit", "sha": sha, "verb": "save", "target": message or ""})
    if message:  # the user's own words about this save, keyed by its commit
        from ugh.intent.turns import record_turn

        record_turn(repo, key=sha, key_kind="sha", actor="human", channel="cli", text=message)
    # close the capture window: the turns and activity since the previous save become its manifest
    prev_ts = gb.commit_times().get(prev_head) if prev_head and not load_manifests(repo) else None
    record_manifest(repo, sha=sha, ops=[op for op in opindex.index_ops(repo) if sha in op.provenance],
                    end=time.time(), prev_save_ts=prev_ts)
    rec = Record(repo)
    steps = rec.ctx.of_commit.get(sha, [])
    if as_label:
        names.name(repo, rec.ops(steps), as_label)
        rec.names = names.load(repo)
    try:
        with busy("running the check…"):
            obs = observe.check(repo)
        if obs is not None:
            observe.record(repo, sha, obs)
        observed = obs and observe.of_commit(repo, sha, rec.ctx.parent[sha])
    except OSError as e:
        obs, observed = None, {"not_recorded": str(e)}
    rows = [{**rec.step(st), "ran": planner.ran(rec.ctx, obs, st, sha)}
            for st in sorted(steps, key=lambda st: step_address(st.unit, st.sha))]
    return {"ok": True, "saved": True, "commit": sha, "steps": rows, "observed": observed}


def _observed_line(out: dict) -> str:
    """What the check observed on a save: its verdict, its output against the parent's and how many
    of the save's steps it ran."""
    seen = out["observed"]
    if "not_recorded" in seen:
        return f"observation not recorded: {seen['not_recorded']}"
    parts = [observed_text(seen)]
    known = [st["ran"] for st in out["steps"] if st["ran"] is not None]
    if not seen["lines_recorded"]:
        parts.append("lines not recorded")
    elif known:
        parts.append(f"the check ran {sum(known)} of {len(known)} step(s)")
    return "; ".join(parts)


def _render_save(out: dict, message: str | None, as_label: str | None) -> None:
    if not out["saved"]:
        print(f"✓ {out['message']}")
        if as_label:
            print("  --as names a save's steps; nothing was saved, so nothing to name.")
        return
    title = f' "{message}"' if message else ""
    print(f"✓ save {out['commit'][:7]}{title}")
    for st in out["steps"]:
        print(f"    {st['kind']:<7} {st['address']}" + (f"  [{st['name']}]" if st["name"] else ""))
    if out["observed"] is not None:
        print(f"  {_observed_line(out)}")
    print("  ⤺ undo this save, and take its edits back off the working copy:  ugh undo")


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
