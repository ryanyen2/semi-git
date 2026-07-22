"""Feature-scoped intent segmentation: the missing object between a whole feature (too big,
timeless) and a single op (too small, wordless). See docs/design/2026-07-21-intent-feature-
entanglement.md.

The feature tree (`op_leaf`) says *which* ops belong to a feature; git `history()` says *when*
each landed. This module cuts a feature's own ops -- ordered by commit-index -- into a few
contiguous **segments**, each one coherent intent, so "which version of this feature do I go back
to" has an answer: a segment. This is the entanglement the old cross-feature `theme` never had:
the feature axis *scopes* the cut (we never segment across features), the time axis *orders* it.

This module is the deterministic rungs 0/1 of the fallback ladder -- pure, LLM-free, byte-stable
across rebuilds, same discipline as `sgt.intent.group`:

  - rung 0 (`feature_runs`): a feature's ops grouped by their earliest-witnessing commit-index.
    One *run* per commit that touched the feature -- the smallest recorded chapter, its commit
    subject already a human label.
  - rung 1 (`segment_runs`): adjacent runs merge into one segment unless the boundary between them
    is *strong*. Strength combines three signals with no LLM (§4.2 of the design doc):
      * novelty / behavioral entropy -- a run that creates or removes symbols changed behavior; a
        run that only modifies existing ones (rename an arg, add a param, tweak) did not, and
        merges into its neighbour rather than opening a new chapter.
      * conventional-commit scope shift (`feat(intent)` -> `fix(vscode)`).
      * a commit-index gap -- the feature went dormant and was picked up later (sessionization).

The critical safety invariant (mirrors `sgt.intent.theme`): **op membership is a deterministic
function of `(feature_id, the whole per-commit runs a segment covers)`.** The boundary decision --
here a heuristic, in `sgt.intent.theme_segment` an LLM -- only ever chooses *where* to cut and
*what to call it*; it never emits an op-id. So `sgt revert <feature>@<n>` resolves to a
deterministic op-set and runs the identical `verbs.plan_revert_op_set` path every revert uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sgt.core import opindex
from sgt.core.op import Op, is_bottom, is_content_bearing
from sgt.lens.cluster import commit_scope
from sgt.store.gitbind import GitBinding

# -- rung-1 boundary tuning (all explicit, all testable) ------------------------------------------
W_SCOPE = 1.0        # a conventional-commit scope change is a full boundary on its own.
W_GAP = 1.0          # a large commit-index gap (feature went dormant, then resumed) is a boundary.
W_NOVELTY = 1.0      # a fully-novel run (all new/removed symbols) is a boundary on its own.
GAP_THRESHOLD = 12   # commits between two runs of *the same feature* that mark a context switch.
CUT_THRESHOLD = 1.0  # cut between adjacent runs iff the combined score reaches this.
MAX_SEGMENTS = 8     # soft cap: past this, offline segmentation merges its weakest adjacent seams
# back together so a long-lived feature stays readable without an LLM. The LLM rung consolidates
# more intelligently; this is only the no-key floor.


@dataclass(frozen=True)
class Run:
    """One feature's ops that landed in one commit -- the atom of segmentation. `novelty` is the
    behavioral-change weight (§4.2): the fraction of this run's content-symbol touches that create
    or remove a symbol (structural change) rather than modify one in place (a tweak)."""

    feature_id: str
    commit_index: int
    commit_sha: str
    subject: str
    scope: str | None
    op_ids: frozenset[str]
    novelty: float


@dataclass(frozen=True)
class Segment:
    """A contiguous run of one feature's history sharing one intent -- the rewind unit. `label`/
    `rationale`/`source` are metadata (heuristic here, LLM in `theme_segment`); `op_ids` is the
    deterministic union of the covered runs' ops, the only thing a revert resolves against."""

    feature_id: str
    seg_index: int
    label: str
    rationale: str
    op_ids: frozenset[str]
    commit_shas: tuple[str, ...]
    first_index: int
    last_index: int
    novelty: float
    source: str  # "fallback" here; "llm" once `theme_segment` has named it

    @property
    def op_count(self) -> int:
        return len(self.op_ids)


def _novelty(ops: list[Op]) -> float:
    """Behavioral entropy of a run: of every content-bearing symbol touch across its ops, the
    fraction that *creates* (`before is None`) or *removes* (`is_bottom(after)`) a symbol -- a
    change to what the feature does -- versus *modifies* one already alive (a tweak). Anchors and
    residue-ordering internals are not content, so they never sway the score. A run with no
    content touches (only synthetic bookkeeping) scores 0 -- it opens no chapter."""
    structural = total = 0
    for op in ops:
        for sym, (before, after) in op.footprint.items():
            if not is_content_bearing(sym):
                continue
            total += 1
            if before is None or is_bottom(after):
                structural += 1
    return structural / total if total else 0.0


def feature_runs(repo: str | Path, op_leaf: dict[str, str]) -> dict[str, list[Run]]:
    """Rung 0: every feature's ops grouped into per-commit `Run`s, chronological. Keyed on the
    *earliest* of each op's provenance commits that appears in `history()` -- the same
    earliest-provenance rule `history_view`/`group.atoms` use, so the three axes agree on when an
    op happened. An op with no in-history provenance, or whose feature is unknown to `op_leaf`, is
    skipped (it has no place on this feature-by-time grid); it is never dropped from the store,
    only from this projection. Pure and deterministic: repeated calls on an unchanged store return
    identical runs in identical order."""
    repo = Path(repo)
    rows = GitBinding(repo).history()
    commit_index = {sha: i for i, (sha, _p, _s) in enumerate(rows)}
    subject_of = {sha: subject for sha, _p, subject in rows}
    ops = opindex.index_ops(repo)
    by_id = {op.id: op for op in ops}

    # (feature, commit_index) -> op ids
    buckets: dict[tuple[str, int], list[str]] = {}
    sha_at: dict[int, str] = {}
    for op in ops:
        leaf = op_leaf.get(op.id)
        if leaf is None:
            continue
        witnessed = [sha for sha in op.provenance if sha in commit_index]
        if not witnessed:
            continue
        earliest = min(witnessed, key=lambda s: commit_index[s])
        idx = commit_index[earliest]
        sha_at[idx] = earliest
        buckets.setdefault((leaf, idx), []).append(op.id)

    runs: dict[str, list[Run]] = {}
    for (leaf, idx), op_ids in buckets.items():
        sha = sha_at[idx]
        subject = subject_of.get(sha, "")
        runs.setdefault(leaf, []).append(Run(
            feature_id=leaf,
            commit_index=idx,
            commit_sha=sha,
            subject=subject,
            scope=commit_scope(subject),
            op_ids=frozenset(op_ids),
            novelty=_novelty([by_id[oid] for oid in op_ids]),
        ))
    for leaf in runs:
        runs[leaf].sort(key=lambda r: (r.commit_index, r.commit_sha))
    return runs


def _boundary_score(a: Run, b: Run) -> float:
    """How strongly runs `a` -> `b` (consecutive in one feature) should be cut apart. Scope shift
    and a dormancy gap are full boundaries; a novel `b` opens a chapter proportional to how much
    new behavior it introduces. A same-scope, no-gap, modify-only `b` scores 0 and merges."""
    score = 0.0
    if a.scope and b.scope and a.scope != b.scope:
        score += W_SCOPE
    if (b.commit_index - a.commit_index) >= GAP_THRESHOLD:
        score += W_GAP
    score += b.novelty * W_NOVELTY
    return score


def _cut_points(runs: list[Run]) -> list[int]:
    """Indices `k` (1..len-1) where run `k` starts a new segment, cheapest-seam-first capped at
    `MAX_SEGMENTS`. First collects every seam scoring >= `CUT_THRESHOLD`; if that leaves too many
    segments, drops the lowest-scoring seams until within the cap (a long-lived feature stays
    readable offline). Deterministic: ties broken by commit-index."""
    seams = [(i, _boundary_score(runs[i - 1], runs[i])) for i in range(1, len(runs))]
    cuts = [i for i, s in seams if s >= CUT_THRESHOLD]
    if len(cuts) + 1 > MAX_SEGMENTS:
        kept = sorted(
            (i for i, _ in seams if i in cuts),
            key=lambda i: (-dict(seams)[i], runs[i].commit_index),
        )[:MAX_SEGMENTS - 1]
        cuts = sorted(kept)
    return cuts


def _segment_label(runs: list[Run]) -> tuple[str, str]:
    """A deterministic (label, rationale) for a segment of runs, offline. The label is the subject
    of the segment's *highest-novelty* run (the commit that changed the most behavior -- more
    informative than blindly taking the first), trimmed; the rationale states the span. Ready-made
    from the user's own commit messages, never invented -- KTD2's "a commit subject is already a
    human label"."""
    lead = max(runs, key=lambda r: (r.novelty, -r.commit_index))
    label = (lead.subject or lead.commit_sha[:8]).strip()[:60] or lead.commit_sha[:8]
    n = len(runs)
    rationale = (
        f"{n} commit(s), {sum(len(r.op_ids) for r in runs)} op(s)"
        if n > 1 else "one commit"
    )
    return label, rationale


def segment_runs(runs: list[Run]) -> list[Segment]:
    """Rung 1: cut one feature's chronological runs into contiguous `Segment`s. Every run lands in
    exactly one segment (a total partition of the feature's ops -- KTD2), boundaries chosen by
    `_cut_points`, each segment labeled from its own commits. `source="fallback"`; the LLM rung
    renames/re-cuts on top of this."""
    if not runs:
        return []
    cuts = set(_cut_points(runs))
    segments: list[Segment] = []
    current: list[Run] = []
    for i, run in enumerate(runs):
        if i in cuts and current:
            segments.append(_finish_segment(current, len(segments)))
            current = []
        current.append(run)
    if current:
        segments.append(_finish_segment(current, len(segments)))
    return segments


def _finish_segment(runs: list[Run], seg_index: int) -> Segment:
    label, rationale = _segment_label(runs)
    op_ids = frozenset().union(*(r.op_ids for r in runs))
    return Segment(
        feature_id=runs[0].feature_id,
        seg_index=seg_index,
        label=label,
        rationale=rationale,
        op_ids=op_ids,
        commit_shas=tuple(r.commit_sha for r in runs),
        first_index=runs[0].commit_index,
        last_index=runs[-1].commit_index,
        novelty=max(r.novelty for r in runs),
        source="fallback",
    )


def pin_key(seg: Segment) -> str:
    """The stable identity a user relabel is keyed on: the segment's first commit sha. Survives a
    rebuild as long as that commit still starts a segment; if a boundary shift moves it mid-chapter
    the pin harmlessly stops matching (same graceful-staleness as a feature-label pin)."""
    return seg.commit_shas[0] if seg.commit_shas else ""


def apply_label_pins(segments: list[Segment], pins_for_feature: dict[str, str] | None) -> list[Segment]:
    """Override a checkpoint's label with the user's relabel (`sgt intent relabel`), keyed by
    `pin_key`. A matched segment is re-labeled and marked `source="user"` -- the highest-precedence
    label, above both the LLM and the deterministic fallback (mirrors how a feature-label pin wins
    over `label_tree`'s output). Unmatched pins are ignored, never an error."""
    if not pins_for_feature:
        return segments
    out: list[Segment] = []
    for seg in segments:
        label = pins_for_feature.get(pin_key(seg))
        out.append(_relabel(seg, label.strip()[:60] or seg.label, seg.rationale, "user")
                   if label and label.strip() else seg)
    return out


def resolve_checkpoint(repo: str | Path, spec: str) -> tuple[frozenset[str], str] | None:
    """Resolve a `<feature>@<n>` checkpoint spec to `(op_ids, display_label)`, or `None` if it
    isn't a checkpoint spec / doesn't resolve. `<feature>` matches a feature id exactly, else a
    feature *label* (case-insensitive); `<n>` is the 0-based segment index in that feature's
    chronological segmentation (persisted-overlay-aware). The returned `op_ids` is the segment's
    deterministic op-set -- exactly what `sgt revert` removes -- so a checkpoint revert runs the
    identical `plan_revert_op_set` path every other revert uses (the KTD6 safety invariant: the
    boundary/label may be LLM-chosen, the op membership never is)."""
    if "@" not in spec:
        return None
    feat_part, _, idx_part = spec.rpartition("@")
    if not idx_part.isdigit() or not feat_part:
        return None
    from sgt import state
    from sgt.lens.tree import load as load_tree

    tree_result = load_tree(repo)
    if tree_result is None:
        return None
    op_leaf = tree_result["op_leaf"]
    nodes = tree_result["nodes"]

    feature_id = feat_part if feat_part in nodes else None
    if feature_id is None:  # a unique feature-id *prefix* (the short handle `sgt intent list` prints)
        prefix_hits = [nid for nid in nodes if nid.startswith(feat_part) and not nodes[nid]["children"]]
        if len(prefix_hits) == 1:
            feature_id = prefix_hits[0]
    if feature_id is None:  # else a case-insensitive label match against leaf features
        want = feat_part.strip().lower()
        matches = [nid for nid, nd in nodes.items()
                   if not nd["children"] and nd.get("label", "").strip().lower() == want]
        if len(matches) == 1:
            feature_id = matches[0]
    if feature_id is None:
        return None

    runs = feature_runs(repo, op_leaf).get(feature_id)
    if not runs:
        return None
    persisted = state.load_json(repo, "intent_segments", default={})
    segs = overlay_persisted(runs, persisted.get(feature_id))
    idx = int(idx_part)
    if not (0 <= idx < len(segs)):
        return None
    seg = segs[idx]
    label = nodes.get(feature_id, {}).get("label", feature_id)
    return seg.op_ids, f"{label}@{idx}: {seg.label}"


def overlay_persisted(runs: list[Run], record: list[dict] | None) -> list[Segment]:
    """Build one feature's segments, preferring persisted LLM boundaries+labels when present.

    `record` is `.sgt/intent/segments.json`'s per-feature entry (written by `sgt intent build`):
    a chronological list of `{commit_shas, label, rationale, source}`. Each entry re-groups the
    feature's runs by *commit sha* -- so op membership is still a deterministic function of which
    whole runs the segment covers, never anything the LLM emitted (the KTD6 safety invariant). A
    run whose sha the record doesn't mention (a commit landed since the last build) is appended as
    its own trailing fallback segment rather than dropped, so the projection always covers every
    run. `record=None` (never built, or the feature is new) falls straight through to the
    deterministic rung-1 cut."""
    if not record:
        return segment_runs(runs)

    run_by_sha = {r.commit_sha: r for r in runs}
    claimed: set[str] = set()
    groups: list[tuple[list[Run], dict]] = []
    for entry in record:
        member = [run_by_sha[sha] for sha in entry.get("commit_shas", [])
                  if sha in run_by_sha and sha not in claimed]
        if member:
            claimed.update(r.commit_sha for r in member)
            groups.append((sorted(member, key=lambda r: (r.commit_index, r.commit_sha)), entry))

    # Any run not named by the record (a newer commit) becomes its own trailing segment so the
    # partition stays total; deterministic-labeled, marked fallback.
    leftover = sorted((r for r in runs if r.commit_sha not in claimed),
                      key=lambda r: (r.commit_index, r.commit_sha))
    for run in leftover:
        groups.append(([run], None))

    groups.sort(key=lambda g: (g[0][0].commit_index, g[0][0].commit_sha))
    out: list[Segment] = []
    for seg_index, (member, entry) in enumerate(groups):
        base = _finish_segment(member, seg_index)
        if entry is not None:
            label = (entry.get("label") or base.label).strip()[:60] or base.label
            base = _relabel(base, label, entry.get("rationale", base.rationale),
                            entry.get("source", "llm"))
        out.append(base)
    return out


def _relabel(seg: Segment, label: str, rationale: str, source: str) -> Segment:
    from dataclasses import replace
    return replace(seg, label=label, rationale=rationale, source=source)


def deterministic_segments(repo: str | Path, op_leaf: dict[str, str]) -> dict[str, list[Segment]]:
    """The whole offline pass: every feature's runs, cut into segments (rungs 0/1). Deterministic
    and pure; the one entry point a caller (`intent_view`, `theme_segment`'s fallback) needs when
    no LLM is available. Features iterated in sorted id order for a stable projection."""
    runs = feature_runs(repo, op_leaf)
    return {fid: segment_runs(runs[fid]) for fid in sorted(runs)}
