"""U25 closure-scale measurement (plan 002, D1/U25 — the gate for U29).

For every feature node F in a repo's built feature tree, compute the downward closure
`↓ops(F)` = every op F's op-set transitively builds on (`order.downset_in` unioned over F's ops).
That closure is exactly what selecting F at the feature layer (U29's `sgt select`) would drag into
the working ideal. We report, over all feature nodes:

  - closure size (number of ops), and
  - dragged-feature count (distinct *other* features whose ops appear in the closure).

The pre-registered gate (D1, fixed before measuring — no post-hoc redefinition):
  median closure <= 25 ops AND median dragged features <= 3,
  AND >= 80% of feature-node selections within BOTH bounds (<=25 ops, <=3 features),
  on BOTH real corpora (this repo's op store = BET-C; the SGT_PROBE_REPO ~5k-commit repo = BET-E).
The 80%-within fraction is the load-bearing clause: a median bound alone greens the gate even with
40% of selections unreadable.

Usage:  python experiments/closure_scale/measure.py [REPO]   (default: this repo)
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

from sgt.core import lens, order
from sgt.core.store import Store
from sgt.lens.map import build_map

CLOSURE_MAX = 25   # D1: median closure and per-selection closure bound (ops)
FEATURES_MAX = 3   # D1: median dragged features and per-selection bound
WITHIN_MIN = 0.80  # D1: fraction of selections that must satisfy BOTH bounds


def measure(repo: str | Path) -> dict:
    repo = Path(repo)
    lens.get(repo)  # mine-on-contact so the ideal reflects current reality
    ideal = lens.current_ideal(repo)
    ideal_ids = ideal.op_ids
    ops = Store(repo).all_ops()
    declared = lens.load_declared_orset(repo).live()

    build_map(repo)  # (re)build the feature tree so op_leaf covers the current ideal
    from sgt.lens import tree as tree_mod
    tr = tree_mod.load(repo) or {}
    op_leaf: dict[str, str] = tr.get("op_leaf", {})

    # feature id -> its in-ideal op-set (only leaves that actually carry ops are selectable)
    feats: dict[str, set[str]] = {}
    for op_id in ideal_ids:
        f = op_leaf.get(op_id)
        if f is not None:
            feats.setdefault(f, set()).add(op_id)

    rows = []
    for f, fops in feats.items():
        closure: set[str] = set()
        for o in fops:
            closure |= order.downset_in(o, ideal_ids, ops, declared)
        dragged = {op_leaf.get(c) for c in closure} - {f, None}
        rows.append({"feature": f, "own_ops": len(fops), "closure": len(closure),
                     "dragged": len(dragged)})

    n = len(rows)
    if n == 0:
        return {"repo": str(repo), "features": 0, "error": "no feature nodes with ops"}
    sizes = sorted(r["closure"] for r in rows)
    drags = sorted(r["dragged"] for r in rows)
    within = sum(1 for r in rows if r["closure"] <= CLOSURE_MAX and r["dragged"] <= FEATURES_MAX)
    within_frac = within / n

    median_closure = statistics.median(sizes)
    median_dragged = statistics.median(drags)
    passed = (median_closure <= CLOSURE_MAX and median_dragged <= FEATURES_MAX
              and within_frac >= WITHIN_MIN)
    return {
        "repo": str(repo),
        "ideal_ops": len(ideal_ids),
        "features": n,
        "median_closure": median_closure,
        "p90_closure": sizes[min(n - 1, int(0.9 * n))],
        "max_closure": sizes[-1],
        "median_dragged": median_dragged,
        "p90_dragged": drags[min(n - 1, int(0.9 * n))],
        "max_dragged": drags[-1],
        "within_bounds_frac": round(within_frac, 4),
        "within_count": within,
        "gate": "GREEN" if passed else "RED",
        "thresholds": {"closure_max": CLOSURE_MAX, "features_max": FEATURES_MAX,
                       "within_min": WITHIN_MIN},
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    result = measure(target)
    import json
    print(json.dumps(result, indent=2))
