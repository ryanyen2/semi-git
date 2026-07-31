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

    # D1: best-effort push of the land log ref, so teammates can use it for base recovery too. The
    # branch push above is the one that must succeed; this is advisory transport, not correctness.
    from sgt.core.sync import log as _log
    log_ref = _log.log_ref(branch)
    gb.push_ref(remote, f"{log_ref}:{log_ref}")

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
    import sys

    from sgt.core import sync as sync_mod
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.core.sync import MinerVersionMismatch
    from sgt.store.gitbind import GitError

    # The confirm step. On an interactive tty (not --json) `land` shows the consequence feedforward
    # first -- where your work is going, the fork that blocks it, the oracle gate the confirm runs --
    # in place of applying blind, and lets the user back out. `--json` and a non-tty keep applying
    # immediately (the machine/CI contract): no new args, only what the user sees interactively.
    if not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        from sgt.api import land_preview_view

        from ._common import confirm_collab

        pview = land_preview_view(repo, branch)
        if not confirm_collab(pview, f"land onto {pview['target']}?"):
            print("  aborted — nothing landed.")
            return 1

    try:
        report = sync_mod.land(repo, branch=branch)
    except DirtyWorkingTreeError:
        return _dirty_fail(repo, as_json)
    except (GitError, ValueError, MinerVersionMismatch) as e:
        return _fail_json(str(e), as_json)

    from sgt.api import land_view

    view = land_view(report)
    if as_json:
        return _emit_json({"ok": report.landed, **view})

    if report.landed:
        print(f"✓ land {report.branch}: {report.land_sha[:12]} (+{report.ops_added} op(s))")
        if report.attempts > 1:
            print(f"    re-unioned after losing {report.attempts - 1} CAS race(s)")
        if report.advisory:
            print(f"    ⚠ {report.advisory}")
        return 0

    print(f"✗ land {report.branch}: {report.blocked_reason}")
    for sym, a, b in report.forks:
        print(f"    {sym}: sgt merge-op {a[:8]} {b[:8]}")
    if report.advisory:
        print(f"    ⚠ {report.advisory}")
    return 1


def _dirty_fail(repo: str, as_json: bool) -> int:
    """A refusal, not a traceback (F-audit 0.3): a `DirtyWorkingTreeError` at the CLI boundary
    becomes the uncommitted-file list plus the *actual* remedy (`git commit`), not the internal
    "`sgt put` or commit first" message (no such verb) with no files. Read-only: names what git
    itself sees dirty, which is exactly the clean-tree precondition sync/land refused on."""
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
    the caller can offer a `sgt restore` hint. Fork tips are excluded -- their remedy is `merge-op`,
    surfaced separately -- so a restore hint never points at the wrong resolution."""
    from sgt.core import lens
    from sgt.core.store import Store

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
        print(f"      {label} — bring it back: sgt restore {r['op'][:8]}")


def _open_fork_reminder(repo: str) -> None:
    """Repeat the open-fork warning on *every* sync while a fork is open (F12), not only the sync
    that first surfaced it -- an up-to-date sync would otherwise stay silent about a fork the user
    still hasn't resolved. Reads the durable committed `.sgt/forks.json` via `forks_view`."""
    from sgt.api import forks_view

    view = forks_view(repo)
    if not view["open"]:
        return
    print(f"    ⚠ {view['open']} open fork(s) still awaiting resolution:")
    for rec in view["forks"]:
        print(f"      {rec['symbol']} ({rec['file']}): {rec['remedy']}")


def _sync(repo: str, remote: str | None, branch: str | None, as_json: bool) -> int:
    import sys

    from sgt.core import sync as sync_mod
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.core.sync import MinerVersionMismatch
    from sgt.store.gitbind import GitError

    # The confirm step. On an interactive tty (not --json) `sync` shows the feedforward first -- the
    # ops it would fold in and any fork that would *surface* (a sync never blocks; the fork-free part
    # still merges) -- and lets the user back out before the merge lands. `--json` and a non-tty keep
    # merging immediately (the machine/CI contract): no new args, only what the user sees.
    if not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        from sgt.api import sync_preview_view

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

    from sgt.core import lens

    before_ids = lens.current_ideal(repo).op_ids  # F12: capture the pre-merge ideal to diff casualties
    try:
        report = sync_mod.sync(repo, remote=remote, branch=branch)
    except DirtyWorkingTreeError:
        return _dirty_fail(repo, as_json)
    except (GitError, ValueError, MinerVersionMismatch) as e:
        return _fail_json(str(e), as_json)

    from sgt.api import sync_view

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
    # unless the fork count is prominent (`sgt forks` lists the remedies).
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
        print("    ⚠ their tip carries sgt ops but no witnessed provenance (trailers/record) — used "
              "union semantics; have them re-commit through sgt so the `Sgt-Op:` trailers are "
              "stamped, push, then sync again")
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
