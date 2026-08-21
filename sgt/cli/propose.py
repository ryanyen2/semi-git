"""`sgt propose` (plan U24, C10, U32): the review object end-to-end.

`sgt propose create [--base REF] [--title "..."] [--description "..."]` captures the current ideal's
Δ over `REF`'s committed ideal as a committed, immutable review object (rejecting a Δ that forks the
base); `sgt propose status <id>` reports staleness by re-union (current / clean-reunion / fork);
`sgt propose land <id> [--subset <feature-id|label> ...]` advances the base branch by the U23 CAS
(refusing a stale-forked proposal), landing only the named delta features when `--subset` is given
(refusing a subset that omits a feature another chosen feature requires, naming it); `sgt propose
render <id> --github` emits a suggested branch name and a PR body in plain markdown a reviewer
without sgt can act on; `sgt propose publish <id> [--remote origin]` pushes that branch and
creates or updates a GitHub PR from the same rendering via `gh`. `--json` gives the canonical
`sgt.api` projection throughout.
"""

from __future__ import annotations

from ._common import _emit_json, _fail, _fail_json

_USAGE = ('usage: sgt propose create [--base REF] [--title "..."] [--description "..."] [--json] | '
          'sgt propose status <id> [--checklist] [--json] | '
          'sgt propose land <id> [--subset <feature-id|label> ...] [--json] | '
          'sgt propose render <id> --github [--json] | '
          'sgt propose publish <id> [--remote origin] [--json]')


def register(subs, parent) -> None:
    p = subs.add_parser("propose", parents=[parent])
    p.add_argument("sub", nargs="?")
    p.add_argument("id", nargs="?")
    p.add_argument("--base", default="main")
    p.add_argument("--title")
    p.add_argument("--description")
    p.add_argument("--github", action="store_true")
    p.add_argument("--subset", nargs="*", default=None)
    p.add_argument("--remote", default="origin")
    p.add_argument("--checklist", action="store_true")
    p.set_defaults(func=_cmd_propose)


def _cmd_propose(args) -> int:
    return _propose(".", args.sub, args.id, args.base, args.title, args.description,
                    args.github, args.subset, args.remote, args.checklist, args.as_json)


def _propose(repo: str, sub: str | None, pid: str | None, base: str, title: str | None,
             description: str | None, github: bool, subset: list[str] | None, remote: str,
             checklist: bool, as_json: bool) -> int:
    from sgt.core import propose

    if sub not in ("create", "status", "land", "render", "publish"):
        print(_USAGE)
        return 2

    if sub == "create":
        return _create(repo, propose, base, title, description, as_json)

    if pid is None:
        print(_USAGE)
        return 2
    if sub == "status":
        return _status(repo, pid, checklist, as_json)
    if sub == "render":
        return _render(repo, propose, pid, github, as_json)
    if sub == "publish":
        return _publish(repo, propose, pid, remote, as_json)
    return _land(repo, propose, pid, subset, as_json)


def _create(repo, propose, base, title, description, as_json) -> int:
    from sgt.api import proposal_view

    try:
        p = propose.create(repo, base_ref=base, title=title, description=description)
    except ValueError as e:
        return _fail_json(str(e), as_json)
    if as_json:
        return _emit_json(proposal_view(repo, p.id))
    print(f"✓ proposal {p.id} (base {p.base_ref}, +{len(p.delta_ids)} op(s), "
          f"{len(p.feature_delta)} feature(s))")
    return 0


def _status(repo, pid, checklist, as_json) -> int:
    from sgt.api import proposal_review_view, proposal_view

    view = proposal_review_view(repo, pid) if checklist else proposal_view(repo, pid)
    if "error" in view:
        return _emit_json(view) if as_json else _fail(view["error"])
    if as_json:
        return _emit_json(view)
    st = view["status"]
    print(f"proposal {pid}: {st['state']}")
    if st.get("note"):
        print(f"  {st['note']}")
    for f in st["forks"]:
        print(f"  ⚠ {f['symbol']}: {f['remedy']}")
    return 0


def _render(repo, propose, pid, github, as_json) -> int:
    from sgt.api import proposal_view

    if not github:
        print("usage: sgt propose render <id> --github [--json]")
        return 2
    view = proposal_view(repo, pid)
    if "error" in view:
        return _emit_json(view) if as_json else _fail(view["error"])
    rendered = propose.render_github(view)
    if as_json:
        return _emit_json(rendered)
    print(f"branch: {rendered['branch']}")
    print(f"title:  {rendered['pr_title']}\n")
    print(rendered["pr_body"])
    return 0


def _land(repo, propose, pid, subset, as_json) -> int:
    import sys

    from sgt.api import land_view
    from sgt.core.lens import DirtyWorkingTreeError
    from sgt.core.sync import MinerVersionMismatch
    from sgt.store.gitbind import GitError

    accept_ids = None
    if subset is not None:
        accept_ids, err = _resolve_subset(repo, pid, subset)
        if err is not None:
            return _fail_json(err, as_json)

    # The confirm step. On an interactive tty (not --json) `propose land` shows the same land
    # feedforward -- the ops it would advance the base branch by, a stale-fork blocker, the oracle
    # gate -- scoped to this proposal's Δ, and lets the user back out. `--json`/non-tty apply
    # immediately (the machine/CI contract): no new args, only what the user sees interactively.
    if not as_json and sys.stdin.isatty() and sys.stdout.isatty():
        from sgt.api import proposal_land_preview_view

        from ._common import confirm_collab

        try:
            pview = proposal_land_preview_view(repo, pid, accept_ids=accept_ids)
        except (DirtyWorkingTreeError, GitError, ValueError, MinerVersionMismatch) as e:
            return _fail_json(str(e), as_json)
        if not confirm_collab(pview, f"land proposal {pid} onto {pview['target']}?"):
            print("  aborted — nothing landed.")
            return 1

    try:
        report = propose.land(repo, pid, accept_ids=accept_ids)
    except (DirtyWorkingTreeError, GitError, ValueError, MinerVersionMismatch) as e:
        return _fail_json(str(e), as_json)

    view = land_view(report)
    if as_json:
        return _emit_json({"ok": report.landed, **view})
    if report.landed:
        print(f"✓ propose land {report.branch}: {report.land_sha[:12]} (+{report.ops_added} op(s))")
        return 0
    print(f"✗ propose land {report.branch}: {report.blocked_reason}")
    for sym, a, b in report.forks:
        print(f"    {sym}: sgt advanced merge-op {a[:8]} {b[:8]}")
    return 1


def _resolve_subset(repo, pid, subset: list[str]) -> tuple[list[str] | None, str | None]:
    """Resolve `--subset`'s feature-id-or-label refs against `proposal_review_view`'s checklist:
    `(accept_ids, None)` on success, `(None, error_message)` if a ref doesn't match a delta
    feature, or a chosen feature omits a feature it `requires` (named in the message)."""
    from sgt.api import proposal_review_view

    view = proposal_review_view(repo, pid)
    if "error" in view:
        return None, view["error"]

    by_ref = {}
    label_by_id = {}
    for f in view["feature_checklist"]:
        by_ref[f["feature_id"]] = f
        by_ref[f["label"]] = f
        label_by_id[f["feature_id"]] = f["label"]

    chosen = []
    for ref in subset:
        f = by_ref.get(ref)
        if f is None:
            return None, f"no feature {ref!r} in this proposal's delta"
        chosen.append(f)

    chosen_ids = {f["feature_id"] for f in chosen}
    for f in chosen:
        missing = [r for r in f["requires"] if r not in chosen_ids]
        if missing:
            names = ", ".join(label_by_id.get(r, r) for r in missing)
            return None, f"{f['label']!r} requires {names} -- include it in --subset too"

    accept_ids = sorted({op_id for f in chosen for op_id in f["op_ids"]})
    return accept_ids, None


def _publish(repo, propose, pid, remote, as_json) -> int:
    """`sgt propose publish <id> [--remote origin]` (plan U32, D7): push the proposal's rendered
    PR branch (`GitBinding.push_head_as`) and create or update (if a PR already exists for that
    branch) a GitHub PR via `gh`, from `propose.render_github`'s title/body -- so a later render
    (Δ changed, a claim landed) updates the PR in place rather than duplicating it."""
    import json
    import shutil
    import subprocess

    from sgt.api import proposal_view
    from sgt.store.gitbind import GitBinding, GitError

    if shutil.which("gh") is None:
        msg = "the `gh` CLI is required for `sgt propose publish` (https://cli.github.com) -- not on PATH"
        return _fail_json(msg, as_json)

    view = proposal_view(repo, pid)
    if "error" in view:
        return _emit_json(view) if as_json else _fail(view["error"])
    rendered = propose.render_github(view)
    branch = rendered["branch"]

    # Phase 1.2 push ordering (§D): the PR branch commit's `Sgt-Op:` trailers name ops that live only
    # on `refs/sgt/state`, so publish + push that ref to success before publishing the branch -- and
    # abort if it can't, so the PR never references ops that aren't durable on the remote.
    from pathlib import Path

    from sgt.core.sync import state_ref as _state_ref

    gb = GitBinding(repo)
    try:
        _state_ref.publish_and_push(gb, Path(repo), remote)
    except _state_ref.StateRefError as e:
        msg = f"could not publish sgt state to {remote} -- PR branch NOT pushed (its ops would be dangling): {e}"
        return _fail_json(msg, as_json)

    try:
        gb.push_head_as(remote, branch)
    except GitError as e:
        return _fail_json(str(e), as_json)

    existing = subprocess.run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
        cwd=repo, capture_output=True, text=True,
    )
    numbers = json.loads(existing.stdout) if existing.returncode == 0 and existing.stdout.strip() else []

    if numbers:
        action = "updated"
        proc = subprocess.run(
            ["gh", "pr", "edit", str(numbers[0]["number"]),
             "--title", rendered["pr_title"], "--body", rendered["pr_body"]],
            cwd=repo, capture_output=True, text=True,
        )
    else:
        action = "created"
        base_branch = view["base_ref"].rsplit("/", 1)[-1]
        proc = subprocess.run(
            ["gh", "pr", "create", "--head", branch, "--base", base_branch,
             "--title", rendered["pr_title"], "--body", rendered["pr_body"]],
            cwd=repo, capture_output=True, text=True,
        )

    if proc.returncode != 0:
        msg = f"gh pr {action} failed: {proc.stderr.strip()}"
        return _fail_json(msg, as_json)

    if as_json:
        return _emit_json({"ok": True, "action": action, "branch": branch, "gh_output": proc.stdout.strip()})
    print(f"✓ propose publish {pid}: PR {action} on branch {branch}")
    if proc.stdout.strip():
        print(proc.stdout.strip())
    return 0
