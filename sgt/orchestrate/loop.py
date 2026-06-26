"""The orchestration spine — graph-only.

sgt does not author code: the coding agent (or a human) writes it. This module exposes the
operations that manipulate the *semantic graph* and reconstruct the tree from it — `plan`
(decompose an intent into reviewable PLANNED nodes), `merge`/`split` (reshape the plan),
`revert`/`restore` (plug features in and out, gated and re-materialized), and `reconcile`
(re-gate held quarantines). Recording the agent's edits lives in `orchestrate/sync.py`
(`checkpoint`/`sync`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sgt.agents import intent_dsl
from sgt.agents.plan_context import build_plan_context
from sgt.agents.planner import PlannerError, decompose
from sgt.agents.resolve import resolve
from sgt.config import get_client, get_model
from sgt.decisions.store import build_decisions, load_meta, save_meta
from sgt.lifecycle import algebra
from sgt.orchestrate.constraint import ConstraintGraph, SubTask
from sgt.orchestrate.quarantine import attempt_recommute
from sgt.project import Project
from sgt.store.gitbind import new_node_id
from sgt.store.graph import EdgeType, Node, NodeKind, NodeStatus


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
    def plan(self, intent: str, confirm: Callable[[str, list[str]], bool] | None = None) -> Report:
        """Decompose an intent into durable, reviewable PLANNED nodes — and stop.

        sgt authors no code here. Intent reaches the *same* ``SubTask`` schema two ways
        (``agents/intent_dsl``): a **canonical** statement (``ADD …``/``EXTEND …``) parses
        deterministically offline; **freeform** prose is rendered to a canonical program by the
        LLM, echoed via ``confirm`` for approval, then parsed — and if there is no key/confirm or
        the user declines, it falls back to the rich planner. Each resulting sub-task becomes a
        PLANNED node carrying its declared provides/needs and DEPENDS_ON edges; the coding agent
        later implements them via `checkpoint --fulfills`. An atomic intent persists one node.
        """
        if (blocked := self._guard("plan")):
            return blocked
        cb = self.project.materialize()
        try:
            graph, alts, echoes = self._decompose_intent(intent, cb, confirm)
        except PlannerError as ex:
            return Report("plan", False, message=f"could not plan: {ex}")

        key_to_id: dict[str, str] = {}
        nodes: list[Node] = []
        enrichment: dict[str, dict] = {}  # node id -> authored slug/context/consequence/alternatives
        for task in graph.tasks():
            nid = new_node_id()
            key_to_id[task.key] = nid
            nodes.append(Node(id=nid, kind=NodeKind.CAPABILITY, intent=task.intent,
                              status=NodeStatus.PLANNED, provides=list(task.provides),
                              needs=list(task.needs)))
            authored = {k: v for k, v in
                        (("slug", task.slug), ("context", task.context),
                         ("consequence", task.consequence)) if v}
            if task.key in alts:  # user-asserted Alternative from a REPLACE … BECAUSE …
                authored["alternatives"] = alts[task.key]
            if authored:
                enrichment[nid] = authored
        edges = [(key_to_id[k], key_to_id[d])
                 for k, ds in graph.dependencies().items() for d in ds
                 if k in key_to_id and d in key_to_id]
        dropped = self.project.add_plan(nodes, edges)
        # Persist authored rationale into the decisions sidecar (keyed by node id == a planned
        # decision's id), so every surface reads the enriched plan via sgt.api with no extra LLM
        # call. Merges, so a later distill/checkpoint can refine without clobbering.
        if enrichment:
            meta = load_meta(self.project.sgt_dir)
            for nid, authored in enrichment.items():
                meta.setdefault(nid, {}).update(authored)
            save_meta(self.project.sgt_dir, meta)
        self.project.commit(f"plan: {len(nodes)} node(s) — {intent[:50]}")
        msg = f"planned {len(nodes)} node(s)"
        if echoes:
            msg += " as: " + " | ".join(echoes)
        if dropped:
            msg += f"; dropped {len(dropped)} cyclic dependency edge(s)"
        return Report("plan", True, message=msg, landed=[n.id for n in nodes])

    def _decompose_intent(
        self, intent: str, cb: dict, confirm: Callable[[str, list[str]], bool] | None
    ) -> tuple[ConstraintGraph, dict[str, list], list[str]]:
        """Resolve an intent to a ``(ConstraintGraph, alternatives-by-key, canonical echoes)``.

        Canonical DSL is parsed offline; freeform is normalized to canonical (LLM) and confirmed,
        else it degrades to the rich planner. Both ends produce the same ``SubTask`` shape, so the
        caller's projection into PLANNED nodes is identical regardless of how the intent arrived.
        """
        if (parsed := intent_dsl.parse(intent)) is not None:
            g, alts = self._graph_from_parsed([parsed])
            return g, alts, [parsed.canonical]

        # Freeform: render to a canonical program (LLM) and confirm before planning from it.
        if confirm is not None and (client := self._client()) is not None:
            lines = intent_dsl.normalize(intent, cb, client=client, model=get_model())
            parsed_list = [p for ln in lines if (p := intent_dsl.parse(ln)) is not None]
            if parsed_list and confirm(intent, [p.canonical for p in parsed_list]):
                g, alts = self._graph_from_parsed(parsed_list)
                return g, alts, [p.canonical for p in parsed_list]

        # Fall back to the rich planner (also the no-key / non-interactive path — unchanged).
        try:
            context = build_plan_context(self.project, intent)
        except Exception:  # noqa: BLE001 — never let context-building block planning
            context = None
        graph = self._decompose(intent, cb, repo_path=self.repo_path, context=context)
        echoes = [intent_dsl.render(provides=t.provides, needs=t.needs, intent=t.intent)
                  for t in graph.tasks()]
        return graph, {}, echoes

    def _graph_from_parsed(self, parsed: list) -> tuple[ConstraintGraph, dict[str, list]]:
        """Build a one-task-per-statement ``ConstraintGraph`` from parsed canonical intents.

        A revise verb (``EXTEND``/``REPLACE``/``REMOVE``) whose target resolves to an existing lane
        adopts that lane's owned names as its ``provides``, so ``_fold_planned_revisions`` folds the
        planned node into the lane as a REVISE rather than spawning a phantom lane. An unresolved
        target degrades to a fresh capability. A ``REPLACE … BECAUSE …`` reason becomes a
        user-asserted ``Alternative`` returned keyed by sub-task.
        """
        g = ConstraintGraph()
        alts: dict[str, list] = {}
        for i, p in enumerate(parsed):
            key = f"dsl-{i + 1}"
            provides = list(p.provides)
            if p.is_revise and p.target and (names := self._lane_names(p.target)):
                provides = names
            g.add(SubTask(key=key, intent=p.intent, provides=provides, needs=list(p.needs),
                          slug=p.slug, context=p.context))
            if p.alternative:
                option, why = p.alternative
                alts[key] = [{"option": option, "why_rejected": why or "",
                              "source": "user", "confidence": "high"}]
        return g, alts

    def _lane_names(self, target: str) -> list[str]:
        """Top-level names a resolved lane owns (declared ``provides`` ∪ footprint def-names)."""
        r = resolve(self.project, target)
        if not r.ok or r.lane is None:
            return []
        names: set[str] = set()
        if r.node_id and self.project.graph.has(r.node_id):
            names.update(self.project.graph.get(r.node_id).provides)
        for d in build_decisions(self.project):
            if d.feature != r.lane:
                continue
            for k in d.footprint:
                nm = k.split("::", 1)[1] if "::" in k else ""
                if nm and not nm.startswith(("from ", "import ", "__")):
                    names.add(nm)
        return sorted(names)

    def _client(self):
        """An OpenAI client, or ``None`` offline — so the DSL/plan path degrades without a key."""
        try:
            return get_client(self.repo_path)
        except Exception:  # noqa: BLE001 — no key / no .env is a normal offline state
            return None

    # -- plan-editing (reshape PLANNED drafts; inert, gate nothing) ---------
    def _planned(self, action: str, ref: str) -> tuple[str | None, Report | None]:
        """Resolve ``ref`` to a PLANNED node id, or a refusal Report.

        Plan-editing only reshapes drafts: realized (ACTIVE) or held (QUARANTINED) work is
        re-decomposed in history-space (a later phase), so a non-PLANNED target is refused with a
        pointer rather than silently mangling the log.
        """
        r = resolve(self.project, ref)
        if not r.ok or r.node_id is None or not self.project.graph.has(r.node_id):
            return None, Report(action, False, message=f"no draft matches {ref!r}")
        node = self.project.graph.get(r.node_id)
        if node.status is not NodeStatus.PLANNED:
            return None, Report(action, False, node_id=r.node_id, message=(
                f"{r.node_id} is {node.status.value}, not a plan draft; "
                "revert it to take it out, or re-checkpoint to revise the realized code"))
        return r.node_id, None

    def _safe_dep(self, src: str, dst: str) -> None:
        """Add a ``src DEPENDS_ON dst`` edge if it is new, between live nodes, and acyclic."""
        g = self.project.graph
        if src == dst or not (g.has(src) and g.has(dst)) or dst in g.successors(src):
            return
        if not g.would_create_cycle(src, dst):
            g.add_edge(src, dst, EdgeType.DEPENDS_ON)

    def merge(self, refs: list[str]) -> Report:
        """Fold several PLANNED drafts into the first (the survivor) — one coherent draft.

        The survivor absorbs the others' ``provides``/``needs`` (deduped) and keeps their intents in
        its provenance; every edge incident to a merged draft is redirected onto the survivor
        (dependencies it pointed at, and dependents that pointed at it), so the plan's dependency
        structure is preserved. Authored alternatives union onto the survivor. Lossless to the tree:
        drafts are inert, so nothing materializes differently.
        """
        if len(refs) < 2:
            return Report("merge", False, message="merge needs at least two drafts")
        ids: list[str] = []
        for ref in refs:
            nid, refusal = self._planned("merge", ref)
            if refusal:
                return refusal
            if nid not in ids:
                ids.append(nid)
        if len(ids) < 2:
            return Report("merge", False, message="refs all resolve to one draft; nothing to merge")
        if (blocked := self._guard("merge")):
            return blocked

        g = self.project.graph
        survivor, merged = ids[0], ids[1:]
        merged_set = set(merged)
        sv = g.get(survivor)
        for m in merged:
            mn = g.get(m)
            for nm in mn.provides:
                if nm not in sv.provides:
                    sv.provides.append(nm)
            for nm in mn.needs:
                if nm not in sv.needs:
                    sv.needs.append(nm)
            sv.provenance.append(f"merged: {mn.intent}")
            for dep in g.successors(m):  # m's dependencies -> the survivor now depends on them
                if dep not in merged_set:
                    self._safe_dep(survivor, dep)
            for pred in g.predecessors(m):  # m's dependents -> now depend on the survivor
                if pred not in merged_set:
                    self._safe_dep(pred, survivor)

        meta = load_meta(self.project.sgt_dir)
        sv_meta = meta.setdefault(survivor, {})
        sv_alts = sv_meta.get("alternatives", [])
        for m in merged:
            sv_alts.extend(meta.get(m, {}).get("alternatives", []))
            meta.pop(m, None)
        if sv_alts:
            sv_meta["alternatives"] = sv_alts
        save_meta(self.project.sgt_dir, meta)

        self.project.remove_nodes(merged_set)
        self.project.commit(f"merge: {len(merged)} draft(s) into {survivor}")
        return Report("merge", True, node_id=survivor, landed=[survivor],
                      message=f"merged {len(merged)} draft(s) into {survivor}")

    def split(self, ref: str, intents: list[str]) -> Report:
        """Replace one PLANNED draft with several — each piece a new draft.

        Each piece string is parsed by the intent DSL: a canonical statement carries precise
        ``provides``/``needs``; freeform is kept as intent text (an island until refined). The
        original's edges are dropped with it; the pieces are relinked by their declared interface
        (the same needs↔provides rule ``plan`` uses), so a dependent reconnects to whichever piece
        provides the name it needs. Any of the original's ``provides`` no piece claims is reported
        as unassigned rather than silently lost.
        """
        if len(intents) < 2:
            return Report("split", False, message="split needs at least two pieces "
                          "(to replace one draft, revert it and plan again)")
        nid, refusal = self._planned("split", ref)
        if refusal:
            return refusal
        if (blocked := self._guard("split")):
            return blocked

        original_provides = list(self.project.graph.get(nid).provides)
        new_nodes: list[Node] = []
        authored: dict[str, dict] = {}
        covered: set[str] = set()
        for piece in intents:
            p = intent_dsl.parse(piece)
            new_id = new_node_id()
            if p is not None:
                provides, needs, slug, intent_text = list(p.provides), list(p.needs), p.slug, p.intent
                rationale = {k: v for k, v in (("slug", slug), ("context", p.context)) if v}
                if p.alternative:
                    opt, why = p.alternative
                    rationale["alternatives"] = [{"option": opt, "why_rejected": why or "",
                                                  "source": "user", "confidence": "high"}]
            else:
                provides, needs, intent_text = [], [], piece.strip()
                rationale = {"slug": " ".join(piece.split()[:5]).rstrip(".")}
            covered.update(provides)
            new_nodes.append(Node(id=new_id, kind=NodeKind.CAPABILITY, intent=intent_text,
                                  status=NodeStatus.PLANNED, provides=provides, needs=needs))
            if rationale:
                authored[new_id] = rationale

        self.project.remove_nodes({nid})
        self.project.add_plan(new_nodes, edges=[])
        self._relink_planned({n.id for n in new_nodes})
        if authored:
            meta = load_meta(self.project.sgt_dir)
            for new_id, rationale in authored.items():
                meta.setdefault(new_id, {}).update(rationale)
            save_meta(self.project.sgt_dir, meta)

        self.project.commit(f"split: {nid} -> {len(new_nodes)} draft(s)")
        unassigned = [nm for nm in original_provides if nm not in covered]
        msg = f"split {nid} into {len(new_nodes)} draft(s)"
        if unassigned:
            msg += f"; unassigned provides (no piece claims): {', '.join(unassigned)}"
        return Report("split", True, node_id=nid, landed=[n.id for n in new_nodes], message=msg)

    def _relink_planned(self, new_ids: set[str]) -> None:
        """Add needs↔provides DEPENDS_ON edges touching the new pieces (additive, acyclic).

        Scoped to edges with a new piece on one end so re-deriving a split doesn't churn unrelated
        plan edges; uses the same name-matching rule the planner projects edges with.
        """
        planned = [n for n in self.project.graph.nodes() if n.status is NodeStatus.PLANNED]
        provider: dict[str, str] = {}
        for n in planned:
            for nm in n.provides:
                provider.setdefault(nm, n.id)
        for n in planned:
            for nm in n.needs:
                owner = provider.get(nm)
                if owner and owner != n.id and (n.id in new_ids or owner in new_ids):
                    self._safe_dep(n.id, owner)

    # -- recompose verbs (edit the frontier) -------------------------------
    def _resolve_target(self, action: str, ref: str):
        """Resolve a ref to a lane (+ optional pinned decision), or a refusal Report."""
        r = resolve(self.project, ref)
        if r.kind == "missing" or r.lane is None:
            return None, Report(action, False, message=f"no feature matches {ref!r}")
        if r.kind == "ambiguous":
            return None, Report(action, False,
                                message=f"ambiguous ref {ref!r}: {', '.join(r.matches)}")
        return r, None

    def _dry_run(self, action: str, lane: str, decision_id: str | None) -> Report:
        """Preview a recompose: materialize the candidate frontier, write nothing."""
        decisions = build_decisions(self.project)
        before = self.project.materialize()
        sel, changed, err = (algebra.plan_revert(self.project, lane) if action == "revert"
                             else algebra.plan_restore(self.project, lane, decision_id))
        if err:
            return Report(action, False, message=err)
        after = self.project._compose(sel, decisions)
        delta = _codebase_delta(before, after)
        return Report(action, True, message=f"{action} --dry-run (nothing written) — {delta}",
                      landed=changed)

    def emit_payload(self, action: str, ref: str) -> dict:
        """Structured dry-run for a UI: the per-file before/after of a revert/restore.

        Computes the candidate frontier and materializes it without saving (no tree write, no
        commit), so a client can render a real diff and a refusal witness. Returns
        ``{ok, files: {path: {before, after}}}`` on success, or a refusal/resolution error.
        """
        r = resolve(self.project, ref)
        if r.kind == "missing" or r.lane is None:
            return {"ok": False, "error": f"could not resolve {ref!r} ({r.kind})", "matches": r.matches}
        if r.kind == "ambiguous":
            return {"ok": False, "error": f"ambiguous ref {ref!r}", "matches": r.matches}
        decisions = build_decisions(self.project)
        before = self.project.materialize()
        sel, changed, err = (algebra.plan_revert(self.project, r.lane) if action == "revert"
                             else algebra.plan_restore(self.project, r.lane, r.decision_id))
        if err:
            return {"ok": False, "action": action, "node_id": r.node_id, "message": err}
        after = self.project._compose(sel, decisions)
        files = {
            f: {"before": before.get(f, ""), "after": after.get(f, "")}
            for f in sorted(set(before) | set(after))
            if before.get(f, "") != after.get(f, "")
        }
        return {"ok": True, "action": action, "node_id": r.node_id, "lane": r.lane,
                "removed": changed, "files": files, "message": _codebase_delta(before, after)}

    def revert(self, ref: str, emit: bool = False) -> Report:
        """Plug a feature out of HEAD.

        For realized work this sets its lane (and live dependents) OFF in the frontier — lossless
        and reversible via ``restore``. For a *PLANNED* draft (no recorded effects) it drops the
        node outright: discarding a draft you decided against loses nothing, and there is no
        composition to take it out of. Both are "I don't want this"; the target's status picks which.
        """
        r, refusal = self._resolve_target("revert", ref)
        if refusal:
            return refusal
        node = (self.project.graph.get(r.node_id)
                if r.node_id and self.project.graph.has(r.node_id) else None)
        if node is not None and node.status is NodeStatus.PLANNED:
            if emit:
                return Report("revert", True, node_id=r.node_id, landed=[r.node_id],
                              message="revert --dry-run: would drop planned draft")
            if (blocked := self._guard("revert")):
                return blocked
            self.project.remove_nodes({r.node_id})
            self.project.commit(f"revert: drop planned {r.node_id}")
            return Report("revert", True, node_id=r.node_id, landed=[r.node_id],
                          message="dropped planned draft")
        if emit:
            return self._dry_run("revert", r.lane, None)
        if (blocked := self._guard("revert")):
            return blocked
        outcome = algebra.revert(self.project, r.lane)
        if not outcome.ok:
            return Report("revert", False, node_id=r.node_id, message=outcome.message)
        self.project.commit(f"revert: {r.lane} off ({', '.join(outcome.changed)})")
        return Report("revert", True, node_id=r.node_id,
                      message=f"reverted {len(outcome.changed)} lane(s)", landed=outcome.changed)

    def restore(self, ref: str, emit: bool = False) -> Report:
        """Plug a feature back into HEAD (or pin it to an earlier decision): set the lane on."""
        r, refusal = self._resolve_target("restore", ref)
        if refusal:
            return refusal
        if emit:
            return self._dry_run("restore", r.lane, r.decision_id)
        if (blocked := self._guard("restore")):
            return blocked
        outcome = algebra.restore(self.project, r.lane, r.decision_id)
        if not outcome.ok:
            return Report("restore", False, node_id=r.node_id, message=outcome.message)
        pin = f" @ {r.decision_id}" if r.decision_id else ""
        self.project.commit(f"restore: {r.lane}{pin} ({', '.join(outcome.changed)})")
        return Report("restore", True, node_id=r.node_id,
                      message=f"restored {len(outcome.changed)} lane(s)", landed=outcome.changed)

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
            r = resolve(self.project, ref)
            if r.node_id is None:
                return Report("reconcile", False, message=f"could not resolve {ref!r} ({r.kind})")
            if r.node_id not in pending:
                return Report("reconcile", False, node_id=r.node_id,
                              message=f"{r.node_id} is not a pending quarantine")
            pending = [r.node_id]
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

    # -- composition naming / comparison -----------------------------------
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
