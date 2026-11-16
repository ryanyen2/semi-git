"""`ugh sync [remote] [branch]` (plan U15, R19/AE4): fetch a teammate's work, union the op
store, reconcile pins and the feature tree, and surface any same-symbol chain fork
(with the `ugh resolve` remedy) instead of doing a textual merge. `push` lives alongside it."""

from __future__ import annotations

from ._common import _emit_json, _fail_json


def register(subs, parent) -> None:
    p = subs.add_parser("sync", parents=[parent])
    p.add_argument("remote", nargs="?")
    p.add_argument("branch", nargs="?")
    p.set_defaults(func=_cmd_sync)

    pp = subs.add_parser("push", parents=[parent])
    pp.add_argument("remote", nargs="?")
    pp.add_argument("branch", nargs="?")
    pp.set_defaults(func=_cmd_push)


def _cmd_sync(args) -> int:
    return _sync(".", args.remote, args.branch, args.as_json)


def _cmd_push(args) -> int:
    return _push(".", args.remote, args.branch, args.as_json)


def _push(repo: str, remote: str | None, branch: str | None, as_json: bool) -> int:
    """`ugh push [remote] [branch]` (C7): a non-forcing `git push`. On a non-fast-forward rejection
    (the remote moved), route the user to `ugh sync` rather than ever forcing -- exactly git's own
    contract, so hosting-platform protection rules keep working."""
    from ugh.store.gitbind import GitBinding, GitError, PushRejected

    gb = GitBinding(repo)
    remote = remote or gb.default_remote()
    branch = branch or gb.default_branch()
    if branch is None:
        msg = "no branch to push -- HEAD has no upstream and isn't on a named branch"
        return _fail_json(msg, as_json)

    # Phase 1.2 push ordering (§D): the branch commits' `Sgt-Op:` trailers name ops that live only in
    # `refs/ugh/state`, so publish this clone's local mirror onto that ref and push it to success
    # FIRST -- the ref must be durable before the branch references it. If it cannot be published
    # (contention it can't reconcile), ABORT the whole push: never publish a branch whose ops aren't
    # durable on the remote. `publish_and_push` reconciles a non-ff rejection as a CRDT merge, so this
    # only aborts on a genuinely unrecoverable failure.
    from pathlib import Path

    from ugh.core.sync import state_ref as _state_ref

    try:
        _state_ref.publish_and_push(gb, Path(repo), remote)
    except _state_ref.StateRefError as e:
        msg = f"could not publish ugh state to {remote} -- branch NOT pushed (its ops would be dangling): {e}"
        return _fail_json(msg, as_json)

    try:
        sha = gb.push(remote, branch)
    except PushRejected as e:
        remedy = f"ugh sync {remote} {branch}"
        if as_json:
            return _emit_json({"ok": False, "error": str(e), "rejected": True, "remedy": remedy})
        print(f"✗ push {remote}/{branch}: rejected (the remote moved) -- run `{remedy}`, then push again")
        return 1
    except (GitError, ValueError) as e:
        return _fail_json(str(e), as_json)

    if as_json:
        return _emit_json({"ok": True, "remote": remote, "branch": branch, "pushed_sha": sha})
    print(f"✓ push {remote}/{branch}: {sha[:12]}")
    return 0


def _dirty_fail(repo: str, as_json: bool) -> int:
    """A refusal, not a traceback (F-audit 0.3): a `DirtyWorkingTreeError` at the CLI boundary
    becomes the uncommitted-file list plus the *actual* remedy (`git commit`), not the internal
    "`ugh put` or commit first" message (no such verb) with no files. Read-only: names what git
    itself sees dirty, which is exactly the clean-tree precondition sync refused on."""
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    files = [line[3:] for line in proc.stdout.splitlines() if line[3:].strip()]
    listing = "".join(f"\n      {f}" for f in files)
    msg = (
        "working tree has uncommitted changes -- commit them first (e.g. `git commit -am ...`), "
        "then re-run" + listing
    )
    return _fail_json(msg, as_json)


def _closure_casualties(repo: str, before_ids, forks) -> list[dict]:
    """Ops that left the local ideal when the merge folded in (F12): a teammate's revert can
    closure-remove YOUR fresh dependent work, and today sync says only "✓ merged". Diff the
    recorded ideal before/after the merge (a pure read) and name each casualty with its symbols, so
    the caller can offer a `ugh restore` hint. Fork tips are excluded -- their remedy is `ugh
    resolve`, surfaced separately -- so a restore hint never points at the wrong resolution."""
    from ugh.core import lens
    from ugh.core.store import Store

    fork_tips = {tip for _sym, a, b in forks for tip in (a, b)}
    removed = (before_ids - lens.current_ideal(repo).op_ids) - fork_tips
    if not removed:
        return []
    store = Store(repo)
    rows = []
    for oid in removed:
        op = store.get(oid)
        rows.append({"op": oid, "symbols": sorted(op.footprint) if op is not None else []})
    return sorted(rows, key=lambda r: (r["symbols"], r["op"]))


def _print_casualties(rows: list[dict]) -> None:
    if not rows:
        return
    print(f"    ⚠ {len(rows)} op(s) left your tree in this merge (a teammate's revert can closure-"
          "remove work built on it):")
    for r in rows:
        label = ", ".join(r["symbols"]) or "(no symbol)"
        print(f"      {label} — bring it back: ugh restore {r['op'][:8]}")


def _open_fork_reminder(repo: str) -> None:
    """Repeat the open-fork warning on *every* sync while a fork is open (F12), not only the sync
    that first surfaced it -- an up-to-date sync would otherwise stay silent about a fork the user
    still hasn't resolved. Reads the durable committed `.sgt/forks.json` via `forks_view`."""
    from ugh.api import forks_view

    view = forks_view(repo)
    if not view["open"]:
        return
    print(f"    ⚠ {view['open']} open fork(s) still awaiting resolution:")
    for rec in view["forks"]:
        print(f"      {rec['symbol']} ({rec['file']}): {rec['remedy']}")


def _sync(repo: str, remote: str | None, branch: str | None, as_json: bool) -> int:
    import sys

    from ugh.core import sync as sync_mod
    from ugh.core.lens import DirtyWorkingTreeError
    from ugh.core.sync import MinerVersionMismatch
    from ugh.store.gitbind import GitError

    # The confirm step. On an interactive tty (not --json) `sync` shows the feedforward first -- the
    # ops it would fold in and any fork that would *surface* (a sync never blocks; the fork-free part
    # still merges) -- and lets the user back out before the merge lands. `--json` and a non-tty keep
    # merging immediately (the machine/CI contract): no new args, only what the user sees.
    if not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        from ugh.api import sync_preview_view

        from ._common import confirm_collab

        try:
            pview = sync_preview_view(repo, remote, branch)
        except DirtyWorkingTreeError:
            return _dirty_fail(repo, as_json)
        except (GitError, ValueError, MinerVersionMismatch) as e:
            return _fail_json(str(e), as_json)
        if pview.get("up_to_date"):
            print(f"✓ sync {pview['remote']}/{pview['target']}: already up to date")
            _open_fork_reminder(repo)
            return 0
        if not confirm_collab(pview, f"sync {pview['remote']}/{pview['target']}?"):
            print("  aborted — nothing synced.")
            return 1

    from ugh.core import lens

    before_ids = lens.current_ideal(repo).op_ids  # F12: capture the pre-merge ideal to diff casualties
    try:
        report = sync_mod.sync(repo, remote=remote, branch=branch)
    except DirtyWorkingTreeError:
        return _dirty_fail(repo, as_json)
    except (GitError, ValueError, MinerVersionMismatch) as e:
        return _fail_json(str(e), as_json)

    from ugh.api import sync_view

    view = sync_view(report)
    up_to_date = report.message == "already up to date"
    casualties = [] if up_to_date else _closure_casualties(repo, before_ids, report.forks)
    if as_json:
        return _emit_json(
            {"ok": report.merge_sha is not None or up_to_date, **view, "removed_ops": casualties}
        )

    if up_to_date:
        print(f"✓ sync {report.remote}/{report.branch}: already up to date")
        _open_fork_reminder(repo)
        return 0

    # A merge landed (the fork-free part at least). Report it, then loudly surface any open fork --
    # D5's load-bearing loudness: a forked symbol sitting at the common ancestor reads as data loss
    # unless the fork count is prominent.
    icon = "✓" if report.merged else "⚠"
    print(f"{icon} sync {report.remote}/{report.branch}: merged {report.merge_sha[:12]}")
    if report.ops_added:
        print(f"    +{report.ops_added} op(s)")
    _print_casualties(casualties)  # F12: name the ops the merge/closure removed from the local tree
    # R12 loudness: a degraded base or a lost-provenance tip fell back to weaker semantics -- never
    # silent. `merged` above may read as clean, so name the recovery path that was refused.
    if report.base_recovery == "none":
        print("    ⚠ base recovery: none — no witnessed merge-base; used union semantics "
              "(cannot delete work one side removed)")
    if report.theirs_recovery == "none":
        print("    ⚠ their tip carries ugh ops but no witnessed provenance (trailers/record) — used "
              "union semantics; have them re-commit through ugh so the `Sgt-Op:` trailers are "
              "stamped, push, then sync again")
    if report.forks:
        print(f"    ⚠ {len(report.forks)} OPEN FORK(S) — forked symbol(s) sit at the common ancestor "
              f"until resolved:")
        for sym, _a, _b in report.forks:
            print(f"      {sym}: ugh resolve {sym}")
    if report.pin_contradictions:
        print(f"    ⚠ {len(report.pin_contradictions)} pin contradiction(s):")
        for c in report.pin_contradictions:
            print(f"      {c.detail}")
    return 1 if report.forks else 0
