"""Derived rationale records (intent-ledger M1, design doc
`docs/plans/2026-07-31-002-intent-ledger-architecture.md` §4.2-§4.3): the *reflection* layer
over the raw `sgt.intent.turns` evidence and the op store. Where a turn is one thing the user
said, a rationale is the derived answer to "why do these ops exist" -- `{subject ops, reason,
actor, evidence, confirmed}` -- produced by transcribing what the workflow already aligned.

Reflection is inference, not recording: even with the real conversation in hand, mapping messy
turns to the ops that landed is a guess. So every record is honest about its footing -- `confirmed`
distinguishes a human-endorsed record from an inferred one, an empty `reason` (`open`/no evidence)
means "unknown" rather than a fabricated why, and `evidence` points back at the turns the guess
rests on so it stays auditable.

**M1 is local-tier.** Records live in `.sgt/local/rationale.json` (never synced), exactly like
`turns`. The committed, team-shared tier -- with its CRDT merge, stable sha+footprint anchors, and
read-time liveness join -- is M2 work, gated on the state-model rework in the workflow-hardening
plan (2026-07-31-001 Phase 1.2); shipping the committed artifact before that lands would drop it
into the very merge surface that plan is evacuating. Until then this is the reader for `sgt why`
and the label feed, proving the bet with zero sync surface.

**The planned path (M1).** For plan-loop work, `sgt.loop.match.confirm_match` has already aligned a
cluster of real ops to the plan steps that predicted them (`plan_matches.json`). Reflection there
is transcription: read the matched steps' rationale/title as the reason, the plan-intake turns as
evidence, the ops' provenance sha + footprint as anchors, and emit one record. The unplanned path
(segmenting a tangled session's conversation and aligning it to op-clusters) is the M3 research bet
-- deliberately not here.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from sgt import state
from sgt.intent.turns import capture_lock, turns_for

_ARTIFACT = "intent_rationale"
REFLECTOR_VERSION = "1"


def load_rationale(repo: str | Path) -> dict[str, dict]:
    """The whole local rationale store, `{rationale-id: record}` -- empty if none derived yet."""
    return state.load_json(repo, _ARTIFACT, default={})


def _fp_digest(footprint) -> str:
    """A stable digest of an op's footprint (its symbol set) -- the secondary anchor a future
    committed-tier rebind (M2) uses when an op id churns under a miner bump. Equal footprints
    digest equally, so a re-mined op is re-findable by its shape."""
    return hashlib.sha256(json.dumps(sorted(footprint), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _rationale_id(subject_ops: list[str], reason: str | None, actor: str,
                  relations: list[dict] | None = None) -> str:
    """Content address over a record's identifying fields, so re-reflecting the same alignment is a
    no-op. `evidence`/`ts`/`confirmed` are excluded: the same (subject, reason, actor) is the same
    claim regardless of when it was derived or which turns happened to be cited. Relations ARE
    identity: "supersedes X" and "supersedes Y" are different claims -- without them, every
    `retire_open`'s closing record (`subject=[]`, reason "marked done") collides onto one id and
    retiring a second open intent silently no-ops (testbed 2026-07-31)."""
    rels = sorted((r.get("type", ""), r.get("target", "")) for r in (relations or []))
    payload = json.dumps([sorted(subject_ops), reason, actor, rels], ensure_ascii=False)
    return "r-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_rationale(repo: str | Path, *, subject: list[dict], reason: str | None, actor: str,
                     evidence: list[str], confirmed: bool = False, open: bool = False,
                     predicted_fp: str | None = None, predicted_symbols: list[str] | None = None,
                     relations: list[dict] | None = None,
                     ts: float | None = None, recorded_by: str = "reflector") -> str | None:
    """Write one rationale record. `subject` is a list of `{op, sha, fp}` anchors (empty for an
    `open` unfulfilled-intent record). Idempotent by (subject ops, reason, actor); returns the id
    (fresh or existing), or `None` when there is nothing to say (no subject and not an open record).
    Does not overwrite an existing id -- a correction supersedes via a new record, never a mutation
    (append-only, so a future committed-tier merge stays a conflict-free union).

    `predicted_symbols` (open records only) stores the step's predicted footprint *symbols*, not
    just the `predicted_fp` digest -- overlap-retire (`auto_retire_open`) needs the symbols to test
    coverage against what later landed; a digest can only be equality-checked, so an open record
    without this field can be aged out but never overlap-retired."""
    subject_ops = [s["op"] for s in subject]
    relations = relations or []
    if not subject_ops and not open and not relations:
        return None  # nothing to say: no ops, not an open intent, not a closing/supersession
    with capture_lock(repo):
        store = load_rationale(repo)
        rid = _rationale_id(subject_ops, reason, actor, relations)
        if rid in store:
            return rid
        store[rid] = {
            "id": rid, "subject": subject, "predicted_fp": predicted_fp,
            "predicted_symbols": list(predicted_symbols or []), "open": open,
            "reason": reason, "actor": actor, "confirmed": confirmed,
            "evidence": list(evidence), "relations": list(relations),
            "ts": time.time() if ts is None else ts,
            "recorded_by": recorded_by, "reflector_version": REFLECTOR_VERSION,
        }
        state.save_json_if_changed(repo, _ARTIFACT, store)
    return rid


def _subject_for(repo: str | Path, op_ids) -> list[dict]:
    """Build `{op, sha, fp}` anchors for `op_ids` -- the op's first provenance sha (a stable anchor
    across miner bumps, unlike the id) and its footprint digest. Ops absent from the store (e.g. a
    still-hollow prediction) are skipped."""
    from sgt.core.store import Store

    store = Store(repo)
    subject = []
    for op_id in op_ids:
        op = store.get(op_id)
        if op is None:
            continue
        sha = min(op.provenance) if op.provenance else None
        subject.append({"op": op_id, "sha": sha, "fp": _fp_digest(op.footprint)})
    return subject


def reflect_planned_match(repo: str | Path, session_id: str, op_ids: list[str]) -> str | None:
    """Transcribe a just-confirmed plan match into one local rationale record (the M1 planned path).
    Reason = the matched steps' rationale (falling back to their titles); evidence = the plan-intake
    turns; actor = human (a plan is the user's own intent); `confirmed=False` (inferred -- a human
    endorses it later via a correction). A no-op when the session record or its matched ops are
    gone. Called at the end of `confirm_match`, where op<->step alignment is known and free."""
    sessions = state.load_json(repo, "plan_sessions", default={})
    record = sessions.get(session_id)
    if record is None:
        return None
    wanted = set(op_ids)
    reasons = [
        (step.get("rationale") or step.get("title") or "").strip()
        for step in record.get("steps", [])
        if wanted & set(step.get("matched_op_ids", []))
    ]
    reason = "; ".join(r for r in reasons if r) or None
    subject = _subject_for(repo, op_ids)
    if not subject:
        return None
    evidence = [t["id"] for t in turns_for(repo, session_id, key_kind="plan")]
    return record_rationale(repo, subject=subject, reason=reason, actor="human",
                            evidence=evidence, confirmed=False)


def reflect_open_intents(repo: str | Path, session_id: str) -> list[str]:
    """When a plan session is *walked away from* (`abandon`, incl. the staleness sweep) with steps
    still pending, record each as an `open` intent -- rather than letting its hollow op be deleted
    silently. Deliberately NOT called from `mark_done`: that verb asserts the work is finished
    (done differently than predicted), so its pending steps are not unfulfilled intents, and
    minting them would fill the open surface with already-landed noise (testbed 2026-07-31: every
    such record was a false open). These resurface via `sgt intent open` and recall, and retire
    when a human runs `sgt intent done`. A no-op for an unknown session. Called *before* the
    pending hollows are unlinked, so the step's prediction is still readable."""
    sessions = state.load_json(repo, "plan_sessions", default={})
    record = sessions.get(session_id)
    if record is None:
        return []
    evidence = [t["id"] for t in turns_for(repo, session_id, key_kind="plan")]
    out = []
    for step in record.get("steps", []):
        if step.get("status") != "pending":
            continue
        # Title first: it IS the stated intent ("Document theme usage"); the step's rationale is
        # planner prose *about* the step and reads oddly as an intent on the open surface.
        reason = (step.get("title") or step.get("rationale") or "").strip() or None
        predicted = step.get("predicted_footprint") or []
        rid = record_rationale(
            repo, subject=[], reason=reason, actor="human", evidence=evidence,
            open=True, predicted_fp=_fp_digest(predicted) if predicted else None,
            predicted_symbols=list(predicted))
        if rid:
            out.append(rid)
    return out


def retire_open(repo: str | Path, rid: str, reason: str = "marked done",
                recorded_by: str = "user") -> str | None:
    """Retire an open intent: write a closing record that supersedes it, so it leaves the open
    surface. `None` if `rid` is not a *live* open intent (unknown, not open, or already retired) --
    so a second retire is a no-op. The open record is kept as history (append-only); only its
    standing changes. `recorded_by` distinguishes a human close (`sgt intent done`, the default
    "user") from an automatic drain (`auto_retire_open`, "reflector")."""
    if rid not in {r["id"] for r in open_intents(repo)}:
        return None
    # actor "human", same vocabulary as turns/reflection (never "user"); `recorded_by` carries
    # the writer distinction.
    return record_rationale(repo, subject=[], reason=reason, actor="human", evidence=[],
                            open=False, relations=[{"type": "supersedes", "target": rid}],
                            recorded_by=recorded_by)


def _live_footprint_symbols(repo: str | Path) -> set[str]:
    """The symbols live at the current ideal's frontier -- the "what exists now" overlap-retire
    tests a stated-but-never-landed intent's predicted symbols against. A symbol is live when its
    frontier tip's after-version is not `BOTTOM` (the same liveness test `covered_paths` and the
    selection verbs use). Empty (never an error) on an unmined/empty repo."""
    from sgt.core import lens, opindex, order
    from sgt.core.op import is_bottom

    ops = opindex.index_ops(repo)
    ideal = lens.current_ideal(repo)
    by_id = {op.id: op for op in ops}
    return {
        sym for sym, tip in order.frontier(ideal.op_ids, ops).items()
        if tip in by_id and not is_bottom(by_id[tip].footprint[sym][1])
    }


def auto_retire_open(repo: str | Path, *, max_age_days: float = 30.0,
                     now: float | None = None) -> list[str]:
    """Drain the residual automatically, so an open intent never lingers unactionable and there is
    no open/done queue to groom (intent-ledger P1, design §3.4). Two mechanisms, run at the save
    beat (where new work lands), each writing a superseding record so the open leaves the "needs
    attention" surface while its history is kept:

    - **overlap-retire (fulfilled):** every one of the intent's predicted symbols is now live in the
      ideal, so the thing it stated exists -- later work landed it, inside a plan or out. Requires
      *full* coverage deliberately: a partial match is too weak a signal to silently close a stated
      intent, and the residual honesty (§3.4) is worth an occasional un-retired item over a
      wrong-retired one. The residual risk is a step that *modifies* a pre-existing symbol: its
      symbol was already live, so overlap can't see the specific change, and the intent may retire a
      beat early -- an accepted heuristic cost (the record is kept; a human can still read it).
      Symbol matching is the path-suffix-lenient `_symbol_matches` (the planner names
      `storage.py::add`, the miner stores `pkg/storage.py::add`). An intent with no
      `predicted_symbols` (an older record, or a fallback decomposition that predicted nothing)
      cannot be judged this way and is left to age.
    - **age-retire (stale):** an open intent older than `max_age_days` is retired unambiguously --
      a stated intention no one has acted on in a month is not "what needs attention" anymore.

    Returns the retired ids. A pure no-op (no reads beyond the store) when nothing is open."""
    import time

    now = time.time() if now is None else now
    opens = open_intents(repo)
    if not opens:
        return []
    live = _live_footprint_symbols(repo)
    retired: list[str] = []
    for r in opens:
        preds = r.get("predicted_symbols") or []
        fulfilled = bool(preds) and bool(live) and all(
            any(_symbol_matches(p, m) for m in live) for p in preds
        )
        aged = (now - r.get("ts", now)) > max_age_days * 86400
        if fulfilled:
            if retire_open(repo, r["id"], reason="landed (footprint overlap)",
                           recorded_by="reflector"):
                retired.append(r["id"])
        elif aged:
            if retire_open(repo, r["id"], reason="aged out (stale intent)",
                           recorded_by="reflector"):
                retired.append(r["id"])
    return retired


def edit_rationale(repo: str | Path, rid_prefix: str, reason: str) -> str | None:
    """Human correction (`sgt intent edit <id> "<reason>"`): supersede a record with a confirmed
    one carrying the user's own reason, same subject. The optional lever -- capture never depends
    on it. `None` when the prefix matches no record or several (never guess)."""
    store = load_rationale(repo)
    hits = [r for r in store.values() if r["id"].startswith(rid_prefix)]
    if len(hits) != 1 or not reason.strip():
        return None
    old = hits[0]
    return record_rationale(
        repo, subject=old["subject"], reason=reason.strip(), actor="human",
        evidence=old.get("evidence", []), confirmed=True, open=old.get("open", False),
        predicted_fp=old.get("predicted_fp"),
        relations=[{"type": "supersedes", "target": old["id"]}], recorded_by="user")


def _symbol_matches(query: str, mined: str) -> bool:
    """Lenient join between the symbol an agent *names* and the symbol the miner *stores*
    (`repo/relative/path.py::name`). Agents reliably know the file basename and the symbol name,
    not the repo-relative prefix (testbed 2026-07-31: a recall for `__main__.py::add` silently
    missed `tinytask/__main__.py::add`). Symbol names must match exactly; the file part matches
    when equal or when either is a path-suffix of the other."""
    if query == mined:
        return True
    qf, _, qn = query.partition("::")
    mf, _, mn = mined.partition("::")
    if qn != mn:
        return False
    return mf.endswith("/" + qf) or qf.endswith("/" + mf)


def recall(repo: str | Path, symbols: list[str]) -> dict:
    """The agent-recall read (design §4.4, local tier): live rationale whose subject ops touch
    `symbols`, plus every open intent -- "before you edit here, this is why it is the way it is,
    and this was stated but never landed." Pure read over local stores; empty stores mean empty
    lists, never an error."""
    from sgt.core.store import Store

    recs = list(load_rationale(repo).values())
    dead = _superseded_ids(recs)
    store = Store(repo)
    wanted = list(dict.fromkeys(symbols))
    matched = []
    for r in recs:
        if r["id"] in dead or r.get("open") or not r.get("reason"):
            continue
        ops = [s["op"] for s in r.get("subject", [])]
        touched = set()
        for op_id in ops:
            op = store.get(op_id)
            if op is not None:
                touched.update(op.footprint)
        overlap = sorted(m for m in touched if any(_symbol_matches(q, m) for q in wanted))
        if overlap or not wanted:
            matched.append({"reason": r["reason"], "actor": r["actor"],
                            "confirmed": r["confirmed"], "symbols": overlap,
                            "evidence": len(r.get("evidence", []))})
    matched.sort(key=lambda m: -len(m["symbols"]))
    opens = [{"id": r["id"], "reason": r["reason"]} for r in open_intents(repo)]
    return {"rationale": matched, "open_intents": opens}


def _superseded_ids(records: list[dict]) -> set[str]:
    """The ids any record in `records` supersedes -- those are historical, not the current why."""
    return {
        rel["target"] for r in records for rel in r.get("relations", [])
        if rel.get("type") == "supersedes"
    }


def for_op(repo: str | Path, op_id: str) -> list[dict]:
    """Every rationale record whose subject includes `op_id`, live (unsuperseded) first, then by
    recency. Each record gains a `superseded` flag. The read-time liveness join against the current
    ideal (demote rationale whose code was reverted) is M2 -- here supersession is the only
    liveness signal, and M1 mints none, so in practice all records read live."""
    recs = [r for r in load_rationale(repo).values() if any(s["op"] == op_id for s in r["subject"])]
    superseded = _superseded_ids(recs)
    for r in recs:
        r["superseded"] = r["id"] in superseded
    return sorted(recs, key=lambda r: (r["superseded"], -r["ts"]))


def open_intents(repo: str | Path) -> list[dict]:
    """Unfulfilled-intent records (`open=True`) not yet retired by a superseding record -- surfaced
    by `sgt intent open` and recall, retired by `sgt intent done` or a later overlap match."""
    recs = list(load_rationale(repo).values())
    superseded = _superseded_ids(recs)
    return sorted(
        (r for r in recs if r.get("open") and r["id"] not in superseded),
        key=lambda r: -r["ts"],
    )
