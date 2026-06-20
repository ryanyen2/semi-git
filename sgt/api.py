"""The canonical JSON projection of the semantic tree — one schema, many clients.

Every machine-readable surface (the CLI's ``--json`` mode, the MCP server, the VSCode
extension, and the Textual TUI) renders the *same* dicts produced here, so the views can
never drift apart. The functions are pure over a freshly-opened ``Project`` and take no
network/LLM dependency: reads are offline (origin: graph ops + read verbs need no key).

Shapes (stable; additive changes only):

* ``node_view``    — one node: id, kind, status, intent, deps, dependents, provenance, conflict.
* ``graph_view``   — every node + the typed edge list + count.
* ``show_view``    — ``node_view`` plus effects and the full conflict witness.
* ``status_view``  — node/effect counts, materialized files (+ line counts), drift.
* ``conflicts_view`` — open conflicts: each held node, what it lost to, and why.
* ``blame_view``   — per-file line spans -> owning node, with the node metadata a UI needs.
* ``export_view``  — the whole graph (nodes, edges, per-node effects, witnesses) for a graph view.
"""

from __future__ import annotations

from sgt.agents.resolve import resolve_ref
from sgt.effects.attribute import attribute
from sgt.effects.model import EffectError
from sgt.store.graph import EdgeType


def node_view(project, n) -> dict:
    w = project.witnesses.get(n.id)
    return {
        "id": n.id,
        "kind": n.kind.value,
        "status": n.status.value,
        "intent": n.intent,
        "depends_on": list(project.graph.successors(n.id)),
        "dependents": list(project.graph.predecessors(n.id)),
        "provenance": list(n.provenance),
        "commits": [c[:8] for c in n.commit_ids],
        "conflict": w.get("reason") if w else None,
    }


def graph_view(project) -> dict:
    nodes = [node_view(project, n) for n in project.graph.nodes()]
    edges = [
        {"src": e.src, "dst": e.dst, "type": e.type.value} for e in project.graph.edges()
    ]
    return {"nodes": nodes, "edges": edges, "count": len(nodes)}


def show_view(project, ref: str) -> dict:
    r = resolve_ref(project.graph, ref)
    if r.node_id is None:
        return {"error": f"could not resolve {ref!r} ({r.kind})", "matches": r.matches}
    n = project.graph.get(r.node_id)
    view = node_view(project, n)
    view["effects"] = [
        {"op": e.op.value, "target": e.target, "file": e.file}
        for e in project.bundles.get(n.id, [])
    ]
    if (w := project.witnesses.get(n.id)):
        view["conflict"] = {
            "reason": w.get("reason"),
            "held": w.get("held", []),
            "against": w.get("against", []),
        }
    return view


def status_view(project) -> dict:
    try:
        cb = project.materialize()
    except EffectError as ex:
        return {"nodes": len(project.graph.nodes()), "error": f"cannot materialize: {ex}"}
    drift = project.check_drift()
    return {
        "nodes": len(project.graph.nodes()),
        "files": [{"path": p, "lines": len(cb[p].splitlines())} for p in sorted(cb)],
        "effects": sum(len(b) for b in project.bundles.values()),
        "drift": {
            "any": drift.any,
            "modified": drift.modified,
            "added": drift.added,
            "deleted": drift.deleted,
            "summary": drift.summary(),
        },
    }


def conflicts_view(project) -> dict:
    from sgt.merge import conflicts

    out = []
    for c in conflicts(project):
        out.append({
            "node_id": c.held.node_id,
            "intent": c.held.intent,
            "reason": c.reason,
            "against": [{"node_id": s.node_id, "intent": s.intent} for s in c.against],
        })
    return {"conflicts": out, "count": len(out)}


def blame_view(project, file: str) -> dict:
    """Line spans -> owning node for one materialized file, plus the node metadata a UI needs.

    ``drift`` flags whether the working-tree copy of ``file`` diverges from the replay the
    spans were computed against — so a UI can mark a stale overlay rather than mislead.
    """
    try:
        spans_by_file = attribute(project)
    except EffectError as ex:
        return {"file": file, "error": f"cannot attribute: {ex}"}
    spans = spans_by_file.get(file)
    if spans is None:
        return {"file": file, "error": "file is not managed by the semantic graph", "spans": []}
    used = {s.node_id for s in spans if s.node_id}
    nodes = {
        nid: {
            "intent": project.graph.get(nid).intent,
            "kind": project.graph.get(nid).kind.value,
            "status": project.graph.get(nid).status.value,
        }
        for nid in used
        if project.graph.has(nid)
    }
    drift = project.check_drift()
    return {
        "file": file,
        "spans": [s.to_dict() for s in spans],
        "nodes": nodes,
        "drift": file in drift.modified or file in drift.deleted,
    }


def export_view(project) -> dict:
    """Everything a graph view needs in one payload: nodes, edges, effects, witnesses."""
    g = graph_view(project)
    bundles = project.bundles
    for n in g["nodes"]:
        n["effects"] = [
            {"op": e.op.value, "target": e.target, "file": e.file}
            for e in bundles.get(n["id"], [])
        ]
        if (w := project.witnesses.get(n["id"])):
            n["witness"] = {
                "reason": w.get("reason"),
                "held": w.get("held", []),
                "against": w.get("against", []),
            }
    return g
