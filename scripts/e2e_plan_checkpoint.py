"""Live end-to-end: the graph-only spine — plan -> implement -> checkpoint -> operate.

sgt authors no code here. The planner (graph-level LLM) decomposes an intent into PLANNED
nodes; *we* (standing in for the coding agent) write the code; `checkpoint --fulfills` distills
it under the planned node and flips it ACTIVE; then revert/--emit operate on the graph. Only the
plan step calls the LLM — fulfillment and the graph ops are deterministic/offline.

Run: uv run python scripts/e2e_plan_checkpoint.py
"""
from __future__ import annotations

import sys
import tempfile

REPO = "/Users/ryanyen2/repos/semi-git"
sys.path.insert(0, REPO)
from sgt.config import load_env  # noqa: E402
from sgt.orchestrate.loop import Orchestrator  # noqa: E402
from sgt.orchestrate.sync import run_sync  # noqa: E402
from sgt.project import Project  # noqa: E402

load_env(REPO)
_YES = lambda c: True  # noqa: E731


def graph(proj, label):
    print(f"\n--- graph: {label} ---")
    for n in proj.graph.nodes():
        tag = "" if n.status.value == "active" else f" [{n.status.value}]"
        deps = proj.graph.successors(n.id)
        dep = f"  -> {', '.join(deps)}" if deps else ""
        print(f"  {n.id} [{n.kind.value}]{tag}: {n.intent[:56]}{dep}")


def write(wd, name, src):
    with open(f"{wd}/{name}", "w") as f:
        f.write(src)


def main():
    wd = tempfile.mkdtemp(prefix="sgt-plan-ck-")
    print(f"workdir: {wd}")
    proj = Project.init(wd)
    orch = Orchestrator(proj, repo_path=wd)

    print("\n>>> plan (LLM decomposition; no code authored)")
    rep = orch.plan("add validate(email) and a normalize(email) that lowercases it")
    print(f"  {rep.action} ok={rep.ok}: {rep.message}")
    proj = Project.open(wd)
    graph(proj, "after plan")
    planned = [n.id for n in proj.graph.nodes() if n.status.value == "planned"]

    print("\n>>> implement each planned node by hand + checkpoint --fulfills (offline)")
    # A real coding agent ADDS each function to the file (it never deletes prior work), so the
    # file grows cumulatively and each checkpoint distills only the newly-added def.
    impls = {
        "validate": "import re\n\n\ndef validate(email):\n    return bool(re.match(r'[^@]+@[^@]+', email))\n",
        "normalize": "\n\ndef normalize(email):\n    return email.strip().lower()\n",
    }
    file_src = ""
    for nid in planned:
        node = proj.graph.get(nid)
        which = "validate" if "valid" in node.intent.lower() else "normalize"
        file_src += impls[which]
        write(wd, "emails.py", file_src)
        rep = run_sync(proj, repo_path=wd, confirm=_YES, fulfills=nid, intent=node.intent)
        print(f"  fulfill {nid[:8]} ({which}): {rep.message}")
        proj = Project.open(wd)

    graph(proj, "after fulfillment")
    print(f"\nmaterialized emails.py:\n{proj.materialize().get('emails.py', '(none)')}")

    print(">>> revert --emit (dry-run preview, writes nothing)")
    target = proj.graph.nodes()[0].id
    rep = Orchestrator(proj, repo_path=wd).revert(target, emit=True)
    print(f"  {rep.message}")
    print(f"  (graph unchanged: {len(proj.graph.nodes())} nodes still present)")

    print("\n>>> revert for real")
    rep = Orchestrator(proj, repo_path=wd).revert(target)
    print(f"  {rep.message}")
    proj = Project.open(wd)
    graph(proj, "after revert")
    print(f"\nproject valid: {proj.valid()}")


if __name__ == "__main__":
    main()
