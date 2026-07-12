"""`sgt session`: named scratch-tree lifecycle for agentic sessions (plan U30, D5).

A session is a thin wrapper around a real `git worktree` -- an isolated checkout on its own
branch, sharing the main repo's object store -- plus a bookkeeping record in
`.sgt/local/sessions.json` (name, branch, scratch path, the branch it will land onto, the
op-ids present at that base, owning pid, start time). D5 rejected a session daemon: there is no
background process. `start`/`land`/`gc` are each one call; the only thing that ever loops is the
CLI's own `--watch` poll, and only for as long as it is asked to.

Provenance flow: a session's newly-minted ops get `Attribution.session` stamped on `land`, once
the landing commit is mined into the *main* repo's store -- the same append-only, id-excluded
enrichment U14's `sgt.loop.match._stamp_session` already uses for its own, unrelated notion of
"session" (a plan-intake session). The two concepts share the convention, not the code.

Owning-pid liveness (the crash-vs-leak distinction `gc` needs): `start` records
`os.getppid()` -- the invoking agent/shell, since sgt's own CLI process is transient and its pid
means nothing by the time anyone checks. `gc` treats a session as dead once `os.kill(pid, 0)`
raises `ProcessLookupError` (POSIX, stdlib, no new dependency).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from sgt.core import lens
from sgt.core.op import Attribution
from sgt.core.store import Store
from sgt.core.sync import land as _sync_land
from sgt.state import load_json, save_json
from sgt.store.gitbind import GitBinding, GitError

_ARTIFACT = "sessions"


class SessionError(Exception):
    """A session verb was asked to do something the current session records don't support --
    unknown name, name collision, or a scratch path that already exists on disk."""


@dataclass(frozen=True)
class Session:
    name: str
    branch: str  # the scratch tree's own branch, `sgt-session/<name>`
    scratch: str  # absolute path to the worktree
    target_branch: str  # the branch `land` advances
    base_ref: str  # the sha the scratch tree was checked out from
    base_op_ids: tuple[str, ...]  # the ideal at base_ref -- lets `land`/`status` diff "new since start"
    owner_pid: int
    started_at: float


def _load(repo) -> dict[str, Session]:
    raw = load_json(repo, _ARTIFACT, {})
    return {
        name: Session(
            name=name, branch=body["branch"], scratch=body["scratch"],
            target_branch=body["target_branch"], base_ref=body["base_ref"],
            base_op_ids=tuple(body["base_op_ids"]), owner_pid=body["owner_pid"],
            started_at=body["started_at"],
        )
        for name, body in raw.items()
    }


def _save(repo, sessions: dict[str, Session]) -> None:
    save_json(repo, _ARTIFACT, {
        s.name: {
            "branch": s.branch, "scratch": s.scratch, "target_branch": s.target_branch,
            "base_ref": s.base_ref, "base_op_ids": list(s.base_op_ids),
            "owner_pid": s.owner_pid, "started_at": s.started_at,
        }
        for s in sessions.values()
    })


def list_sessions(repo) -> tuple[Session, ...]:
    return tuple(sorted(_load(repo).values(), key=lambda s: s.name))


def _require(repo, name: str) -> Session:
    session = _load(repo).get(name)
    if session is None:
        raise SessionError(f"no such session {name!r} -- `sgt session status` lists active ones")
    return session


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else -- still alive
    return True


def start(repo, name: str, base: str | None = None) -> Session:
    """Materialize a fresh `git worktree` off `base` (a branch name; default: the repo's current
    branch) onto a new `sgt-session/<name>` branch, and record it. Refuses a name collision --
    `sgt session land`/`gc` the existing one first, same as git refuses a duplicate worktree."""
    from sgt import state

    sessions = _load(repo)
    if name in sessions:
        raise SessionError(f"session {name!r} already exists -- `sgt session land` or `gc` it first")

    gb = GitBinding(repo)
    if base is None:
        ref_name = gb.symbolic_ref()
        if ref_name is None:
            raise SessionError("sgt session start requires a branch checked out (HEAD is detached); pass --base")
        target_branch = ref_name.rsplit("/", 1)[-1]
    else:
        target_branch = base
    base_sha = gb.rev_parse(f"refs/heads/{target_branch}")
    if base_sha is None:
        raise SessionError(f"no such branch {target_branch!r}")

    scratch = state.subdir(repo, "local", "sessions", name)
    if scratch.exists():
        raise SessionError(f"scratch path {scratch} already exists -- run `sgt session gc` first")
    scratch.parent.mkdir(parents=True, exist_ok=True)

    branch = f"sgt-session/{name}"
    gb.worktree_add(scratch, branch, base_sha)

    base_ideal = lens.ideal_for_ref(repo, base_sha)
    session = Session(
        name=name, branch=branch, scratch=str(scratch), target_branch=target_branch,
        base_ref=base_sha, base_op_ids=tuple(sorted(base_ideal.op_ids)),
        owner_pid=os.getppid(), started_at=time.time(),
    )
    sessions[name] = session
    _save(repo, sessions)
    return session


def new_op_ids(session: Session) -> frozenset[str]:
    """The ops this session's scratch tree has added since it started -- mine-on-contact first
    to absorb any committed-but-not-yet-mined work, then diff against the recorded base."""
    ideal = lens.get(session.scratch)
    return frozenset(ideal.op_ids) - set(session.base_op_ids)


def footprint(session: Session) -> frozenset[str]:
    """The symbols touched by `new_op_ids(session)` -- the footprint a sibling session's own new
    ops might collide with (the early-fork warning's input)."""
    store = Store(session.scratch)
    by_id = {op.id: op for op in store.all_ops()}
    return frozenset(
        sym for op_id in new_op_ids(session) if op_id in by_id for sym in by_id[op_id].footprint
    )


def overlaps(repo) -> tuple[dict, ...]:
    """Every pair of live sessions whose new-op footprints share a symbol -- reported, never
    blocked (S6/D5's early *warning*, not a lock): `{"a", "b", "symbols"}` per colliding pair."""
    sessions = list_sessions(repo)
    prints = {s.name: footprint(s) for s in sessions}
    found = []
    for i, a in enumerate(sessions):
        for b in sessions[i + 1:]:
            shared = prints[a.name] & prints[b.name]
            if shared:
                found.append({"a": a.name, "b": b.name, "symbols": sorted(shared)})
    return tuple(found)


def land(repo, name: str):
    """Land a session's work: diff its scratch tree's ideal against its recorded base to find
    the ops it minted, advance `target_branch` by the U23 CAS land (`sgt.core.sync.land`) run
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


def gc(repo, force: bool = False) -> tuple[str, ...]:
    """Reap sessions whose recorded pid is no longer alive (a crashed agent's abandoned scratch
    tree) -- `force` reaps every session regardless of liveness. Age alone can't distinguish a
    crash from a long-running agent mid-edit, so liveness is the only signal (D5's pitfall)."""
    sessions = _load(repo)
    targets = list(sessions.values()) if force else [s for s in sessions.values() if not is_alive(s.owner_pid)]
    gb = GitBinding(repo)
    reaped = []
    for s in targets:
        try:
            gb.worktree_remove(s.scratch, force=True)
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
