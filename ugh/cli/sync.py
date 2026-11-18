"""`ugh sync [remote] [branch]` brings a teammate's branch in by a merge built unit by unit
(`ugh.core.sync`) and merges the names; `ugh push [remote] [branch]` publishes names and prompts,
then pushes the branch, never forcing."""

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

    # Names and prompts go first, so a branch is never published ahead of the names its steps carry.
    from pathlib import Path

    from ugh.core.sync import state_ref as _state_ref

    try:
        _state_ref.publish_and_push(gb, Path(repo), remote)
    except _state_ref.StateRefError as e:
        msg = f"could not publish ugh state (names, prompts) to {remote} -- branch not pushed: {e}"
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
    """The refusal for a dirty tree: the uncommitted files and the remedy."""
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    listing = "".join(f"\n      {line[3:]}" for line in proc.stdout.splitlines() if line[3:].strip())
    return _fail_json("working tree has uncommitted changes -- `ugh save` them first, then re-run"
                      + listing, as_json)


def _sync(repo: str, remote: str | None, branch: str | None, as_json: bool) -> int:
    from ugh.api import sync_view
    from ugh.core import sync
    from ugh.core.lens import DirtyWorkingTreeError
    from ugh.store.gitbind import GitError

    try:
        report = sync.sync(repo, remote=remote, branch=branch)
    except DirtyWorkingTreeError:
        return _dirty_fail(repo, as_json)
    except (GitError, ValueError) as e:
        return _fail_json(str(e), as_json)
    view = sync_view(repo, report)
    if as_json:
        return _emit_json(view)
    moved = "" if report.message == "already up to date" else f" {report.head[:12]}"
    print(f"✓ sync {report.remote}/{report.branch}: {report.message}{moved}")
    if view["choices"]:
        print(f"    ⚠ {len(view['choices'])} definition(s) changed differently on both sides — ours is "
              "in the file, and `ugh save` waits until each is resolved:")
        for address in view["choices"]:
            print(f"      {address}  →  ugh resolve {address}")
    for line in _name_conflict_lines(view["name_conflicts"]):
        print(line)
    return 0


def _name_conflict_lines(conflicts: list[dict]) -> list[str]:
    """Each op both sides relabelled differently: its address and both labels (ours was kept)."""
    if not conflicts:
        return []
    return ([f"    ⚠ {len(conflicts)} step(s) renamed differently on both sides — kept ours:"]
            + [f'      {c["address"]}: ours "{c["ours"]}", theirs "{c["theirs"]}"' for c in conflicts])
