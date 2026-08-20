"""Recompute grounding retention (grounded / store) for each corpus clone, read-only.

`run.json` records the store size (`fsck.checked`) but no grounded count, so the coefficient the
paper leans on -- rho(honest rate, grounding retention) = +0.55 -- had no committed inputs at all.
This recovers them. Retention is a pure function of a store's op set:

    store    = len(Store(repo).all_ops())
    grounded = len(order._grounded(all ids, ops, declared edges))

`_grounded` is the downward-closure fixpoint (`order.py:393`) -- the largest well-founded subset of
the store, i.e. the ops that bottom out at a chain head. Nothing here mines, opens a lens, or
touches `current_ideal`, so it neither writes cache state nor changes what a later measurement
would see: `Store.all_ops` and `lens._load_declared` are both plain reads.

    python -u docs/eval/v3-corpus/retention.py [--corpus docs/eval/v3-corpus] [--work /tmp/v3]
                                              [--out docs/eval/v3-corpus/retention.json]

Validation targets from the ledger's F81 prose (line 5650): SDAR 1.00, fullcontrol 0.93,
Index-anisora 0.92, logicanalyzer 0.92, bleak 0.35. If those five do not come back, this is
computing something other than what was published, and the numbers must not be used.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sgt.core import lens, order  # noqa: E402
from sgt.core.store import Store  # noqa: E402


def retention(repo: Path) -> dict:
    ops = Store(repo).all_ops()
    if not ops:
        return {"error": "empty store"}
    ids = {op.id for op in ops}
    grounded = order._grounded(ids, ops, lens._load_declared(repo))
    return {
        "store": len(ops),
        "grounded": len(grounded),
        "retention": round(len(grounded) / len(ops), 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="docs/eval/v3-corpus")
    ap.add_argument("--work", default="/tmp/v3")
    ap.add_argument("--out")
    args = ap.parse_args()
    corpus, work = Path(args.corpus), Path(args.work)
    excluded = set(json.loads((corpus / "settled.json").read_text())["excluded"])

    out: dict[str, dict] = {}
    print(f"{'repo':40s} {'store':>7} {'grounded':>9} {'retention':>10}")
    for d in sorted(corpus.iterdir()):
        if not (d / "run.json").is_file() or d.name in excluded:
            continue
        repo = work / d.name
        if not (repo / ".sgt").is_dir():
            print(f"{d.name:40s} missing store")
            return 2
        r = retention(repo)
        out[d.name] = r
        if "error" in r:
            print(f"{d.name:40s} {r['error']}")
            continue
        print(f"{d.name:40s} {r['store']:>7} {r['grounded']:>9} {r['retention']:>10.4f}")

    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
