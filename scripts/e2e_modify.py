"""Live check that `sgt modify` re-derives a REAL body change via replace_def.

Verifies the new capability against the real OpenAI backend:
  1. A feature lands a function with a known behavior.
  2. `modify` on that feature makes the model emit a replace_def that CHANGES the
     existing function's behavior in place (not a second same-named def).
  3. The result is invariant-valid, runnable, and reflects the new behavior.
  4. Reverting the feature removes the whole bundle (original add + the replace).

Run:  uv run python scripts/e2e_modify.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from sgt.adapter.openai_agent import OpenAICodingAgent
from sgt.config import load_env
from sgt.effects.invariants import codebase_valid
from sgt.effects.model import EffectOp
from sgt.orchestrate.loop import Orchestrator
from sgt.project import Project

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    load_env(REPO_ROOT)
    workdir = tempfile.mkdtemp(prefix="sgt-modify-")
    print(f"workdir: {workdir}")
    proj = Project.init(workdir)
    orch = Orchestrator(proj, OpenAICodingAgent(repo_path=workdir), repo_path=workdir)

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  PASS: " if cond else "  FAIL: ") + msg)
        if not cond:
            failures.append(msg)

    print("\n1) add a clamp(n) that clamps n to the range 0..10")
    r1 = orch.ingest(
        "Add a function clamp(n) in util.py that returns n clamped to the range 0 "
        "to 10 inclusive (so values below 0 become 0 and values above 10 become 10)."
    )
    print(f"  -> ok={r1.ok} node={r1.node_id} :: {r1.message}")
    node = r1.node_id
    cb = proj.materialize()
    print(f"\n----- util.py -----\n{cb.get('util.py','')}")
    check(r1.ok and node is not None, "clamp landed as a feature")
    ns: dict = {}
    exec(cb["util.py"], ns)
    check(ns["clamp"](-5) == 0 and ns["clamp"](50) == 10 and ns["clamp"](5) == 5,
          "clamp(n) honors the 0..10 range")

    print("\n2) modify clamp: change the upper bound from 10 to 100")
    r2 = orch.modify(node, "Change clamp so the upper bound is 100 instead of 10.")
    print(f"  -> ok={r2.ok} node={r2.node_id} :: {r2.message}")
    for d in r2.landed:
        print(f"    landed: {d}")
    ops = {e.op for e in proj.bundles[node]}
    print(f"  ops now in the feature bundle: {sorted(o.value for o in ops)}")
    check(r2.ok, "modify landed")
    check(EffectOp.REPLACE_DEF in ops,
          "modify emitted a replace_def (changed the existing function in place)")

    cb = proj.materialize()
    print(f"\n----- util.py -----\n{cb.get('util.py','')}")
    check(codebase_valid(cb), "codebase invariant-valid after modify")
    # exactly one clamp definition — no duplicate same-named def
    check(cb["util.py"].count("def clamp(") == 1, "still exactly one clamp definition")
    ns = {}
    exec(cb["util.py"], ns)
    check(ns["clamp"](50) == 50, "clamp(50) -> 50 (new upper bound 100 honored)")
    check(ns["clamp"](500) == 100, "clamp(500) -> 100 (clamps at the new bound)")
    check(ns["clamp"](-5) == 0, "lower bound still 0 (behavior preserved)")

    print("\n3) revert the feature — the whole bundle (add + replace) goes")
    rev = orch.revert(node)
    print(f"  -> ok={rev.ok} :: {rev.message}")
    cb = proj.materialize()
    check(rev.ok and "util.py" not in cb or "clamp" not in cb.get("util.py", ""),
          "clamp fully removed on revert")
    check(codebase_valid(proj.materialize()), "codebase invariant-valid after revert")

    print("\n" + "=" * 60)
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED — modify re-derives a real body change via replace_def.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
