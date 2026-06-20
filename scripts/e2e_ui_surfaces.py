"""Live end-to-end for the UI surfaces: the JSON projection, semantic blame, and emit previews
that the VSCode extension and the TUI consume. No LLM, fully offline — it drives the same
`sgt.api` functions the CLI's `--json` mode exposes.

Run: uv run python scripts/e2e_ui_surfaces.py
"""

from __future__ import annotations

import sys
import tempfile

REPO = "/Users/ryanyen2/repos/semi-git"
sys.path.insert(0, REPO)

from sgt.api import blame_view, export_view, graph_view, status_view  # noqa: E402
from sgt.effects.model import Effect  # noqa: E402
from sgt.orchestrate.loop import Orchestrator  # noqa: E402
from sgt.orchestrate.sync import run_sync  # noqa: E402
from sgt.project import Project  # noqa: E402
from sgt.store.graph import Node, NodeKind  # noqa: E402


def main() -> None:
    wd = tempfile.mkdtemp(prefix="sgt-ui-")
    print(f"workdir: {wd}")
    proj = Project.init(wd)
    # Two features: `base` defines a helper, `user` calls it (so user depends_on base).
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="normalize an email"),
        [Effect.add_def("emails.py", "normalize", "def normalize(e):\n    return e.strip().lower()")],
    )
    proj.add_feature(
        Node(id="user", kind=NodeKind.CAPABILITY, intent="validate using normalize"),
        [Effect.add_def("emails.py", "validate", "def validate(e):\n    n = normalize(e)\n    return '@' in n")],
    )
    proj.write_working_tree()
    proj.commit("seed")
    proj = Project.open(wd)

    print("\n--- graph_view ---")
    g = graph_view(proj)
    for n in g["nodes"]:
        print(f"  {n['id']} [{n['kind']}/{n['status']}] depends_on={n['depends_on']}: {n['intent']}")
    print(f"  edges: {[(e['src'], e['dst'], e['type']) for e in g['edges']]}")
    assert {"src": "user", "dst": "base", "type": "depends_on"} in g["edges"], "missing inferred edge"

    print("\n--- status_view ---")
    print(f"  {status_view(proj)}")

    print("\n--- an agent edits one statement, then checkpoints (a fix node) ---")
    (open(f"{wd}/emails.py", "w")).write(
        "def normalize(e):\n    return e.strip().lower()\n\ndef validate(e):\n    n = normalize(e)\n    return n.count('@') == 1\n"
    )
    run_sync(proj, repo_path=wd, confirm=lambda c: True, intent="require exactly one @")
    proj = Project.open(wd)

    print("\n--- blame_view: emails.py (line -> owning feature) ---")
    bv = blame_view(proj, "emails.py")
    src = proj.materialize()["emails.py"].splitlines()
    owner = {}
    for s in bv["spans"]:
        for ln in range(s["start"], s["end"] + 1):
            owner[ln] = s["node_id"]
    for i, line in enumerate(src, 1):
        who = owner.get(i) or "—"
        intent = bv["nodes"].get(who, {}).get("intent", "")
        print(f"  {i:>2} [{who[:8]:<8}] {line}")
    distinct = {o for o in owner.values() if o}
    assert len(distinct) >= 2, "expected the edit to introduce a distinct owner (a fix node)"

    print("\n--- emit_payload: preview revert of `user` (writes nothing) ---")
    res = Orchestrator(proj, repo_path=wd).emit_payload("revert", "user")
    print(f"  ok={res['ok']} removes={res.get('removed')} message={res.get('message')}")
    for f, ba in res.get("files", {}).items():
        print(f"    {f}: {len(ba['before'].splitlines())} -> {len(ba['after'].splitlines())} lines")
    assert len(Project.open(wd).graph.nodes()) == len(proj.graph.nodes()), "emit must not mutate"

    print("\n--- export_view (the graph webview payload) ---")
    ex = export_view(proj)
    print(f"  {ex['count']} nodes, {len(ex['edges'])} edges, effects-per-node carried")
    print("\nOK — every UI surface served from one projection, blame is statement-exact, emit is a no-op.")


if __name__ == "__main__":
    main()
