"""Rung 2 of the feature-scoped segmentation ladder (see docs/design/2026-07-21-intent-feature-
entanglement.md): an LLM re-cuts and *names* each feature's chronological runs into coherent
intent chapters, in the user's own language. Mirrors `sgt.intent.theme.IntentThemer` line for
line -- content-hash cache, deterministic offline fallback, `source` tagging, retry-on-fallback.

The safety invariant this module exists to protect (identical to `theme.py`): **the LLM never
emits an op-id.** Its schema carries commit shas only; every returned sha is validated as one this
feature actually showed, and the persisted record stores commit-sha groups, not op-ids. Op
membership is always re-derived deterministically from those shas (`segment.overlay_persisted`),
so a wrong LLM boundary is a visible, adjustable mis-default in the preview -- never a silent
destructive edit. And the LLM's grouping is coalesced into *contiguous* blocks at build time
(`_coalesce`): a checkpoint is a stretch of a feature's timeline, so a non-contiguous grouping is
split into the contiguous chapters it implies rather than persisted as an ill-formed span.

Single-run features skip the LLM entirely (nothing to segment); the call is made only for a
feature whose history has more than one commit-run, one call + one cache entry per feature.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from sgt import state
from sgt.config import get_client, get_model
from sgt.core.op import is_content_bearing
from sgt.intent.segment import Run, feature_runs, resolve_feature_spec, segment_runs

EFFORT = "low"
MAX_RUNS = 60  # keeps the per-feature prompt bounded on a long-lived feature; a feature with more
# runs than this is segmented deterministically (the LLM prompt would blow past a useful window).
MAX_WORKERS = 6  # concurrent per-feature LLM calls in flight -- network-bound, bounded to be a
# considerate API citizen (mirrors `sgt.lens.label`'s labeler concurrency).


class SegmentGroup(BaseModel):
    label: str  # 2-5 words, Title Case, the intent in the user's language
    rationale: str  # one line: what this chapter of the feature was for
    commit_shas: list[str]  # 8-char prefixes from the shown runs -- validated, never trusted


class SegmentPlan(BaseModel):
    segments: list[SegmentGroup]


def _feature_key(runs: list[Run]) -> str:
    """Content hash of a feature's run sequence: each run's sha + subject + scope, in order.
    Unchanged history -> unchanged key -> a cache hit that never re-pays or re-names (the flicker
    [[label-cache-and-rebuild-architecture]] fixed once, applied here)."""
    parts: list[str] = []
    for r in runs:
        parts.extend((r.commit_sha, r.subject, r.scope or ""))
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()[:12]


def _kind_summary(runs_ops) -> str:
    from collections import Counter
    c = Counter(op.kind for op in runs_ops)
    return ", ".join(f"{k}×{n}" for k, n in c.most_common())


def _run_line(run: Run, by_id, prompt_for) -> str:
    ops = [by_id[oid] for oid in run.op_ids if oid in by_id]
    syms = sorted({s for op in ops for s in op.footprint if is_content_bearing(s)})
    sym_txt = ", ".join(s.split("::", 1)[-1] for s in syms[:8]) or "(layout only)"
    prompt = prompt_for(run.commit_sha)
    prompt_txt = f" | prompt: {prompt[:120]}" if prompt else ""
    return (f"{run.commit_sha[:8]} | {run.subject[:80]} | {_kind_summary(ops)} | "
            f"nov={run.novelty:.2f} | {sym_txt}{prompt_txt}")


def _split_window(runs: list[Run], record: list[dict] | None) -> tuple[list[dict], list[Run]]:
    """Split a feature's chronological runs into `(frozen_prefix, window)` for the incremental tail
    re-cut (§3.4). An intent chapter is a claim about a *past* stretch, and future commits are not
    evidence about it, so every committed chapter but the last is frozen verbatim; only the last
    persisted chapter plus any newer commits (the `window`) is live and re-cut. A record with fewer
    than two chapters freezes nothing -- the whole feature is the window (a first build, or a single
    remaining chapter). `MAX_RUNS` then applies to the window, not the whole timeline, so a long-
    lived feature keeps getting LLM segmentation instead of falling back."""
    if not record or len(record) < 2:
        return [], runs
    frozen = record[:-1]
    frozen_shas = {sha for entry in frozen for sha in entry.get("commit_shas", [])}
    window = [r for r in runs if r.commit_sha not in frozen_shas]  # last chapter + newer commits
    return frozen, window


def _coalesce(runs: list[Run], run_label: dict[str, tuple[str, str]]) -> list[dict]:
    """Turn a per-run label assignment into contiguous, non-overlapping, total segment records
    (chronological). Consecutive runs carrying the *same* label coalesce into one chapter; a label
    change (or a non-contiguous reuse of a label) starts a new one. Guarantees every run lands in
    exactly one record -- the KTD2 total-partition property the deterministic rung also holds."""
    records: list[dict] = []
    prev_label: str | None = None
    for run in runs:  # already chronological
        label, rationale = run_label[run.commit_sha]
        if label != prev_label or not records:
            records.append({"commit_shas": [], "label": label, "rationale": rationale, "source": "llm"})
            prev_label = label
        records[-1]["commit_shas"].append(run.commit_sha)
    return records


class SegmentThemer:
    def __init__(self, repo: str | Path = ".") -> None:
        self._repo = repo
        self._client = None
        self.cache: dict = state.load_json(repo, "intent_cache", default={})
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0
        self._lock = threading.Lock()  # guards token/call counters across concurrent LLM calls

    @property
    def client(self):
        if self._client is None:
            self._client = get_client(self._repo)
        return self._client

    def _request(self, prompt: str, schema):
        r = self.client.responses.parse(
            model=get_model(self._repo), input=prompt, text_format=schema,
            reasoning={"effort": EFFORT},
        )
        with self._lock:
            self.calls += 1
            self.tokens_in += r.usage.input_tokens
            self.tokens_out += r.usage.output_tokens
        return r.output_parsed

    def segment_feature(self, feature_label: str, runs: list[Run], by_id, prompt_for,
                        record: list[dict] | None = None) -> list[dict]:
        """Cut+name one feature's runs into contiguous chapter records -- the single-feature form of
        `segment_features`, which is the one source of truth for the single-run / over-`MAX_RUNS` /
        cache-hit / LLM-cut / offline-fallback handling (so the two can't drift). A multi-run feature
        gets one cached LLM call that groups consecutive runs and names each; the grouping is
        validated (shown shas only) and coalesced into contiguous blocks. `record` is the feature's
        previous persisted entry (`None` re-cuts the whole feature)."""
        return self.segment_features([(feature_label, runs)], by_id, prompt_for, [record])[0]

    def _segment_compute(self, feature_label: str, runs: list[Run], by_id, prompt_for) -> tuple[list[dict], str]:
        """One multi-run feature's LLM cut + validation, returning ``(records, source)`` WITHOUT
        touching the cache -- split out of `segment_feature` so `segment_features` can run features
        concurrently and persist their results from the main thread (the cache isn't thread-safe).
        Assumes the caller already handled the single-run / over-`MAX_RUNS` / cache-hit cases."""
        lines = "\n".join(f"[{i}] {_run_line(r, by_id, prompt_for)}" for i, r in enumerate(runs))
        prompt = (
            f"A feature in a semantic version-control tool -- \"{feature_label}\" -- was built up "
            "over the commits below, oldest first. Cut this timeline into a few coherent chapters, "
            "each one thing the developer was doing (scaffolding it, handling a case, fixing a bug, "
            "polishing). A run that only tweaks existing code (low nov, kind mostly rework/extend) "
            "belongs with the chapter it refines, not its own chapter.\n"
            "For each chapter give: label (2-5 words, Title Case, in the developer's own terms), "
            "rationale (one line), and commit_shas -- the exact 8-char prefixes from the list that "
            "belong to it. Chapters must be contiguous stretches of the timeline and together cover "
            "every commit exactly once. Never invent a sha not shown.\n\n"
            f"Commits (sha | subject | op-kinds | novelty | symbols[ | prompt]):\n{lines}\n"
        )
        try:
            plan = self._request(prompt, SegmentPlan)
            return self._validate(plan, runs), "llm"
        except Exception:
            return self._fallback(runs), "fallback"

    def segment_features(self, items: list[tuple[str, list[Run]]], by_id, prompt_for,
                        prior_records: list[list[dict] | None] | None = None) -> list[list[dict]]:
        """Resolve many features' chapter records at once (the `build_segments` hot loop, and the
        shared implementation `segment_feature` delegates to for one). Single-run features,
        over-`MAX_RUNS` features, and cache hits are served inline with zero network; the remaining
        multi-run cuts run their LLM calls concurrently (`ThreadPoolExecutor`, network-bound), then
        each result is written to the cache sequentially in this thread (the cache isn't
        thread-safe). Records are kept in input order.

        `prior_records[i]` (aligned with `items`, `None` = re-cut whole) drives the §3.4 incremental
        tail re-cut: `_split_window` freezes every chapter but the last, and only the live window is
        cut/cached (`_feature_key` of the *window*, not the whole timeline) and then re-joined onto
        the frozen prefix. So an unchanged prefix never re-pays, every `pin_key`/`@n` below the tail
        survives by construction, and `MAX_RUNS` bounds the window rather than the timeline."""
        prior_records = prior_records or [None] * len(items)
        results: list[list[dict] | None] = [None] * len(items)
        split: list[tuple[list[dict], list[Run]]] = [([], [])] * len(items)
        keys: list[str | None] = [None] * len(items)
        misses: list[int] = []
        for i, (_label, runs) in enumerate(items):
            frozen, window = _split_window(runs, prior_records[i])
            split[i] = (frozen, window)
            rec = prior_records[i]
            # An unchanged, llm-sourced tail is spliced through verbatim: re-deriving it would
            # demote a single-run (or over-`MAX_RUNS`) window to the deterministic fallback --
            # label churn on a pure no-op rebuild. Gated on source == "llm" so a fallback-sourced
            # tail still re-enters the live window and gets the retry-on-fallback policy (the
            # same gate the cache-hit path enforces below).
            if (rec and window and rec[-1].get("source") == "llm"
                    and {r.commit_sha for r in window} == set(rec[-1].get("commit_shas", []))):
                results[i] = frozen + rec[-1:]
                continue
            if len(window) <= 1 or len(window) > MAX_RUNS:
                results[i] = frozen + self._fallback(window)
                continue
            key = "\x02seg-" + _feature_key(window)
            keys[i] = key
            cached = self.cache.get(key)
            if cached is not None and cached.get("source") == "llm":
                results[i] = frozen + cached["records"]
            else:
                misses.append(i)

        if misses:
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(misses))) as pool:
                computed = list(pool.map(
                    lambda i: self._segment_compute(items[i][0], split[i][1], by_id, prompt_for), misses,
                ))
            for i, (records, source) in zip(misses, computed):
                self.cache[keys[i]] = {"source": source, "records": records}
                results[i] = split[i][0] + records
        return results  # type: ignore[return-value]

    def _validate(self, plan: SegmentPlan, runs: list[Run]) -> list[dict]:
        """Turn the LLM's (possibly messy) grouping into clean contiguous records. Assign each run
        the label of the first LLM group that claims its sha (shown-sha-validated, first-wins on
        overlap); a run no group claimed keeps its deterministic label; then coalesce consecutive
        same-label runs (`_coalesce`). This tolerates every LLM failure mode -- invented shas,
        overlaps, gaps, non-contiguous groupings -- without ever trusting an unshown id or dropping
        a run."""
        shown = {r.commit_sha[:8]: r.commit_sha for r in runs}
        # deterministic per-run label fallback: the run's own commit subject
        run_label: dict[str, tuple[str, str]] = {
            r.commit_sha: ((r.subject or r.commit_sha[:8])[:60] or r.commit_sha[:8], "one commit")
            for r in runs
        }
        assigned: set[str] = set()
        for g in plan.segments:
            label = (g.label or "").strip()[:60]
            if not label:
                continue
            for prefix in g.commit_shas:
                sha = shown.get(prefix[:8])
                if sha and sha not in assigned:
                    run_label[sha] = (label, (g.rationale or "").strip() or "one chapter")
                    assigned.add(sha)
        return _coalesce(runs, run_label)

    def _fallback(self, runs: list[Run]) -> list[dict]:
        """Deterministic records from the rung-1 cut -- byte-stable, no network."""
        return [
            {"commit_shas": list(s.commit_shas), "label": s.label,
             "rationale": s.rationale, "source": "fallback"}
            for s in segment_runs(runs)
        ]

    def save(self) -> None:
        state.save_json_if_changed(self._repo, "intent_cache", self.cache)

    def cost_line(self) -> str:
        est = self.tokens_in / 1e6 * 0.25 + self.tokens_out / 1e6 * 2.0
        return (f"segmenter: {self.calls} live calls, {self.tokens_in} in + {self.tokens_out} out "
                f"tokens (~${est:.4f})")


def _resolve_recut(spec: str | None, nodes: dict) -> set[str]:
    """Resolve `sgt intent build --recut <feature>`'s feature spec to the leaf feature ids whose
    whole timeline should be re-cut (freezing nothing). Matches a feature id exactly, else a unique
    id *prefix* (`f-`-prefixed or the bare hex the graph prints), else a case-insensitive feature
    label -- the same resolution `resolve_checkpoint` uses. An unresolved/ambiguous spec re-cuts
    nothing (the incremental tail path stays in effect); `None` re-cuts nothing."""
    fid = resolve_feature_spec(spec, nodes) if spec else None
    return {fid} if fid else set()


def build_segments(repo: str | Path, recut: str | None = None) -> dict[str, list[dict]]:
    """The segmentation write path (mirrors `theme.build_themes` and `map.build_map`): cut+name
    every feature's runs (rung 2, `SegmentThemer`) and persist to committed
    `.sgt/intent/segments.json` -- `{feature_id: [{commit_shas, label, rationale, source}]}`.
    Only the boundary+label decision is stored; op membership is re-derived on read (KTD6).

    Incremental by default (§3.4): each feature's previous persisted record freezes all but its last
    chapter, so only the live tail window is re-cut/re-named and every checkpoint below it survives
    a rebuild by construction. `recut=<feature spec>` (the `--recut` escape hatch, mirroring
    `sgt map --rebuild`) re-cuts one whole feature from scratch when the frozen history itself needs
    reinterpreting.

    Like `build_themes`, deliberately NOT auto-triggered by sync/land: it needs
    `GitBinding.history()`, which only reflects merged history once the merge commit exists.
    Rebuilt on demand (`sgt intent build`). Content-hash caching keeps a re-build cheap -- an
    unchanged tail window hits the cache; only a feature with new commits costs a live call."""
    from sgt.core.store import Store
    from sgt.intent.prompts import prompt_for as _prompt_for
    from sgt.lens.tree import load as load_tree

    repo = Path(repo)
    tree_result = load_tree(repo)
    op_leaf = tree_result["op_leaf"] if tree_result else {}
    nodes = tree_result["nodes"] if tree_result else {}
    by_id = {op.id: op for op in Store(repo).all_ops()}
    runs_by_feature = feature_runs(repo, op_leaf)

    def prompt_for(sha: str):
        return _prompt_for(repo, sha)

    themer = SegmentThemer(repo)
    fids = sorted(runs_by_feature)
    items = [(nodes.get(fid, {}).get("label", fid), runs_by_feature[fid]) for fid in fids]
    prior = state.load_json(repo, "intent_segments", default={})
    recut_ids = _resolve_recut(recut, nodes)
    prior_records = [None if fid in recut_ids else prior.get(fid) for fid in fids]
    out: dict[str, list[dict]] = dict(zip(
        fids, themer.segment_features(items, by_id, prompt_for, prior_records)))
    themer.save()
    state.save_json_if_changed(repo, "intent_segments", out)
    return out
