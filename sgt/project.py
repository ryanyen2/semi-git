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
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from sgt.effects.diff import files_differ
from sgt.effects.invariants import codebase_valid
from sgt.effects.model import Codebase, Effect, EffectError, EffectOp, materialize
from sgt.store.clock import VersionVector
from sgt.store.gitbind import GitBinding, GitError, init_store
from sgt.store.graph import EdgeType, Node, NodeStatus, SemanticGraph
from sgt.store.oplog import EffectLog, LogEntry
from sgt.store.replica import ReplicaIdentity

EFFECTS_FILE = "effects.json"
GRAPH_FILE = "graph.json"

# Directories never scanned for out-of-band source (tooling, not the managed tree).
_IGNORE_DIRS = {".sgt", ".git", ".venv", "venv", "__pycache__", "node_modules"}


@dataclass
class DriftReport:
    """Where the working tree disagrees with the replay of the active effects."""

    modified: list[str] = field(default_factory=list)  # content differs from expected
    added: list[str] = field(default_factory=list)      # on disk, unknown to the graph
    deleted: list[str] = field(default_factory=list)     # graph expects it, gone from disk

    @property
    def any(self) -> bool:
        return bool(self.modified or self.added or self.deleted)

    def summary(self) -> str:
        parts = []
        if self.modified:
            parts.append("modified: " + ", ".join(self.modified))
        if self.added:
            parts.append("new: " + ", ".join(self.added))
        if self.deleted:
            parts.append("deleted: " + ", ".join(self.deleted))
        return "; ".join(parts) or "no drift"


class Project:
    def __init__(self, repo_path: str | Path, replica_id: str | None = None):
        self.repo = Path(repo_path)
        self.git = GitBinding(repo_path)
        self.graph = SemanticGraph()
        # Node landing order. NOTE: materialization no longer reads this — `active_effects`
        # replays the log in its own causal `order_key` total order. `self.order` is retained
        # only as the node-arrival record the merge engine consults (`merge/engine.py`); every
        # mutation keeps it in step with the log so the two never diverge.
        self.order: list[str] = []
        # The append-only effect log is the authored source of truth for effects; the
        # per-node `bundles` view is derived from it (see the `bundles` property).
        self.log = EffectLog()
        # `replica_id` is injectable so tests can assert deterministic conflict tie-breaks.
        self.replica = ReplicaIdentity.load_or_create(self.sgt_dir, replica_id=replica_id)
        self.vv = VersionVector()  # this replica's observed frontier
        self.managed_files: set[str] = set()
        # node_id -> {"reason": str, "held": [str], "against": [node_id]} (R32)
        self.witnesses: dict[str, dict] = {}

    @property
    def bundles(self) -> dict[str, list[Effect]]:
        """Per-node effect lists, derived from the log (the old authored dict)."""
        return self.log.bundles()

    # -- authoring (stamp every effect with identity + causality) ----------
    def _stamp(self, node_id: str, effect: Effect) -> LogEntry:
        """Mint an id, advance the version vector, and wrap `effect` as a log entry."""
        eid = self.replica.mint()
        self.vv = self.vv.increment(self.replica.replica_id)
        stamped = dataclasses.replace(effect, eid=eid)
        return LogEntry(eid=eid, node_id=node_id, effect=stamped,
                        author=self.replica.replica_id, vv=self.vv)

    def _append_effects(self, node_id: str, effects: list[Effect]) -> None:
        for e in effects:
            self.log.append(self._stamp(node_id, e))

    # -- lifecycle ---------------------------------------------------------
    @property
    def sgt_dir(self) -> Path:
        return self.repo / ".sgt"

    @classmethod
    def init(cls, repo_path: str | Path, replica_id: str | None = None) -> "Project":
        init_store(repo_path)
        proj = cls(repo_path, replica_id=replica_id)
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
            proj.managed_files = set(d.get("managed_files", []))
            proj.witnesses = dict(d.get("witnesses", {}))
            if "log" in d:
                proj.log = EffectLog.from_dict(d["log"])
                proj.vv = proj.log.frontier()
            else:
                # Migrate the legacy order+bundles store into the log: stamp each effect
                # in its old replay order so identity/causality exist going forward.
                legacy = {
                    nid: [Effect.from_dict(e) for e in effs]
                    for nid, effs in d.get("bundles", {}).items()
                }
                for nid in proj.order:
                    proj._append_effects(nid, legacy.get(nid, []))
        return proj

    def save(self) -> None:
        self.sgt_dir.mkdir(parents=True, exist_ok=True)
        self.graph.save(self.sgt_dir / GRAPH_FILE)
        (self.sgt_dir / EFFECTS_FILE).write_text(
            json.dumps(
                {
                    "order": self.order,
                    "log": self.log.to_dict(),
                    "managed_files": sorted(self.managed_files),
                    "witnesses": self.witnesses,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- materialization ---------------------------------------------------
    def active_effects(self) -> list[Effect]:
        """Active effects in the canonical replay order — a pure function of the log.

        Order is the total order ``(vv.rank, author, counter)`` (oplog ``order_key``),
        which is a linear extension of causal happens-before. Because a use is authored
        only after its definition is observed, that definition causally precedes the use
        and so sorts before it — dependency order falls out of causal order, no separate
        topological pass needed. This makes materialization replica-independent: two
        replicas with the same effects replay identically (SEC).
        """
        active = {
            nid for nid in self.log.node_ids()
            if self.graph.has(nid) and self.graph.get(nid).status is NodeStatus.ACTIVE
        }
        entries = sorted(self.log.live_entries(active), key=lambda e: e.order_key)
        return [e.effect for e in entries]

    def materialize(self) -> Codebase:
        return materialize(self.active_effects())

    def _safe_path(self, path: str) -> Path:
        """Resolve a managed path under the repo root, refusing any escape (defense-in-depth).

        Effect paths originate from the distiller's ``relative_to(self.repo)`` output, so a
        traversal (``../``, absolute) should never occur — but ``write_working_tree`` is the one
        place untrusted path text becomes a filesystem write, so we assert containment rather
        than trust the upstream.
        """
        rp = self.repo.resolve()
        fp = (rp / path).resolve()
        if fp != rp and rp not in fp.parents:
            raise EffectError(f"refusing to write outside the repo: {path!r}")
        return fp

    def write_working_tree(self) -> Codebase:
        cb = self.materialize()
        for path, src in cb.items():
            fp = self._safe_path(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(src + ("\n" if not src.endswith("\n") else ""), encoding="utf-8")
        # delete managed files no node produces anymore
        for path in self.managed_files - set(cb):
            fp = self._safe_path(path)
            if fp.exists():
                fp.unlink()
        self.managed_files |= set(cb)
        return cb

    # -- mutations ---------------------------------------------------------
    def add_feature(self, node: Node, effects: list[Effect], extra_dep_ids: list[str] | None = None) -> None:
        node.effect_bundle_id = node.id
        self.graph.add_node(node)
        self.order.append(node.id)
        self._append_effects(node.id, effects)
        deps = set(extra_dep_ids or []) | self._infer_dependencies(effects)
        for dep in deps:
            if dep != node.id and self.graph.has(dep) and not self.graph.would_create_cycle(node.id, dep):
                self.graph.add_edge(node.id, dep, EdgeType.DEPENDS_ON)

    def add_plan(self, nodes: list[Node], edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Persist tentative PLANNED nodes plus their declared DEPENDS_ON edges.

        A planned node carries no effects (``effect_bundle_id`` stays ``None`` and nothing
        is appended to the log), so it is inert: ``active_effects``/``materialize`` skip it
        (they admit only ACTIVE nodes) until a ``checkpoint --fulfills`` lands real effects
        under its id and flips it ACTIVE. Nodes are added before edges so a planned->planned
        dependency resolves.

        Returns the edges that were *dropped* because they would create a dependency cycle, so
        the caller can surface them rather than silently losing a declared dependency.
        """
        for node in nodes:
            node.status = NodeStatus.PLANNED
            self.graph.add_node(node)
        dropped: list[tuple[str, str]] = []
        for src, dst in edges:
            if src == dst or not (self.graph.has(src) and self.graph.has(dst)):
                continue
            if self.graph.would_create_cycle(src, dst):
                dropped.append((src, dst))
                continue
            self.graph.add_edge(src, dst, EdgeType.DEPENDS_ON)
        return dropped

    def extend_feature(self, node_id: str, effects: list[Effect]) -> None:
        """Attach more effects to an existing node (refine/fix stays in its history).

        Newly-referenced features become DEPENDS_ON edges so that a later edit (e.g.
        a `replace_def` body that now calls another feature) is closure-correct on
        revert, not just caught by the validity gate.
        """
        self._append_effects(node_id, effects)
        for dep in self._infer_dependencies(effects):
            if (dep != node_id and self.graph.has(dep)
                    and not self.graph.would_create_cycle(node_id, dep)):
                self.graph.add_edge(node_id, dep, EdgeType.DEPENDS_ON)

    def remove_nodes(self, node_ids: set[str]) -> None:
        for nid in node_ids:
            if self.graph.has(nid):
                self.graph.remove_node(nid)
            self.witnesses.pop(nid, None)
        self.log.tombstone(set(node_ids))
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
        self._append_effects(node.id, effects)
        # Held code is not active, so it must not linger on disk: mark its files managed so
        # write_working_tree removes them (the effects survive in the log, restored on resolve).
        self.managed_files |= {e.file for e in effects}
        # Anchor for closure (R35): the nodes it was meant to integrate with, PLUS any
        # node whose names the held effects reference. The union keeps the quarantine
        # reachable by revert even when nothing landed in this run (avoids an orphan).
        anchors = set(against_ids) | self._infer_dependencies(effects)
        self.witnesses[node.id] = {
            "reason": reason, "held": list(held_descs), "against": sorted(anchors),
        }
        for dep in anchors:
            if dep != node.id and self.graph.has(dep) and not self.graph.would_create_cycle(node.id, dep):
                self.graph.add_edge(node.id, dep, EdgeType.DEPENDS_ON)

    def fulfill(self, node_id: str, effects: list[Effect], intent: str | None = None) -> None:
        """Land the coding agent's real effects under a PLANNED node and flip it ACTIVE.

        sgt authors nothing here: ``effects`` were distilled from the agent's on-disk edits.
        The node joins the replay order last (its effects were gated against the full current
        tree, like a resolved quarantine). The planned intent is kept in ``provenance``; if a
        fresh intent is declared it becomes the node's intent (reality wins, KTD3).
        """
        node = self.graph.get(node_id)
        node.effect_bundle_id = node_id
        self._append_effects(node_id, effects)
        if intent and intent != node.intent:
            node.provenance.append(f"planned: {node.intent}")
            node.intent = intent
        node.status = NodeStatus.ACTIVE
        if node_id in self.order:
            self.order.remove(node_id)
        self.order.append(node_id)
        for dep in self._infer_dependencies(effects):
            if (dep != node_id and self.graph.has(dep)
                    and not self.graph.would_create_cycle(node_id, dep)):
                self.graph.add_edge(node_id, dep, EdgeType.DEPENDS_ON)

    def quarantine_existing(self, node_id: str, effects: list[Effect], reason: str,
                            held_descs: list[str], against_ids: list[str],
                            intent: str | None = None) -> None:
        """Transition an existing node (e.g. a PLANNED one being fulfilled) to QUARANTINED.

        Unlike ``quarantine`` (which adds a *new* held node), this attaches the held effects
        to a node already in the graph and flips its status — so a ``checkpoint --fulfills``
        whose code does not yet commute marks *that* node held (recoverable via ``reconcile``)
        instead of spawning an anonymous quarantine. The held bundle is *replaced* (not
        appended), so re-holding a node with revised code does not accumulate stale effects.
        A freshly declared ``intent`` is adopted, keeping the prior intent as provenance —
        the held node carries the same history a fulfilled one would (KTD3).
        """
        node = self.graph.get(node_id)
        if intent and intent != node.intent:
            node.provenance.append(f"planned: {node.intent}")
            node.intent = intent
        node.status = NodeStatus.QUARANTINED
        node.effect_bundle_id = node_id
        self.log.replace_node_effects(node_id, [self._stamp(node_id, e) for e in effects])
        # Held code must not linger on disk (see quarantine): mark its files managed for cleanup.
        self.managed_files |= {e.file for e in effects}
        if node_id not in self.order:
            self.order.append(node_id)
        anchors = set(against_ids) | self._infer_dependencies(effects)
        self.witnesses[node_id] = {
            "reason": reason, "held": list(held_descs), "against": sorted(anchors),
        }
        for dep in anchors:
            if dep != node_id and self.graph.has(dep) and not self.graph.would_create_cycle(node_id, dep):
                self.graph.add_edge(node_id, dep, EdgeType.DEPENDS_ON)

    def resolve_quarantine(self, node_id: str, effects: list[Effect]) -> None:
        """Reconcile a quarantine: replace its effects and flip it ACTIVE (R33)."""
        self.log.replace_node_effects(node_id, [self._stamp(node_id, e) for e in effects])
        self.graph.get(node_id).status = NodeStatus.ACTIVE
        self.witnesses.pop(node_id, None)
        # The rewritten effects were validated against the FULL current codebase, so the
        # node must replay last — move it to the end of the materialization order.
        if node_id in self.order:
            self.order.remove(node_id)
        self.order.append(node_id)
        deps = self._infer_dependencies(effects)
        for dep in deps:
            if (dep != node_id and self.graph.has(dep)
                    and not self.graph.would_create_cycle(node_id, dep)):
                self.graph.add_edge(node_id, dep, EdgeType.DEPENDS_ON)

    def _snapshot_sgt(self) -> dict[str, str | None]:
        """Current on-disk `.sgt` payload, for transactional rollback on git failure."""
        snap: dict[str, str | None] = {}
        for fn in (GRAPH_FILE, EFFECTS_FILE):
            p = self.sgt_dir / fn
            snap[fn] = p.read_text(encoding="utf-8") if p.exists() else None
        return snap

    def _restore_sgt(self, snap: dict[str, str | None]) -> None:
        for fn, content in snap.items():
            p = self.sgt_dir / fn
            if content is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(content, encoding="utf-8")

    def commit(self, message: str, node_id: str | None = None) -> str:
        self.write_working_tree()
        # Persist `.sgt` then commit git; if the commit fails, roll `.sgt` back so the
        # semantic state never advances past git (no split-brain).
        snapshot = self._snapshot_sgt()
        self.save()
        try:
            sha = self.git.commit_all(message, node_id=node_id)
        except GitError:
            self._restore_sgt(snapshot)
            raise
        if node_id and self.graph.has(node_id):
            self.graph.get(node_id).commit_ids.append(sha)
            self.save()
        return sha

    def valid(self) -> bool:
        # A materialize that raises (e.g. a suspend left a dangling precondition) is an
        # invalid state, not a crash — callers (switch/revert) roll back on False.
        try:
            return codebase_valid(self.materialize())
        except EffectError:
            return False

    # -- drift (tree <-> graph reconciliation, R5) ------------------------
    def _disk_sources(self) -> Codebase:
        """Python sources actually on disk: managed files plus any new `.py`."""
        out: Codebase = {}
        for p in self.repo.rglob("*.py"):
            rel = p.relative_to(self.repo)
            if any(part in _IGNORE_DIRS or part.startswith(".") for part in rel.parts):
                continue
            try:
                out[str(rel)] = p.read_text(encoding="utf-8")
            except OSError:
                continue
        return out

    def check_drift(self) -> DriftReport:
        """Compare the working tree against the replay of the active effects.

        Drift means the tree carries changes the graph did not author — a hand edit, a
        direct ``git`` commit, or another agent. Surfacing it lets ``sgt sync`` distill
        those changes back into the graph instead of ``write_working_tree`` clobbering them.
        """
        try:
            expected = self.materialize()
        except EffectError:
            return DriftReport()  # broken graph state is a separate problem from drift
        disk = self._disk_sources()
        modified = [f for f in expected if f in disk and files_differ(expected[f], disk[f])]
        deleted = [f for f in expected if f not in disk]
        added = [f for f in disk if f not in expected]
        return DriftReport(sorted(modified), sorted(added), sorted(deleted))

    # -- dependency inference (scope-qualified, file-aware) ---------------
    def _defines(self) -> dict[str, set[tuple[str, str]]]:
        """``(file, top_level_name)`` pairs each node introduces.

        Keyed by file so a use of ``foo`` in ``app.py`` never links to a *different*
        node that happens to define ``foo`` in another file. Only top-level names are
        cross-node referenceable by bare name; method paths (``A.foo``) belong to the
        node that defined the enclosing class.
        """
        out: dict[str, set[tuple[str, str]]] = {}
        for nid, effs in self.bundles.items():
            names: set[tuple[str, str]] = set()
            for e in effs:
                if e.op is EffectOp.ADD_DEF and "." not in e.target:
                    names.add((e.file, e.target))
                elif e.op is EffectOp.SET_CONST:
                    names.add((e.file, e.target))
            out[nid] = names
        return out

    def _infer_dependencies(self, effects: list[Effect]) -> set[str]:
        """Nodes whose defined names are used by `effects` (so `effects` depend on them).

        A use matches a definition only when they agree on file — either a same-file bare
        reference, or a ``from <module> import name`` that resolves to the module's file.
        """
        used = _used_names(effects)
        deps: set[str] = set()
        for nid, names in self._defines().items():
            if names & used:
                deps.add(nid)
        return deps


def _used_names(effects: list[Effect]) -> set[tuple[str, str]]:
    """``(file, name)`` references made by `effects`, with imports resolved to a file."""
    used: set[tuple[str, str]] = set()
    for e in effects:
        if e.op is EffectOp.ADD_CALL:
            used.add((e.file, e.payload.get("callee", "")))
        if e.op in (EffectOp.REPLACE_DEF, EffectOp.REMOVE_DEF):
            # editing/removing a unit depends on whoever defines its top-level container
            used.add((e.file, e.target.split(".")[0]))
        src = e.payload.get("source", "")
        if src:
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    modfile = node.module.replace(".", "/") + ".py"
                    for alias in node.names:
                        if alias.name != "*":
                            used.add((modfile, alias.name))
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    used.add((e.file, node.func.id))
                elif isinstance(node, ast.Name):
                    used.add((e.file, node.id))
    return {(f, u) for (f, u) in used if u}
