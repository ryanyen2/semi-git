"""Assemble extracted entities into a connected graph and reduce it for display.

Edges are *containment* (a class contains its methods) plus *calls/imports* (a reference
resolves to a defining entity, file-aware so cross-file edges follow imports — mirroring the
dependency inference in ``sgt/effects``). The graph is connected wherever the code is, which
is the whole point: the feature forest dissolves because real code is linked.

For display we expose the **transitive reduction** of the calls/imports edges (KTD8): edges
implied by a longer path are dropped so the map shows direct relationships only. Reduction is
defined on a DAG, so cycles (mutual recursion, circular imports) are collapsed via SCC and
their internal edges kept intact. Containment edges are always kept. The full edge set is
retained alongside the reduced one for queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tree_sitter import Parser

from sgt.entities.extract import (
    Entity,
    _EXT_LANG,
    _language,
    _language_for,
    extract_codebase,
)

_IGNORE_DIRS = {".sgt", ".git", ".venv", "venv", "__pycache__", "node_modules"}


@dataclass(frozen=True)
class EntityEdge:
    src: str  # entity id
    dst: str  # entity id
    type: str  # "contains" | "calls" | "imports"

    def to_dict(self) -> dict:
        return {"src": self.src, "dst": self.dst, "type": self.type}


@dataclass
class EntityGraph:
    entities: list[Entity]
    edges: list[EntityEdge]  # full set
    reduced_edges: list[EntityEdge]  # transitive reduction (calls/imports) + containment
    components: list[list[str]]  # weakly-connected component membership (entity ids)


# -- working-tree reader -----------------------------------------------------
def read_entity_sources(repo: Path) -> dict[str, str]:
    """All supported-language sources in the working tree (whole-repo, KTD1: disk is truth)."""
    out: dict[str, str] = {}
    for ext in _EXT_LANG:
        for p in repo.rglob(f"*{ext}"):
            rel = p.relative_to(repo)
            if any(part in _IGNORE_DIRS or part.startswith(".") for part in rel.parts):
                continue
            try:
                out[str(rel)] = p.read_text(encoding="utf-8")
            except OSError:
                continue
    return out


# -- reference extraction ----------------------------------------------------
def _callee_name(node) -> str | None:
    """The leaf name a call/new node targets (`foo`, or `m` in `obj.m()`), else None."""
    fn = node.child_by_field_name("function") or node.child_by_field_name("constructor")
    if fn is None:
        return None
    if fn.type in ("identifier",):
        return fn.text.decode("utf-8", "replace")
    # Python `obj.m()` -> attribute; TS `obj.m()` -> member_expression.
    if fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        return attr.text.decode("utf-8", "replace") if attr else None
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        return prop.text.decode("utf-8", "replace") if prop else None
    return None


_CALL_TYPES = {"call", "call_expression", "new_expression"}


def _references(path: str, source: str) -> list[tuple[int, str]]:
    """``(line, callee_leaf_name)`` for every call/instantiation in the file."""
    lang = _language_for(path)
    if lang is None:
        return []
    tree = Parser(_language(lang)).parse(bytes(source, "utf-8"))
    refs: list[tuple[int, str]] = []

    def walk(node) -> None:
        if node.type in _CALL_TYPES:
            name = _callee_name(node)
            if name:
                refs.append((node.start_point[0] + 1, name))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return refs


def _innermost_owner(line: int, file_ents: list[Entity]) -> Entity | None:
    """The entity with the tightest line range containing ``line`` (the caller)."""
    best: Entity | None = None
    for e in file_ents:
        if e.start_line <= line <= e.end_line:
            if best is None or e.start_line > best.start_line:
                best = e
    return best


# -- transitive reduction ----------------------------------------------------
def _sccs(nodes: list[str], succ: dict[str, set[str]]) -> dict[str, int]:
    """Tarjan SCC (iterative). Returns node -> component-id."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    ncomp = 0

    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(sorted(succ.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(sorted(succ.get(w, ())))))
                    advanced = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            if low[v] == index[v]:
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp[w] = ncomp
                    if w == v:
                        break
                ncomp += 1
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
    return comp


def _dag_reachable_without(u: str, v: str, succ: dict[str, set[str]]) -> bool:
    """Is ``v`` reachable from ``u`` ignoring the direct ``u->v`` edge? (DAG)"""
    seen: set[str] = set()
    stack = [w for w in succ.get(u, ()) if w != v]
    while stack:
        cur = stack.pop()
        if cur == v:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(succ.get(cur, ()))
    return False


def _transitive_reduction(ref_edges: list[EntityEdge]) -> list[EntityEdge]:
    """Reduce calls/imports edges: collapse SCCs, reduce the acyclic condensation."""
    nodes = sorted({e.src for e in ref_edges} | {e.dst for e in ref_edges})
    succ: dict[str, set[str]] = {n: set() for n in nodes}
    for e in ref_edges:
        succ[e.src].add(e.dst)
    comp = _sccs(nodes, succ)

    # Condensation adjacency (between distinct SCCs only).
    cond: dict[int, set[int]] = {}
    for e in ref_edges:
        cu, cv = comp[e.src], comp[e.dst]
        if cu != cv:
            cond.setdefault(cu, set()).add(cv)
    # Edges of the condensation DAG that survive transitive reduction.
    kept_cond: set[tuple[int, int]] = set()
    for cu, dsts in cond.items():
        for cv in dsts:
            if not _dag_reachable_without(cu, cv, cond):
                kept_cond.add((cu, cv))

    out: list[EntityEdge] = []
    for e in ref_edges:
        cu, cv = comp[e.src], comp[e.dst]
        # Keep intra-SCC (cycle) edges intact; keep inter-SCC edges on surviving condensation edges.
        if cu == cv or (cu, cv) in kept_cond:
            out.append(e)
    return out


# -- assembly ----------------------------------------------------------------
def build_entity_graph(codebase: dict[str, str]) -> EntityGraph:
    entities = extract_codebase(codebase)
    by_id = {e.id: e for e in entities}
    ents_by_file: dict[str, list[Entity]] = {}
    for e in entities:
        ents_by_file.setdefault(e.file, []).append(e)

    # leaf-name index for reference resolution (file-aware preference)
    leaf_to_ids: dict[str, list[str]] = {}
    for e in entities:
        leaf_to_ids.setdefault(e.name.split(".")[-1], []).append(e.id)

    contains: list[EntityEdge] = []
    for e in entities:
        if e.container is not None:
            parent_id = f"{e.file}::{e.container}"
            if parent_id in by_id:
                contains.append(EntityEdge(parent_id, e.id, "contains"))

    ref_edges: list[EntityEdge] = []
    seen_refs: set[tuple[str, str]] = set()
    for file, src in sorted(codebase.items()):
        file_ents = ents_by_file.get(file, [])
        if not file_ents:
            continue
        for line, name in _references(file, src):
            owner = _innermost_owner(line, file_ents)
            if owner is None:
                continue
            candidates = leaf_to_ids.get(name, [])
            same_file = [cid for cid in candidates if by_id[cid].file == file]
            if len(same_file) == 1:
                target = same_file[0]
                etype = "calls"
            elif len(candidates) == 1:
                target = candidates[0]
                etype = "imports" if by_id[target].file != file else "calls"
            else:
                continue  # unresolved or ambiguous -> no false edge
            if target == owner.id:
                continue  # self-reference
            key = (owner.id, target)
            if key in seen_refs:
                continue
            seen_refs.add(key)
            ref_edges.append(EntityEdge(owner.id, target, etype))

    edges = contains + ref_edges
    reduced_edges = contains + _transitive_reduction(ref_edges)
    components = _components([e.id for e in entities], edges)
    return EntityGraph(entities, edges, reduced_edges, components)


def owning_nodes(
    entities: list[Entity], spans_by_file: dict[str, list[dict]]
) -> dict[str, str | None]:
    """Map each entity -> the feature node that owns the plurality of its lines (else None).

    ``spans_by_file`` is ``attribute()``'s per-file blame spans as dicts (``start``/``end``/
    ``node_id``). Plurality (most owned lines wins, ties broken by node id for determinism)
    is robust to a decorator or blank line at the def boundary. Entities in files with no
    blame — untracked code, all TypeScript — resolve to None and render dim (R3/R5).
    """
    out: dict[str, str | None] = {}
    for e in entities:
        spans = spans_by_file.get(e.file)
        if not spans:
            out[e.id] = None
            continue
        counts: dict[str, int] = {}
        for sp in spans:
            nid = sp.get("node_id")
            if nid is None:
                continue
            lo, hi = max(sp["start"], e.start_line), min(sp["end"], e.end_line)
            if lo <= hi:
                counts[nid] = counts.get(nid, 0) + (hi - lo + 1)
        out[e.id] = max(counts, key=lambda k: (counts[k], k)) if counts else None
    return out


def _components(node_ids: list[str], edges: list[EntityEdge]) -> list[list[str]]:
    """Weakly-connected components (union-find), each a sorted list of entity ids."""
    parent = {n: n for n in node_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        if a in parent and b in parent:
            parent[find(a)] = find(b)

    for e in edges:
        union(e.src, e.dst)
    groups: dict[str, list[str]] = {}
    for n in node_ids:
        groups.setdefault(find(n), []).append(n)
    return [sorted(g) for g in sorted(groups.values(), key=lambda g: sorted(g)[0])]
