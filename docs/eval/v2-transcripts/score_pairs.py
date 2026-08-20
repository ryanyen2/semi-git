#!/usr/bin/env python3
"""WP-V2 step 4: pairwise agreement between "same human request" and "same sgt feature".

    python docs/eval/v2-transcripts/score_pairs.py symbols-<name>.json --repo <path> --out <dir>

Ground truth comes from `map_symbols.py`: request -> the symbols its edits touched. sgt's answer is the
leaf membership of its feature tree (see `sgt_symbol_features`, which records the three wrong places I
read it from first).

Rules fixed before running, all of them forced by the data rather than chosen for a nicer number:

* **The ground truth is not a partition; sgt's side is.** A symbol can be edited by several requests
  (13-372 per repo), so ground-truth clusters overlap and a pair is positive if the two symbols
  co-occur in *at least one* request. sgt's leaves, measured, are disjoint -- no symbol is in two
  leaves. Precision and recall are computed over pairs, per R4.
* **The universe is the intersection.** Only symbols that appear in both the transcript record and
  sgt's ops are scored. A symbol sgt never saw (edited but never committed, or committed outside the
  mined range) is a coverage number, not a clustering error, and is reported as one.
* **Two baselines are printed next to every F1**, because a bare F1 here is unreadable. "One feature
  for everything" scores recall 1.0 and precision = the positive base rate, which reaches F1 0.39-0.64
  on repos with 4-11 request clusters. "Same file => same feature" is the one that matters: most leaf
  members are symbols that sat unchanged in a touched file, so if sgt cannot beat the directory tree
  it has not earned the clustering.
* **ARI needs partitions**, so it is computed on the unambiguous subset (symbols with exactly one
  request and exactly one feature) and reported with that subset's size. Secondary, as the plan says.
* **Split-error rate**: the share of request clusters whose symbols land in more than one feature.
  Secondary, and reported with the median number of features a request is spread across, because "at
  least two" and "spread over nine" are different failures.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path


def bare(symbol: str) -> tuple[str, str]:
    """`path::__residue__::Name` -> (`path::Name`, "residue"). Substantive symbols pass through."""
    path, _, rest = symbol.partition("::")
    for tag, kind in (("__residue__::", "residue"), ("__anchor__::", "anchor")):
        if rest.startswith(tag):
            return f"{path}::{rest[len(tag):]}", kind
    return symbol, "substantive"


def sgt_symbol_features(repo: Path) -> tuple[dict[str, set[str]], dict[str, int], dict[str, set[str]]]:
    """symbol -> the leaf feature(s) whose membership contains it, a record-kind breakdown, and the
    record kind(s) each symbol is carried into a leaf by.

    Membership is `.sgt/tree/tree.json`'s leaf `members` lists, which *are* symbol ids -- the tree
    clusters symbols, and `op_leaf` is a derived op->leaf vote over them (`sgt/lens/tree.py:570`).
    Three coverage traps were hit before this line was right. Each one silently turned a coverage hole
    into a metric, so each is named:

    1. Reading `sgt log --json`'s `cells`, the way `census.py` does. `cells` is the rail's display
       projection, not a membership table.
    2. Reading `op_leaf` and expanding each op's footprint. That over-attributes: an op is assigned by
       *plurality vote*, so a footprint symbol that is in no leaf inherits the leaf its neighbours voted
       for. It scored 30 of CodeNav's 255 symbols where `members` scores 16.
    3. Excluding `__residue__` records, which is right for V1's question ("what did this episode
       *edit*") and wrong for V2's ("which feature does this symbol *belong to*"). A residue record is
       how sgt says "this symbol is here, unchanged", and `_rehome_pseudo_members` deliberately files it
       under its anchor entity's lane. Excluding it dropped **184 of CodeNav's 255** symbols and left a
       6% universe. Kinds are counted and reported instead of filtered.

    Separately: a no-horizon `sgt init` mines HEAD and then backfills history *backward, one
    10s-deadline-bounded chunk per `get()` call* (`sgt/core/lens.py:24-29`, `:709`), so until
    `.sgt/local/backfill.json` says `reached_genesis` only the newest slice of history exists at all --
    53 of 261 commits, while `sgt log` still reports `commit_count: 261`. Asserted below.
    """
    state = json.loads((repo / ".sgt/local/backfill.json").read_text())["data"]
    ref = (repo / ".git/HEAD").read_text().strip().removeprefix("ref: ")
    if ref not in state:  # the tree was built for some other ref; scoring it would compare two repos
        raise SystemExit(f"{repo}: no backfill record for the checked-out ref {ref} -- "
                         f"the persisted tree belongs to {sorted(state)}. Sync this ref first.")
    if not state[ref].get("reached_genesis"):
        raise SystemExit(f"{repo}: genesis backfill unfinished on {ref} -- only the newest slice of "
                         "history is mined. Run `sgt log --refresh` until "
                         "`.sgt/local/backfill.json` reports reached_genesis, then `sgt log --rebuild`.")
    tree = json.loads((repo / ".sgt/tree/tree.json").read_text())["data"]
    out: dict[str, set[str]] = defaultdict(set)
    kinds: dict[str, set[str]] = defaultdict(set)
    for node in tree["nodes"].values():
        if node.get("children"):
            continue
        for member in node.get("members") or []:
            sym, kind = bare(member)
            out[sym].add(node["id"])
            kinds[sym].add(kind)
    breakdown = {k: sum(1 for s in kinds.values() if k in s) for k in ("substantive", "residue", "anchor")}
    return out, breakdown, kinds


def sgt_symbol_intents(repo: Path) -> dict[str, set[str]]:
    """symbol -> the *intent label* of the leaf/leaves holding it, i.e. the part of a leaf label
    before the ` · <directory>` suffix.

    Measured, not assumed: a leaf label is `<intent> · <dir>`, and one intent routinely spans several
    leaves (CodeNav: 25 intents over >1 leaf, the widest over 11). Those sibling leaves are *not*
    siblings in the hierarchy -- for 21 of the 25 the lowest common ancestor is the root, median LCA
    purity 0.017 -- so no interior node represents an intent and there is no coarser node to score
    against. Grouping leaves by this label is the only intent-level unit the tree actually offers,
    and it is what a user would have to do by hand. Diagnostic only (see the call site)."""
    tree = json.loads((repo / ".sgt/tree/tree.json").read_text())["data"]
    out: dict[str, set[str]] = defaultdict(set)
    for node in tree["nodes"].values():
        if node.get("children"):
            continue
        intent = (node.get("label") or node["id"]).split(" · ")[0].strip() or node["id"]
        for member in node.get("members") or []:
            out[bare(member)[0]].add(intent)
    return out


def pairs(clusters: list[set[str]]) -> set[tuple[str, str]]:
    got: set[tuple[str, str]] = set()
    for c in clusters:
        got.update(combinations(sorted(c), 2))
    return got


def prf(universe: list[str], gt_map: dict[str, set[str]],
        sgt_map: dict[str, set[str]]) -> dict[str, float | int]:
    """Pairwise precision/recall/F1 over one symbol universe. Used only by the grounded-subset
    diagnostic below; the pre-registered primary is computed inline in `main` and untouched."""
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
    # The subset's own base rate and null F1, without which a small-n F1 is unreadable: 16 symbols
    # drawn mostly from one request make "everything is one feature" score very well by itself.
    total = len(universe) * (len(universe) - 1) // 2
    base = len(gt_p) / total if total else 0.0
    return {"n_symbols": len(universe), "requests": len(gt_by), "features": len(sgt_by),
            "universe_pairs": total, "gt_positive": len(gt_p), "sgt_positive": len(sgt_p),
            "positive_base_rate": round(base, 4),
            "null_f1_all_one_feature": round(2 * base / (base + 1), 4) if base else 0.0,
            "precision": round(p, 4), "recall": round(r, 4),
            "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0}


def ari(labels_a: list[int], labels_b: list[int]) -> float:
    """Adjusted Rand Index, from the contingency table. No sklearn dependency (CLAUDE.md §8)."""
    from math import comb
    table: dict[tuple[int, int], int] = defaultdict(int)
    ra: dict[int, int] = defaultdict(int)
    rb: dict[int, int] = defaultdict(int)
    for a, b in zip(labels_a, labels_b):
        table[(a, b)] += 1
        ra[a] += 1
        rb[b] += 1
    n = len(labels_a)
    if n < 2:
        return float("nan")
    index = sum(comb(v, 2) for v in table.values())
    sa = sum(comb(v, 2) for v in ra.values())
    sb = sum(comb(v, 2) for v in rb.values())
    expected = sa * sb / comb(n, 2)
    maximum = (sa + sb) / 2
    return (index - expected) / (maximum - expected) if maximum != expected else 1.0


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

    sgt_map, kind_breakdown, kind_of = sgt_symbol_features(repo)
    gt_map: dict[str, set[str]] = defaultdict(set)
    for req in data["requests"]:
        for s in req["symbols"]:
            gt_map[s].add(req["request_id"])

    universe = sorted(set(gt_map) & set(sgt_map))
    only_gt = sorted(set(gt_map) - set(sgt_map))
    if len(universe) < 2:
        raise SystemExit(f"universe is {len(universe)} symbols; nothing to score")

    gt_by_cluster: dict[str, set[str]] = defaultdict(set)
    sgt_by_cluster: dict[str, set[str]] = defaultdict(set)
    for s in universe:
        for c in gt_map[s]:
            gt_by_cluster[c].add(s)
        for f in sgt_map[s]:
            sgt_by_cluster[f].add(s)

    gt_pairs = pairs(list(gt_by_cluster.values()))
    sgt_pairs = pairs(list(sgt_by_cluster.values()))
    hit = gt_pairs & sgt_pairs
    total = len(universe) * (len(universe) - 1) // 2
    precision = len(hit) / len(sgt_pairs) if sgt_pairs else 0.0
    recall = len(hit) / len(gt_pairs) if gt_pairs else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    base = len(gt_pairs) / total if total else 0.0
    null_f1 = 2 * base / (base + 1) if base else 0.0

    # Second baseline, mandatory now that residue membership is counted: most of a leaf's members are
    # symbols that sat unchanged in a file some op touched, so "same file => same feature" is close to
    # what sgt could be doing for free. If sgt does not beat this, its clustering adds nothing over the
    # directory tree.
    by_file: dict[str, set[str]] = defaultdict(set)
    for s in universe:
        by_file[s.split("::", 1)[0]].add(s)
    file_pairs = pairs(list(by_file.values()))
    fp_hit = gt_pairs & file_pairs
    file_p = len(fp_hit) / len(file_pairs) if file_pairs else 0.0
    file_r = len(fp_hit) / len(gt_pairs) if gt_pairs else 0.0
    file_f1 = 2 * file_p * file_r / (file_p + file_r) if file_p + file_r else 0.0

    # Grounded subset. NOT pre-registered: added after F31 showed that only 5-10% of the scored
    # symbols are *substantive* members of a leaf. The other 90-95% reach a leaf only through a
    # `__residue__` record, because their ops were dropped from the ideal -- one unproduced `before`
    # version (a path rename minting a nested symbol's version under the deleted path) ungrounds a
    # 237-symbol op and evicts every symbol in it. So the primary F1 mostly measures where sgt files
    # a symbol it believes was never edited. This subset asks the intended question on the symbols
    # sgt actually holds an edit for. It is a diagnostic, reported next to the primary and never in
    # place of it (R2/R3) -- and on two of three repos it is far too small to carry a claim.
    grounded = [s for s in universe if "substantive" in kind_of[s]]
    grounded_prf = prf(grounded, gt_map, sgt_map) if len(grounded) >= 2 else {"n_symbols": len(grounded)}

    # Intent-label grouping. Also NOT pre-registered, and added for the reason above: the pairwise
    # question is "does one request's work land in one feature", and a leaf is finer than a feature --
    # one intent is sharded across leaves by directory, with no interior node uniting them. This asks
    # the same question of the coarsest unit the tree offers. Reported beside the primary, not instead.
    intent_map = sgt_symbol_intents(repo)
    intent_prf = prf([s for s in universe if s in intent_map], gt_map, intent_map)

    unamb = [s for s in universe if len(gt_map[s]) == 1 and len(sgt_map[s]) == 1]
    gt_ids = {c: i for i, c in enumerate(sorted({next(iter(gt_map[s])) for s in unamb}))}
    sgt_ids = {f: i for i, f in enumerate(sorted({next(iter(sgt_map[s])) for s in unamb}))}
    ari_value = ari([gt_ids[next(iter(gt_map[s]))] for s in unamb],
                    [sgt_ids[next(iter(sgt_map[s]))] for s in unamb])

    spread = sorted(len({f for s in syms for f in sgt_map[s]}) for syms in gt_by_cluster.values())
    split_rate = sum(1 for v in spread if v > 1) / len(spread) if spread else 0.0
    median_spread = spread[len(spread) // 2] if spread else 0

    report = {
        "symbols_file": str(args.symbols), "repo": str(repo),
        "coverage": {"gt_symbols": len(gt_map), "known_to_sgt": len(universe),
                     "fraction": round(len(universe) / len(gt_map), 4),
                     "sgt_members_by_record_kind": kind_breakdown,
                     "gt_only_examples": only_gt[:12]},
        "clusters": {"requests": len(gt_by_cluster), "features": len(sgt_by_cluster)},
        "pairs": {"universe_pairs": total, "gt_positive": len(gt_pairs),
                  "sgt_positive": len(sgt_pairs), "agree": len(hit),
                  "positive_base_rate": round(base, 4)},
        "primary": {"precision": round(precision, 4), "recall": round(recall, 4),
                    "f1": round(f1, 4), "null_f1_all_one_feature": round(null_f1, 4),
                    "beats_null": f1 > null_f1,
                    "file_baseline_f1": round(file_f1, 4),
                    "file_baseline_precision": round(file_p, 4),
                    "file_baseline_recall": round(file_r, 4),
                    "beats_file_baseline": f1 > file_f1},
        "diagnostic_grounded_subset": grounded_prf,
        "diagnostic_intent_label_grouping": intent_prf,
        "secondary": {"ari_unambiguous_subset": round(ari_value, 4), "ari_n": len(unamb),
                      "split_error_rate": round(split_rate, 4),
                      "median_features_per_request": median_spread,
                      "max_features_per_request": spread[-1] if spread else 0},
    }
    name = args.symbols.stem.replace("symbols-", "")
    (out / f"pairs-{name}.json").write_text(json.dumps(report, indent=1))

    print(f"{name}: {len(universe)} symbols scored of {len(gt_map)} in the transcript record "
          f"({report['coverage']['fraction']:.0%} known to sgt)  ·  "
          f"{len(gt_by_cluster)} requests vs {len(sgt_by_cluster)} features")
    print(f"  precision {precision:.3f}  recall {recall:.3f}  F1 {f1:.3f}"
          f"   [null 'one feature for everything' F1 {null_f1:.3f}"
          f" — {'beats it' if f1 > null_f1 else 'DOES NOT BEAT IT'}]")
    print(f"  baseline 'same file ⇒ same feature': precision {file_p:.3f} recall {file_r:.3f} "
          f"F1 {file_f1:.3f}  — sgt {'beats it' if f1 > file_f1 else 'DOES NOT BEAT IT'}")
    # A subset with no positive pairs makes P/R/F1 all 0/0. Printing "F1 0.000" there reads as "sgt
    # scored zero" when it means "there was nothing to score", so say which it is.
    if len(grounded) < 2:
        diag = "  — too few to score"
    elif not grounded_prf["gt_positive"]:
        diag = (f"  — undefined: {grounded_prf['universe_pairs']} pair(s), none positive in the "
                "transcript record")
    else:
        diag = (f"  precision {grounded_prf['precision']:.3f} recall {grounded_prf['recall']:.3f} "
                f"F1 {grounded_prf['f1']:.3f}  [subset null F1 "
                f"{grounded_prf['null_f1_all_one_feature']:.3f}]")
    print(f"  diagnostic, symbols sgt holds a real edit for (substantive members): "
          f"{len(grounded)}/{len(universe)}{diag}")
    print(f"  diagnostic, leaves grouped by intent label ({intent_prf['features']} intents vs "
          f"{len(sgt_by_cluster)} leaves): precision {intent_prf['precision']:.3f} "
          f"recall {intent_prf['recall']:.3f} F1 {intent_prf['f1']:.3f}")
    print(f"  base rate {base:.1%} of {total} pairs positive  ·  ARI {ari_value:.3f} "
          f"on {len(unamb)} unambiguous symbols")
    print(f"  split-error rate {split_rate:.0%} of requests; median request spans "
          f"{median_spread} features, max {report['secondary']['max_features_per_request']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
