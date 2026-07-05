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

from sgt.agents.resolve import resolve
from sgt.decisions.structure import decision_structure
from sgt.decisions.structure import resolve_footprint as _resolve_footprint
from sgt.effects.attribute import attribute
from sgt.effects.model import EffectError
from sgt.store.graph import EdgeType, NodeStatus


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
    r = resolve(project, ref)
    if not r.ok or r.node_id is None:
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
    # PLANNED nodes carry no effects, so they never show as on-disk drift. A reopened session
    # whose only outstanding work is an unimplemented plan would otherwise read as "nothing to
    # do" — surface it here so every client (MCP/CLI/TUI/extension) can prompt the user to
    # continue implementing rather than report a clean tree as done.
    planned = [
        {
            "id": n.id,
            "intent": n.intent,
            "kind": n.kind.value,
            "provides": list(n.provides),
            "needs": list(n.needs),
        }
        for n in project.graph.nodes()
        if n.status is NodeStatus.PLANNED
    ]
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
        "pending": {
            "count": len(planned),
            "planned": planned,
            "summary": (
                f"{len(planned)} decision(s) planned but not yet implemented"
                if planned else "no pending plan"
            ),
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
    return _assemble_entity_view(project, g, owners)


def timeframe_view(project, frame: int) -> dict:
    """The map as of checkpoint ordinal ``frame`` — the scrubber's per-frame projection.

    Structure comes from ``materialize_at(frame)`` (tracked features at that frame); the overlay
    is frame-accurate, derived from the very entries that frame replays (each carries its owning
    ``node_id``) rather than current-state blame. Same shape as ``entity_graph_view`` plus
    ``frame`` — so the webview can diff adjacent frames and highlight born/grown/retired regions.
    """
    from sgt.store.graph import NodeStatus
    from sgt.entities.graph import build_entity_graph

    g = build_entity_graph(project.materialize_at(frame))
    active = {
        nid for nid in project.log.node_ids()
        if project.graph.has(nid) and project.graph.get(nid).status is NodeStatus.ACTIVE
    }
    # Frame overlay: the entry that produced each entity (add/replace target) owns it; last wins.
    owners: dict[str, str | None] = {e.id: None for e in g.entities}
    for entry in sorted(project.log.live_entries(active), key=lambda e: e.order_key):
        if 0 < entry.landing <= frame:
            ent_id = f"{entry.effect.file}::{entry.effect.target}"
            if ent_id in owners:
                owners[ent_id] = entry.node_id

    view = _assemble_entity_view(project, g, owners)
    view["frame"] = frame
    return view


def _assemble_entity_view(project, g, owners: dict) -> dict:
    """Shared assembly: entity dicts (+depends_on, +node_id), edges, reduction, components, clusters."""
    deps: dict[str, list[str]] = {e.id: [] for e in g.entities}
    for e in g.reduced_edges:
        if e.type in ("calls", "imports"):
            deps[e.src].append(e.dst)
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


def decision_graph_view(project) -> dict:
    """The decision DAG: decisions, lifecycle edges (stored), and derived dependency.

    ``revises`` / ``fork`` edges are intrinsic (from each decision's lineage). ``builds-on``
    edges and the ``clash`` set are **derived** by projecting the entity dependency graph
    (``entity_graph_view``) through each decision's footprint — never authored — so the graph
    is reproducible and cannot vibe. ``builds-on`` and clashes are computed among the
    in-force (frontier) decisions, the nodes a "now" view draws. Pure, offline.
    """
    from sgt.decisions.store import build_decisions, load_frontier
    from sgt.store.graph import EdgeType

    decisions = build_decisions(project)
    frontier = load_frontier(project, decisions)
    in_force_ids = frontier.in_force()

    try:
        eg = entity_graph_view(project)
    except Exception:
        eg = {"entities": []}
    entity_ids = {e["id"] for e in eg.get("entities", [])}
    entity_dep = {e["id"]: set(e.get("depends_on", [])) for e in eg.get("entities", [])}
    ent_of = {d.id: _resolve_footprint(d.footprint, entity_ids) for d in decisions}

    edges: list[dict] = []
    for d in decisions:
        if d.lifecycle_of:
            kind = "fork" if d.lifecycle_kind.value == "fork" else "revises"
            edges.append({"src": d.id, "dst": d.lifecycle_of, "type": kind})

    # Planned dependency lineage. A planned node has no footprint, so the entity-derived builds-on
    # below never reaches it — without help it floats as an orphan. We connect it two ways, both
    # derived (never authored): (1) its authored `depends_on` graph edges to sibling planned nodes;
    # (2) a *name bridge* — the names a planned node `needs`/`provides`, resolved against what every
    # other decision (landed or planned) provides, so "update run_pipeline to use retrieve_from_graph"
    # links to both the landed `run_pipeline` lane and the planned `retrieve_from_graph` node.
    seen_edges = {(e["src"], e["dst"], e["type"]) for e in edges}

    def _add_builds_on(src: str, dst: str) -> None:
        key = (src, dst, "builds-on")
        if src != dst and key not in seen_edges:
            seen_edges.add(key)
            edges.append({"src": src, "dst": dst, "type": "builds-on", "derived": True})

    feature_of = {d.id: d.feature for d in decisions}
    if hasattr(project, "graph"):
        graph = project.graph
        # Declared plan dependencies persist as builds-on between the *decisions* of those nodes,
        # even after they land. The call graph alone misses compositional deps — a stats fn that
        # consumes a loader's output without calling it — which would otherwise orphan both. Map
        # each node to its latest decision so a revised lane keeps a single incident edge.
        dec_of_node: dict[str, str] = {}
        for d in sorted(decisions, key=lambda d: d.landing):
            dec_of_node[d.node_id] = d.id
        for e in graph.edges():
            if e.type is EdgeType.DEPENDS_ON:
                s, t = dec_of_node.get(e.src), dec_of_node.get(e.dst)
                if s and t and feature_of.get(s) != feature_of.get(t):
                    _add_builds_on(s, t)

        # The declared-`needs` bridge, persisted for the life of a decision (planned OR landed). The
        # call graph alone loses a declared data-flow dependency once code lands — a JSON writer that
        # takes `compute_summary`'s output as an argument never *calls* it — so we link each decision
        # to whoever provides a name its node `needs`. name -> provider decision: landed decisions
        # provide their footprint target def names, planned ones their declared `provides`; match the
        # full target and its last dotted segment so `Class.method` and a bare `method` both resolve.
        def _names(raw: str) -> set[str]:
            return {raw, raw.rsplit(".", 1)[-1]}

        provider_of: dict[str, str] = {}
        for d in decisions:
            if d.status.value == "planned":
                node = graph.get(d.node_id) if graph.has(d.node_id) else None
                provided = node.provides if node else []
            else:
                provided = [k.split("::", 1)[1] for k in d.footprint if "::" in k]
            for raw in provided:
                for nm in _names(raw):
                    provider_of.setdefault(nm, d.id)  # first (deepest in time) provider wins

        for d in decisions:
            node = graph.get(d.node_id) if graph.has(d.node_id) else None
            if node is None:
                continue
            wanted = {nm for raw in node.needs for nm in _names(raw)}
            for nm in wanted:
                owner = provider_of.get(nm)
                if owner and owner != d.id and feature_of.get(owner) != d.feature:
                    _add_builds_on(d.id, owner)

    # builds-on / clash are derived at the LANE level: a lane owns the union of its footprints
    # up to its in-force decision (an entity touched once and not re-touched is still owned), and
    # the edge is drawn between the two lanes' in-force decision nodes.
    in_force_of: dict[str, object] = {d.feature: d for d in decisions if d.id in frontier.in_force()}
    cum_ent: dict[str, set] = {}
    for feat, dec in in_force_of.items():
        keys: set[str] = set()
        for d in decisions:
            if d.feature == feat and d.landing <= dec.landing:
                keys |= set(d.footprint)
        cum_ent[feat] = _resolve_footprint(list(keys), entity_ids)

    clash: list[dict] = []
    feats = sorted(in_force_of)
    for fb in feats:
        for fa in feats:
            if fa == fb:
                continue
            if any(dep in cum_ent[fa] for eb in cum_ent[fb] for dep in entity_dep.get(eb, ())):
                edges.append({
                    "src": in_force_of[fb].id, "dst": in_force_of[fa].id,
                    "type": "builds-on", "derived": True,
                })
            shared = cum_ent[fa] & cum_ent[fb]
            if shared and fb < fa:
                clash.append({
                    "a": in_force_of[fa].id, "b": in_force_of[fb].id, "entities": sorted(shared),
                })

    # A landed decision the frontier selects is reported as in_force (a frontier property,
    # not a log one — so it's stamped here in the projection, not in the store). Each decision
    # also carries a deterministic `structure` (defines/uses/used_by) read from the entity graph
    # (or, for a planned node, its declared provides/needs) — the faithful, offline description.
    has_graph = hasattr(project, "graph")
    out_decisions = []
    for d in decisions:
        dd = d.to_dict()
        if d.id in in_force_ids:
            dd["status"] = "in_force"
        node = project.graph.get(d.node_id) if has_graph and project.graph.has(d.node_id) else None
        dd["structure"] = decision_structure(node, eg, ent_of.get(d.id, set()))
        out_decisions.append(dd)

    return {
        "decisions": out_decisions,
        "edges": edges,
        "frontier": frontier.selection,
        "head": _primary_head(out_decisions, edges, in_force_ids),
        "clash": clash,
        "count": len(decisions),
    }


def _primary_head(decisions: list[dict], edges: list[dict], in_force_ids: set[str]) -> str | None:
    """The one decision a human reads as HEAD: the integrator.

    The frontier holds one tip per lane (2–6 across the stress corpus), but every project has a single
    dominant *integrator* — an in-force decision nothing builds on (zero builds-on in-degree) that
    itself builds on the most others. We pick it by ``(out-degree, dependency depth, landing)``. With
    no integrator (a pure spine / single lane) we fall back to the newest in-force tip. Deterministic.
    """
    if not in_force_ids:
        return None
    landing = {d["id"]: d["landing"] for d in decisions}
    succ: dict[str, list[str]] = {d["id"]: [] for d in decisions}
    indeg: dict[str, int] = {d["id"]: 0 for d in decisions}
    for e in edges:
        if e["type"] in ("builds-on", "revises") and e["src"] in succ and e["dst"] in succ:
            succ[e["src"]].append(e["dst"])
            indeg[e["dst"]] = indeg.get(e["dst"], 0) + 1

    memo: dict[str, int] = {}

    def depth(n: str, seen: frozenset) -> int:
        if n in memo:
            return memo[n]
        if n in seen:
            return 0
        memo[n] = max((1 + depth(m, seen | {n}) for m in succ[n]), default=0)
        return memo[n]

    integrators = [d for d in in_force_ids if indeg.get(d, 0) == 0 and succ.get(d)]
    pool = integrators or list(in_force_ids)
    # `in_force_ids` is a set, so ties in the ranking key must break on the id itself —
    # otherwise HEAD flips with set-iteration order (a fan-out with equal-rank tips).
    return max(pool, key=lambda d: (len(succ.get(d, [])), depth(d, frozenset()), landing.get(d, 0), d))


def frontier_view(project) -> dict:
    """The current composition: the in-force decision per lane, and the lane list."""
    from sgt.decisions.store import build_decisions, load_frontier

    decisions = build_decisions(project)
    return {
        "selection": load_frontier(project, decisions).selection,
        "lanes": sorted({d.feature for d in decisions}),
    }


def frontier_diff(a: dict, b: dict) -> dict:
    """Decision-level delta between two frontier selections (feature -> decision id)."""
    added, revised, revoked = [], [], []
    for f in sorted(set(a) | set(b)):
        if f in b and f not in a:
            added.append({"feature": f, "decision": b[f]})
        elif f in a and f not in b:
            revoked.append({"feature": f, "decision": a[f]})
        elif a[f] != b[f]:
            revised.append({"feature": f, "from": a[f], "to": b[f]})
    return {"added": added, "revised": revised, "revoked": revoked}


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
