"""The alignment orchestrator: the impure layer that runs the pure A--F stages of
`sgt.intent.align` over *real* captured turns and *real* stored ops, and writes the ALIGN-region
results as `sgt.intent.rationale` records.

`align.py` stays pure and synthetic-testable (its stages are functions of their inputs); this module
holds the two adapters that ground them in the store -- a turn reader (turns -> episodes) and an op
reader (store -> `CandidateOp`s) -- plus the top-level `align_session` that composes them.

**Op time is real wall-clock.** Each op's `CandidateOp.ts` is the committer date of the earliest
commit that embodies it (`gitbind.commit_times()` over `opindex.earliest_commit_sha`). For sgt the
committer date is the save beat, so it is directly comparable to a conversation turn's wall-clock --
which is what makes stage D's temporal generator (and thus the episode "chunks") informative rather
than firing on everything.

**Reliabilities are fit over a global pool.** Stage E's EM degenerates on a per-session handful of
candidates (it latches onto a rarely-firing signal as match-evidence), so `align_session` collects
every session's candidate firing-patterns into one pool and fits once over the whole thing --
`align_candidates` is then handed that `rel` rather than re-fitting per session.

**Not wired to the save beat yet.** Running on today's near-empty corpus would write *false* ALIGN
records that leak into `recall()`/`for_op()`; wiring into `porcelain._save` waits until the corpus
is mature enough to calibrate. Call this directly (`write=False` for a dry run) meanwhile.
"""

from __future__ import annotations

from pathlib import Path

from sgt.core import opindex
from sgt.intent import align, rationale, review, turns
from sgt.store.gitbind import GitBinding


def _candidate_ops(repo: str | Path) -> tuple[list[align.CandidateOp], dict[str, frozenset[str]]]:
    """Adapter: the store's ops as stage-D `CandidateOp`s, plus the requires-adjacency the requires
    generator hops over. Uses the one canonical time-axis reader (`earliest_commit_sha`) so this
    view agrees with every other time-aware projection on *when* an op happened, and stamps each op
    with its earliest commit's real committer timestamp (`None` when the op is embodied by no commit
    in history -- which simply withholds it from the temporal generator).

    The requires-graph is symmetric: an op that `requires (sym, ver)` is adjacent to the op whose
    footprint *produced* `(sym, ver)`, in both directions (stage D expands from surfaced seeds in
    either direction, so the caller supplies both edges)."""
    gb = GitBinding(repo)
    rows = gb.history()
    ops = opindex.index_ops(repo)
    sha_of = opindex.earliest_commit_sha(gb, rows, ops)
    times = gb.commit_times()

    cand_ops: list[align.CandidateOp] = []
    # (symbol, after-version) -> the op that produced it. Last-write-wins on a collision (two ops
    # sharing a footprint entry, e.g. a delete->re-add rebirth to identical content): tolerated,
    # since this only feeds the recall-first `requires` generator and the colliding ops are
    # byte-identical -- precision is stage E/F's job, not the blocker's.
    producer: dict[tuple[str, str], str] = {}
    for op in ops:
        sha = sha_of.get(op.id)
        ts = float(times[sha]) if sha in times else None
        cand_ops.append(align.CandidateOp(id=op.id, symbols=frozenset(op.footprint), ts=ts))
        for sym, (_before, after) in op.footprint.items():
            producer[(sym, after)] = op.id

    adj: dict[str, set[str]] = {}  # requires-adjacency, built once producer is complete
    for op in ops:
        for req in op.requires:
            dep = producer.get(req)
            if dep is None or dep == op.id:
                continue
            adj.setdefault(op.id, set()).add(dep)
            adj.setdefault(dep, set()).add(op.id)
    requires_adj = {oid: frozenset(deps) for oid, deps in adj.items()}
    return cand_ops, requires_adj


def _episodes_from_records(records: list[dict]) -> tuple[list[align.Episode], list[dict]]:
    """Run stages A->B->C over one session's captured turns (already in `seq` order). Keeps only
    *human-authored* turns (`actor == "human"`) -- the aligner writes each record as the user's own
    voice (`actor="human"`), so an agent's captured paraphrase must never feed it. Of those, types
    each turn (A) and keeps the ones that carry alignment signal (intent + correction; backchannels
    and questions are dropped), resolves references (B), and segments into episodes (C).

    Returns `(episodes, kept_turns)` where `kept_turns[i]` is the source turn record for the i-th
    segmented turn -- `Episode.turns` are indices into it, so the writer recovers each episode's
    evidence ids and reason text. `prev_user_text` is threaded across the human turns (an interleaved
    agent turn is skipped, not carried) so stage A can type a correction against the turn it repairs."""
    seg_turns: list[align.SegTurn] = []
    kept: list[dict] = []
    prev_user_text: str | None = None
    for rec in records:
        if rec.get("actor") != "human":
            continue
        text = rec["text"]
        typed = align.type_turn(text, prev_user_text)
        prev_user_text = text
        if not typed.aligns:
            continue
        resolved = align.resolve_references(text)
        seg_turns.append(align.SegTurn(
            symbols=resolved.symbols, ts=float(rec["ts"]),
            is_repair=(typed.kind == align.TURN_CORRECTION),
            words=tuple(align._content_words(text))))
        kept.append(rec)
    return align.segment_episodes(seg_turns), kept


def _episodes_for_session(
    repo: str | Path, session_id: str
) -> tuple[list[align.Episode], list[dict]]:
    """`_episodes_from_records` for one session read straight from the store -- the standalone entry
    point (`align_session` groups all sessions in one scan instead of calling this per session)."""
    return _episodes_from_records(turns.turns_for(repo, session_id, key_kind="chat"))


def align_session(repo: str | Path, *, write: bool = True) -> dict:
    """Align every captured chat session's turns to the ops that landed, writing one rationale record
    per ALIGN-region (episode, op) pair and queuing every REVIEW-region pair for human adjudication
    (`sgt.intent.review`, kept out of the ledger so an unconfirmed guess never leaks into recall).
    Returns a counts summary `{sessions, episodes, candidates, aligned, reviewed}`.

    Reliabilities are fit once over the *global* candidate pool (all sessions) -- the cold-start
    fix -- then reused for every episode's scoring. Set `write=False` for a dry run that scores and
    counts but writes nothing (used to eyeball a young corpus before trusting it)."""
    cand_ops, requires_adj = _candidate_ops(repo)
    # Group every chat turn by session in one store scan (`turns_for` would re-parse the whole turn
    # store once per session). Records are sorted by `seq` to restore capture order within a session.
    by_session: dict[str, list[dict]] = {}
    for t in turns.load_turns(repo).values():
        if t["key_kind"] == "chat":
            by_session.setdefault(t["key"], []).append(t)
    sessions = sorted(by_session)

    # Pass 1: build every episode's candidates and the global firing-pattern pool.
    work: list[tuple[str, align.Episode, list[dict], list[align.Candidate]]] = []
    pool: list[frozenset[str]] = []
    ep_count: dict[str, int] = {}
    for sid in sessions:
        episodes, kept = _episodes_from_records(sorted(by_session[sid], key=lambda t: t["seq"]))
        ep_count[sid] = len(episodes)
        for ep in episodes:
            cands = align.generate_candidates(ep, cand_ops, requires_adj=requires_adj)
            work.append((sid, ep, kept, cands))
            pool.extend(c.generators for c in cands)

    summary = {"sessions": len(sessions), "episodes": len(work),
               "candidates": len(pool), "aligned": 0, "reviewed": 0}
    if not pool:
        return summary

    # Pass 2: fit once over the global pool, then score + write per episode.
    rel = align.fit_reliabilities(pool)
    for sid, ep, kept, cands in work:
        scored = align.align_candidates(cands, concern_count=ep_count[sid], rel=rel)
        ep_recs = [kept[i] for i in ep.turns]
        reason = " ".join(r["text"].strip() for r in ep_recs).strip() or None
        evidence = [r["id"] for r in ep_recs]
        for cand, sc in zip(cands, scored, strict=True):  # align_candidates preserves input order
            if sc.region == align.REVIEW:
                summary["reviewed"] += 1
                if not write:
                    continue
                subject = rationale._subject_for(repo, [sc.op_id])
                if not subject:
                    continue
                signals = [{"name": g, "value": 1.0} for g in sorted(cand.generators)]
                review.record_review(repo, subject=subject, reason=reason, evidence=evidence,
                                     posterior=sc.posterior, signals=signals,
                                     aligner_version=align.ALIGNER_VERSION)
            elif sc.region == align.ALIGN:
                summary["aligned"] += 1
                if not write:
                    continue
                subject = rationale._subject_for(repo, [sc.op_id])
                if not subject:
                    continue
                signals = [{"name": g, "value": 1.0} for g in sorted(cand.generators)]
                rationale.record_rationale(
                    repo, subject=subject, reason=reason, actor="human",
                    evidence=evidence, confirmed=False,
                    confidence=sc.posterior, signals=signals,
                    aligner_version=align.ALIGNER_VERSION, recorded_by="aligner")
    return summary
