"""LLM rationale distillation for decisions — graph reasoning only, never authors code.

Given a decision's stated intent and the code it produced, the model reconstructs the ADR
rationale: Context (the need that preceded it), Consequence (what the codebase now guarantees),
and the Alternatives weighed. It reasons over the *actual change* — there is no seeded example, so
it is data/case-agnostic. Alternatives are the model's inference and are marked low-confidence
(``source="distilled"``) per R3, so a surface never presents a fabricated road-not-taken as fact.

Results land in the ``.sgt/decisions.json`` sidecar that ``build_decisions`` merges, so the CLI,
MCP, VS Code and TUI all show the enriched rationale with no per-surface work. With no API key the
whole path degrades to a no-op (rationale stays empty); it never blocks the offline graph.
"""

from __future__ import annotations

import json

from sgt.config import get_client, get_model
from sgt.decisions.structure import decision_structure, resolve_footprint, structure_phrase


def _structure_for(project, decision) -> dict:
    """Deterministic ``{defines, uses, used_by}`` for a decision, from the entity graph."""
    from sgt.api import entity_graph_view

    try:
        eg = entity_graph_view(project)
    except Exception:  # noqa: BLE001 — structure is grounding, never fatal to distillation
        eg = {"entities": []}
    entity_ids = {e["id"] for e in eg.get("entities", [])}
    owned = resolve_footprint(decision.footprint, entity_ids)
    node = (project.graph.get(decision.node_id)
            if hasattr(project, "graph") and project.graph.has(decision.node_id) else None)
    return decision_structure(node, eg, owned)

_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "context": {"type": "string"},
        "consequence": {"type": "string"},
        "alternatives": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "option": {"type": "string"},
                    "why_rejected": {"type": "string"},
                },
                "required": ["option", "why_rejected"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["slug", "context", "consequence", "alternatives"],
    "additionalProperties": False,
}

_SYS = (
    "You reconstruct the rationale behind a software decision from the change it produced. "
    "You are given the stated intent, the code, AND a STRUCTURE block listing — from static "
    "analysis of the call graph — exactly what this change defines, what those definitions use, "
    "and what uses them. Treat the STRUCTURE block as ground truth: your rationale MUST be "
    "consistent with it and must not assert relationships it does not contain. Infer four things "
    "and nothing else: "
    "Slug — a human title for the decision, at most 5 words, no trailing period; "
    "Context — the situation or need that preceded this decision; "
    "Consequence — what the codebase now guarantees as a result; "
    "Alternatives — other approaches that were plausible here and, for each, why it would lose. "
    "Ground every claim in the provided code and STRUCTURE; never invent APIs, files, or facts. "
    "Alternatives are your reasoned inference — keep each to one short clause."
)


def distill_rationale(project, decision, *, client=None, model: str | None = None,
                      structure: dict | None = None) -> dict:
    """Infer ``{slug, context, consequence, alternatives}`` for one decision via the LLM (raises offline).

    ``structure`` is the deterministic ``{defines, uses, used_by}`` summary; when omitted it is
    computed from the entity graph here so the prompt is always grounded (hybrid: facts pin the
    prose). Callers that already have it (the projection) pass it in to avoid re-parsing.
    """
    client = client or get_client(project.repo)
    cb = project.materialize()
    files = sorted({k.split("::", 1)[0] for k in decision.footprint})
    src = "\n\n".join(f"# {f}\n{cb.get(f, '')[:2000]}" for f in files) or "(no source on disk)"
    if structure is None:
        structure = _structure_for(project, decision)
    user = (
        f"Intent: {decision.intent.decision}\n"
        f"Entities touched: {', '.join(decision.footprint) or '(none)'}\n"
        f"STRUCTURE: {structure_phrase(structure) or '(no static structure)'}\n\n"
        f"Code:\n{src}"
    )
    resp = client.chat.completions.create(
        model=model or get_model(),
        messages=[{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "rationale", "schema": _SCHEMA, "strict": True}},
    )
    return json.loads(resp.choices[0].message.content)


def distill_all(project, *, only: str | None = None, overwrite: bool = False, client=None) -> int:
    """Distill rationale for decisions missing it (or just ``only``); write the sidecar. Returns count.

    Offline (no key) returns 0 without raising — the rationale fields stay empty and the graph is
    unaffected. A per-decision failure is skipped, not fatal.
    """
    from sgt.decisions.store import build_decisions, load_meta, save_meta

    if client is None:
        try:
            client = get_client(project.repo)
        except RuntimeError:
            return 0

    meta = load_meta(project.sgt_dir)
    n = 0
    for d in build_decisions(project):
        if only and d.id != only:
            continue
        if not overwrite and d.intent.context:  # already has authored/distilled rationale
            continue
        try:
            r = distill_rationale(project, d, client=client)
        except Exception:  # noqa: BLE001 — one bad decision shouldn't abort the batch
            continue
        entry = meta.setdefault(d.id, {})
        if r.get("slug") and not entry.get("slug"):  # don't clobber a planner/human slug
            entry["slug"] = r.get("slug")
        entry["context"] = r.get("context")
        entry["consequence"] = r.get("consequence")
        entry["alternatives"] = [
            {"option": a["option"], "why_rejected": a["why_rejected"],
             "source": "distilled", "confidence": "low"}
            for a in r.get("alternatives", [])
        ]
        n += 1
    if n:
        save_meta(project.sgt_dir, meta)
    return n
