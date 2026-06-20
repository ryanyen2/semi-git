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
        for task in graph.tasks():
            nid = new_node_id()
            key_to_id[task.key] = nid
            nodes.append(Node(id=nid, kind=NodeKind.CAPABILITY, intent=task.intent,
                              status=NodeStatus.PLANNED, provides=list(task.provides),
                              needs=list(task.needs)))
        edges = [(key_to_id[k], key_to_id[d])
                 for k, ds in graph.dependencies().items() for d in ds
                 if k in key_to_id and d in key_to_id]
        dropped = self.project.add_plan(nodes, edges)
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
