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


def entity_graph_view(project) -> dict:
    """The deterministic code-entity graph parsed from the working tree (disk-canonical).

    Functions/classes/methods as entities; edges are containment + calls/imports. ``edges`` is
    the full set; ``reduced_edges`` is the transitive reduction used for layout (KTD8). Each
    entity carries ``depends_on`` (its direct reduced calls/imports targets). Pure over a
    freshly-opened ``Project``; no LLM/network. The ``entities`` extra (tree-sitter) is imported
    lazily so core surfaces without it still import ``sgt.api``.
    """
    from sgt.entities.graph import build_entity_graph, owning_nodes, read_entity_sources

    g = build_entity_graph(read_entity_sources(project.repo))
    deps: dict[str, list[str]] = {e.id: [] for e in g.entities}
    for e in g.reduced_edges:
        if e.type in ("calls", "imports"):
            deps[e.src].append(e.dst)

    # Feature overlay: which feature owns each entity, from semantic blame (disk-vs-materialized
    # line numbers align for tracked clean files; untracked/TS files have no blame -> None).
    spans_by_file: dict[str, list[dict]] = {}
    if hasattr(project, "log") and hasattr(project, "graph"):
        try:
            spans_by_file = {
                f: [s.to_dict() for s in sps] for f, sps in attribute(project).items()
            }
        except EffectError:
            spans_by_file = {}
    owners = owning_nodes(g.entities, spans_by_file)

    entities = []
    for ent in g.entities:
        d = ent.to_dict()
        d["depends_on"] = deps.get(ent.id, [])
        d["node_id"] = owners.get(ent.id)
        entities.append(d)

    return {
        "entities": entities,
        "edges": [e.to_dict() for e in g.edges],
        "reduced_edges": [e.to_dict() for e in g.reduced_edges],
        "components": g.components,
        "clusters": _clusters(project, g, owners),
        "count": len(g.entities),
    }


def _clusters(project, g, owners: dict) -> list[dict]:
    """Read-only capability clustering for the projection (reports persisted identity, never writes).

    Adjacency = features that co-own entities in a file, plus feature-dependency edges. Identity is
    matched against the persisted store; refreshing/relabeling (and the LLM path) happen in
    ``sgt.entities.cluster.refresh_clusters``, not on this read.
    """
    members = sorted({nid for nid in owners.values() if nid})
    if not members:
        return []
    from sgt.entities.cluster import cluster_features, load_cluster_store

    files_of: dict[str, set[str]] = {}
    for ent in g.entities:
        nid = owners.get(ent.id)
        if nid:
            files_of.setdefault(ent.file, set()).add(nid)
    adjacency: set[frozenset] = set()
    for feats in files_of.values():
        fl = sorted(feats)
        for i in range(len(fl)):
            for j in range(i + 1, len(fl)):
                adjacency.add(frozenset((fl[i], fl[j])))
    if hasattr(project, "graph"):
        for f in members:
            if project.graph.has(f):
                for dep in project.graph.successors(f):
                    if dep in members:
                        adjacency.add(frozenset((f, dep)))
    prior = load_cluster_store(project.sgt_dir) if hasattr(project, "sgt_dir") else {}
    return [c.to_dict() for c in cluster_features(members, adjacency, prior)]


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
