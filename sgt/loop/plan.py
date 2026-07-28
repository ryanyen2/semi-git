"""Plan intake: decompose a plan into predicted hollow ops (plan U14, R18/R21).

A plan step becomes one hollow op (`Op.off_chain=True`, `kind="planned"`), never entering
`all_ops()` (R18's substrate -- `sgt.core.store.Store.add_hollow`), so a prediction can never
cause a phantom fork or touch the ideal algebra. Decomposition tries the LLM first
(`_llm_decompose`, mirroring `sgt.lens.label.Labeler`'s call/fallback shape), and falls back to a
deterministic offline split (`_fallback_decompose`) on any failure -- a plan session always
exists, even offline; only the prediction quality differs.

Sessions live in `.sgt/local/plan_sessions.json`, the same small-JSON-table convention
`sgt.core.rewrite` established for drafts (`_drafts_path`/`_load_drafts`/`_save_drafts`).
`baseline_op_ids` is the store's op-id set at intake time, so `sgt.loop.match.compute_checkpoint`
only ever considers ops mined *since* -- the plan's own "next commit is the graceful-degrade
signal."
"""

from __future__ import annotations

import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from sgt import state
from sgt.config import get_client, get_model
from sgt.core.op import make_op
from sgt.core.store import Store

_PENDING = "…pending…"  # hollow after_version placeholder -- never a real content hash (mirrors rewrite.py)
_PLAN_SENTINEL_PREFIX = "__plan__::"

EFFORT = "low"

# A plan with no activity (no intake, no confirmed match) for this long is presumed walked-away and
# is reaped on the next `intake` (the housekeeping beat). Deliberately generous -- reaping deletes a
# session's still-pending hollows, so it must never fire on a plan an agent is slowly working; a
# terminal `completed`/explicit `abandon` is the normal way a plan leaves the active surface.
STALE_SECONDS = 7 * 24 * 3600

# Far shorter than STALE_SECONDS and *non-destructive*: a session idle past this (and with no
# uncommitted work flowing toward it -- that check lives in `sgt.api.plan_view`, not here) is
# *derived* as "stalled" for the review surface, so the user can see an interrupted plan and resume
# it long before the 7-day reap silently deletes it. Purely a display threshold -- nothing is
# stored or deleted at this age.
STALLED_SECONDS = 3600


class PlanStep(BaseModel):
    title: str
    predicted_footprint: list[str] = []
    predicted_feature: str | None = None
    rationale: str = ""


class PlanDecomposition(BaseModel):
    steps: list[PlanStep]


@dataclass(frozen=True)
class PlanSession:
    """A freshly-`intake`n plan: `steps` are plain dicts (the JSON-table's own shape) so a
    caller reading back `.sgt/local/plan_sessions.json` gets identical objects to what `intake`
    returns -- no separate wire format to keep in sync."""

    session_id: str
    plan_text: str
    created_ts: float
    last_activity_ts: float
    status: str
    baseline_op_ids: tuple[str, ...]
    steps: tuple[dict, ...]


def _load_sessions(repo: Path) -> dict:
    return state.load_json(repo, "plan_sessions", default={})


def _save_sessions(repo: Path, table: dict) -> None:
    state.save_json(repo, "plan_sessions", table)


def active_sessions(repo: str | Path) -> dict:
    """Every *active* session, keyed by id -- the review surface's read side (mirrors
    `sgt.core.rewrite.pending_drafts`). A `completed` (every step matched, or explicitly
    `mark_done`) or already-abandoned plan is history, not review surface, so it drops out here:
    that is what stops the workbench and status bar from rendering a growing pile of finished
    plans. The full table -- including completed history -- is still readable via `_load_sessions`
    for provenance."""
    return {sid: rec for sid, rec in _load_sessions(Path(repo)).items() if rec["status"] == "active"}


# -- decomposition (LLM first, deterministic fallback second) -------------------------------------

def _llm_decompose(repo: Path, plan_text: str) -> PlanDecomposition | None:
    """Grounds the prompt in the real repo's own feature tree (if `sgt map` has run), so
    `predicted_feature` names a real id, not a guess in a vacuum. Each feature is listed with its
    file set, not just its label -- a label like "RAG pipeline" alone under-grounds a step like
    "update model identifiers"; the files it actually owns are the stronger signal. Any failure
    (no API key, no network, a malformed response) returns `None` -- the caller falls back."""
    from sgt.lens.tree import load as load_tree

    try:
        result = load_tree(repo)
        nodes = result["nodes"] if result else {}
        leaves = sorted(nid for nid, nd in nodes.items() if not nd["children"])
        feature_lines = []
        for nid in leaves:
            nd = nodes[nid]
            files = sorted({m.split("::", 1)[0] for m in nd["members"]})[:8]
            feature_lines.append(f"{nid}: {nd.get('label', nid)} (files: {', '.join(files)})")
        features = "\n".join(feature_lines)
        prompt = (
            "Decompose this engineering plan into discrete implementation steps for a coding "
            "agent to execute one at a time.\n"
            "For each step, name: title (short, imperative), predicted_footprint (best-guess "
            "`file::symbol` ids the step will touch: when the step centers on a named function, "
            "class, or method, include it at symbol granularity as `file::Symbol` -- reserve a "
            "bare `file` with no `::` for genuine whole-file work like a module skeleton, and "
            "use [] only when you truly cannot name a file), "
            "predicted_feature (the id of the ONE feature below this step most likely belongs "
            "to, or null if none plausibly fit -- never invent an id not in the list), "
            "rationale (one line).\n"
            "Match predicted_feature by file overlap first, label second -- if a step's likely "
            "files sit under one feature's file set (or there is only one feature and the plan's "
            "scope plausibly touches it), name that feature rather than defaulting to null; only "
            "use null when truly no feature plausibly fits.\n\n"
            f"Known features (id: label (files)):\n{features or '(none -- no feature tree built yet)'}\n\n"
            f"Plan:\n{plan_text}\n"
        )
        client = get_client(repo)
        r = client.responses.parse(
            model=get_model(repo), input=prompt, text_format=PlanDecomposition,
            reasoning={"effort": EFFORT},
        )
        return r.output_parsed
    except Exception:
        return None


_NUMBERED_RE = re.compile(r"^\s*\d+[.)]\s+")


def _fallback_decompose(plan_text: str) -> PlanDecomposition:
    """Deterministic, offline, free: one step per numbered-list line, or per blank-line-separated
    paragraph when there's no numbered list. Predicted footprint/feature are always empty --
    there's nothing to guess them from without an LLM."""
    lines = plan_text.splitlines()
    numbered = [_NUMBERED_RE.sub("", line).strip() for line in lines if _NUMBERED_RE.match(line)]
    if numbered:
        titles = numbered
    else:
        titles = [p.strip().replace("\n", " ") for p in re.split(r"\n\s*\n", plan_text) if p.strip()]
    if not titles and plan_text.strip():
        titles = [plan_text.strip().replace("\n", " ")]
    return PlanDecomposition(steps=[PlanStep(title=t) for t in titles])


def _backfill_predicted_feature(repo: Path, steps: list[PlanStep]) -> None:
    """Deterministic safety net for steps the LLM left unplaced: plurality-vote each step's
    `predicted_footprint` symbols against the real tree's leaf membership (identical tie-break --
    smallest leaf id -- to `sgt.lens.tree.assign_ops_to_leaves`), so a step whose guessed footprint
    actually lands in a known feature gets placed even when the LLM's label-only guess nulled it.
    A no-op when there's no tree yet, or a step's guessed symbols don't match anything real."""
    from sgt.lens.tree import load as load_tree
    from sgt.lens.tree import leaf_member_index

    result = load_tree(repo)
    if result is None:
        return
    member_leaf = leaf_member_index(result["nodes"])
    for step in steps:
        if step.predicted_feature is not None:
            continue
        votes = Counter(member_leaf[sym] for sym in step.predicted_footprint if sym in member_leaf)
        if not votes:
            continue
        top_count = votes.most_common(1)[0][1]
        step.predicted_feature = min(leaf for leaf, count in votes.items() if count == top_count)


# -- intake / abandon / staleness sweep ------------------------------------------------------------

def intake(repo: str | Path, plan_text: str, session_id: str | None = None,
           claude_session_id: str | None = None) -> PlanSession:
    """Decompose `plan_text` (LLM first, fallback second) and mint one hollow op per step,
    off-chain. `session_id` defaults to a fresh `uuid4` hex; an explicit id is accepted so tests
    are deterministic. `claude_session_id`, when the drafting agent can read its own
    `$CLAUDE_CODE_BRIDGE_SESSION_ID`, is stored so a stalled plan can be resumed directly with
    `claude --resume <id>` (else the resume affordance falls back to Claude Code's session picker)."""
    repo = Path(repo)
    store = Store(repo)
    session_id = session_id or uuid.uuid4().hex
    now = time.time()

    sweep_stale_sessions(repo, STALE_SECONDS, now=now)  # housekeeping beat: reap walked-away plans

    decomposition = _llm_decompose(repo, plan_text) or _fallback_decompose(plan_text)
    _backfill_predicted_feature(repo, decomposition.steps)
    baseline_op_ids = tuple(sorted(op.id for op in store.all_ops()))

    steps: list[dict] = []
    for i, step in enumerate(decomposition.steps):
        footprint = {sym: (None, _PENDING) for sym in step.predicted_footprint}
        footprint[f"{_PLAN_SENTINEL_PREFIX}{session_id}::step{i}"] = (None, _PENDING)
        hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent=step.title)
        store.add_hollow(hollow)
        steps.append({
            "hollow_id": hollow.id,
            "title": step.title,
            "predicted_footprint": list(step.predicted_footprint),
            "predicted_feature": step.predicted_feature,
            "rationale": step.rationale,
            "status": "pending",
            "matched_op_ids": [],
        })

    table = _load_sessions(repo)
    table[session_id] = {
        "plan_text": plan_text,
        "created_ts": now,
        "last_activity_ts": now,
        "status": "active",
        "claude_session_id": claude_session_id,
        "baseline_op_ids": list(baseline_op_ids),
        "steps": steps,
    }
    _save_sessions(repo, table)

    from sgt.intent.prompts import record_prompt

    record_prompt(repo, session_id, plan_text)
    return PlanSession(
        session_id=session_id, plan_text=plan_text, created_ts=now, last_activity_ts=now,
        status="active", baseline_op_ids=baseline_op_ids, steps=tuple(steps),
    )


def abandon(repo: str | Path, session_id: str) -> bool:
    """Deletes every still-pending step's hollow file and drops the session record. `False` if
    `session_id` is unknown."""
    repo = Path(repo)
    table = _load_sessions(repo)
    record = table.get(session_id)
    if record is None:
        return False
    store = Store(repo)
    for step in record["steps"]:
        if step["status"] == "pending":
            (store.hollow_dir / step["hollow_id"]).unlink(missing_ok=True)
    del table[session_id]
    _save_sessions(repo, table)
    return True


def mark_done(repo: str | Path, session_id: str) -> bool:
    """Explicitly close a session an agent (or human) has finished with. Unlike `abandon` (which
    deletes the record), the record is kept as `completed` history -- so `sgt revert --session` and
    the trust queue can still attribute its landed work -- while its still-pending hollows are
    cleaned up and it leaves the active review surface. `False` if `session_id` is unknown.

    A fully-matched session already reaches `completed` on its own (see
    `sgt.loop.match.confirm_match`); this is the manual close for a plan whose remaining steps were
    done differently than predicted and will never match."""
    repo = Path(repo)
    table = _load_sessions(repo)
    record = table.get(session_id)
    if record is None:
        return False
    store = Store(repo)
    for step in record["steps"]:
        if step["status"] == "pending":
            (store.hollow_dir / step["hollow_id"]).unlink(missing_ok=True)
    record["status"] = "completed"
    record["last_activity_ts"] = time.time()
    _save_sessions(repo, table)
    return True


def sweep_stale_sessions(repo: str | Path, max_age_seconds: float, now: float | None = None) -> list[str]:
    """Abandons any active session whose `last_activity_ts` has aged out past `max_age_seconds`.
    `now` is injectable for deterministic tests. Returns the sorted list of abandoned session ids."""
    repo = Path(repo)
    now = time.time() if now is None else now
    table = _load_sessions(repo)
    stale = sorted(
        sid for sid, rec in table.items()
        if rec["status"] == "active" and now - rec["last_activity_ts"] > max_age_seconds
    )
    for sid in stale:
        abandon(repo, sid)
    return stale
