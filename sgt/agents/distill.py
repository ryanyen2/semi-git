"""Distillation agent: cluster + label raw out-of-band changes (R5/R24).

`sgt/effects/diff.py` answers *what* changed (a flat list of typed effects). This agent
answers *what it means*: it groups related effects into coherent units and labels each
as either a refinement of an existing node or a new feature — the "system-distilled,
user-corrected" graph behavior applied to a drift instead of a prompt.

The deterministic `fallback_cluster` (group by file, attribute to the file's sole owner)
keeps `sgt sync` working without the LLM and is what the tests drive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sgt.config import get_client, get_model
from sgt.effects.model import Effect

_LANES = ["capability", "concept", "infrastructure", "fix"]


@dataclass
class Cluster:
    intent: str
    effects: list[Effect] = field(default_factory=list)
    target: str | None = None      # existing node id to extend, or None for a new node
    kind: str = "capability"


class DistillError(Exception):
    """Raised when the model returns an empty or malformed clustering."""


# ---------------------------------------------------------------------------
def _active_owner_of_file(project, file: str) -> str | None:
    owners = {
        nid for nid, effs in project.bundles.items()
        if any(e.file == file for e in effs)
        and project.graph.has(nid)
        and project.graph.get(nid).status.value == "active"
    }
    return next(iter(owners)) if len(owners) == 1 else None


def fallback_cluster(effects: list[Effect], project) -> list[Cluster]:
    """Deterministic clustering: one cluster per file, attributed to its sole owner."""
    by_file: dict[str, list[Effect]] = {}
    for e in effects:
        by_file.setdefault(e.file, []).append(e)
    clusters: list[Cluster] = []
    for file, effs in by_file.items():
        owner = _active_owner_of_file(project, file)
        intent = f"reconcile out-of-band changes to {file}"
        clusters.append(Cluster(intent=intent, effects=effs, target=owner,
                                kind="fix" if owner else "capability"))
    return clusters


_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "intent": {"type": "string", "description": "what this group of changes accomplishes"},
                    "target": {"type": "string", "description": "id of the existing node this refines, or empty for new"},
                    "kind": {"type": "string", "enum": _LANES},
                    "effect_indices": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["intent", "target", "kind", "effect_indices"],
            },
        }
    },
    "required": ["clusters"],
}

_SYSTEM = """You are the distillation agent for semi-git. The user changed code outside
the tool; we extracted the typed effects that represent those changes. Group the effects
into the SMALLEST set of coherent features. For each group:
- If it clearly refines/repairs an EXISTING node, set `target` to that node's id.
- Otherwise leave `target` empty and give a short `intent` and a `kind`.
- List the 0-based `effect_indices` belonging to the group. Cover every effect exactly once.
Do not invent changes; only organize the ones given.

Refactor rule (important): a refactor where old code is removed and replacement code is
added is ONE change, not two. If a `remove_*` of a symbol and an `add_*`/`replace_*` that
supersedes it (a rename, a move into a class, a consolidation of helpers) are halves of the
same refactor, put them in the SAME group — never split a removal from the addition that
replaces it. When that refactor reworks an EXISTING node's code, target that node so
reverting the group restores the original. Only emit a brand-new node for genuinely new
behavior, not for code that merely moved or was renamed."""


def llm_cluster(effects: list[Effect], project, repo_path: str = ".", model: str | None = None) -> list[Cluster]:
    if not effects:
        return []
    client = get_client(repo_path)
    nodes_desc = "\n".join(
        f"- {n.id} [{n.kind.value}]: {n.intent}" for n in project.graph.nodes()
    ) or "(no nodes yet)"
    eff_desc = "\n".join(
        f"[{i}] {e.op.value} {e.target} ({e.file})" for i, e in enumerate(effects)
    )
    user = f"Existing nodes:\n{nodes_desc}\n\nExtracted effects:\n{eff_desc}"
    try:
        resp = client.chat.completions.create(
            model=model or get_model(),
            messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "distillation", "schema": _SCHEMA, "strict": True}},
            temperature=0,
        )
        payload = json.loads(resp.choices[0].message.content)
    except Exception as ex:  # noqa: BLE001 - caller degrades to fallback_cluster
        raise DistillError(f"distillation failed: {type(ex).__name__}: {ex}") from ex

    clusters: list[Cluster] = []
    claimed: set[int] = set()
    for c in payload.get("clusters", []):
        idxs = [i for i in c.get("effect_indices", []) if 0 <= i < len(effects) and i not in claimed]
        claimed.update(idxs)
        if not idxs:
            continue
        target = c.get("target") or None
        if target and not project.graph.has(target):
            target = None  # hallucinated id -> treat as new
        clusters.append(Cluster(
            intent=c.get("intent", "").strip() or "reconcile changes",
            effects=[effects[i] for i in idxs],
            target=target,
            kind=c.get("kind", "capability"),
        ))
    # Any effect the model failed to place still gets reconciled (never dropped).
    leftover = [effects[i] for i in range(len(effects)) if i not in claimed]
    if leftover:
        clusters.extend(fallback_cluster(leftover, project))
    if not clusters:
        raise DistillError("distillation produced no clusters")
    return clusters
