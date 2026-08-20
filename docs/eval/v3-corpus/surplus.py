"""The other half of fidelity: paths the rebuild materialises that do not exist at HEAD.

Section 6.2's rate is the fraction of files *at HEAD* reproduced byte for byte, so a file the
rebuild invents cannot enter its denominator and cannot fail -- the metric sees missing and wrong
content, never surplus. This measures the surplus, and the two-sided rate that counts a rebuild
correct only when it produces HEAD's file set and nothing more.

Surplus is read off the stored `fsck_tree` payload as `drift - tracked`: a materialised path with
no HEAD bytes falls through `fsck_tree`'s classification to `drift` (`lens.py:1513`), and a drifted
path git does not track is one sgt composes and the repository does not have. That proxy was checked
against a direct measurement on `yanshengjia__ml-road` -- `code(current_ideal)` minus HEAD's tree
gives 31 paths, `drift - tracked` gives the same 31, identical sets. Same tier and symlink filters
as `recompute.py`, so numerator and denominator are the same population.

    python -u docs/eval/v3-corpus/surplus.py [--corpus docs/eval/v3-corpus] [--work /tmp/v3]

Read-only: `git ls-files` and `lstat`. No mining.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from recompute import through_symlink, tracked_paths  # noqa: E402
from sgt.core import tiers  # noqa: E402


def measure(rec: dict, repo: Path) -> dict:
    ft = rec["fsck_tree"]
    cfg = tiers.load_tiers(repo)
    tracked = [p for p in tracked_paths(repo) if not through_symlink(repo, p)]
    scope = {p for p in tracked if tiers.resolve_tier(p, cfg) != "ignored"}
    drift, back = set(ft["drift"]), set(ft.get("backstop_kept") or [])
    failed = (drift | back) & scope
    surplus = {p for p in drift - set(tracked)
               if not through_symlink(repo, p) and tiers.resolve_tier(p, cfg) != "ignored"}
    denom = len(scope | surplus)
    return {
        "scope": len(scope),
        "failed": len(failed),
        "surplus": len(surplus),
        "honest": round(1 - len(failed) / len(scope), 4) if scope else None,
        "tree_exact": round((len(scope) - len(failed)) / denom, 4) if denom else None,
        "examples": sorted(surplus)[:5],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="docs/eval/v3-corpus")
    ap.add_argument("--work", default="/tmp/v3")
    args = ap.parse_args()
    corpus, work = Path(args.corpus), Path(args.work)
    excluded = set(json.loads((corpus / "settled.json").read_text())["excluded"])

    rows = []
    print(f"{'repo':42s} {'scope':>6} {'failed':>7} {'surplus':>8} {'honest':>7} {'tree-exact':>11}")
    for d in sorted(corpus.iterdir()):
        if not (d / "run.json").is_file() or d.name in excluded:
            continue
        r = measure(json.loads((d / "run.json").read_text()), work / d.name)
        rows.append(r)
        print(f"{d.name:42s} {r['scope']:>6} {r['failed']:>7} {r['surplus']:>8} "
              f"{r['honest']:>7} {r['tree_exact']:>11}")

    n = len(rows)
    scope = sum(r["scope"] for r in rows)
    failed = sum(r["failed"] for r in rows)
    surplus = sum(r["surplus"] for r in rows)
    print(f"\n{n} repositories; {sum(1 for r in rows if r['surplus'])} materialise at least one "
          f"path absent from HEAD")
    print(f"surplus paths {surplus}; median surplus per in-scope file "
          f"{statistics.median(r['surplus'] / r['scope'] for r in rows):.3f}")
    print(f"median honest {statistics.median(r['honest'] for r in rows):.4f}  ->  "
          f"median tree-exact {statistics.median(r['tree_exact'] for r in rows):.4f}")
    print(f"pooled honest {1 - failed / scope:.4f}  ->  "
          f"pooled tree-exact {(scope - failed) / (scope + surplus):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
