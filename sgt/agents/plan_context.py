"""Graph-driven context for the planner — a compact, retrieved view instead of the whole tree.

Rendering every file on every `plan` grows the prompt without bound (the stress corpus showed it
climbing as the codebase did) and buries the signal. Instead we give the planner two things:

1. A **capability map** — the HEAD composition (in-force decisions) with the names each provides. This
   is O(lanes), always cheap, and tells the planner what already exists *and what it is called*, so it
   declares `needs`/`provides` against real names (less `preprocess` vs `preprocess_data` drift).
2. **Retrieved code** — entities seeded by name overlap with the intent, expanded one hop over the
   call graph (graph-driven RAG), rendered up to a char budget. Relevant code, bounded size.

Degrades gracefully: an empty/new project (no entities) falls back to rendering the small codebase;
no LLM, no embeddings — keyword + graph structure only, so it stays offline and deterministic.
"""

from __future__ import annotations

import re

_STOP = {
    "the", "a", "an", "to", "of", "and", "for", "with", "that", "from", "into", "by", "add",
    "function", "implement", "create", "use", "using", "new", "given", "each", "this", "its",
}


def _tokens(s: str) -> set[str]:
    """Lowercase content tokens, splitting snake_case and camelCase, dropping stopwords/short words."""
    out: set[str] = set()
    for part in re.split(r"[^a-zA-Z0-9]+", s or ""):
        for w in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", part):
            w = w.lower()
            if len(w) >= 3 and w not in _STOP:
                out.add(w)
    return out


def _entity_source(codebase: dict[str, str], e: dict) -> str:
    txt = codebase.get(e["file"])
    if not txt:
        return ""
    lines = txt.splitlines()
    s, en = e.get("start_line", 1) or 1, e.get("end_line", len(lines)) or len(lines)
    body = "\n".join(lines[s - 1:en])
    return f"# {e['file']}::{e['name']}\n{body}"


def build_plan_context(project, intent: str, *, budget_chars: int = 4000, cap_features: int = 40) -> str:
    """A compact planner context: capability map (always) + call-graph-retrieved relevant code."""
    from sgt.api import entity_graph_view
    from sgt.decisions.store import build_decisions, load_frontier

    # Slice entity bodies from the SAME source the entity graph parsed (the working tree), not from
    # materialize() — the two can lay out lines differently, which would slice the wrong def.
    try:
        codebase = project._disk_sources()
    except Exception:  # noqa: BLE001
        codebase = project.materialize()

    # 1) capability map = the HEAD composition with the names each in-force decision provides. This
    # is O(lanes), so at very large scale it is itself capped: keep the most intent-relevant ones
    # (name/slug overlap with the intent), then by recency, up to `cap_features`, with a count of the
    # rest — the planner sees what's most relevant to this intent without re-listing a 500-lane repo.
    decisions = build_decisions(project)
    in_force = load_frontier(project, decisions).in_force()
    toks = _tokens(intent)

    def _cap_score(d):
        names = {k.split("::", 1)[1] for k in d.footprint if "::" in k}
        rel = len(toks & _tokens(f"{d.intent.slug or d.intent.decision} {' '.join(names)}"))
        return (rel, d.landing)

    in_force_decs = sorted((d for d in decisions if d.id in in_force), key=_cap_score, reverse=True)
    shown = in_force_decs[:cap_features]
    cap_lines = []
    for d in sorted(shown, key=lambda d: d.landing):  # display oldest→newest for readability
        names = sorted({k.split("::", 1)[1] for k in d.footprint if "::" in k})
        slug = d.intent.slug or d.intent.decision
        cap_lines.append(f"- {slug} (provides: {', '.join(names[:6]) or '—'})")
    if len(in_force_decs) > cap_features:
        cap_lines.append(f"- (+{len(in_force_decs) - cap_features} more capabilities, not shown)")
    cap_map = "\n".join(cap_lines) or "(nothing built yet)"

    # 2) retrieved code: seed entities by name overlap with the intent, expand one call-graph hop.
    try:
        ents = entity_graph_view(project).get("entities", [])
    except Exception:  # noqa: BLE001 — retrieval is best-effort; never block planning
        ents = []
    if not ents:
        body = _render_codebase(codebase) if codebase else "(empty — new project)"
        return f"Existing capabilities (HEAD):\n{cap_map}\n\nCode relevant to this intent:\n{body}"

    by_id = {e["id"]: e for e in ents}

    def score(e: dict) -> int:
        return len(toks & _tokens(f"{e['name']} {e['file']}"))

    seeds = sorted((e for e in ents if score(e) > 0), key=score, reverse=True)
    dep = {e["id"]: set(e.get("depends_on", [])) for e in ents}
    rev: dict[str, set[str]] = {}
    for sid, ds in dep.items():
        for t in ds:
            rev.setdefault(t, set()).add(sid)

    seed_ids = {e["id"] for e in seeds}
    neighbors = [
        by_id[n] for s in seed_ids for n in sorted(dep.get(s, set()) | rev.get(s, set()))
        if n in by_id and n not in seed_ids
    ]
    chunks, used, seen = [], 0, set()
    for e in [*seeds, *neighbors]:
        if e["id"] in seen:
            continue
        src = _entity_source(codebase, e)
        if not src:
            continue
        if chunks and used + len(src) > budget_chars:
            break
        chunks.append(src)
        used += len(src)
        seen.add(e["id"])
    code = "\n\n".join(chunks) or "(no directly-relevant code; build on the capabilities above)"
    return f"Existing capabilities (HEAD):\n{cap_map}\n\nCode relevant to this intent:\n{code}"


def _render_codebase(cb: dict[str, str]) -> str:
    return "\n\n".join(f"### {p}\n```python\n{cb[p] or '(empty)'}\n```" for p in sorted(cb))
