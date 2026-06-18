"""The runtime that ties the semantic graph, the effect log, and git together.

A `Project` owns the `.sgt` state: the semantic DAG plus an ordered effect log
(node id -> its effect-bundle). The working tree is the *replay of active nodes'
effects* — so reverting a feature is "drop its bundle and re-materialize". Files
the project manages are tracked so a revert can delete a file that no node produces
anymore. Dependencies between nodes are inferred from which node defines a name that
another node's effects use, which is what makes revert-closure correct.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from sgt.effects.invariants import codebase_valid
from sgt.effects.model import Codebase, Effect, EffectOp, materialize
from sgt.store.gitbind import GitBinding, init_store
from sgt.store.graph import EdgeType, Node, NodeStatus, SemanticGraph

EFFECTS_FILE = "effects.json"
GRAPH_FILE = "graph.json"


class Project:
    def __init__(self, repo_path: str | Path):
        self.repo = Path(repo_path)
        self.git = GitBinding(repo_path)
        self.graph = SemanticGraph()
        self.order: list[str] = []
        self.bundles: dict[str, list[Effect]] = {}
        self.managed_files: set[str] = set()
        # node_id -> {"reason": str, "held": [str], "against": [node_id]} (R32)
        self.witnesses: dict[str, dict] = {}

    # -- lifecycle ---------------------------------------------------------
    @property
    def sgt_dir(self) -> Path:
        return self.repo / ".sgt"

    @classmethod
    def init(cls, repo_path: str | Path) -> "Project":
        init_store(repo_path)
        proj = cls(repo_path)
        proj.save()
        return proj

    @classmethod
    def open(cls, repo_path: str | Path) -> "Project":
        proj = cls(repo_path)
        gpath = proj.sgt_dir / GRAPH_FILE
        if gpath.exists():
            proj.graph = SemanticGraph.load(gpath)
        epath = proj.sgt_dir / EFFECTS_FILE
        if epath.exists():
            d = json.loads(epath.read_text(encoding="utf-8"))
            proj.order = list(d.get("order", []))
            proj.bundles = {
                nid: [Effect.from_dict(e) for e in effs]
                for nid, effs in d.get("bundles", {}).items()
            }
            proj.managed_files = set(d.get("managed_files", []))
            proj.witnesses = dict(d.get("witnesses", {}))
        return proj

    def save(self) -> None:
        self.sgt_dir.mkdir(parents=True, exist_ok=True)
        self.graph.save(self.sgt_dir / GRAPH_FILE)
        (self.sgt_dir / EFFECTS_FILE).write_text(
            json.dumps(
                {
                    "order": self.order,
                    "bundles": {nid: [e.to_dict() for e in effs] for nid, effs in self.bundles.items()},
                    "managed_files": sorted(self.managed_files),
                    "witnesses": self.witnesses,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- materialization ---------------------------------------------------
    def active_effects(self) -> list[Effect]:
        out: list[Effect] = []
        for nid in self.order:
            if self.graph.has(nid) and self.graph.get(nid).status is NodeStatus.ACTIVE:
                out.extend(self.bundles.get(nid, []))
        return out

    def materialize(self) -> Codebase:
        return materialize(self.active_effects())

    def write_working_tree(self) -> Codebase:
        cb = self.materialize()
        for path, src in cb.items():
            fp = self.repo / path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(src + ("\n" if not src.endswith("\n") else ""), encoding="utf-8")
        # delete managed files no node produces anymore
        for path in self.managed_files - set(cb):
            fp = self.repo / path
            if fp.exists():
                fp.unlink()
        self.managed_files |= set(cb)
        return cb

    # -- mutations ---------------------------------------------------------
    def add_feature(self, node: Node, effects: list[Effect], extra_dep_ids: list[str] | None = None) -> None:
        node.effect_bundle_id = node.id
        self.graph.add_node(node)
        self.order.append(node.id)
        self.bundles[node.id] = list(effects)
        deps = set(extra_dep_ids or []) | self._infer_dependencies(effects)
        for dep in deps:
            if dep != node.id and self.graph.has(dep) and not self.graph.would_create_cycle(node.id, dep):
                self.graph.add_edge(node.id, dep, EdgeType.DEPENDS_ON)

    def extend_feature(self, node_id: str, effects: list[Effect]) -> None:
        """Attach more effects to an existing node (refine/fix stays in its history).

        Newly-referenced features become DEPENDS_ON edges so that a later edit (e.g.
        a `replace_def` body that now calls another feature) is closure-correct on
        revert, not just caught by the validity gate.
        """
        self.bundles.setdefault(node_id, []).extend(effects)
        for dep in self._infer_dependencies(effects):
            if (dep != node_id and self.graph.has(dep)
                    and not self.graph.would_create_cycle(node_id, dep)):
                self.graph.add_edge(node_id, dep, EdgeType.DEPENDS_ON)

    def remove_nodes(self, node_ids: set[str]) -> None:
        for nid in node_ids:
            if self.graph.has(nid):
                self.graph.remove_node(nid)
            self.bundles.pop(nid, None)
            self.witnesses.pop(nid, None)
        self.order = [n for n in self.order if n not in node_ids]

    # -- quarantine (R32/R33/R35) -----------------------------------------
    def quarantine(self, node: Node, effects: list[Effect], reason: str,
                   held_descs: list[str], against_ids: list[str]) -> None:
        """Record held effects as a durable QUARANTINED node + witness.

        The node's effects live in the bundle store but are excluded from
        materialization by its QUARANTINED status, and it depends on the nodes it was
        meant to integrate with so reverting them GCs the quarantine too (R35).
        """
        node.status = NodeStatus.QUARANTINED
        node.effect_bundle_id = node.id
        self.graph.add_node(node)
        self.order.append(node.id)
        self.bundles[node.id] = list(effects)
        self.witnesses[node.id] = {
            "reason": reason, "held": list(held_descs), "against": list(against_ids),
        }
        for dep in against_ids:
            if dep != node.id and self.graph.has(dep) and not self.graph.would_create_cycle(node.id, dep):
                self.graph.add_edge(node.id, dep, EdgeType.DEPENDS_ON)

    def resolve_quarantine(self, node_id: str, effects: list[Effect]) -> None:
        """Reconcile a quarantine: replace its effects and flip it ACTIVE (R33)."""
        self.bundles[node_id] = list(effects)
        self.graph.get(node_id).status = NodeStatus.ACTIVE
        self.witnesses.pop(node_id, None)
        deps = self._infer_dependencies(effects)
        for dep in deps:
            if (dep != node_id and self.graph.has(dep)
                    and not self.graph.would_create_cycle(node_id, dep)):
                self.graph.add_edge(node_id, dep, EdgeType.DEPENDS_ON)

    def commit(self, message: str, node_id: str | None = None) -> str:
        self.write_working_tree()
        self.save()
        sha = self.git.commit_all(message, node_id=node_id)
        if node_id and self.graph.has(node_id):
            self.graph.get(node_id).commit_ids.append(sha)
            self.save()
        return sha

    def valid(self) -> bool:
        return codebase_valid(self.materialize())

    # -- dependency inference ---------------------------------------------
    def _defines(self) -> dict[str, set[str]]:
        """Top-level names each node introduces."""
        out: dict[str, set[str]] = {}
        for nid, effs in self.bundles.items():
            names: set[str] = set()
            for e in effs:
                if e.op in (EffectOp.ADD_DEF, EffectOp.SET_CONST):
                    names.add(e.target)
            out[nid] = names
        return out

    def _infer_dependencies(self, effects: list[Effect]) -> set[str]:
        """Nodes whose defined names are used by `effects` (so `effects` depend on them)."""
        used = _used_names(effects)
        deps: set[str] = set()
        for nid, names in self._defines().items():
            if names & used:
                deps.add(nid)
        return deps


def _used_names(effects: list[Effect]) -> set[str]:
    used: set[str] = set()
    for e in effects:
        if e.op is EffectOp.ADD_CALL:
            used.add(e.payload.get("callee", ""))
        if e.op is EffectOp.REPLACE_DEF:
            used.add(e.target)  # replacing f depends on whoever defines f
        src = e.payload.get("source", "")
        if src:
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    used.add(node.func.id)
                elif isinstance(node, ast.Name):
                    used.add(node.id)
    return {u for u in used if u}
