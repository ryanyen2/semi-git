"""Phase 5 cohesion/stability harness (feature-timeline redesign plan, 2026-07-21): measures the
*production* clustering pipeline (`sgt.lens.cluster` / `sgt.lens.tree`) against the metrics Phase 5
is gated on, before any signal-weight change is trialed near `_fuse` (`tree.py`) / `cluster.signals`:

  - per-leaf co-commit cohesion: what fraction of each feature's co-commit (episode) edge weight
    stays within that feature vs. leaks to another one. 1.0 = every co-commit episode touching
    this feature's members stays inside it; near 0 = the feature is glued to others almost
    entirely by cross-feature episodes.
  - cross-feature edge mass: the fraction of the fused graph's total weight that
    `tree.feature_edges` rolls up across leaf boundaries -- the "how blobby is the cut" number.
  - Greene id-stability: rebuild from scratch (`force_rebuild=True`, the `sgt map --rebuild` path)
    against the currently persisted tree, never saved, and measure what fraction of previously-
    known feature ids continue (event="continuation"/"merge") rather than die.

Read-only against the target repo: every call passes `refresh_cache=False` / `refresh_caches=False`
and nothing is ever `.save()`d -- running this against a real checkout (default: this repo) never
mutates `.sgt/tree/tree.json`, pins, or any on-disk cache.

The plan's Phase 5 spec also names "the stress projects noted in memory" (`scripts/graph_stress/`,
a 5-project LLM-agent corpus) as an additional target. That corpus's source was removed from the
tree at 740594d5 ("Nl to sgt commands") -- recoverable via
`git show 740594d5~1:scripts/graph_stress/driver.py` -- and it exists to measure a *different*
thing (compose-conflict / drift rate for the contracts spike,
`docs/plans/2026-06-30-001-feat-contracts-substrate-spike-plan.md`), not clustering cohesion.
Reviving an LLM-agent-driven multi-project corpus is a large side-quest orthogonal to this gate;
this repo's own 200+-commit history is real, organic, feature-shaped history and is a sufficient
(and more directly relevant) subject for a clustering-quality regression gate. `run()` takes any
repo path, so pointing it at another checkout later is a one-line change if that need reappears.

Usage:
    .venv/bin/python -m experiments.patch_clustering.cohesion_harness [REPO_PATH ...]
    .venv/bin/python -m experiments.patch_clustering.cohesion_harness --replay [REPO_PATH ...]

`--replay` runs the Phase 0 (temporal-prior plan, 2026-07-28) replay protocol instead: it
reconstructs the op store at a sequence of historical commits and runs the production incremental
`tree.build` at each, recording the α=0 identity/agreement/cohesion/label-churn baseline the
Phase B temporal prior is measured against. Also read-only against the target repo.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from math import log2
from time import perf_counter
from pathlib import Path

from sgt.core import opindex
from sgt.core.lens import current_ideal, ideal_for_ref
from sgt.lens import cluster, tree
from sgt.lens.map import _op_touch_weights
from sgt.store.gitbind import GitBinding


def cohesion(nodes: dict, ops: list, node_set: set[str], hubs: set[str]) -> dict[str, float]:
    """Per-leaf co-commit cohesion (see module docstring). Leaves with no scored co-commit weight
    at all (e.g. every member is a lone residue segment) are simply absent from the result -- there
    is nothing to be cohesive or incoherent about."""
    member_leaf = tree.leaf_member_index(nodes)
    commit = cluster.commit_edges(ops, node_set, hubs)
    internal: dict[str, float] = {}
    total: dict[str, float] = {}
    for pair, w in commit.items():
        a, b = tuple(pair)
        leaf_a, leaf_b = member_leaf.get(a), member_leaf.get(b)
        if leaf_a is None or leaf_b is None:
            continue
        for leaf in {leaf_a, leaf_b}:
            total[leaf] = total.get(leaf, 0.0) + w
        if leaf_a == leaf_b:
            internal[leaf_a] = internal.get(leaf_a, 0.0) + w
    return {leaf: internal.get(leaf, 0.0) / t for leaf, t in total.items() if t > 0}


def cross_feature_mass(nodes: dict, fused: dict[frozenset, float]) -> float | None:
    """Fraction of the fused graph's total edge weight that crosses a leaf boundary. `None` when
    the fused graph carries no weight at all (an empty/degenerate repo) -- there is no mass to
    divide."""
    total = sum(fused.values())
    if total <= 0:
        return None
    cross = sum(e["weight"] for e in tree.feature_edges(nodes, fused))
    return cross / total


def continuation_rate(previous_nodes: dict, events: list[dict]) -> dict:
    """What fraction of `previous_nodes`' leaves continue (are not named in a `death` event) after
    a rebuild. The number Phase 5's later signal-weight trials must not regress."""
    old_leaves = {nid for nid, nd in previous_nodes.items() if not nd["children"]}
    deaths = {e["feature_id"] for e in events if e["event"] == "death"}
    continued = old_leaves - deaths
    rate = len(continued) / len(old_leaves) if old_leaves else None
    by_kind = {
        kind: sum(1 for e in events if e["event"] == kind)
        for kind in ("continuation", "merge", "split", "birth", "death")
    }
    return {"old_leaf_count": len(old_leaves), "continuation_rate": rate, "events_by_type": by_kind}


def greene_stability(repo: Path, ops: list, ideal, previous: dict | None) -> dict:
    """Rebuild from scratch against the currently persisted tree (never saved) and score
    `continuation_rate` on the result. `previous=None` (a repo with no committed tree yet) reports
    an empty/no-op stability record rather than fabricating a rate with no denominator."""
    if previous is None or not previous.get("nodes"):
        return {"old_leaf_count": 0, "continuation_rate": None, "events_by_type": {}}
    rebuilt = tree.build(repo, ops, ideal, previous=previous, force_rebuild=True, refresh_caches=False)
    return continuation_rate(previous["nodes"], rebuilt["identity_events"])


def plurality_agreement(old_leaves: dict, new_leaves: dict) -> float | None:
    """Σ_L max_c |L ∩ c| / |common members| over members present in both cuts (plan §3.1): the
    fraction of old-leaf members that land in their old leaf's plurality new-leaf. 1.0 = every old
    leaf maps wholly into one new leaf; lower = old leaves shattered across new ones. `None` when
    the two cuts share no member (nothing to agree about). This is the objective the Phase B
    anchored-CPM prior maximizes, so it is the primary before/after number for the α sweep."""
    old_of = {m: lid for lid, ms in old_leaves.items() for m in ms}
    new_of = {m: cid for cid, ms in new_leaves.items() for m in ms}
    common = set(old_of) & set(new_of)
    if not common:
        return None
    per_old: dict[str, Counter] = {}
    for m in common:
        per_old.setdefault(old_of[m], Counter())[new_of[m]] += 1
    agree = sum(c.most_common(1)[0][1] for c in per_old.values())
    return agree / len(common)


def variation_of_information(old_leaves: dict, new_leaves: dict) -> float | None:
    """Meilă variation of information (bits) between the two cuts over their common members: 0 =
    identical partition, growing with both splits and merges. Complements plurality agreement --
    plurality is blind to a leaf that *merges* with another (every member still finds a plurality),
    VI charges for it. `None` when the cuts share no member."""
    old_of = {m: lid for lid, ms in old_leaves.items() for m in ms}
    new_of = {m: cid for cid, ms in new_leaves.items() for m in ms}
    common = set(old_of) & set(new_of)
    n = len(common)
    if n == 0:
        return None
    a = Counter(old_of[m] for m in common)
    b = Counter(new_of[m] for m in common)
    joint = Counter((old_of[m], new_of[m]) for m in common)
    vi = 0.0
    for (x, y), nxy in joint.items():
        pxy = nxy / n
        vi -= pxy * (log2(pxy / (a[x] / n)) + log2(pxy / (b[y] / n)))
    return vi


def spurious_churn(events: list[dict], prev_leaves: dict, cur_leaves: dict, theta: float = tree.THETA) -> int:
    """Deaths that are really renames: a dead old leaf whose member set still Jaccard-overlaps some
    born new leaf ≥ θ (the same threshold Greene matched on). A non-zero count means feature
    identity was dropped where the members say it should have carried -- exactly the churn the
    Phase B temporal prior is meant to suppress, so this is the identity-stability regression number."""
    deaths = [e["feature_id"] for e in events if e["event"] == "death"]
    births = [e["feature_id"] for e in events if e["event"] == "birth"]
    birth_sets = [cur_leaves[b] for b in births if b in cur_leaves]
    churned = 0
    for d in deaths:
        dm = prev_leaves.get(d)
        if dm is not None and any(tree._jaccard(dm, bm) >= theta for bm in birth_sets):
            churned += 1
    return churned


def _mock_labeler(repo: Path):
    """A deterministic, offline stand-in for the LLM labeler: each cluster's name is a hash of its
    membership-driven prompt body (position-independent -- the batch header and any trailing
    whitespace are stripped before hashing), so identical members always yield the same label and
    graded reuse (§3.2) can be exercised across the replay without a live API. `.calls` still counts
    exactly as the real batch path does, so the label-churn / calls-saved numbers are faithful."""
    from sgt.lens import label as label_mod

    class _Usage:
        input_tokens = 1
        output_tokens = 1

    class _Resp:
        def __init__(self, parsed):
            self.output_parsed = parsed
            self.usage = _Usage()

    class _MockResponses:
        def parse(self, **kwargs):
            groups = kwargs["input"].split("=== Group ")[1:]
            items = []
            for g in groups:
                idx = int(g.split(" ", 1)[0])
                body = g.partition("\n")[2].strip()
                digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
                items.append(label_mod._BatchItem(index=idx, label=f"L{digest}", rationale="mock"))
            return _Resp(label_mod._FeatureLabelBatch(items=items))

    class _MockClient:
        def __init__(self):
            self.responses = _MockResponses()

    labeler = label_mod.Labeler(repo)
    labeler._client = _MockClient()
    return labeler


def _label_churn(prev_labels: dict[str, str], cur_labels: dict[str, str]) -> dict:
    """How many feature ids common to both steps changed label. The churn number graded reuse
    (§3.2) drives down; `renamed`/`common` is the rate."""
    common = set(prev_labels) & set(cur_labels)
    renamed = sum(1 for fid in common if prev_labels[fid] != cur_labels[fid])
    return {"common": len(common), "renamed": renamed}


def replay(repo: str | Path, window: int = 30) -> dict:
    """Phase 0 replay protocol (plan §4): reconstruct the op store at each commit of a contiguous
    tail window (the last `window` commits) and run the *production* incremental `tree.build` at
    each, feeding the previous commit's result as `previous` -- the exact path `sgt map` takes
    across two real, *adjacent* commits. Records, per step, the identity events + spurious churn,
    plurality agreement + VI against the prior cut, per-leaf cohesion + cross-feature edge mass,
    label churn + LLM (mock) calls, and wall-clock. This is the α=0 baseline the Phase B temporal
    prior is measured against.

    The window is *consecutive* commits, not a sparse sample across all history: "exactly the
    production incremental path" means one commit at a time, so each transition is a real
    small-edit incremental splice. A sparse sample (points thousands of ops apart) would make every
    step a near-total recluster and measure the sampling gap, not incremental stability. The
    window's first commit is cold-seeded (`previous=None`, a full resplit -- the natural seed); the
    measured transitions are all the adjacent steps after it.

    Faithful *and* read-only: the incremental path reads the fused-graph snapshot of the previous
    build (`tree._load_fused_snapshot`, normally the on-disk cache keyed by tree fingerprint). Here
    that lookup is redirected to an in-memory `{fingerprint: fused}` store populated from each
    build's returned `_fused`, so the historical replay never reads the real repo's (wrong-
    fingerprint) snapshot and never writes one -- no disk state is touched. Structural edges are
    read at each historical `head` (threaded through `build`), so the coupling signal matches that
    point in time; `refresh_caches=False` keeps even the head-keyed structural cache read-only.

    The chain feeds each build the *pre-label* result of the previous point (`build` is label-free;
    identity/splice never read labels). Production's `label_tree` DEDUP -- a merge of siblings the
    LLM named identically -- is a semantic-label-driven post-process that can't be reproduced
    offline (a mock labeler's hash-names would merge arbitrarily and corrupt the chained tree), so
    it is deliberately omitted here. Label churn is instead measured directly against Phase A's
    graded reuse (`_measure_labels`) without mutating the tree: this counts calls-saved and renames
    faithfully while leaving the clustering-stability numbers driven purely by `build`."""
    repo = Path(repo)
    gb = GitBinding(repo)
    all_ops = opindex.index_ops(repo)
    shas = [sha for sha, _parent, _subj in gb.history()]  # oldest-first
    if len(shas) < 2:
        return {"repo": str(repo), "alpha": getattr(cluster, "STABILITY_ALPHA", 0.0), "points": [],
                "note": "fewer than 2 commits -- nothing to replay"}
    points = shas[-window:]

    snap_store: dict[str, dict] = {}
    orig_load = tree._load_fused_snapshot
    tree._load_fused_snapshot = lambda _repo, fingerprint: snap_store.get(fingerprint)
    labeler = _mock_labeler(repo)

    prev_result: dict | None = None
    prev_leaves: dict = {}
    prev_labels: dict[str, str] = {}
    steps: list[dict] = []
    try:
        for sha in points:
            ref_commits = set(gb.commit_shas(sha))
            ops_s = [op for op in all_ops if set(op.provenance) & ref_commits]
            ideal_s = ideal_for_ref(repo, sha)

            t0 = perf_counter()
            result = tree.build(repo, ops_s, ideal_s, previous=prev_result, refresh_caches=False, head=sha)
            t_build = perf_counter()
            cur_leaves = tree._leaf_members(result["nodes"])
            cur_labels = _measure_labels(result, ops_s, labeler)  # graded reuse; does not mutate result
            t_label = perf_counter()

            node_set, hubs, _cc, _st = cluster.signals(repo, ops_s, ideal_s, refresh_cache=False, head=sha)
            coh = sorted(cohesion(result["nodes"], ops_s, node_set, hubs).values())

            step: dict = {
                "sha": sha[:8],
                "n_ops": len(ops_s),
                "n_leaves": len(cur_leaves),
                "cohesion_median": coh[len(coh) // 2] if coh else None,
                "cross_feature_edge_mass": cross_feature_mass(result["nodes"], result["_fused"]),
                "llm_calls": labeler.calls,
                "wall_clock_s": {"build": round(t_build - t0, 3), "label": round(t_label - t_build, 3)},
            }
            if prev_result is not None:
                events = result["identity_events"]
                step["events_by_type"] = {
                    kind: sum(1 for e in events if e["event"] == kind)
                    for kind in ("continuation", "merge", "split", "birth", "death")
                }
                step["spurious_churn"] = spurious_churn(events, prev_leaves, cur_leaves)
                step["plurality_agreement"] = plurality_agreement(prev_leaves, cur_leaves)
                step["variation_of_information"] = variation_of_information(prev_leaves, cur_leaves)
                step["label_churn"] = _label_churn(prev_labels, cur_labels)
            steps.append(step)

            snap_store[tree._tree_fingerprint(result["nodes"])] = result["_fused"]
            prev_result, prev_leaves, prev_labels = result, cur_leaves, cur_labels
    finally:
        tree._load_fused_snapshot = orig_load

    return {"repo": str(repo), "alpha": getattr(cluster, "STABILITY_ALPHA", 0.0), "n_points": len(points), "points": steps}


def _measure_labels(result: dict, ops: list, labeler) -> dict[str, str]:
    """Name every leaf via Phase A's graded, generation-anchored reuse (`leaf_request` +
    `label_many`, op-touch weights) with the persistent mock labeler, WITHOUT mutating `result`
    (no DEDUP, no `label`/`why` written). Returns `{feature_id: label}`. The labeler's in-memory
    cache carries across steps -- never `.save()`d -- so a leaf whose membership drifts less than
    the weighted-Jaccard budget keeps its name and costs no `.calls` increment, exactly measuring
    the reuse Phase A buys over the timeline."""
    weights = _op_touch_weights(ops)
    leaves = [(fid, nd["members"]) for fid, nd in result["nodes"].items() if not nd["children"]]
    entries = [labeler.leaf_request(fid, members, weights) for fid, members in leaves]
    outs = labeler.label_many(entries)
    return {fid: fl.label for (fid, _members), fl in zip(leaves, outs)}


def run(repo: str | Path) -> dict:
    repo = Path(repo)
    ops = opindex.index_ops(repo)  # footprint/provenance only, matching build_map's own read path
    ideal = current_ideal(repo)
    node_set, hubs, _cochange, _structural = cluster.signals(repo, ops, ideal, refresh_cache=False)
    _all_nodes, fused = tree.fused_graph(repo, ops, ideal, refresh_structural_cache=False)
    previous = tree.load(repo)

    report: dict = {"repo": str(repo), "signals_version": cluster.SIGNALS_VERSION, "n_ops": len(ops)}
    if previous is not None and previous.get("nodes"):
        coh_values = sorted(cohesion(previous["nodes"], ops, node_set, hubs).values())
        report["cohesion"] = {
            "median": coh_values[len(coh_values) // 2] if coh_values else None,
            "min": coh_values[0] if coh_values else None,
            "n_leaves_scored": len(coh_values),
        }
        report["cross_feature_edge_mass"] = cross_feature_mass(previous["nodes"], fused)
    else:
        report["cohesion"] = None
        report["cross_feature_edge_mass"] = None
    report["greene_stability"] = greene_stability(repo, ops, ideal, previous)
    return report


def main(argv: list[str]) -> int:
    if "--replay" in argv:
        rest = [a for a in argv if a != "--replay"]
        repos = [Path(a) for a in rest] or [Path.cwd()]
        for repo in repos:
            print(json.dumps(replay(repo), indent=2, sort_keys=True))
        return 0
    repos = [Path(a) for a in argv] or [Path.cwd()]
    for repo in repos:
        print(json.dumps(run(repo), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
