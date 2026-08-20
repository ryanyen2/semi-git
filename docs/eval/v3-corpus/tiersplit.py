"""Split the corpus honest rate by tier, which §6.2 says it has not done.

The self-hosted figure is split: 104 whole-file records reproduce at 0.97, 252
decomposed ones at 0.23, and the pooled 0.44 is the average of a hard problem and
a trivial one. The corpus median 0.33 is pooled the same way, so it inherits the
same confound -- a repository that is mostly documentation scores higher for a
reason that has nothing to do with the design. This measures the split.

Tier comes from `tiers.resolve_tier(path, cfg)` (`sgt/core/tiers.py:208`):
`entity` is a file sgt decomposes into functions, `opaque` is one it records
whole, `ignored` is out of scope. A file is a failure if it appears in the
`fsck_tree` `drift` or `backstop_kept` list, exactly as `recompute.py` counts it.

Read-only: `git ls-files` and `lstat` on the clone plus the lists already stored
in run.json. No mining, no writes.

    python -u docs/eval/v3-corpus/tiersplit.py [--corpus docs/eval/v3-corpus] [--work /tmp/v3]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from recompute import through_symlink, tracked_paths  # noqa: E402


def split(rec: dict, repo: Path) -> dict | None:
    from sgt.core import tiers

    ft = rec.get("fsck_tree")
    if not isinstance(ft, dict) or ft.get("drift") is None:
        return None

    cfg = tiers.load_tiers(repo)
    tracked = [p for p in tracked_paths(repo) if not through_symlink(repo, p)]
    tier = {}
    for p in tracked:
        t = tiers.resolve_tier(p, cfg)
        if t != "ignored":
            tier[p] = t

    failed = (set(ft["drift"]) | set(ft.get("backstop_kept") or [])) & set(tier)
    out = {}
    for name in ("entity", "opaque"):
        scope = [p for p, t in tier.items() if t == name]
        bad = [p for p in scope if p in failed]
        out[name] = {
            "scope": len(scope),
            "failed": len(bad),
            "rate": round(1 - len(bad) / len(scope), 4) if scope else None,
        }
    out["pooled"] = round(1 - len(failed) / len(tier), 4) if tier else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="docs/eval/v3-corpus")
    ap.add_argument("--work", default="/tmp/v3")
    args = ap.parse_args()

    corpus, work = Path(args.corpus), Path(args.work)
    rows = []
    for d in sorted(corpus.iterdir()):
        run = d / "run.json"
        if not run.is_file():
            continue
        repo = work / d.name
        if not (repo / ".git").exists():
            print(f"{d.name:40s} no clone at {repo}")
            continue
        r = split(json.loads(run.read_text()), repo)
        if r is None:
            print(f"{d.name:40s} no fsck_tree payload")
            continue
        rows.append((d.name, r))

    print(f"\n{'repo':38s} {'ent':>5} {'ent_r':>7} {'opq':>5} {'opq_r':>7} {'pooled':>7}")
    for name, r in rows:
        e, o = r["entity"], r["opaque"]
        print(f"{name:38s} {e['scope']:>5} {str(e['rate']):>7} "
              f"{o['scope']:>5} {str(o['rate']):>7} {str(r['pooled']):>7}")

    def med(key):
        vals = [r[key]["rate"] for _, r in rows if r[key]["rate"] is not None]
        return round(statistics.median(vals), 4), len(vals)

    def pooled(key):
        s = sum(r[key]["scope"] for _, r in rows)
        f = sum(r[key]["failed"] for _, r in rows)
        return round(1 - f / s, 4) if s else None, s

    em, en = med("entity")
    om, on = med("opaque")
    ep, es = pooled("entity")
    op, os_ = pooled("opaque")
    pm = round(statistics.median([r["pooled"] for _, r in rows if r["pooled"] is not None]), 4)
    print(f"\n{len(rows)} repositories")
    print(f"  median pooled            {pm}")
    print(f"  median entity (decomposed) {em}   over {en} repos")
    print(f"  median opaque (whole-file) {om}   over {on} repos")
    print(f"  pooled entity            {ep}   over {es} files")
    print(f"  pooled opaque            {op}   over {os_} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
