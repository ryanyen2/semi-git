"""Decomposition agent: turn one intent into a transient constraint graph (R28).

The planner proposes coordination-free sub-tasks, each declaring the names it
`provides` and `needs` (plus optional explicit `depends_on`), so the orchestrator can
layer and fan them out. An atomic intent returns a single sub-task — the signal the
orchestrator uses to skip fan-out and run the existing single-agent stream.

This is a semi-git-owned policy (RL-ready later, origin R27/R23); v1 is LLM-prompted.
"""

from __future__ import annotations

import json

from sgt.config import get_client, get_model
from sgt.effects.model import Codebase
from sgt.orchestrate.constraint import ConstraintError, ConstraintGraph, SubTask

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "subtasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string", "description": "short kebab handle, unique in this plan"},
                    "slug": {"type": "string",
                             "description": "a human title for this decision, AT MOST 5 words, no trailing period"},
                    "intent": {"type": "string", "description": "a self-contained coding request"},
                    "context": {"type": "string",
                                "description": "the situation or need that makes this sub-task necessary"},
                    "consequence": {"type": "string",
                                    "description": "what the codebase will guarantee once this lands"},
                    "provides": {"type": "array", "items": {"type": "string"},
                                 "description": "top-level names this task will define"},
                    "needs": {"type": "array", "items": {"type": "string"},
                              "description": "names it requires from other sub-tasks"},
                    "depends_on": {"type": "array", "items": {"type": "string"},
                                   "description": "keys of sub-tasks that must land first"},
                },
                "required": ["key", "slug", "intent", "context", "consequence",
                             "provides", "needs", "depends_on"],
            },
        }
    },
    "required": ["subtasks"],
}

_SYSTEM = """You are the decomposition planner for semi-git, a semantic version-control tool.
A sub-task is a DECISION: one coherent capability a reviewer would version, revert, or suspend as a
unit — NOT a single function. Decompose the intent into the FEWEST such capabilities, then compose.
Rules:
- A capability usually defines SEVERAL top-level names (a public entry point plus its helpers). Put
  all of them in that one sub-task's `provides`. Do NOT make a separate sub-task for a helper that
  only exists to serve another sub-task — fold it in. Split only when a part is independently useful,
  independently landable, or something else already needs it on its own.
  Example: "load a CSV and compute summary statistics (mean, median)" is ONE sub-task
  (`provides: [load_csv, compute_summary]`), not three. "add a CLI and a web API over the same core"
  is TWO (the surfaces are independently useful), each `needs`-ing the core.
- Declare `provides` (the top-level names this capability defines) and `needs` (names it calls that
  ANOTHER sub-task provides). A task that needs another's output lists that name in `needs` (or the
  producing key in `depends_on`). Prefer disjoint `provides` so capabilities compose cleanly.
- ENHANCING existing code is a REVISION, not a new capability. If the intent modifies, extends, or
  improves something that already exists (see the capability map of what's already built), set
  `provides` to the EXISTING name(s) being changed — do NOT invent a new name for a change to existing
  behavior. Example: "add memory tracking to the timer" when a `measure_time` already exists →
  `provides: [measure_time]` (it revises the timer), NOT `provides: [measure_memory]`.
- For each sub-task also give: `slug` (a human title, at most 5 words), `context` (the need that
  precedes it), and `consequence` (what the codebase guarantees once it lands). Ground these in the
  intent and current codebase; do not invent files or APIs.
- If the intent is one cohesive capability, return EXACTLY ONE sub-task. Two or three is typical;
  more than ~4 for a single intent almost always means you are splitting at the function level — recombine.
- Do not invent work beyond the intent."""


class PlannerError(Exception):
    """Raised when the model returns an empty or malformed decomposition."""


def decompose(intent: str, codebase: Codebase, repo_path: str = ".", model: str | None = None,
              context: str | None = None) -> ConstraintGraph:
    client = get_client(repo_path)
    # `context` is the compact, graph-driven view (capability map + retrieved code) built by the
    # caller; fall back to rendering the whole codebase only when none is supplied (small/new repos).
    rendered = context if context is not None else f"Current codebase:\n{_render(codebase)}"
    user = f"{rendered}\n\nIntent to decompose:\n{intent}"
    try:
        resp = client.chat.completions.create(
            model=model or get_model(),
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "decomposition", "schema": _SCHEMA, "strict": True}},
            temperature=0,
        )
        payload = json.loads(resp.choices[0].message.content)
    except Exception as ex:  # noqa: BLE001 - the loop degrades to single-agent on failure
        raise PlannerError(f"decomposition failed: {type(ex).__name__}: {ex}") from ex

    subtasks = payload.get("subtasks", [])
    if not subtasks:
        raise PlannerError("decomposition returned no sub-tasks")

    graph = ConstraintGraph()
    seen: set[str] = set()
    for i, st in enumerate(subtasks):
        key = st.get("key") or f"task-{i + 1}"
        while key in seen:  # defensive: keep keys unique
            key = f"{key}-{i + 1}"
        seen.add(key)
        graph.add(SubTask(
            key=key,
            intent=st.get("intent", "").strip() or intent,
            provides=[n for n in st.get("provides", []) if n],
            needs=[n for n in st.get("needs", []) if n],
            depends_on=[k for k in st.get("depends_on", []) if k],
            slug=(st.get("slug") or "").strip() or None,
            context=(st.get("context") or "").strip() or None,
            consequence=(st.get("consequence") or "").strip() or None,
        ))
    # A model can emit a cyclic decomposition (explicit depends_on, or a needs<->provides
    # loop). Validate layerability here so the caller degrades to single-agent rather than
    # crashing on an uncaught ConstraintError downstream.
    try:
        graph.layers()
    except ConstraintError as ex:
        raise PlannerError(f"decomposition is not a DAG: {ex}") from ex
    return graph


def _render(cb: Codebase) -> str:
    if not cb:
        return "(empty — new project)"
    return "\n\n".join(f"### {p}\n```python\n{cb[p] or '(empty)'}\n```" for p in sorted(cb))
