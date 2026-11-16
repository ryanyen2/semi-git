"""Read side of agentic scratch-tree sessions (plan U30, D5): the records in
`.sgt/local/sessions.json` (name, branch, scratch path, target branch, the op-ids present at the
base, owning pid, start time), the ops a session's scratch tree added since its base, and
addressing landed work by its `Attribution.session` (`ops_by_session`)."""

from __future__ import annotations

from dataclasses import dataclass

from ugh.core import lens, opindex
from ugh.core.store import Store
from ugh.state import load_json
from ugh.store.gitbind import GitBinding

_ARTIFACT = "sessions"


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


def list_sessions(repo) -> tuple[Session, ...]:
    return tuple(sorted(_load(repo).values(), key=lambda s: s.name))


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


def _since_base(repo, ideal_ids, base_ids, commits) -> frozenset[str]:
    """`ideal_ids` minus `base_ids`, keeping an op only when its provenance names one of `commits`
    (the commits since the base) or is empty (uncommitted work, or an op a verb minted)."""
    by_id = {op.id: op for op in opindex.index_ops(repo)}
    return frozenset(
        oid for oid in frozenset(ideal_ids) - set(base_ids)
        if oid in by_id and (not by_id[oid].provenance or commits.intersection(by_id[oid].provenance))
    )


def ops_by_session(repo, name: str) -> frozenset[str]:
    """Every op in the main repo's store carrying `Attribution(session=name)` (plan U31, S7) --
    addressing by provenance. Attribution outlives the session record itself (`land` drops the
    record but the stamped attribution is permanent), so this works for a long-landed session
    exactly as it does right after `land`."""
    return frozenset(
        op.id for op in Store(repo).all_ops() if any(a.session == name for a in op.attribution)
    )
