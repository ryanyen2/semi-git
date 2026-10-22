"""`ugh session`: named scratch-tree lifecycle for agentic sessions (plan U30, D5).

A session is a thin wrapper around a real `git worktree` -- an isolated checkout on its own
branch, sharing the main repo's object store -- plus a bookkeeping record in
`.sgt/local/sessions.json` (name, branch, scratch path, the branch it will land onto, the
op-ids present at that base, owning pid, start time). D5 rejected a session daemon: there is no
background process. `start`/`land`/`gc` are each one call; the only thing that ever loops is the
CLI's own `--watch` poll, and only for as long as it is asked to.

Provenance flow: a session's newly-minted ops get `Attribution.session` stamped on `land`, once
the landing commit is mined into the *main* repo's store -- the same append-only, id-excluded
enrichment U14's `ugh.loop.match._stamp_session` already uses for its own, unrelated notion of
"session" (a plan-intake session). The two concepts share the convention, not the code.

Owning-pid liveness (the crash-vs-leak distinction `gc` needs): `start` records Claude Code's
own pid (`CLAUDE_PID`) when it runs under Claude Code, else `os.getppid()` -- the invoking shell,
since ugh's own CLI process is transient and its pid means nothing by the time anyone checks. `gc` treats a session as dead once `os.kill(pid, 0)`
raises `ProcessLookupError` (POSIX, stdlib, no new dependency).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from ugh.core import lens, opindex
from ugh.core.op import Attribution
from ugh.core.store import Store
from ugh.core.sync import land as _sync_land
from ugh.state import load_json, save_json
from ugh.store.gitbind import GitBinding, GitError

_ARTIFACT = "sessions"


class SessionError(Exception):
    """A session verb was asked to do something the current session records don't support --
    unknown name, name collision, or a scratch path that already exists on disk."""


@dataclass(frozen=True)
class Session:
    name: str
    branch: str  # the scratch tree's own branch, `ugh-session/<name>`
    scratch: str  # absolute path to the worktree
    target_branch: str  # the branch `land` advances
    base_ref: str  # the sha the scratch tree was checked out from
    base_op_ids: tuple[str, ...]  # the ideal at base_ref -- lets `land`/`status` diff "new since start"
    owner_pid: int
    started_at: float
    claude_session_id: str | None = None  # the Claude Code session that started it, if any
    started_by: str | None = None  # "claude" (CLAUDE_PID was set at start) | "terminal"; None = older record


def _load(repo) -> dict[str, Session]:
    raw = load_json(repo, _ARTIFACT, {})
    return {
        name: Session(
            name=name, branch=body["branch"], scratch=body["scratch"],
            target_branch=body["target_branch"], base_ref=body["base_ref"],
            base_op_ids=tuple(body["base_op_ids"]), owner_pid=body["owner_pid"],
            started_at=body["started_at"], claude_session_id=body.get("claude_session_id"),
            started_by=body.get("started_by"),
        )
        for name, body in raw.items()
    }


def _save(repo, sessions: dict[str, Session]) -> None:
    save_json(repo, _ARTIFACT, {
        s.name: {
            "branch": s.branch, "scratch": s.scratch, "target_branch": s.target_branch,
            "base_ref": s.base_ref, "base_op_ids": list(s.base_op_ids),
            "owner_pid": s.owner_pid, "started_at": s.started_at,
            "claude_session_id": s.claude_session_id, "started_by": s.started_by,
        }
        for s in sessions.values()
    })


def list_sessions(repo) -> tuple[Session, ...]:
    return tuple(sorted(_load(repo).values(), key=lambda s: s.name))


def _require(repo, name: str) -> Session:
    session = _load(repo).get(name)
    if session is None:
        raise SessionError(f"no such session {name!r} -- `ugh session status` lists active ones")
    return session


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else -- still alive
    return True


def start(repo, name: str, base: str | None = None, task: str | None = None) -> Session:
    """Materialize a fresh `git worktree` off `base` (a branch name; default: the repo's current
    branch) onto a new `ugh-session/<name>` branch, and record it. Refuses a name collision --
    `ugh session land`/`gc` the existing one first, same as git refuses a duplicate worktree.

    `task`, if given, is the prompt/intent the agent was started with -- recorded in the intent
    overlay's prompt sidecar (plan U3/KTD5) keyed by this session's `name`, the same key `land`
    later stamps onto `Attribution.session`. Optional: a session started with no known task simply
    has no prompt recorded, same as any other unwitnessed provenance key."""
    from ugh import state

    sessions = _load(repo)
    if name in sessions:
        raise SessionError(f"session {name!r} already exists -- `ugh session land` or `gc` it first")

    gb = GitBinding(repo)
    if base is None:
        ref_name = gb.symbolic_ref()
        if ref_name is None:
            raise SessionError("ugh session start requires a branch checked out (HEAD is detached); pass --base")
        target_branch = ref_name.rsplit("/", 1)[-1]
    else:
        target_branch = base
    base_sha = gb.rev_parse(f"refs/heads/{target_branch}")
    if base_sha is None:
        raise SessionError(f"no such branch {target_branch!r}")

    scratch = state.subdir(repo, "local", "sessions", name)
    if scratch.exists():
        raise SessionError(f"scratch path {scratch} already exists -- run `ugh session gc` first")
    scratch.parent.mkdir(parents=True, exist_ok=True)

    branch = f"ugh-session/{name}"
    gb.worktree_add(scratch, branch, base_sha)

    base_ideal = lens.ideal_for_ref(repo, base_sha)
    session = Session(
        name=name, branch=branch, scratch=str(scratch), target_branch=target_branch,
        base_ref=base_sha, base_op_ids=tuple(sorted(base_ideal.op_ids)),
        # An agent runs `ugh session start` from a tool shell that exits as soon as the command
        # does, so `getppid()` read dead within milliseconds. Claude Code exports its own pid
        # (`CLAUDE_PID`, the long-lived `claude` process each tool shell is a child of) and its
        # session id (`CLAUDE_CODE_SESSION_ID`, the key a plan records as `claude_session_id`).
        owner_pid=int(os.environ.get("CLAUDE_PID") or os.getppid()), started_at=time.time(),
        claude_session_id=os.environ.get("CLAUDE_CODE_SESSION_ID") or None,
        started_by="claude" if os.environ.get("CLAUDE_PID") else "terminal",
    )
    sessions[name] = session
    _save(repo, sessions)

    if task:
        from ugh.intent.prompts import record_prompt
        from ugh.intent.turns import record_turn

        record_prompt(repo, name, task)
        record_turn(repo, key=name, key_kind="session", actor="human", channel="cli",
                    text=task, ts=session.started_at)

    return session


def new_op_ids(session: Session) -> frozenset[str]:
    """The ops this session's scratch tree has added since it started -- mine-on-contact first
    to absorb any committed-but-not-yet-mined work, then diff against the recorded base.

    The diff alone is not enough. The scratch tree re-mines the shared history into its own
    store, and an ideal is not a function of the code: after a `ugh revert` on the target branch
    the main ideal holds the verb's authored ops while the scratch holds what mining that commit
    yields, so the diff named ops nobody in the session wrote (6 after one revert on a 12-commit
    demo), and `land` stamped the session's name onto the main store's copies of them. An op is
    the session's only when no base commit alone witnesses it: provenance in a session commit, or
    none at all (the scratch's uncommitted work, or an op a verb minted inside the scratch)."""
    ideal = lens.get(session.scratch)
    commits = {sha for sha, _parent, _subject in GitBinding(session.scratch).history(since=session.base_ref)}
    return _since_base(session.scratch, ideal.op_ids, session.base_op_ids, commits)


def target_op_ids(repo, session: Session) -> frozenset[str]:
    """The ops `session.target_branch` has gained since the session's base: its ideal now, read
    the way `start` read the base (`lens.ideal_for_ref`, from the main store), minus `base_op_ids`,
    under the same provenance rule as `new_op_ids`. Empty when the branch has not moved. A pure
    read: never mines, so it sees what the branch's commits record, not a dirty main tree."""
    gb = GitBinding(repo)
    tip = gb.rev_parse(f"refs/heads/{session.target_branch}")
    if tip is None or tip == session.base_ref:
        return frozenset()
    commits = {sha for sha, _parent, _subject in gb.history(since=session.base_ref, target=tip)}
    return _since_base(repo, lens.ideal_for_ref(repo, tip).op_ids, session.base_op_ids, commits)


def _since_base(repo, ideal_ids, base_ids, commits) -> frozenset[str]:
    """`ideal_ids` minus `base_ids`, keeping an op only when its provenance names one of `commits`
    (the commits since the base) or is empty (uncommitted work, or an op a verb minted)."""
    by_id = {op.id: op for op in opindex.index_ops(repo)}
    return frozenset(
        oid for oid in frozenset(ideal_ids) - set(base_ids)
        if oid in by_id and (not by_id[oid].provenance or commits.intersection(by_id[oid].provenance))
    )


def footprint(session: Session, ids: frozenset[str] | None = None) -> frozenset[str]:
    """The symbols touched by `new_op_ids(session)` -- the footprint a sibling session's own new
    ops might collide with (the early-fork warning's input). `ids` is that set when the caller
    already has it. Reads the footprint-only index, never the ops' images, so the cost is one
    pass over a memoised parse instead of decoding the scratch's whole store."""
    ids = new_op_ids(session) if ids is None else ids
    return frozenset(sym for op in opindex.index_ops(session.scratch) if op.id in ids for sym in op.footprint)


def overlaps(repo, new_ids: dict[str, frozenset[str]] | None = None) -> tuple[dict, ...]:
    """Every pair of live sessions whose new-op footprints share a symbol -- reported, never
    blocked (S6/D5's early *warning*, not a lock): `{"a", "b", "symbols"}` per colliding pair.
    `new_ids` (session name -> `new_op_ids`) spares a caller that has them a second mine-on-contact
    per session, which was most of `sessions_view`'s cost."""
    sessions = list_sessions(repo)
    new_ids = new_ids or {}
    prints = {s.name: footprint(s, new_ids.get(s.name)) for s in sessions}
    found = []
    for i, a in enumerate(sessions):
        for b in sessions[i + 1:]:
            shared = prints[a.name] & prints[b.name]
            if shared:
                found.append({"a": a.name, "b": b.name, "symbols": sorted(shared)})
    return tuple(found)


def land(repo, name: str):
    """Land a session's work: diff its scratch tree's ideal against its recorded base to find
    the ops it minted, advance `target_branch` by the U23 CAS land (`ugh.core.sync.land`) run
    *against the scratch tree* (worktrees share refs, so the CAS is against the one shared
    branch record regardless of which worktree issues it), then -- once the landing commit is
    mined into the *main* repo -- stamp `session=name` onto each new op's structured attribution
    there (the copy everyone else's tooling actually reads). On success, the scratch worktree is
    removed and the session record dropped; a refused land (fork/red oracle/contention) leaves
    both in place so the agent can fix and retry."""
    session = _require(repo, name)
    ids = new_op_ids(session)
    report = _sync_land(session.scratch, branch=session.target_branch)
    if not report.landed:
        return report

    lens.get(repo)  # mine-on-contact in the main repo: absorb the just-landed commit (R9)
    store = Store(repo)
    for op_id in ids:
        op = store.get(op_id)
        if op is None or not op.provenance:
            continue
        store.attribute(op_id, tuple(Attribution(sha=sha, session=name) for sha in op.provenance))

    gb = GitBinding(repo)
    try:
        gb.worktree_remove(session.scratch, force=True)
    except GitError:
        pass  # scratch already gone (e.g. manually cleaned up) -- the record drop below still matters
    sessions = _load(repo)
    del sessions[name]
    _save(repo, sessions)
    return report


def pending_work(session: Session) -> tuple[str, ...]:
    """Human-readable reasons a session's scratch tree still holds work worth keeping:
    `"uncommitted changes"` (dirty source outside `.sgt/`) and/or `"unlanded commits"` (the scratch
    branch has advanced past what's reachable from `target_branch`). Empty when the scratch is clean
    and fully landed (safe to reap) or already gone. This is `gc`'s real safety net: liveness alone
    (F19: `owner_pid` is `getppid()`) can wrongly read a live agent's session as dead."""
    if not os.path.isdir(session.scratch):
        return ()
    gb = GitBinding(session.scratch)
    reasons = []
    if gb.has_dirty_source():
        reasons.append("uncommitted changes")
    head = gb.head()
    if head is not None and not gb.is_ancestor(head, f"refs/heads/{session.target_branch}"):
        reasons.append("unlanded commits")
    return tuple(reasons)


def activity(session: Session) -> tuple[int, bool, float]:
    """What a surface shows about a session beside the process fact (`is_alive`): the commits on its
    branch that its target branch lacks, whether its scratch has uncommitted source, and when it last
    changed -- the newest of its start, those commits, and its dirty files' mtimes. `is_alive` says
    whether the process that started it still exists, which for an agent is the whole Claude Code
    process; this says whether anything is happening. A display read: `gc`'s guard stays
    `pending_work`, which refuses rather than counts when the target branch cannot be resolved."""
    if not os.path.isdir(session.scratch):
        return 0, False, session.started_at
    gb = GitBinding(session.scratch)
    commits = gb.history_meta(since=f"refs/heads/{session.target_branch}")
    dirty = gb.dirty_source_paths()
    times = [session.started_at] + [ts for _sha, _parent, _subject, ts, _bk in commits if ts]
    for path in dirty:
        try:
            times.append(os.stat(os.path.join(session.scratch, path)).st_mtime)
        except OSError:
            pass  # a deleted file has no mtime; the deletion is still uncommitted work
    return len(commits), bool(dirty), max(times)


def gc(repo, force: bool = False) -> tuple[str, ...]:
    """Reap sessions whose recorded pid is no longer alive (a crashed agent's abandoned scratch
    tree) -- `force` reaps every session regardless of liveness. Age alone can't distinguish a
    crash from a long-running agent mid-edit, so liveness is the only signal (D5's pitfall).

    Without `force`, a dead session whose scratch tree still holds uncommitted changes or unlanded
    commits is left in place (0.4/F19 safety): `pending_work` names what would be lost, and the
    caller should surface it. `force` is the explicit "yes, discard it" override that reaps
    regardless -- including live and dirty sessions."""
    sessions = _load(repo)
    targets = list(sessions.values()) if force else [s for s in sessions.values() if not is_alive(s.owner_pid)]
    gb = GitBinding(repo)
    reaped = []
    for s in targets:
        if not force and pending_work(s):
            continue  # would destroy uncommitted/unlanded work -- refuse without --force
        try:
            gb.worktree_remove(s.scratch, force=True)
        except GitError:
            pass
        try:
            gb.delete_branch(s.branch)  # the worktree's own branch goes with it
        except GitError:
            pass
        del sessions[s.name]
        reaped.append(s.name)
    _save(repo, sessions)
    return tuple(sorted(reaped))


def stale_sessions(repo) -> tuple[Session, ...]:
    """Sessions whose owning pid is dead -- leaked scratch trees `fsck` should report, without
    reaping them (that's `gc`'s job, not a read verb's)."""
    return tuple(s for s in list_sessions(repo) if not is_alive(s.owner_pid))


def ops_by_session(repo, name: str) -> frozenset[str]:
    """Every op in the main repo's store carrying `Attribution(session=name)` (plan U31, S7) --
    addressing by provenance. Attribution outlives the session record itself (`land` drops the
    record but the stamped attribution is permanent), so this works for a long-landed session
    exactly as it does right after `land`."""
    return frozenset(
        op.id for op in Store(repo).all_ops() if any(a.session == name for a in op.attribution)
    )
