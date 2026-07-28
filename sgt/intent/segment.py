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

import re
from dataclasses import dataclass
from pathlib import Path

from sgt.core import opindex
from sgt.core.op import Op, is_behavioral, is_bottom
from sgt.lens.cluster import commit_scope
from sgt.store.gitbind import GitBinding

# -- rung-1 boundary tuning (all explicit, all testable) ------------------------------------------
W_SCOPE = 1.0        # a conventional-commit scope change is a full boundary on its own.
W_GAP = 1.0          # a large commit-index gap (feature went dormant, then resumed) is a boundary.
W_NOVELTY = 1.0      # a fully-novel run (all new/removed symbols) is a boundary on its own.
GAP_THRESHOLD = 12   # commits between two runs of *the same feature* that mark a context switch.
CUT_THRESHOLD = 1.0  # cut between adjacent runs iff the combined score reaches this.
MAX_SEGMENTS = 6     # soft cap: past this, offline segmentation merges its weakest adjacent seams
# back together so a long-lived feature stays readable without an LLM. The LLM rung consolidates
# more intelligently; this is only the no-key floor. Tuned down from 8 -- on the self-hosted repo
# the deterministic rung was already median-1 car/feature, but the handful of features that did hit
# the old cap (8) read as a wall of cars in the timeline; 6 keeps every lane focus-mode-scannable
# without touching the boundary weights that drive the (already healthy) common case.
SEAM_BONUS = 0.5     # (η, plan §3.4) hysteresis added to a seam that already starts a chapter in the
# persisted record, so the deterministic re-cut prefers to *keep* a boundary it drew last time. It
# is strictly below `CUT_THRESHOLD`, so it can only preserve a boundary hovering near threshold,
# never invent one -- and it survives `_cap_cuts` re-ranking (a previously-kept seam scores higher
# and is strictly less likely to be dropped as the weakest). PROVISIONAL: the boundary-flicker
# sweep (§5) is deferred; 0.5 (half the cut threshold) is the shipping default pending it.


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
    """Behavioral entropy of a run: of every *behavioral* symbol touch across its ops (`is_behavioral`
    -- a named entity or whole file), the fraction that *creates* (`before is None`) or *removes*
    (`is_bottom(after)`) a symbol -- a change to what the feature does -- versus *modifies* one
    already alive (a tweak). Anchors and residue-ordering internals are not behaviour, so they never
    sway the score: appending a new entity shifts the residue gap before/after it, and counting that
    shift would sink a genuinely-novel run below the cut threshold (the bug that merged three
    distinct saves into one car). A run with no behavioral touches (only bookkeeping) scores 0."""
    structural = total = 0
    for op in ops:
        for sym, (before, after) in op.footprint.items():
            if not is_behavioral(sym):
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
    gb = GitBinding(repo)
    rows = gb.history()
    commit_index = {sha: i for i, (sha, _p, _s) in enumerate(rows)}
    subject_of = {sha: subject for sha, _p, subject in rows}
    ops = opindex.index_ops(repo)
    by_id = {op.id: op for op in ops}
    # Same time-axis rule as `history_view`/`group.atoms` (`opindex.earliest_commit_sha`): an op's
    # earliest in-history provenance, or -- for a pending, provenance-less save -- the earliest
    # committed `Sgt-Op:` trailer. Without the fallback, just-saved work is dropped from every run,
    # so its per-save cars never appear on the graph.
    sha_of = opindex.earliest_commit_sha(gb, rows, ops)

    # (feature, commit_index) -> op ids
    buckets: dict[tuple[str, int], list[str]] = {}
    sha_at: dict[int, str] = {}
    for op in ops:
        leaf = op_leaf.get(op.id)
        if leaf is None:
            continue
        earliest = sha_of.get(op.id)
        if earliest is None:
            continue
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


def _boundary_score(a: Run, b: Run, prior_boundaries: frozenset[str] = frozenset()) -> float:
    """How strongly runs `a` -> `b` (consecutive in one feature) should be cut apart. Scope shift
    and a dormancy gap are full boundaries; a novel `b` opens a chapter proportional to how much
    new behavior it introduces. A same-scope, no-gap, modify-only `b` scores 0 and merges. A seam
    whose `b` already *started a chapter* in the persisted record gets `SEAM_BONUS` -- pure
    hysteresis (§3.4), sub-threshold, so it can only keep a near-threshold boundary, never add one."""
    score = 0.0
    if a.scope and b.scope and a.scope != b.scope:
        score += W_SCOPE
    if (b.commit_index - a.commit_index) >= GAP_THRESHOLD:
        score += W_GAP
    score += b.novelty * W_NOVELTY
    if b.commit_sha in prior_boundaries:
        score += SEAM_BONUS
    return score


def _cut_points(runs: list[Run], prior_boundaries: frozenset[str] = frozenset()) -> list[int]:
    """Indices `k` (1..len-1) where run `k` starts a new segment, cheapest-seam-first capped at
    `MAX_SEGMENTS`. First collects every seam scoring >= `CUT_THRESHOLD`; if that leaves too many
    segments, drops the lowest-scoring seams until within the cap (a long-lived feature stays
    readable offline). Deterministic: ties spread evenly (`_cap_cuts`) rather than broken by
    commit-index, so a pathological feature where every seam ties (e.g. every commit its own
    scope) merges into a few evenly-sized cars, not a wall of untouched singles plus one lopsided
    blob at whichever end commit-index ordering favored. `prior_boundaries` (persisted chapter
    starts) biases the score toward keeping last build's boundaries (§3.4 hysteresis)."""
    seams = [(i, _boundary_score(runs[i - 1], runs[i], prior_boundaries)) for i in range(1, len(runs))]
    cuts = [i for i, s in seams if s >= CUT_THRESHOLD]
    if len(cuts) + 1 > MAX_SEGMENTS:
        cuts = _cap_cuts(cuts, dict(seams), MAX_SEGMENTS - 1)
    return cuts


def _cap_cuts(cuts: list[int], score_of: dict[int, float], n_keep: int) -> list[int]:
    """Keep the `n_keep` strongest of `cuts` (score descending); within a tied score group, spread
    the kept subset evenly across the group (`_evenly_spaced`) rather than favoring one end. An
    index-ordered tie-break always drops the same-side cuts first, so a feature with many
    identically-scored seams (every run a fresh scope, say) collapses to a string of single-run
    segments plus one oversized trailing/leading merge -- exactly the "many slivers" shape the cap
    exists to avoid. Spreading ties keeps the resulting segments close to evenly sized instead."""
    if n_keep <= 0:
        return []
    ranked = sorted(cuts, key=lambda i: -score_of[i])
    groups: list[list[int]] = []
    for i in ranked:
        if groups and score_of[groups[-1][0]] == score_of[i]:
            groups[-1].append(i)
        else:
            groups.append([i])
    kept: list[int] = []
    for group in groups:
        room = n_keep - len(kept)
        if room <= 0:
            break
        group_sorted = sorted(group)  # natural seam order within the tie
        kept.extend(group_sorted if len(group_sorted) <= room else _evenly_spaced(group_sorted, room))
    return sorted(kept)


def _evenly_spaced(items: list[int], k: int) -> list[int]:
    """`k` of `items` (already sorted), spread evenly across the list rather than the first `k` --
    so capping a tie merges runs throughout the span instead of piling every merge at one end."""
    if k >= len(items):
        return list(items)
    if k <= 0:
        return []
    step = len(items) / k
    return [items[min(int(i * step), len(items) - 1)] for i in range(k)]


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


def _partition_runs(runs: list[Run], prior_boundaries: frozenset[str] = frozenset()) -> list[list[Run]]:
    """Group chronological runs into contiguous chunks at `_cut_points`' boundaries -- the capped
    partition both `segment_runs` and `overlay_persisted`'s un-persisted tail share, so `MAX_SEGMENTS`
    bounds a feature's segment count the same way regardless of which path produced the runs."""
    if not runs:
        return []
    cuts = set(_cut_points(runs, prior_boundaries))
    groups: list[list[Run]] = []
    current: list[Run] = []
    for i, run in enumerate(runs):
        if i in cuts and current:
            groups.append(current)
            current = []
        current.append(run)
    if current:
        groups.append(current)
    return groups


def segment_runs(runs: list[Run], prior_boundaries: frozenset[str] = frozenset()) -> list[Segment]:
    """Rung 1: cut one feature's chronological runs into contiguous `Segment`s. Every run lands in
    exactly one segment (a total partition of the feature's ops -- KTD2), boundaries chosen by
    `_cut_points`, each segment labeled from its own commits. `source="fallback"`; the LLM rung
    renames/re-cuts on top of this. `prior_boundaries` (persisted chapter starts) applies the §3.4
    seam hysteresis; empty (the default) is byte-identical to a first, prior-free cut."""
    return [_finish_segment(m, i) for i, m in enumerate(_partition_runs(runs, prior_boundaries))]


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


def checkpoint_slug(label: str) -> str:
    """A stable, typeable handle derived from a checkpoint's label: lowercase, every run of
    non-alphanumerics collapsed to a single `-`, trimmed, capped at 24 chars (on a `-` boundary
    where one is near the cap, so we never end on a half word). `"validate email"` ->
    `validate-email`; `"fix(effects): resolve the leak"` -> `fix-effects-resolve`. Empty (a
    label with no alphanumerics) is possible -- callers treat an empty slug as "no slug match"."""
    s = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    if len(s) <= 24:
        return s
    cut = s[:24].rstrip("-")
    dash = cut.rfind("-")
    if dash >= 16:  # a boundary close enough to the cap -- end there, not mid-word
        cut = cut[:dash]
    return cut


def resolve_checkpoint(repo: str | Path, spec: str) -> tuple[frozenset[str], str] | None:
    """Resolve a `<feature>@<n>` or `<feature>:<slug>` checkpoint spec to `(op_ids,
    display_label)`, or `None` if it isn't a checkpoint spec / doesn't resolve. `<feature>` matches
    a feature id exactly, else a unique id *prefix*, else a feature *label* (case-insensitive).
    `<n>` is the 0-based segment index in that feature's chronological segmentation; `<slug>` is a
    `checkpoint_slug` of the segment's label -- the meaningful handle, since `@n` is a positional
    counter reassigned on every rebuild. A slug matching two segments (identical labels) is
    ambiguous and does not resolve -- `@n` stays the disambiguator. The returned `op_ids` is the
    segment's deterministic op-set -- exactly what `sgt revert` removes -- so a checkpoint revert
    runs the identical `plan_revert_op_set` path every other revert uses (the KTD6 safety
    invariant: the boundary/label may be LLM-chosen, the op membership never is)."""
    # Split into the feature part and a selector, either @<index> or :<slug>. Feature ids are
    # `f-XXXXXXXX` (no `@`/`:`), so a right-partition cleanly isolates the selector; `@` wins when
    # both appear (an explicit index is unambiguous).
    if "@" in spec and spec.rpartition("@")[2].isdigit():
        feat_part, _, sel = spec.rpartition("@")
        by_index, want = True, sel
    elif ":" in spec:
        feat_part, _, sel = spec.rpartition(":")
        by_index, want = False, checkpoint_slug(sel)
    else:
        return None
    if not feat_part or not want:
        return None

    from sgt import state
    from sgt.lens.tree import load as load_tree

    tree_result = load_tree(repo)
    if tree_result is None:
        return None
    op_leaf = tree_result["op_leaf"]
    nodes = tree_result["nodes"]

    feature_id = feat_part if feat_part in nodes else None
    if feature_id is None and feat_part:  # a unique feature-id *prefix* -- `f-`-prefixed or the bare hex the graph prints
        prefix_hits = [nid for nid in nodes
                       if (nid.startswith(feat_part) or nid.startswith("f-" + feat_part))
                       and not nodes[nid]["children"]]
        if len(prefix_hits) == 1:
            feature_id = prefix_hits[0]
    if feature_id is None:  # else a case-insensitive label match against leaf features
        want_feat = feat_part.strip().lower()
        matches = [nid for nid, nd in nodes.items()
                   if not nd["children"] and nd.get("label", "").strip().lower() == want_feat]
        if len(matches) == 1:
            feature_id = matches[0]
    if feature_id is None:
        return None

    runs = feature_runs(repo, op_leaf).get(feature_id)
    if not runs:
        return None
    persisted = state.load_json(repo, "intent_segments", default={})
    segs = overlay_persisted(runs, persisted.get(feature_id))
    label = nodes.get(feature_id, {}).get("label", feature_id)

    if by_index:
        idx = int(want)
        if not (0 <= idx < len(segs)):
            return None
        return segs[idx].op_ids, f"{label}@{idx}: {segs[idx].label}"

    hits = [(i, s) for i, s in enumerate(segs) if checkpoint_slug(s.label) == want]
    if len(hits) != 1:  # 0 = unknown slug, >1 = ambiguous -- `@n` disambiguates either way
        return None
    idx, seg = hits[0]
    return seg.op_ids, f"{label}@{idx}: {seg.label}"


def overlay_persisted(runs: list[Run], record: list[dict] | None) -> list[Segment]:
    """Build one feature's segments, preferring persisted LLM boundaries+labels when present.

    `record` is `.sgt/intent/segments.json`'s per-feature entry (written by `sgt intent build`):
    a chronological list of `{commit_shas, label, rationale, source}`. Each entry re-groups the
    feature's runs by *commit sha* -- so op membership is still a deterministic function of which
    whole runs the segment covers, never anything the LLM emitted (the KTD6 safety invariant). Runs
    the record doesn't mention (commits that landed since the last build) are never dropped -- they
    still cover the whole partition -- but they are cut the same capped way `segment_runs` cuts a
    never-built feature (`_partition_runs`), not one raw segment per commit; otherwise a feature
    with a stale record could grow an unbounded trailing wall of single-op chapters as commits
    accrue, exactly the "many slivers" granularity problem `MAX_SEGMENTS` exists to prevent.
    `record=None` (never built, or the feature is new) falls straight through to the deterministic
    rung-1 cut."""
    if not record:
        return segment_runs(runs)

    run_by_sha = {r.commit_sha: r for r in runs}
    claimed: set[str] = set()
    groups: list[tuple[list[Run], dict | None]] = []
    for entry in record:
        member = [run_by_sha[sha] for sha in entry.get("commit_shas", [])
                  if sha in run_by_sha and sha not in claimed]
        if member:
            claimed.update(r.commit_sha for r in member)
            groups.append((sorted(member, key=lambda r: (r.commit_index, r.commit_sha)), entry))

    # Runs the record doesn't name (newer commits) still need to land somewhere -- cut them with
    # the same capped partition a from-scratch feature would get, so the total segment count for
    # this feature can't grow without bound just because it has a partial/stale persisted record.
    leftover = sorted((r for r in runs if r.commit_sha not in claimed),
                      key=lambda r: (r.commit_index, r.commit_sha))
    for member in _partition_runs(leftover):
        groups.append((member, None))

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


def prior_boundaries(record: list[dict] | None) -> frozenset[str]:
    """The commit shas that *start* a chapter in a persisted per-feature record -- the seams the
    §3.4 rung-1 hysteresis (`SEAM_BONUS`) prefers to keep. Each record entry's first sha; empty for
    a never-built feature."""
    return frozenset(
        entry["commit_shas"][0] for entry in (record or []) if entry.get("commit_shas")
    )


def deterministic_segments(repo: str | Path, op_leaf: dict[str, str]) -> dict[str, list[Segment]]:
    """The whole offline pass: every feature's runs, cut into segments (rungs 0/1). The one entry
    point a caller (`intent_view`, `theme_segment`'s fallback) needs when no LLM is available.
    Features iterated in sorted id order for a stable projection. Deterministic given repo state:
    when a prior `intent_segments` record exists, its chapter starts bias the cut (`SEAM_BONUS`
    hysteresis) so a re-derivation doesn't flicker a near-threshold boundary the last build drew;
    with no record it is byte-identical to a first, prior-free cut."""
    from sgt import state

    runs = feature_runs(repo, op_leaf)
    record = state.load_json(repo, "intent_segments", default={})
    return {fid: segment_runs(runs[fid], prior_boundaries(record.get(fid))) for fid in sorted(runs)}
