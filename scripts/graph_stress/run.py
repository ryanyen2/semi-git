"""Entry point: run one stress project end-to-end.

    uv run python -m scripts.graph_stress.run <project_name>

Writes report.md / records.json / final_graph.json under the runs dir and prints a summary.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from sgt.config import load_env

from scripts.graph_stress.driver import Driver
from scripts.graph_stress.projects import PROJECTS

REPO = "/Users/ryanyen2/repos/semi-git"
BASE = "/Users/ryanyen2/repos/test-workspace/stress"


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in PROJECTS:
        print(f"usage: run <{'|'.join(PROJECTS)}>")
        raise SystemExit(2)
    name = sys.argv[1]
    load_env(REPO)
    wd = f"{BASE}/wd/{name}"
    shutil.rmtree(wd, ignore_errors=True)
    Path(wd).mkdir(parents=True, exist_ok=True)
    d = Driver(wd, name, f"{BASE}/runs")
    PROJECTS[name](d)
    summary = d.finish()
    print(json.dumps(summary, indent=2))
    print(f"\nreport: {d.run_dir}/report.md")


if __name__ == "__main__":
    main()
