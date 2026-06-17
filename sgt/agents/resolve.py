"""Deterministic node-ref resolution: fuzzy phrase -> name -> exact id.

A ref the user types (``"rate limiting"``, a node id, or a substring) resolves to a
set of candidate nodes. On a single hit we resolve; on several we disambiguate; on
none we report missing (origin R6, AE1). Exact-id matches bypass fuzzy matching.
"""

from __future__ import annotations

from dataclasses import dataclass

from sgt.store.graph import SemanticGraph


@dataclass
class ResolveResult:
    kind: str  # "exact" | "resolved" | "ambiguous" | "missing"
    matches: list[str]  # node ids

    @property
    def node_id(self) -> str | None:
        return self.matches[0] if len(self.matches) == 1 else None


def resolve_ref(graph: SemanticGraph, ref: str) -> ResolveResult:
    ref = ref.strip()
    # exact id
    if graph.has(ref):
        return ResolveResult("exact", [ref])

    needle = ref.lower()
    # exact (case-insensitive) intent match ranks above substring
    exact_intent = [n.id for n in graph.nodes() if n.intent.lower() == needle]
    if len(exact_intent) == 1:
        return ResolveResult("resolved", exact_intent)

    matches = [
        n.id
        for n in graph.nodes()
        if needle in n.id.lower() or needle in n.intent.lower()
    ]
    if len(matches) == 1:
        return ResolveResult("resolved", matches)
    if len(matches) > 1:
        return ResolveResult("ambiguous", matches)
    return ResolveResult("missing", [])
