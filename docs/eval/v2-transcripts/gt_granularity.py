#!/usr/bin/env python3
"""Sensitivity of WP-V2's pairwise F1 to the *ground truth's* granularity.

    python docs/eval/v2-transcripts/gt_granularity.py symbols-<name>.json --repo <path>

Why this exists, and why it is a diagnostic rather than a replacement metric (R2/R3): the coded error
sample (`sample_errors.py`, seed 20260814) showed that the false-positive mass on CodeNav is pairs whose
two symbols sit in ONE sgt leaf while the transcript assigns them to two different requests -- and those
requests are consecutive turns of one piece of work ("ship the content language", then "make the vscode
extension display it", then "how do I rebuild an english doc in chinese", then "I cannot switch back").
The pre-registered ground truth calls each turn a separate cluster, so sgt is charged a false positive
for grouping work a human would call one feature.

That is a claim about the *unit* of the ground truth, so it is tested by varying the unit and reporting
the whole curve rather than picking the flattering point:

* `k=1` is the pre-registered primary: one user turn = one cluster.
* `k=2,3,5` merge runs of consecutive turns *within a session*, in timestamp order.
* `session` merges every turn of a session.

If F1 climbs steeply with k, the primary is measuring turn-taking rather than clustering quality. If it
stays flat, the turn boundary was not the problem and sgt's split is real. Both readings are reported.
Nothing here is offered as the headline number: coarsening the ground truth after seeing the errors
would be exactly the post-hoc move R3 forbids, which is why every k is printed.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from score_pairs import pairs, sgt_symbol_features


def blocks(requests: list[dict], k: int | None) -> dict[str, str]:
    """request_id -> the id of the cluster it belongs to. `k=None` means whole-session clusters."""
    by_session: dict[str, list[dict]] = defaultdict(list)
    for req in requests:
        by_session[req.get("session") or "?"].append(req)
    out: dict[str, str] = {}
    for session, reqs in by_session.items():
        reqs.sort(key=lambda r: r.get("ts") or "")
        for i, req in enumerate(reqs):
            out[req["request_id"]] = session if k is None else f"{session}#{i // k}"
    return out


def score(universe: list[str], gt_map: dict[str, set[str]],
          sgt_map: dict[str, set[str]]) -> tuple[float, float, float, int, float]:
    gt_by: dict[str, set[str]] = defaultdict(set)
    sgt_by: dict[str, set[str]] = defaultdict(set)
    for s in universe:
        for c in gt_map[s]:
            gt_by[c].add(s)
        for f in sgt_map[s]:
            sgt_by[f].add(s)
    gt_p, sgt_p = pairs(list(gt_by.values())), pairs(list(sgt_by.values()))
    hit = gt_p & sgt_p
    p = len(hit) / len(sgt_p) if sgt_p else 0.0
    r = len(hit) / len(gt_p) if gt_p else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    total = len(universe) * (len(universe) - 1) // 2
    return p, r, f1, len(gt_by), len(gt_p) / total if total else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", type=Path)
    ap.add_argument("--repo", type=Path, required=True)
    args = ap.parse_args()

    data = json.loads(args.symbols.read_text())
    sgt_map, _breakdown, _kinds = sgt_symbol_features(args.repo.expanduser().resolve())
    requests = [r for r in data["requests"] if r["symbols"]]

    gt_raw: dict[str, set[str]] = defaultdict(set)
    for req in requests:
        for s in req["symbols"]:
            gt_raw[s].add(req["request_id"])
    universe = sorted(set(gt_raw) & set(sgt_map))

    name = args.symbols.stem.replace("symbols-", "")
    print(f"{name}: {len(universe)} symbols, {len(requests)} turns with edits")
    print("  ground-truth unit     clusters  base rate  precision  recall     F1")
    for label, k in (("k=1 (pre-registered)", 1), ("k=2 turns", 2), ("k=3 turns", 3),
                     ("k=5 turns", 5), ("whole session", None)):
        block_of = blocks(requests, k)
        gt_map: dict[str, set[str]] = defaultdict(set)
        for s in universe:
            for rid in gt_raw[s]:
                gt_map[s].add(block_of[rid])
        p, r, f1, n_clusters, base = score(universe, gt_map, sgt_map)
        print(f"  {label:21s} {n_clusters:8d}  {base:8.1%}  {p:9.3f}  {r:6.3f}  {f1:5.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
