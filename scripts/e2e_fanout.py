"""Live end-to-end check for the parallel fan-out path (semi-git #2/#3).

Verifies the THESIS against the real OpenAI backend, not just that code runs:
  1. A multi-part intent DECOMPOSES into a constraint graph of >1 sub-task.
  2. The sub-tasks fan out, gate, and land as semantic nodes.
  3. A dependent sub-task sees its providers' code and an inferred dependency edge.
  4. The result is invariant-valid, runnable, and behaves as intended.
  5. Any conflict that arises quarantines with a witness and the run still completes.

Run:  uv run python scripts/e2e_fanout.py
The OpenAI key is loaded from this repo's .env.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from sgt.adapter.openai_agent import OpenAICodingAgent
from sgt.agents.planner import decompose
from sgt.config import load_env
from sgt.effects.invariants import codebase_valid
from sgt.orchestrate.loop import Orchestrator
from sgt.project import Project

REPO_ROOT = Path(__file__).resolve().parent.parent


def banner(t: str) -> None:
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def show_plan(graph) -> int:
    print("Decomposed plan:")
    n = 0
    for i, layer in enumerate(graph.layers(), 1):
        print(f"  layer {i} (parallel):")
        for t in layer:
            n += 1
            needs = f"  [needs: {', '.join(t.needs)}]" if t.needs else ""
            print(f"    - {t.key}: {t.intent[:60]}{needs}")
    return n


def main() -> int:
    load_env(REPO_ROOT)
    workdir = tempfile.mkdtemp(prefix="sgt-fanout-")
    print(f"workdir: {workdir}")
    proj = Project.init(workdir)
    agent = OpenAICodingAgent(repo_path=workdir)

    # auto-confirm checkpoint, but print the plan so we can eyeball the decomposition
    seen_plan = {}

    def confirm(graph):
        seen_plan["n"] = show_plan(graph)
        return True

    orch = Orchestrator(proj, agent, repo_path=workdir, confirm=confirm)

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  PASS: " if cond else "  FAIL: ") + msg)
        if not cond:
            failures.append(msg)

    intent = (
        "In users.py add three functions: validate_email(email) returns True only if "
        "email contains '@'; normalize_email(email) returns the email lowercased and "
        "stripped of surrounding whitespace; and register_email(email) that first "
        "normalizes the email, then validates it, returning the normalized email if "
        "valid and None otherwise."
    )

    banner("1) decompose the multi-part intent")
    graph = decompose(intent, proj.materialize(), repo_path=workdir)
    n_tasks = show_plan(graph)
    check(n_tasks >= 2, f"intent decomposed into >1 sub-task (got {n_tasks})")

    banner("2) fan out + land")
    rep = orch._fanout_or_add(intent, "capability", "email")
    print(f"report: ok={rep.ok} :: {rep.message}")
    for nid in rep.landed:
        print(f"    landed node: {nid}")
    for nid in rep.quarantined:
        w = proj.witnesses.get(nid, {})
        print(f"    QUARANTINED {nid}: {w.get('reason')} — held {w.get('held')}")
    check(rep.ok, "fan-out run completed ok (non-blocking even on conflict)")
    check(len(rep.landed) >= 1, "at least one sub-task landed as a node")

    banner("materialized code")
    cb = proj.materialize()
    for f in sorted(cb):
        print(f"\n----- {f} -----\n{cb[f]}")
    check(codebase_valid(cb), "codebase invariant-valid after fan-out")

    banner("3) dependency edge + behavior")
    # The backend may choose its own filename; find the module that defines the API
    # (per-file name resolution means the three must co-locate to be invariant-valid).
    src = next((c for c in cb.values() if "def register_email" in c), "")
    check("register_email" in src and "validate_email" in src and "normalize_email" in src,
          "all three functions present and co-located in one module")
    # an inferred dependency edge should exist between the register node and a provider
    any_dep = any(proj.graph.successors(n.id) for n in proj.graph.nodes())
    check(any_dep, "at least one inferred dependency edge between landed nodes")

    try:
        ns: dict = {}
        exec(src, ns)
        ok_valid = ns["register_email"]("  Foo@Bar.COM ") == "foo@bar.com"
        ok_invalid = ns["register_email"]("not-an-email") is None
        print(f"  register_email('  Foo@Bar.COM ') -> {ns['register_email']('  Foo@Bar.COM ')!r}")
        print(f"  register_email('not-an-email')   -> {ns['register_email']('not-an-email')!r}")
        check(ok_valid and ok_invalid, "register_email normalizes+validates as intended")
    except Exception as ex:  # noqa: BLE001
        check(False, f"materialized module runnable ({type(ex).__name__}: {ex})")

    banner("semantic graph")
    for n in proj.graph.nodes():
        deps = proj.graph.successors(n.id)
        st = "" if n.status.value == "active" else f" [{n.status.value}]"
        print(f"  {n.id} [{n.kind.value}]{st}: {n.intent[:50]}  -> deps {deps}")

    banner("RESULT")
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED — parallel fan-out decomposes, lands, and composes as intended.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
