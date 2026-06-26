"""Drive one project through a scripted sequence of lifecycle moves, capturing diagnostics.

A *move* is plan / revert / suspend / restore. `plan` runs sgt's LLM decomposition, then the
coding agent (coding_agent.py) implements each new planned node in dependency order and the driver
checkpoints it. Every move snapshots the decision graph (digest + lane render) and, for plans, the
planner's subtasks, the planner context size, and per-node planner-vs-reality name drift.

Reports land under <runs>/<project>/: one markdown a researcher reads top-to-bottom, plus JSON.
sgt authors no code here — the coding agent (an external LLM) does, exactly as designed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sgt.agents.plan_context import build_plan_context
from sgt.api import decision_graph_view
from sgt.orchestrate.loop import Orchestrator
from sgt.orchestrate.sync import run_sync
from sgt.project import Project
from sgt.store.graph import NodeStatus

from .coding_agent import implement
from .digest import graph_digest, render_lanes

_YES = lambda *_a, **_k: True  # noqa: E731


def _topo_planned(node_ids: list[str], graph) -> list[str]:
    """Dependencies-first order over the planned subgraph (so a node's `needs` exist when built)."""
    wanted = set(node_ids)
    order, seen = [], set()

    def visit(n: str) -> None:
        if n in seen:
            return
        seen.add(n)
        for dep in sorted(graph.successors(n)):
            if dep in wanted:
                visit(dep)
        order.append(n)

    for n in sorted(wanted):
        visit(n)
    return order


class Driver:
    def __init__(self, workdir: str, name: str, runs_dir: str):
        self.wd = workdir
        self.name = name
        self.run_dir = Path(runs_dir) / name
        self.run_dir.mkdir(parents=True, exist_ok=True)
        Path(workdir).mkdir(parents=True, exist_ok=True)
        Project.init(workdir)
        self.seen: set[str] = set()
        self.records: list[dict] = []
        self.cost = {"plan_calls": 0, "code_calls": 0, "planner_ctx_chars": []}

    # -- moves ---------------------------------------------------------------
    def plan(self, intent: str, expect: str = "") -> None:
        proj = Project.open(self.wd)
        # measure the REAL (graph-driven, compacted) context the planner will receive
        try:
            ctx_chars = len(build_plan_context(proj, intent))
        except Exception:  # noqa: BLE001
            ctx_chars = -1
        self.cost["planner_ctx_chars"].append(ctx_chars)
        self.cost["plan_calls"] += 1

        rep = Orchestrator(proj, repo_path=self.wd).plan(intent)
        proj = Project.open(self.wd)
        new_planned = [n for n in proj.graph.nodes()
                       if n.status is NodeStatus.PLANNED and n.id not in self.seen]

        subtasks = [{"id": n.id[:8], "intent": n.intent, "provides": list(n.provides),
                     "needs": list(n.needs), "deps": [d[:8] for d in proj.graph.successors(n.id)]}
                    for n in new_planned]

        impls = []
        for nid in _topo_planned([n.id for n in new_planned], proj.graph):
            node = proj.graph.get(nid)
            try:
                out = implement(self.wd, proj.materialize(), node.intent,
                                list(node.provides), list(node.needs))
                self.cost["code_calls"] += 1
            except Exception as ex:  # noqa: BLE001
                impls.append({"id": nid[:8], "error": f"{type(ex).__name__}: {ex}"})
                continue
            for path, content in out["files"].items():
                p = Path(self.wd) / path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            sync = run_sync(proj, repo_path=self.wd, confirm=_YES, fulfills=nid, intent=node.intent)
            proj = Project.open(self.wd)
            drift_landed = proj.graph.has(nid) and proj.graph.get(nid).status is NodeStatus.ACTIVE
            provided = set(node.provides)
            defined = set(out.get("defined", []))
            impls.append({
                "id": nid[:8], "provides": sorted(provided), "defined": sorted(defined),
                "name_drift": sorted(provided - defined) or None,  # declared-but-not-defined
                "landed": drift_landed, "sync": sync.message[:80],
            })
        self.seen |= {n.id for n in new_planned}
        self._snapshot("plan", intent, expect,
                       extra={"subtasks": subtasks, "impls": impls, "planner_ctx_chars": ctx_chars})

    def _resolve(self, selector: str) -> str | None:
        """Pick a node id (lane) whose slug/intent/provides contains the selector (case-insensitive)."""
        proj = Project.open(self.wd)
        view = decision_graph_view(proj)
        sel = selector.lower()
        for d in view["decisions"]:
            it = d.get("intent", {})
            hay = " ".join([it.get("slug") or "", it.get("decision") or "", d["feature"]]).lower()
            if sel in hay:
                # the lane is the feature; ops take a node id — use the feature lane id
                return d["feature"]
        return None

    def _lifecycle(self, kind: str, selector: str, expect: str = "") -> None:
        nid = self._resolve(selector)
        if nid is None:
            self._snapshot(kind, selector, expect, extra={"error": f"no decision matches {selector!r}"})
            return
        orch = Orchestrator(Project.open(self.wd), repo_path=self.wd)
        # One frontier, two recompose verbs: revert plugs a lane out (lossless), restore plugs it
        # back. "suspend" is just a reversible revert now, so it maps to the same verb.
        if kind in ("revert", "suspend"):
            rep = orch.revert(nid)
        elif kind == "restore":
            rep = orch.restore(nid)
        else:
            rep = None
        self._snapshot(kind, selector, expect,
                       extra={"ref": nid[:8], "ok": getattr(rep, "ok", None),
                              "msg": getattr(rep, "message", "")[:120]})

    def revert(self, selector, expect=""): self._lifecycle("revert", selector, expect)
    def suspend(self, selector, expect=""): self._lifecycle("suspend", selector, expect)
    def restore(self, selector, expect=""): self._lifecycle("restore", selector, expect)

    # -- snapshot + report ---------------------------------------------------
    def _snapshot(self, move: str, arg: str, expect: str, extra: dict) -> None:
        view = decision_graph_view(Project.open(self.wd))
        rec = {
            "step": len(self.records) + 1, "move": move, "arg": arg, "expect": expect,
            "digest": graph_digest(view), "lanes": render_lanes(view), **extra,
        }
        self.records.append(rec)

    def finish(self) -> dict:
        (self.run_dir / "records.json").write_text(json.dumps(self.records, indent=2), encoding="utf-8")
        (self.run_dir / "report.md").write_text(self._markdown(), encoding="utf-8")
        final = decision_graph_view(Project.open(self.wd))
        (self.run_dir / "final_graph.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
        return {"name": self.name, "steps": len(self.records), "cost": self.cost,
                "final_digest": graph_digest(final)}

    def _markdown(self) -> str:
        out = [f"# Stress run: {self.name}\n",
               f"plan calls: {self.cost['plan_calls']} · code calls: {self.cost['code_calls']} · "
               f"planner ctx chars: {self.cost['planner_ctx_chars']}\n"]
        for r in self.records:
            out.append(f"\n## Step {r['step']}: {r['move']} — {r['arg'][:80]}")
            if r.get("expect"):
                out.append(f"**Expected:** {r['expect']}")
            d = r["digest"]
            out.append(f"\n`{d['n_decisions']} decisions, {d['n_lanes']} lanes, "
                       f"status={d['by_status']}, edges={d['edge_kinds']}, "
                       f"orphans={d['orphans']}, max_depth={d['max_depth']}`")
            if "subtasks" in r:
                out.append(f"\nplanner ctx: {r['planner_ctx_chars']} chars · "
                           f"{len(r['subtasks'])} subtask(s):")
                for s in r["subtasks"]:
                    out.append(f"  - `{s['id']}` provides={s['provides']} needs={s['needs']} "
                               f"deps={s['deps']} — {s['intent'][:70]}")
                for im in r["impls"]:
                    if im.get("error"):
                        out.append(f"  - impl `{im['id']}` ERROR: {im['error']}")
                    else:
                        drift = f" ⚠name-drift {im['name_drift']}" if im.get("name_drift") else ""
                        out.append(f"  - impl `{im['id']}` provides={im['provides']} "
                                   f"defined={im['defined']} landed={im['landed']}{drift}")
            for k in ("ref", "ok", "msg", "error"):
                if k in r:
                    out.append(f"\n{k}: {r[k]}")
            out.append("\n```\n" + r["lanes"] + "\n```")
        return "\n".join(out)
