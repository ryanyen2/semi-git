"""`sgt sync [remote] [branch]` (plan U15, R19/AE4): fetch a teammate's work, union the op
store, reconcile pins/declared-edges/the feature tree, and surface any same-symbol chain fork
(with the `merge-op`/`pin` remedy) instead of doing a textual merge. Later units land `push`/
`forks` alongside it here (plan U20)."""

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

    pf = subs.add_parser("forks", parents=[parent])
    pf.add_argument("symbol", nargs="?")
    pf.set_defaults(func=_cmd_forks)


def _cmd_sync(args) -> int:
    return _sync(".", args.remote, args.branch, args.as_json)


def _cmd_push(args) -> int:
    return _push(".", args.remote, args.branch, args.as_json)


def _cmd_forks(args) -> int:
    return _forks(".", args.symbol, args.as_json)


def _forks(repo: str, symbol: str | None, as_json: bool) -> int:
    """`sgt forks [<symbol>] [--json]` (C4): list the open same-symbol forks a prior sync recorded
    in committed `.sgt/forks.json`, each with the `sgt merge-op` remedy that closes it. Given a
    symbol, instead show that one fork's two tips' folded file content (`api.fork_detail_view`) --
    a resolution UI's per-tip diff, since neither tip is part of any current ideal to diff against."""
    if symbol is not None:
        from sgt.api import fork_detail_view

        view = fork_detail_view(repo, symbol)
        if "error" in view:
            return _fail_json(view["error"], as_json)
        if as_json:
            return _emit_json(view)
        for tip in view["tips"]:
            print(f"  tip {tip['op_id'][:12]}:")
            for path in sorted(tip["files"]):
                print(f"    {path}")
        print(f"  remedy: {view['remedy']}")
        return 0

    from sgt.api import forks_view

    view = forks_view(repo)
    if as_json:
        return _emit_json(view)
    if not view["open"]:
        print("✓ no open forks")
        return 0
    print(f"⚠ {view['open']} open fork(s):")
    for rec in view["forks"]:
        print(f"  {rec['symbol']} ({rec['file']}): {rec['remedy']}")
    return 0


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
        return _fail_json(msg, as_json)

    try:
        sha = gb.push(remote, branch)
    except PushRejected as e:
        remedy = f"sgt sync {remote} {branch}"
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


def _land_branch(repo: str, branch: str, as_json: bool) -> int:
    """`sgt land <branch>` (plan U23, C9/LAW-G): advance the shared branch record `refs/heads/<branch>`
    by CAS -- union this session's HEAD onto the branch tip, gate the result oracle-green (LAW-G),
    and compare-and-swap the ref, re-unioning on a lost race. A blocked land (red/absent oracle, an
    open fork, or persistent contention) exits non-zero with the reason and the `sgt merge-op` remedy
    for a fork. (`sgt commit`, U11's staged-rewrite-candidate commit, is a distinct verb -- kept out
    of `land`'s name so "land" only ever means the branch-CAS advance.)"""
    from sgt.core import sync as sync_mod
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.core.sync import MinerVersionMismatch
    from sgt.store.gitbind import GitError

    try:
        report = sync_mod.land(repo, branch=branch)
    except (DirtyWorkingTreeError, GitError, ValueError, MinerVersionMismatch) as e:
        return _fail_json(str(e), as_json)

    from sgt.api import land_view

    view = land_view(report)
    if as_json:
        return _emit_json({"ok": report.landed, **view})

    if report.landed:
        print(f"✓ land {report.branch}: {report.land_sha[:12]} (+{report.ops_added} op(s))")
        if report.attempts > 1:
            print(f"    re-unioned after losing {report.attempts - 1} CAS race(s)")
        return 0

    print(f"✗ land {report.branch}: {report.blocked_reason}")
    for sym, a, b in report.forks:
        print(f"    {sym}: sgt merge-op {a[:8]} {b[:8]}")
    return 1


def _sync(repo: str, remote: str | None, branch: str | None, as_json: bool) -> int:
    from sgt.core import sync as sync_mod
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.core.sync import MinerVersionMismatch
    from sgt.store.gitbind import GitError

    try:
        report = sync_mod.sync(repo, remote=remote, branch=branch)
    except (DirtyWorkingTreeError, GitError, ValueError, MinerVersionMismatch) as e:
        return _fail_json(str(e), as_json)

    from sgt.api import sync_view

    view = sync_view(report)
    up_to_date = report.message == "already up to date"
    if as_json:
        return _emit_json({"ok": report.merge_sha is not None or up_to_date, **view})

    if up_to_date:
        print(f"✓ sync {report.remote}/{report.branch}: already up to date")
        return 0

    # A merge landed (the fork-free part at least). Report it, then loudly surface any open fork --
    # D5's load-bearing loudness: a forked symbol sitting at the common ancestor reads as data loss
    # unless the fork count is prominent (`sgt forks` lists the remedies).
    icon = "✓" if report.merged else "⚠"
    print(f"{icon} sync {report.remote}/{report.branch}: merged {report.merge_sha[:12]}")
    if report.ops_added:
        print(f"    +{report.ops_added} op(s)")
    # R12 loudness: a degraded base or a lost-provenance tip fell back to weaker semantics -- never
    # silent. `merged` above may read as clean, so name the recovery path that was refused.
    if report.base_recovery == "none":
        print("    ⚠ base recovery: none — no witnessed merge-base; used union semantics "
              "(cannot delete work one side removed)")
    if report.theirs_recovery == "none":
        print("    ⚠ theirs' tip has sgt ops but no witnessed trailers/record — re-mine on their "
              "side (`sgt log`) or restore the `Sgt-Op:` trailers, then sync again")
    if report.forks:
        print(f"    ⚠ {len(report.forks)} OPEN FORK(S) — forked symbol(s) sit at the common ancestor "
              f"until resolved:")
        for sym, a, b in report.forks:
            print(f"      {sym}: sgt merge-op {a[:8]} {b[:8]}")
    if report.pin_contradictions:
        print(f"    ⚠ {len(report.pin_contradictions)} pin contradiction(s):")
        for c in report.pin_contradictions:
            print(f"      {c.detail}")
    if report.declared_cycles:
        print(f"    ⚠ {len(report.declared_cycles)} declared-edge cycle(s): {report.declared_cycles}")
    return 1 if report.forks else 0
