"""The orchestration spine — graph-only.

sgt does not author code: the coding agent (or a human) writes it. This module exposes the
operations that manipulate the *semantic graph* and reconstruct the tree from it — `plan`
(decompose an intent into reviewable PLANNED nodes), `revert`/`switch` (plug features in and
out, gated and re-materialized), and `reconcile` (re-gate held quarantines). Recording the
agent's edits lives in `orchestrate/sync.py` (`checkpoint`/`sync`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sgt.agents.planner import PlannerError, decompose
from sgt.agents.resolve import resolve_ref
from sgt.decisions.store import load_meta, save_meta
from sgt.effects.model import EffectError
from sgt.lifecycle.algebra import revert_feature, switch_feature
from sgt.orchestrate.quarantine import attempt_recommute
from sgt.project import Project
from sgt.store.gitbind import new_node_id
from sgt.store.graph import Node, NodeKind, NodeStatus


@dataclass
class Report:
    action: str
    ok: bool
    message: str = ""
    node_id: str | None = None
    landed: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _codebase_delta(before: dict[str, str], after: dict[str, str]) -> str:
    """Human summary of how a graph op would change the materialized tree (for --emit)."""
    parts: list[str] = []
    for f in sorted(set(before) | set(after)):
        if f in before and f not in after:
            parts.append(f"{f}: removed")
        elif f in after and f not in before:
            parts.append(f"{f}: added")
        elif before[f] != after[f]:
            parts.append(f"{f}: {len(before[f].splitlines())} -> {len(after[f].splitlines())} lines")
    return "; ".join(parts) or "no file changes"


class Orchestrator:
    def __init__(
        self,
        project: Project,
        repo_path: str = ".",
        decomposer: Callable[..., object] = decompose,
        force: bool = False,
    ):
        self.project = project
        self.repo_path = repo_path
        self._decompose = decomposer
        self.force = force

    def _guard(self, action: str) -> Report | None:
        """Block a mutating op when the tree drifted, so we never clobber (R5)."""
        if self.force:
            return None
        drift = self.project.check_drift()
        if drift.any:
            return Report(action, False, message=(
                f"out-of-band changes detected ({drift.summary()}); "
                "run `sgt checkpoint` to record them, or pass --force to overwrite"))
        return None

    # -- plan (persist a reviewable decomposition; no code authored) -------
    def plan(self, intent: str) -> Report:
        """Decompose an intent into durable, reviewable PLANNED nodes — and stop.

        sgt authors no code here: the planner (graph-level LLM reasoning) proposes a
        decomposition, each sub-task becomes a PLANNED node carrying its declared
        provides/needs and DEPENDS_ON edges, and the coding agent later implements them
        and lands them via `checkpoint --fulfills`. An atomic intent persists one node.
        """
        if (blocked := self._guard("plan")):
            return blocked
        cb = self.project.materialize()
        try:
            graph = self._decompose(intent, cb, repo_path=self.repo_path)
        except PlannerError as ex:
            return Report("plan", False, message=f"could not plan: {ex}")

        key_to_id: dict[str, str] = {}
        nodes: list[Node] = []
        enrichment: dict[str, dict] = {}  # node id -> authored slug/context/consequence
        for task in graph.tasks():
            nid = new_node_id()
            key_to_id[task.key] = nid
            nodes.append(Node(id=nid, kind=NodeKind.CAPABILITY, intent=task.intent,
                              status=NodeStatus.PLANNED, provides=list(task.provides),
                              needs=list(task.needs)))
            authored = {k: v for k, v in
                        (("slug", task.slug), ("context", task.context),
                         ("consequence", task.consequence)) if v}
            if authored:
                enrichment[nid] = authored
        edges = [(key_to_id[k], key_to_id[d])
                 for k, ds in graph.dependencies().items() for d in ds
                 if k in key_to_id and d in key_to_id]
        dropped = self.project.add_plan(nodes, edges)
        # Persist the planner's rationale into the decisions sidecar (keyed by node id == a
        # planned decision's id), so every surface reads the enriched plan via sgt.api with no
        # extra LLM call. Merges, so a later distill/checkpoint can refine without clobbering.
        if enrichment:
            meta = load_meta(self.project.sgt_dir)
            for nid, authored in enrichment.items():
                meta.setdefault(nid, {}).update(authored)
            save_meta(self.project.sgt_dir, meta)
        self.project.commit(f"plan: {len(nodes)} node(s) — {intent[:50]}")
        msg = f"planned {len(nodes)} node(s)"
        if dropped:
            msg += f"; dropped {len(dropped)} cyclic dependency edge(s)"
        return Report("plan", True, message=msg, landed=[n.id for n in nodes])

    # -- dry-run preview (the --emit escape hatch) -------------------------
    def _emit(self, action: str, nid: str, *, on: bool = False) -> Report:
        """Preview a revert/switch on a throwaway copy — never writes the tree or commits.

        The op runs against a fresh ``Project.open`` sandbox (discarded on return), so the
        agent can see the semantic delta + any refusal witness and apply the change itself.
        """
        sandbox = Project.open(self.repo_path)
        before = sandbox.materialize()
        if action == "revert":
            outcome = revert_feature(sandbox, nid)
        else:
            outcome = switch_feature(sandbox, nid, on)
        if not outcome.ok:
            return Report(action, False, node_id=nid,
                          message=f"{action} --emit: would be refused — {outcome.message}")
        delta = _codebase_delta(before, sandbox.materialize())
        return Report(action, True, node_id=nid, landed=outcome.removed,
                      message=f"{action} --emit (dry-run, nothing written) — {delta}")

    def emit_payload(self, action: str, ref: str, on: bool = False) -> dict:
        """Structured dry-run for a UI: the per-file before/after of a revert/switch.

        Runs the op on a throwaway ``Project.open`` sandbox (never writes the tree or commits),
        so a client can render a real diff (revision navigation) and a refusal witness without
        mutating anything. Returns ``{ok, files: {path: {before, after}}}`` on success, or a
        refusal/resolution error otherwise.
        """
        r = resolve_ref(self.project.graph, ref)
        if r.node_id is None:
            return {"ok": False, "error": f"could not resolve {ref!r} ({r.kind})", "matches": r.matches}
        nid = r.node_id
        sandbox = Project.open(self.repo_path)
        before = sandbox.materialize()
        outcome = (revert_feature(sandbox, nid) if action == "revert"
                   else switch_feature(sandbox, nid, on))
        if not outcome.ok:
            return {"ok": False, "action": action, "node_id": nid,
                    "message": outcome.message}
        after = sandbox.materialize()
        files = {
            f: {"before": before.get(f, ""), "after": after.get(f, "")}
            for f in sorted(set(before) | set(after))
            if before.get(f, "") != after.get(f, "")
        }
        return {"ok": True, "action": action, "node_id": nid,
                "removed": list(getattr(outcome, "removed", [])),
                "files": files, "message": _codebase_delta(before, after)}

    # -- explicit graph verbs ----------------------------------------------
    def revert(self, ref: str, emit: bool = False) -> Report:
        if not emit and (blocked := self._guard("revert")):
            return blocked
        r = resolve_ref(self.project.graph, ref)
        if r.kind == "missing":
            return Report("revert", False, message=f"no node matches {ref!r}")
        if r.kind == "ambiguous":
            return Report("revert", False, message=f"ambiguous ref {ref!r}: {', '.join(r.matches)}")
        nid = r.matches[0]
        if emit:
            return self._emit("revert", nid)
        outcome = revert_feature(self.project, nid)
        if not outcome.ok:
            return Report("revert", False, node_id=nid, message=outcome.message)
        self.project.commit(f"revert: remove {nid} ({', '.join(outcome.removed)})")
        return Report("revert", True, node_id=nid,
                      message=f"reverted {len(outcome.removed)} node(s)", landed=outcome.removed)

    def reconcile(self, ref: str | None = None) -> Report:
        """Retry the agent-free re-gate on pending quarantine(s) on demand (R33).

        With no ref, reconciles every QUARANTINED node; with a ref, just that one. A node
        whose held effects now commute (e.g. its rival was reverted/suspended) is resolved
        (flipped ACTIVE, effects replay last); the rest stay pending with a refreshed witness.

        Reconcile re-gates the *stored* held effects against current active state — it is the
        recovery path for when a **rival changed** (reverted/suspended) and the held code now
        fits as-is. It is *not* the path for when the **agent revised the code**: that is a
        retry via ``checkpoint --fulfills <held-node>`` (which re-distills the new disk state).
        The drift guard enforces this — reconcile refuses to run over un-checkpointed edits, so
        it never replays stale held effects on top of fresh, unrecorded changes.
        """
        if (blocked := self._guard("reconcile")):
            return blocked
        pending = [n.id for n in self.project.graph.nodes() if n.status is NodeStatus.QUARANTINED]
        if ref:
            r = resolve_ref(self.project.graph, ref)
            if r.node_id is None:
                return Report("reconcile", False, message=f"could not resolve {ref!r} ({r.kind})")
            if r.matches[0] not in pending:
                return Report("reconcile", False, node_id=r.matches[0],
                              message=f"{r.matches[0]} is not a pending quarantine")
            pending = [r.matches[0]]
        if not pending:
            return Report("reconcile", True, message="no pending quarantines")

        resolved: list[str] = []
        still: list[str] = []
        for nid in pending:
            ok, effects, reason = attempt_recommute(self.project, nid)
            if ok and effects is not None:
                self.project.resolve_quarantine(nid, effects)
                resolved.append(nid)
            else:
                self.project.witnesses.get(nid, {})["reason"] = reason
                still.append(nid)

        if resolved:
            self.project.commit(f"reconcile: resolved {', '.join(resolved)}")
        else:
            self.project.save()
        return Report("reconcile", True, message=f"resolved {len(resolved)}, still pending {len(still)}",
                      landed=resolved, quarantined=still)

    def switch(self, ref: str, on: bool, emit: bool = False) -> Report:
        if not emit and (blocked := self._guard("switch")):
            return blocked
        r = resolve_ref(self.project.graph, ref)
        if r.node_id is None:
            return Report("switch", False, message=f"could not resolve {ref!r} ({r.kind})")
        if emit:
            return self._emit("switch", r.matches[0], on=on)
        outcome = switch_feature(self.project, r.matches[0], on)
        if not outcome.ok:
            return Report("switch", False, node_id=r.matches[0], message=outcome.message)
        self.project.commit(f"switch: {r.matches[0]} {'on' if on else 'off'}")
        return Report("switch", True, node_id=r.matches[0],
                      message=f"switched {'on' if on else 'off'}")

    # -- decision-frontier verbs -------------------------------------------
    def compose(self, feature: str, decision_id: str) -> Report:
        """Pin a feature lane to a chosen decision and re-materialize the composition.

        This is compose-feature-versions: hold feature-A@v3 alongside feature-B@latest. The
        selection persists in ``.sgt/frontier.json`` and ``materialize`` then composes from it,
        so the working tree and drift stay consistent. Guarded so it never clobbers un-checkpointed
        edits.
        """
        from sgt.decisions.store import build_decisions, load_frontier, save_frontier

        if (blocked := self._guard("compose")):
            return blocked
        decisions = build_decisions(self.project)
        by_id = {d.id: d for d in decisions}
        d = by_id.get(decision_id)
        if d is None:
            return Report("compose", False, message=f"no decision {decision_id!r}")
        if d.feature != feature:
            return Report("compose", False,
                          message=f"{decision_id} is on lane {d.feature!r}, not {feature!r}")
        frontier = load_frontier(self.project, decisions)
        frontier.selection[feature] = decision_id
        save_frontier(self.project, frontier)
        try:
            self.project.write_working_tree()
        except EffectError as ex:
            return Report("compose", False, message=f"composition does not materialize: {ex}")
        return Report("compose", True, message=f"pinned {feature} -> {decision_id}")

    def tag(self, name: str) -> Report:
        """Name the current frontier — a composition manifest you can return to or diff against."""
        from sgt.decisions.store import build_decisions, load_frontier, load_tags, save_tags

        frontier = load_frontier(self.project, build_decisions(self.project))
        tags = load_tags(self.project.sgt_dir)
        tags[name] = dict(frontier.selection)
        save_tags(self.project.sgt_dir, tags)
        return Report("tag", True, message=f"tagged {name} = {len(frontier.selection)} lane(s)")

    def _selection_for(self, ref: str) -> dict[str, str] | None:
        """Resolve a frontier ref: ``HEAD`` (current) or a tag name -> selection dict."""
        from sgt.decisions.store import build_decisions, load_frontier, load_tags

        if ref in ("HEAD", "head", ""):
            return load_frontier(self.project, build_decisions(self.project)).selection
        return load_tags(self.project.sgt_dir).get(ref)

    def diff(self, ref_a: str, ref_b: str) -> dict:
        """Decision-level delta between two frontier refs (tag names or ``HEAD``)."""
        from sgt.api import frontier_diff

        a, b = self._selection_for(ref_a), self._selection_for(ref_b)
        if a is None or b is None:
            missing = ref_a if a is None else ref_b
            return {"error": f"unknown frontier ref {missing!r}"}
        return frontier_diff(a, b)

    def blast_radius(self, decision_id: str) -> dict:
        """Read-only: the lanes that transitively ``builds-on`` the target decision's lane.

        Reverting a decision disturbs its whole lane, so the cone is computed at lane
        granularity (R11) and returned as the in-force decision of each dependent lane.
        Derived from the entity graph via ``decision_graph_view`` — nothing is mutated.
        """
        from sgt.api import decision_graph_view

        view = decision_graph_view(self.project)
        feature_of = {d["id"]: d["feature"] for d in view["decisions"]}
        if decision_id not in feature_of:
            return {"error": f"no decision {decision_id!r}"}
        target = feature_of[decision_id]
        # lane S builds-on lane D  (edge src is on S, dst on D)
        dependents: dict[str, set[str]] = {}
        for e in view["edges"]:
            if e["type"] == "builds-on":
                s, d = feature_of.get(e["src"]), feature_of.get(e["dst"])
                if s and d and s != d:
                    dependents.setdefault(d, set()).add(s)
        seen: list[str] = []
        stack = [target]
        while stack:
            for lane in sorted(dependents.get(stack.pop(), ())):
                if lane not in seen:
                    seen.append(lane)
                    stack.append(lane)
        frontier = view["frontier"]
        return {
            "decision": decision_id,
            "lane": target,
            "blast_radius": [frontier[lane] for lane in seen if lane in frontier],
        }
