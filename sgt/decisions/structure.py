"""Deterministic structural description of a decision — the faithful half of hybrid rationale.

Read from the entity call/import graph (``sgt/entities``), this describes a decision's code in
terms that cannot be hallucinated: the entities it **defines**, what those **use** (their
calls/imports), and what **uses** them (their dependents). For a PLANNED decision (no footprint
yet) it falls back to the planner's declared ``provides`` / ``needs``, so the same shape describes
intent before any code lands.

Program analysis only — no LLM. ``sgt.decisions.distill`` feeds this to the model as ground truth
so the prose rationale is anchored to real structure; ``sgt.api`` surfaces it directly so a
faithful description shows on every surface even with no API key.
"""

from __future__ import annotations


def resolve_footprint(footprint: list[str], entity_ids: set[str]) -> set[str]:
    """Map a decision's effect targets onto entity-graph node ids.

    Exact match when the target is itself a def-level entity; otherwise the longest entity id in
    the same file that the target sits inside (a statement-level target resolves to its owning
    function/class). Unresolvable targets (e.g. third-party) drop out, producing no false edge.
    """
    out: set[str] = set()
    for key in footprint:
        if key in entity_ids:
            out.add(key)
            continue
        file = key.split("::", 1)[0]
        cands = [eid for eid in entity_ids if eid.split("::", 1)[0] == file and key.startswith(eid + ".")]
        if cands:
            out.add(max(cands, key=len))
    return out


def decision_structure(node, entity_view: dict, owned: set[str]) -> dict:
    """``{defines, uses, used_by}`` — deterministic entity-name lists for one decision.

    ``entity_view`` is an ``entity_graph_view`` payload (entities carry ``id`` / ``name`` /
    ``depends_on``). ``owned`` is the set of entity ids the decision's footprint resolves to
    (the caller already computes this). ``node`` is the decision's graph ``Node`` (or ``None``),
    used only for the planned fallback when ``owned`` is empty.
    """
    entities = entity_view.get("entities", [])
    by_id = {e["id"]: e for e in entities}
    name_of = {e["id"]: e["name"] for e in entities}

    if owned:
        defines = sorted({name_of[i] for i in owned if i in name_of})
        uses = sorted({
            name_of[d]
            for i in owned if i in by_id
            for d in by_id[i].get("depends_on", [])
            if d in name_of and d not in owned
        })
        used_by = sorted({
            e["name"] for e in entities
            if e["id"] not in owned and (set(e.get("depends_on", [])) & owned)
        })
        return {"defines": defines, "uses": uses, "used_by": used_by}

    # planned (or unresolvable) fallback: the declared provides/needs
    provides = list(node.provides) if node is not None else []
    needs = list(node.needs) if node is not None else []
    return {"defines": sorted(set(provides)), "uses": sorted(set(needs)), "used_by": []}


def structure_phrase(s: dict) -> str:
    """One-line human rendering, e.g. ``Defines Bm25Index, query · uses tokenize · used by search``."""
    parts: list[str] = []
    if s.get("defines"):
        parts.append("Defines " + ", ".join(s["defines"]))
    if s.get("uses"):
        parts.append(("uses " if parts else "Uses ") + ", ".join(s["uses"]))
    if s.get("used_by"):
        parts.append(("used by " if parts else "Used by ") + ", ".join(s["used_by"]))
    return " · ".join(parts)
