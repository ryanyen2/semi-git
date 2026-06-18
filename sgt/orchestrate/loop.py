"""The orchestration spine.

A freeform prompt is classified, delegated to the coding backend for typed effects,
gated by the confluence engine, and either landed (new node, or appended to an
existing node's history) or held back as a quarantined conflict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sgt.adapter.base import AgentStatus, CodingAgentAdapter
from sgt.agents.classifier import classify
from sgt.agents.planner import PlannerError, decompose
from sgt.agents.resolve import resolve_ref
from sgt.engine.confluence import (
    INVARIANT_VIOLATED,
    can_land,
    max_coordination_free_batch,
    max_coordination_free_batch_explained,
)
from sgt.lifecycle.algebra import revert_feature, switch_feature
from sgt.orchestrate.constraint import ConstraintGraph
from sgt.orchestrate.dispatch import dispatch_layer
from sgt.orchestrate.quarantine import attempt_rewrite_to_commute
from sgt.project import Project
from sgt.store.gitbind import new_node_id
from sgt.store.graph import Node, NodeKind

# Lanes that represent new work eligible for fan-out (explore stays single-agent).
_FANOUT_LANES = {"capability", "concept", "infrastructure"}

_LANE_KIND = {
    "capability": NodeKind.CAPABILITY,
    "concept": NodeKind.CONCEPT,
    "infrastructure": NodeKind.INFRASTRUCTURE,
    "explore": NodeKind.EXPLORATION,
    "fix": NodeKind.FIX,
}


@dataclass
class Report:
    action: str
    ok: bool
    message: str = ""
    node_id: str | None = None
    lane: str = ""
    landed: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    quarantined: list[str] = field(default_factory=list)


def _desc(e) -> str:
    return f"{e.op.value} {e.target} ({e.file})"


class Orchestrator:
    def __init__(
        self,
        project: Project,
        agent: CodingAgentAdapter,
        repo_path: str = ".",
        confirm: Callable[[ConstraintGraph], bool] | None = None,
        decomposer: Callable[..., ConstraintGraph] = decompose,
        max_workers: int = 4,
        rewrite_attempts: int = 2,
    ):
        self.project = project
        self.agent = agent
        self.repo_path = repo_path
        # Checkpoint before fan-out (R29). Default auto-confirm for headless/tests; the
        # CLI injects an interactive prompt.
        self._confirm = confirm or (lambda graph: True)
        self._decompose = decomposer
        self.max_workers = max_workers
        self.rewrite_attempts = rewrite_attempts

    # -- freeform prompt ---------------------------------------------------
    def ingest(self, prompt: str) -> Report:
        cls = classify(prompt, self.project.graph, repo_path=self.repo_path)
        lane = cls.lane

        if lane == "question":
            return Report("answer", True, lane=lane,
                          message="Classified as a question — no versioned change.")

        # refine/fix attach to an existing node when the target resolves
        if lane in ("refine", "fix") and cls.target and self.project.graph.has(cls.target):
            return self._extend(prompt, cls.target, lane)

        # new capability-bearing work may fan out into a constraint graph
        if lane in _FANOUT_LANES:
            return self._fanout_or_add(prompt, lane, cls.name)

        return self._add(prompt, lane, cls.name)

    def _run_agent(self, prompt: str) -> tuple[object, Report | None]:
        result = self.agent.execute_task(prompt, self.project.materialize())
        if result.status is AgentStatus.FAILED:
            return result, Report("delegate", False, message=f"coding agent failed: {result.error}")
        return result, None

    def _add(self, prompt: str, lane: str, name: str) -> Report:
        result, err = self._run_agent(prompt)
        if err:
            return err
        cb = self.project.materialize()
        admitted, held = max_coordination_free_batch(cb, result.effects)
        if not admitted:
            return Report("land", False, lane=lane,
                          message="nothing landed; all effects conflicted",
                          held=[_desc(e) for e in held])
        nid = new_node_id()
        node = Node(id=nid, kind=_LANE_KIND.get(lane, NodeKind.CAPABILITY), intent=prompt)
        dep_ids = self._resolve_deps(result.depends_on)
        self.project.add_feature(node, admitted, dep_ids)
        self.project.commit(f"feat: {result.summary or prompt[:60]}", node_id=nid)
        return Report("land", True, node_id=nid, lane=lane,
                      message=result.summary,
                      landed=[_desc(e) for e in admitted],
                      held=[_desc(e) for e in held])

    # -- fan-out (R28-R31, R34) -------------------------------------------
    def _fanout_or_add(self, prompt: str, lane: str, name: str) -> Report:
        """Decompose into a constraint graph and fan out; fall back to single-agent."""
        cb = self.project.materialize()
        try:
            graph = self._decompose(prompt, cb, repo_path=self.repo_path)
        except PlannerError:
            return self._add(prompt, lane, name)  # degrade gracefully
        if len(graph) <= 1:
            return self._add(prompt, lane, name)  # atomic intent: inline, no checkpoint (R29)
        if not self._confirm(graph):
            return Report("fanout", False, lane=lane,
                          message="plan rejected at checkpoint; nothing dispatched")
        return self._run_plan(graph, lane)

    def _run_plan(self, graph: ConstraintGraph, lane: str) -> Report:
        kind = _LANE_KIND.get(lane, NodeKind.CAPABILITY)
        landed: list[str] = []
        held_records: list[tuple[object, list, str]] = []  # (task, effects, reason)
        failures: list[str] = []

        # Each layer dispatches concurrently; later layers see earlier layers' landed
        # code because we re-materialize per task before gating (R30).
        for layer in graph.layers():
            cb = self.project.materialize()
            for task, result in dispatch_layer(self.agent, layer, cb, max_workers=self.max_workers):
                if result.status is AgentStatus.FAILED:
                    failures.append(f"{task.key}: {result.error}")
                    continue
                cur = self.project.materialize()
                if can_land(cur, result.effects):
                    nid = new_node_id()
                    self.project.add_feature(Node(id=nid, kind=kind, intent=task.intent), result.effects)
                    landed.append(nid)
                elif result.effects:
                    _, held_expl = max_coordination_free_batch_explained(cur, result.effects)
                    reason = held_expl[0][1] if held_expl else INVARIANT_VIOLATED
                    held_records.append((task, list(result.effects), reason))

        quarantined = self._resolve_or_quarantine(held_records, landed, kind)

        self.project.commit(f"fanout: {len(landed)} landed, {len(quarantined)} quarantined")
        ok = bool(landed) or not held_records
        msg = f"fan-out: {len(landed)} node(s) landed"
        if quarantined:
            msg += f", {len(quarantined)} quarantined"
        if failures:
            msg += f", {len(failures)} backend failure(s)"
        return Report("fanout", ok, lane=lane, message=msg,
                      landed=landed, quarantined=quarantined)

    def _resolve_or_quarantine(self, held_records, landed: list[str], kind) -> list[str]:
        """For each held task: attempt bounded rewrite-to-commute, else quarantine (R33)."""
        quarantined: list[str] = []
        for task, effects, reason in held_records:
            ok, rewritten, new_reason = attempt_rewrite_to_commute(
                self.project, self.agent, task, self.rewrite_attempts
            )
            if ok and rewritten is not None:
                nid = new_node_id()
                self.project.add_feature(Node(id=nid, kind=kind, intent=task.intent), rewritten)
                landed.append(nid)
                continue
            # reconcile exhausted -> durable quarantine carrying the latest witness
            qid = new_node_id()
            self.project.quarantine(
                Node(id=qid, kind=kind, intent=task.intent), effects, new_reason or reason,
                [_desc(e) for e in effects], against_ids=list(landed),
            )
            quarantined.append(qid)
        return quarantined

    def _extend(self, prompt: str, target: str, lane: str) -> Report:
        result, err = self._run_agent(prompt)
        if err:
            return err
        cb = self.project.materialize()
        admitted, held = max_coordination_free_batch(cb, result.effects)
        if not admitted:
            return Report("extend", False, node_id=target, lane=lane,
                          message="nothing landed; all effects conflicted",
                          held=[_desc(e) for e in held])
        self.project.extend_feature(target, admitted)
        self.project.commit(f"{lane}: {result.summary or prompt[:60]}", node_id=target)
        return Report("extend", True, node_id=target, lane=lane,
                      message=result.summary,
                      landed=[_desc(e) for e in admitted],
                      held=[_desc(e) for e in held])

    def modify(self, ref: str, prompt: str) -> Report:
        r = resolve_ref(self.project.graph, ref)
        if r.kind == "ambiguous":
            return Report("modify", False, message=f"ambiguous ref {ref!r}: {', '.join(r.matches)}")
        if r.node_id is None:
            return Report("modify", False, message=f"no node matches {ref!r}")
        return self._extend(prompt, r.matches[0], "refine")

    def _resolve_deps(self, names: list[str]) -> list[str]:
        ids: list[str] = []
        for n in names:
            r = resolve_ref(self.project.graph, n)
            if r.node_id:
                ids.append(r.node_id)
        return ids

    # -- explicit graph verbs ----------------------------------------------
    def revert(self, ref: str) -> Report:
        r = resolve_ref(self.project.graph, ref)
        if r.kind == "missing":
            return Report("revert", False, message=f"no node matches {ref!r}")
        if r.kind == "ambiguous":
            return Report("revert", False, message=f"ambiguous ref {ref!r}: {', '.join(r.matches)}")
        nid = r.matches[0]
        outcome = revert_feature(self.project, nid)
        if not outcome.ok:
            return Report("revert", False, node_id=nid, message=outcome.message)
        self.project.commit(f"revert: remove {nid} ({', '.join(outcome.removed)})")
        return Report("revert", True, node_id=nid,
                      message=f"reverted {len(outcome.removed)} node(s)", landed=outcome.removed)

    def switch(self, ref: str, on: bool) -> Report:
        r = resolve_ref(self.project.graph, ref)
        if r.node_id is None:
            return Report("switch", False, message=f"could not resolve {ref!r} ({r.kind})")
        outcome = switch_feature(self.project, r.matches[0], on)
        if not outcome.ok:
            return Report("switch", False, node_id=r.matches[0], message=outcome.message)
        self.project.commit(f"switch: {r.matches[0]} {'on' if on else 'off'}")
        return Report("switch", True, node_id=r.matches[0],
                      message=f"switched {'on' if on else 'off'}")
