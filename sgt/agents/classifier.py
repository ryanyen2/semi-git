"""Intake classifier: route a freeform prompt into a lane (origin R7, R8).

Lanes: capability / concept / infrastructure / refine / fix / explore / question.
For refine and fix, the model also picks the existing node the prompt targets (or
null), so the orchestrator attaches the work to the right feature's history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sgt.config import get_client, get_model
from sgt.store.graph import SemanticGraph

LANES = ["capability", "concept", "infrastructure", "refine", "fix", "explore", "question"]

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lane": {"type": "string", "enum": LANES},
        "target": {"type": "string", "description": "id of the node this refines/fixes, or empty"},
        "name": {"type": "string", "description": "short kebab name for a new node, or empty"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["lane", "target", "name", "confidence", "reasoning"],
}

_SYSTEM = """You are the intake classifier for semi-git, a semantic version-control tool.
Route the user's prompt into exactly one lane:
- capability: a new user-facing feature/behavior.
- concept: a new cross-cutting subsystem other features depend on (data model, auth).
- infrastructure: build/CI/tooling/scaffolding (not product behavior).
- refine: a clarification or change to an EXISTING node (set `target` to its id).
- fix: repair broken behavior of an EXISTING node (set `target` to its id).
- explore: a speculative/throwaway alternative.
- question: a request for information that changes no code (no versioned effect).
For refine/fix, choose the target node id from the provided list; if unsure, leave
target empty and lower confidence. For new work, propose a short kebab-case `name`."""


@dataclass
class Classification:
    lane: str
    target: str
    name: str
    confidence: float
    reasoning: str


def classify(prompt: str, graph: SemanticGraph, repo_path: str = ".", model: str | None = None) -> Classification:
    client = get_client(repo_path)
    nodes_desc = "\n".join(f"- {n.id} [{n.kind.value}]: {n.intent}" for n in graph.nodes()) or "(no nodes yet)"
    user = f"Existing nodes:\n{nodes_desc}\n\nPrompt:\n{prompt}"
    resp = client.chat.completions.create(
        model=model or get_model(),
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": "classification", "schema": _SCHEMA, "strict": True}},
        temperature=0,
    )
    d = json.loads(resp.choices[0].message.content)
    return Classification(
        lane=d["lane"], target=d["target"], name=d["name"],
        confidence=float(d["confidence"]), reasoning=d["reasoning"],
    )
