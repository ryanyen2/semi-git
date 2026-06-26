"""Compact, human-readable digests of a decision graph + shape metrics.

Two jobs:
1. `graph_digest` — turn a `decision_graph_view` payload into shape metrics a researcher reads at a
   glance (lanes, heads, edge mix, *orphans*, depth) instead of scrolling raw JSON. This is also a
   prototype of the context-compaction the planner needs once graphs get large.
2. `render_lanes` — an ASCII lane/row picture of the graph, for eyeballing layout legibility.

Pure: no LLM, no I/O.
"""

from __future__ import annotations


def _by_id(view: dict) -> dict:
    return {d["id"]: d for d in view.get("decisions", [])}


def graph_digest(view: dict) -> dict:
    """Shape metrics for one decision graph."""
    decs = view.get("decisions", [])
    edges = view.get("edges", [])
    by_status: dict[str, int] = {}
    for d in decs:
        by_status[d["status"]] = by_status.get(d["status"], 0) + 1
    lanes = sorted({d["feature"] for d in decs})

    edge_kinds: dict[str, int] = {}
    for e in edges:
        edge_kinds[e["type"]] = edge_kinds.get(e["type"], 0) + 1

    # An orphan = a decision with no incident edge whose lane has only this one decision (a truly
    # floating dot). A lone decision on a multi-decision lane is connected by the spine, not orphaned.
    incident: set[str] = set()
    for e in edges:
        incident.add(e["src"])
        incident.add(e["dst"])
    lane_size: dict[str, int] = {}
    for d in decs:
        lane_size[d["feature"]] = lane_size.get(d["feature"], 0) + 1
    orphans = [d["id"] for d in decs if d["id"] not in incident and lane_size[d["feature"]] == 1]

    # longest builds-on/revises dependency chain (cycle-guarded)
    succ: dict[str, list[str]] = {d["id"]: [] for d in decs}
    for e in edges:
        if e["src"] in succ and e["dst"] in succ:
            succ[e["src"]].append(e["dst"])
    memo: dict[str, int] = {}

    def depth(n: str, seen: frozenset) -> int:
        if n in memo:
            return memo[n]
        if n in seen:
            return 0
        best = 0
        for m in succ[n]:
            best = max(best, 1 + depth(m, seen | {n}))
        memo[n] = best
        return best

    max_depth = max((depth(d["id"], frozenset()) for d in decs), default=0)

    return {
        "n_decisions": len(decs),
        "by_status": by_status,
        "n_lanes": len(lanes),
        "heads": view.get("frontier", {}),
        "edge_kinds": edge_kinds,
        "orphans": orphans,
        "max_depth": max_depth,
    }


def render_lanes(view: dict, width: int = 64) -> str:
    """A terminal lane/row picture: newest landing on top, one row per decision.

    Mirrors the webview's contract (one column per feature, newest on top) so we can judge layout
    legibility from the same data the extension renders.
    """
    decs = list(view.get("decisions", []))
    if not decs:
        return "(empty)"
    edges = view.get("edges", [])
    iddx = _by_id(view)

    # rank: newest landing on top; lane: stable column per feature in first-seen order
    decs.sort(key=lambda d: (-d["landing"], d["feature"], d["id"]))
    lane_of: dict[str, int] = {}
    for d in decs:
        lane_of.setdefault(d["feature"], len(lane_of))
    rowof = {d["id"]: i for i, d in enumerate(decs)}

    glyph = {"planned": "○", "landed": "●", "in_force": "◉"}
    heads = set(view.get("frontier", {}).values())
    edge_by_dst: dict[str, list[tuple[str, str]]] = {}
    for e in edges:
        edge_by_dst.setdefault(e["src"], []).append((e["dst"], e["type"]))

    lines = []
    nlanes = max(len(lane_of), 1)
    for d in decs:
        cols = [" "] * (nlanes * 2)
        cols[lane_of[d["feature"]] * 2] = glyph.get(d["status"], "?")
        rail = "".join(cols)
        it = d.get("intent", {})
        title = it.get("slug") or it.get("decision") or d["id"]
        head = " ⭠HEAD" if d["id"] in heads else ""
        deps = edge_by_dst.get(d["id"], [])
        depnote = "  →" + ", ".join(f"{t}:{dst[:8]}" for dst, t in deps[:3]) if deps else ""
        lines.append(f"{rail} @{d['landing']:>2} {title[:width]}{head}{depnote}")
    legend = "  ".join(f"{g}={s}" for s, g in glyph.items())
    return "\n".join(lines) + f"\n  [{legend};  lanes={nlanes}]"
