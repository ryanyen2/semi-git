"""Run every stress project sequentially and write a combined corpus summary.

    uv run python -m scripts.graph_stress.run_all

Each project gets a fresh workspace; per-project reports land under runs/<name>/, and a
corpus.json + corpus.md aggregate the final-graph shape metrics across all five (the input to the
layout redesign).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sgt.config import load_env

from scripts.graph_stress.driver import Driver
from scripts.graph_stress.projects import PROJECTS

REPO = "/Users/ryanyen2/repos/semi-git"
BASE = "/Users/ryanyen2/repos/test-workspace/stress"


def main() -> None:
    load_env(REPO)
    summaries = []
    for name, fn in PROJECTS.items():
        wd = f"{BASE}/wd/{name}"
        shutil.rmtree(wd, ignore_errors=True)
        Path(wd).mkdir(parents=True, exist_ok=True)
        print(f"=== running {name} ===", flush=True)
        d = Driver(wd, name, f"{BASE}/runs")
        try:
            fn(d)
        except Exception as ex:  # noqa: BLE001 — keep the corpus going if one project errors
            print(f"  !! {name} errored: {type(ex).__name__}: {ex}", flush=True)
        s = d.finish()
        summaries.append(s)
        fd = s["final_digest"]
        print(f"  {name}: {fd['n_decisions']} dec, {fd['n_lanes']} lanes, "
              f"orphans={len(fd['orphans'])}, depth={fd['max_depth']}, edges={fd['edge_kinds']}",
              flush=True)

    corpus = {"projects": summaries}
    Path(f"{BASE}/runs").mkdir(parents=True, exist_ok=True)
    Path(f"{BASE}/runs/corpus.json").write_text(json.dumps(corpus, indent=2), encoding="utf-8")
    lines = ["# Stress corpus — final-graph shapes\n"]
    for s in summaries:
        fd = s["final_digest"]
        lines.append(f"- **{s['name']}**: {fd['n_decisions']} decisions, {fd['n_lanes']} lanes, "
                     f"{len(fd['orphans'])} orphans, max_depth {fd['max_depth']}, "
                     f"edges {fd['edge_kinds']}, heads {len(fd['heads'])} "
                     f"(plan calls {s['cost']['plan_calls']}, code calls {s['cost']['code_calls']}, "
                     f"max ctx {max(s['cost']['planner_ctx_chars'], default=0)} chars)")
    Path(f"{BASE}/runs/corpus.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
