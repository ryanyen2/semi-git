"""`sgt sync [remote] [branch]` (plan U15, R19/AE4): fetch a teammate's work, union the op
store, reconcile pins/declared-edges/the feature tree, and surface any same-symbol chain fork
(with the `merge-op`/`pin` remedy) instead of doing a textual merge. Later units land `push`/
`forks` alongside it here (plan U20)."""

from __future__ import annotations

from ._common import _emit_json, _fail


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
    """`sgt push [remote] [branch]` (C7): a non-forcing `git push`. On a non-fast-forward rejection
    (the remote moved), route the user to `sgt sync` rather than ever forcing -- exactly git's own
    contract, so hosting-platform protection rules keep working."""
    from sgt.store.gitbind import GitBinding, GitError, PushRejected

    gb = GitBinding(repo)
    remote = remote or gb.default_remote()
    branch = branch or gb.default_branch()
    if branch is None:
        msg = "no branch to push -- HEAD has no upstream and isn't on a named branch"
        return _emit_json({"ok": False, "error": msg}) if as_json else _fail(msg)

    try:
        sha = gb.push(remote, branch)
    except PushRejected as e:
        remedy = f"sgt sync {remote} {branch}"
        if as_json:
            return _emit_json({"ok": False, "error": str(e), "rejected": True, "remedy": remedy})
        print(f"✗ push {remote}/{branch}: rejected (the remote moved) -- run `{remedy}`, then push again")
        return 1
    except (GitError, ValueError) as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _fail(str(e))

    if as_json:
        return _emit_json({"ok": True, "remote": remote, "branch": branch, "pushed_sha": sha})
    print(f"✓ push {remote}/{branch}: {sha[:12]}")
    return 0


def _sync(repo: str, remote: str | None, branch: str | None, as_json: bool) -> int:
    from sgt.core import sync as sync_mod
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.core.sync import MinerVersionMismatch
    from sgt.store.gitbind import GitError

    try:
        report = sync_mod.sync(repo, remote=remote, branch=branch)
    except (DirtyWorkingTreeError, GitError, ValueError, MinerVersionMismatch) as e:
        return _emit_json({"ok": False, "error": str(e)}) if as_json else _fail(str(e))

    from sgt.api import sync_view

    view = sync_view(report)
    if as_json:
        return _emit_json({"ok": report.merged or report.message == "already up to date", **view})

    if not report.merged:
        icon = "✓" if report.message == "already up to date" else "✗"
        print(f"{icon} sync {report.remote}/{report.branch}: {report.message}")
        return 0 if report.message == "already up to date" else 1

    print(f"✓ sync {report.remote}/{report.branch}: merged {report.merge_sha[:12]}")
    if report.ops_added:
        print(f"    +{report.ops_added} op(s)")
    if report.pin_contradictions:
        print(f"    ⚠ {len(report.pin_contradictions)} pin contradiction(s):")
        for c in report.pin_contradictions:
            print(f"      {c.detail}")
    if report.declared_cycles:
        print(f"    ⚠ {len(report.declared_cycles)} declared-edge cycle(s): {report.declared_cycles}")
    return 0
