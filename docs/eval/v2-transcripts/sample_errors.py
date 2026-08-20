#!/usr/bin/env python3
"""WP-V2 step 6: draw a seeded sample of wrong pairs and lay out the evidence needed to code each.

    python docs/eval/v2-transcripts/sample_errors.py symbols-<name>.json --repo <path> --out <dir>

Sampling rule, fixed before any wrong pair was read (R2/R3):

* **Stratified 15 + 15, not proportional.** Recall is far below precision on every repo scored so far,
  so the error mass is overwhelmingly false negatives; a proportional draw of 30 would be ~29 of one
  kind and tell us nothing about the other. The taxonomy's job is to name the *kinds* of error —
  their relative frequency is already reported, exactly, by precision and recall. Each stratum's full
  population size is emitted alongside the sample so a reader can reweight.
* **Seed 20260814**, the seed the plan pre-registers, and the draw is over a sorted pair list so it
  reproduces from the seed alone.
* Both directions are drawn, per R4: `false_negative` = one request, two features (sgt split the work);
  `false_positive` = one feature, two requests (sgt lumped separate work together).

The four codes the plan names, with the rule used for each:

* `split` — a false negative whose two symbols are genuinely one piece of work. sgt's failure.
* `lumped` — a false positive whose two symbols are genuinely separate work. sgt's failure.
* `identity-break` — the pair fails because one symbol changed id (rename/move) between the edit and
  HEAD, so the two sides are talking about different names for one thing. `tree.json`'s
  `identity_events` are dumped for each sampled symbol so this is checkable rather than guessed.
* `extractor-artifact` — the *ground truth* is wrong: the transcript segmenter cut one request into two
  or merged two into one, or an edit was attributed to a symbol it did not really change. These do not
  count against sgt, but per the plan they stay in the published table so the instrument's error share
  is visible.
* `other` — anything the four above do not fit. If this is not near zero the codebook is wrong.

Coding is done by the author, which is the same single-coder limitation as step 5's ceiling; it is
recorded in the output as `coded_by: author` rather than presented as independent.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from score_pairs import bare, pairs, sgt_symbol_features

SEED = 20260814
PER_STRATUM = 15


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", type=Path)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    repo = args.repo.expanduser().resolve()
    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.symbols.read_text())

    sgt_map, _breakdown, _kinds = sgt_symbol_features(repo)
    tree = json.loads((repo / ".sgt/tree/tree.json").read_text())["data"]
    labels = {n["id"]: n.get("label") or "" for n in tree["nodes"].values()}
    identity = tree.get("identity_events") or []

    # Which record kind(s) carry each symbol into a leaf, and under which leaf -- the same
    # normalization score_pairs uses, kept here so a coder can see "residue" vs "edited".
    kind_of: dict[str, set[str]] = defaultdict(set)
    for node in tree["nodes"].values():
        if node.get("children"):
            continue
        for member in node.get("members") or []:
            sym, kind = bare(member)
            kind_of[sym].add(kind)

    gt_map: dict[str, set[str]] = defaultdict(set)
    request_text: dict[str, str] = {}
    for req in data["requests"]:
        request_text[req["request_id"]] = (req.get("text") or req.get("request") or "")[:300]
        for s in req["symbols"]:
            gt_map[s].add(req["request_id"])

    universe = sorted(set(gt_map) & set(sgt_map))
    gt_by: dict[str, set[str]] = defaultdict(set)
    sgt_by: dict[str, set[str]] = defaultdict(set)
    for s in universe:
        for c in gt_map[s]:
            gt_by[c].add(s)
        for f in sgt_map[s]:
            sgt_by[f].add(s)
    gt_pairs = pairs(list(gt_by.values()))
    sgt_pairs = pairs(list(sgt_by.values()))

    strata = {
        "false_negative": sorted(gt_pairs - sgt_pairs),
        "false_positive": sorted(sgt_pairs - gt_pairs),
    }
    rng = random.Random(SEED)
    sample = []
    for name, population in strata.items():
        drawn = population if len(population) <= PER_STRATUM else rng.sample(population, PER_STRATUM)
        for a, b in sorted(drawn):
            sample.append({
                "stratum": name,
                "symbols": [a, b],
                "same_file": a.split("::", 1)[0] == b.split("::", 1)[0],
                "requests": {a: sorted(gt_map[a]), b: sorted(gt_map[b])},
                "features": {a: sorted((f, labels.get(f, "")) for f in sgt_map[a]),
                             b: sorted((f, labels.get(f, "")) for f in sgt_map[b])},
                "record_kinds": {a: sorted(kind_of[a]), b: sorted(kind_of[b])},
                "identity_events": [e for e in identity if any(
                    s in json.dumps(e) for s in (a, b))],
                "code": None,
                "note": None,
            })

    name = args.symbols.stem.replace("symbols-", "")
    report = {
        "repo": str(repo), "seed": SEED, "per_stratum": PER_STRATUM, "coded_by": "author",
        "populations": {k: len(v) for k, v in strata.items()},
        "request_text": request_text,
        "sample": sample,
    }
    (out / f"errors-{name}.json").write_text(json.dumps(report, indent=1))

    print(f"{name}: {len(sample)} pairs drawn (seed {SEED}) from "
          + ", ".join(f"{len(v)} {k}" for k, v in strata.items()))
    for row in sample:
        a, b = row["symbols"]
        fa = ",".join(f"{i}:{lab[:28]}" for i, lab in row["features"][a])
        fb = ",".join(f"{i}:{lab[:28]}" for i, lab in row["features"][b])
        tag = "FN" if row["stratum"] == "false_negative" else "FP"  # not [:2]: both start "fa"
        print(f"  [{tag}] {a}  ({'/'.join(row['requests'][a])} | {fa}"
              f" | {'/'.join(row['record_kinds'][a])})")
        print(f"       {b}  ({'/'.join(row['requests'][b])} | {fb}"
              f" | {'/'.join(row['record_kinds'][b])}){'  [same file]' if row['same_file'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
