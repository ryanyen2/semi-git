"""`sgt sync`: reconcile out-of-band changes back into the semantic graph.

The bidirectional half of the tree<->graph relationship. Instead of `write_working_tree`
clobbering a hand edit, `sync` distills the disk diff into typed effects (deterministic),
clusters + labels them into features (LLM, with a deterministic fallback), shows the plan
for confirmation, and lands each cluster through the *same* confluence gate as any other
mutation. Once landed, re-materialization reproduces the edit — so the clobber cannot happen.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sgt.agents.distill import Cluster, DistillError, fallback_cluster, llm_cluster
from sgt.effects.diff import distill_codebase
from sgt.effects.model import STMT_OPS
from sgt.effects.stmt_distill import promote_body_rewrites
from sgt.engine.confluence import (
    INVARIANT_VIOLATED,
    can_land,
    max_coordination_free_batch_explained,
)
from sgt.project import Project
from sgt.store.gitbind import new_node_id
from sgt.store.graph import Node, NodeKind, NodeStatus

_KIND = {
    "capability": NodeKind.CAPABILITY,
    "concept": NodeKind.CONCEPT,
    "infrastructure": NodeKind.INFRASTRUCTURE,
    "fix": NodeKind.FIX,
}


@dataclass
class SyncReport:
    ok: bool
    message: str = ""
    landed: list[str] = field(default_factory=list)
    extended: list[str] = field(default_factory=list)
    fulfilled: list[str] = field(default_factory=list)   # PLANNED nodes flipped ACTIVE
    quarantined: list[str] = field(default_factory=list)
    swept: list[str] = field(default_factory=list)       # superseded quarantines GC'd this run
    notes: list[str] = field(default_factory=list)


def _desc(e) -> str:
    return f"{e.op.value} {e.target} ({e.file})"


def superseded_quarantines(project, touched: set[tuple[str, str]]) -> list[str]:
    """QUARANTINED nodes made redundant by names (re-)defined in the current run — the zombies.

    A freshly-held node has *passing* preconditions (it's held only because the combined batch
    breaks an invariant). It becomes a *zombie* when the agent re-implements the same work: the
    names it would add are now provided by what just landed, so every held effect's precondition
    fails (``add_def X`` where X is already defined, ``add_assign Y`` where Y is bound, …).

    The ``touched`` guard — `(file, target)` pairs defined by *this run* — is what keeps the sweep
    surgical: it removes only holds the current checkpoint superseded, never a legitimately
    recorded merge/uniqueness conflict against a pre-existing active rival (which stays recoverable
    via suspend + ``reconcile``). Without it, an unrelated checkpoint would wrongly GC such a hold.
    """
    from sgt.effects.model import precondition_holds

    cb = project.materialize()
    dead: list[str] = []
    for n in project.graph.nodes():
        if n.status is not NodeStatus.QUARANTINED:
            continue
        held = project.bundles.get(n.id, [])
        if not held:
            continue
        if not any((e.file, e.target) in touched for e in held):
            continue  # nothing this run touched — not an accretion zombie, leave it
        if all(not precondition_holds(cb.get(e.file, ""), e) for e in held):
            dead.append(n.id)
    return dead


def _sweep_superseded(project, report, touched: set[tuple[str, str]]) -> None:
    """Remove accretion (zombie) quarantines superseded by this run and record them."""
    dead = superseded_quarantines(project, touched)
    if dead:
        project.remove_nodes(set(dead))  # graph + log tombstone + witness + order, all handled
        report.swept.extend(dead)


def _default_clusterer(repo_path: str) -> Callable[[list, Project], list[Cluster]]:
    """LLM clustering, degrading to the deterministic grouping if the backend is unavailable.

    The LLM only *labels/groups* the distilled effects — it is an enhancement, never a
    requirement. A missing API key (``RuntimeError`` from ``get_client``) is just one way the
    backend can be unavailable, so it degrades to deterministic grouping exactly like a network
    failure rather than crashing: a bare ``checkpoint`` works fully offline.
    """
    def cluster(effects, project):
        try:
            return llm_cluster(effects, project, repo_path=repo_path)
        except (DistillError, RuntimeError):
            return fallback_cluster(effects, project)
    return cluster


def _land(project, effects, kind, intent, *, extend, anchors, report) -> None:
    """Gate ``effects`` and either extend ``extend``, add a new node, and/or quarantine the held.

    ``anchors`` seed DEPENDS_ON edges for a new/quarantined node (so a statement-edit node
    points at the function's owner for revert-closure).
    """
    if not effects:
        return
    cb = project.materialize()
    admitted, held = max_coordination_free_batch_explained(
        cb, effects, base_effects=project.active_effects())
    anchor = list(anchors)
    # ``extend`` is only ever an ACTIVE node here: run_sync restricts cluster targets to
    # ACTIVE, because fulfilling a PLANNED node is explicit and atomic (``--fulfills`` ->
    # _fulfill_drift), never a side effect of ad-hoc clustering.
    if extend and project.graph.has(extend):
        if admitted:
            project.extend_feature(extend, admitted)
            report.extended.append(extend)
        anchor = [extend]
    elif admitted:
        nid = new_node_id()
        project.add_feature(Node(id=nid, kind=kind, intent=intent), admitted, list(anchors))
        report.landed.append(nid)
        anchor = [nid]
    if held:
        qid = new_node_id()
        project.quarantine(
            Node(id=qid, kind=kind, intent=intent),
            [e for e, _ in held], held[0][1],
            [_desc(e) for e, _ in held], against_ids=anchor,
        )
        report.quarantined.append(qid)


def _effect_key(e) -> tuple:
    return (e.op, e.target, e.file)


def _strip_held_elsewhere(project, effects, *, keep: str) -> list:
    """Drop effects that already belong to *another* quarantined node's held bundle.

    Held effects are excluded from the active codebase, so they reappear as on-disk drift — but
    that code is the held node's, not whatever is being fulfilled now. Stripping them keeps each
    fulfill scoped to its own node and makes out-of-order recovery sound (a provider fulfill does
    not absorb a dependent's still-held code).
    """
    held_elsewhere = {
        _effect_key(e)
        for n in project.graph.nodes()
        if n.status is NodeStatus.QUARANTINED and n.id != keep
        for e in project.bundles.get(n.id, [])
    }
    if not held_elsewhere:
        return effects
    return [e for e in effects if _effect_key(e) not in held_elsewhere]


def _hold(project, node_id, effects, intent, cb, base, report) -> None:
    """Hold the *whole* drift on ``node_id`` itself (per-node atomicity) with a witness."""
    _, held = max_coordination_free_batch_explained(cb, effects, base_effects=base)
    reason = held[0][1] if held else INVARIANT_VIOLATED
    project.quarantine_existing(node_id, effects, reason, [_desc(e) for e in effects],
                                against_ids=[], intent=intent)
    report.quarantined.append(node_id)
    report.message = f"held {node_id} — does not commute yet ({reason})"


def _fulfill_drift(project, node_id: str, effects, intent: str | None, notes: list[str]) -> SyncReport:
    """Land the whole distilled drift under one node, atomically, routing on the node's status.

    * **PLANNED** — the normal case: fulfill (flip ACTIVE) if everything commutes, else hold
      the node itself QUARANTINED.
    * **QUARANTINED** — a *retry*: the agent revised the code after a previous hold, so replace
      the held bundle with the fresh distilled effects and re-gate (resolve to ACTIVE if it now
      commutes, else stay held with a refreshed witness). This — not ``reconcile`` — is how a
      held node recovers when the *code* changed (``reconcile`` is for when a *rival* changed).
    * **ACTIVE** — extend the feature in place; any non-commuting remainder is held separately.

    (A feature being out of force is a frontier state, not a node status, so it does not route
    here — checkpointing records the edit regardless of whether its lane is currently composed.)
    """
    report = SyncReport(True, notes=notes)
    status = project.graph.get(node_id).status
    cb = project.materialize()
    base = project.active_effects()

    if status is NodeStatus.PLANNED:
        if can_land(cb, effects, base_effects=base):
            project.fulfill(node_id, effects, intent=intent)
            report.fulfilled.append(node_id)
            report.message = f"fulfilled {node_id} ({len(effects)} effect(s))"
        else:
            _hold(project, node_id, effects, intent, cb, base, report)
    elif status is NodeStatus.QUARANTINED:
        if can_land(cb, effects, base_effects=base):
            project.resolve_quarantine(node_id, effects)
            if intent and intent != (node := project.graph.get(node_id)).intent:
                node.provenance.append(f"held: {node.intent}")
                node.intent = intent
            report.fulfilled.append(node_id)
            report.message = f"re-fulfilled {node_id} from revised code ({len(effects)} effect(s))"
        else:
            _hold(project, node_id, effects, intent, cb, base, report)
    else:  # ACTIVE — extend in place, holding any non-commuting remainder separately
        admitted, held = max_coordination_free_batch_explained(cb, effects, base_effects=base)
        if admitted:
            project.extend_feature(node_id, admitted)
            report.extended.append(node_id)
        if held:
            qid = new_node_id()
            project.quarantine(Node(id=qid, kind=NodeKind.FIX, intent=intent or f"held edits for {node_id}"),
                               [e for e, _ in held], held[0][1],
                               [_desc(e) for e, _ in held], against_ids=[node_id])
            report.quarantined.append(qid)
        report.message = (f"extended {node_id} ({len(admitted)} effect(s))"
                          + (f", {len(held)} held" if held else ""))

    # A successful land may have superseded an earlier hold of this same code — GC the zombie so
    # the agent recovers without a manual revert + replan (the accretion fix).
    if report.fulfilled or report.extended:
        _sweep_superseded(project, report, {(e.file, e.target) for e in effects})
    if report.swept:
        report.message += f"; swept {len(report.swept)} superseded quarantine(s)"
    if notes:
        report.message += f"; {len(notes)} change(s) need manual review"
    project.commit(f"checkpoint: {report.message}", node_id=node_id)
    return report


def run_sync(
    project: Project,
    repo_path: str = ".",
    clusterer: Callable[[list, Project], list[Cluster]] | None = None,
    confirm: Callable[[list[Cluster]], bool] | None = None,
    fulfills: str | None = None,
    intent: str | None = None,
) -> SyncReport:
    """Distill on-disk drift back into the graph through the confluence gate.

    With ``fulfills`` set to a node id, the *entire* current drift lands under that node as a
    single cluster (clustering is skipped) — a PLANNED node is flipped ACTIVE, an ACTIVE one
    extended. ``intent`` is the declared label for the change (used as the cluster intent).
    """
    expected = project.materialize()
    actual = project._disk_sources()
    effects, notes = distill_codebase(expected, actual)
    # Refine whole-unit body rewrites into statement ops so concurrent edits to *different*
    # statements of one function commute at merge time (the agent-agnostic granularity win).
    effects, refine_notes = promote_body_rewrites(project.active_effects(), effects, actual)
    notes.extend(refine_notes)

    if fulfills and project.graph.has(fulfills):
        # Drift already owned by *another* held node belongs to that node (held effects are not
        # active, so they surface as drift) — never let one fulfill absorb a sibling's held code.
        # This is what keeps out-of-order recovery sound: fulfilling a provider does not swallow
        # the dependent's code still waiting on disk.
        effects = _strip_held_elsewhere(project, effects, keep=fulfills)
        if not effects:
            return SyncReport(False, message=(
                f"nothing to fulfill {fulfills}: no on-disk changes attributable to it "
                "since the last checkpoint — implement it first, then checkpoint"), notes=notes)
        # --fulfills lands the drift under one node, atomically, routed on the node's status
        # (PLANNED->fulfill, QUARANTINED->retry, ACTIVE->extend). One node at a time.
        return _fulfill_drift(project, fulfills, effects, intent, notes)

    if not effects:
        msg = "no distillable drift" + (f"; {len(notes)} note(s)" if notes else "")
        return SyncReport(True, message=msg, notes=notes)

    clusterer = clusterer or _default_clusterer(repo_path)
    clusters = clusterer(effects, project)
    if confirm is not None and not confirm(clusters):
        return SyncReport(False, message="sync rejected at checkpoint; nothing landed", notes=notes)

    report = SyncReport(True)
    for cl in clusters:
        kind = _KIND.get(cl.kind, NodeKind.CAPABILITY)
        # Only an ACTIVE node is a valid extend target: a PLANNED node is fulfilled solely
        # through the explicit, atomic --fulfills path, never as a side effect of clustering.
        owner = (cl.target if (cl.target and project.graph.has(cl.target)
                               and project.graph.get(cl.target).status is NodeStatus.ACTIVE)
                 else None)
        # Non-statement edits keep the cluster's extend/new-node semantics.
        other = [e for e in cl.effects if e.op not in STMT_OPS]
        _land(project, other, kind, cl.intent, extend=owner, anchors=[], report=report)
        # Statement edits land as their OWN fix node, one per function — never an extend.
        # The T0 merge gate detects a concurrent same-statement conflict only *across*
        # nodes (engine.py:_concurrent_conflict); two replicas extending the shared owner
        # node would hide the conflict under StatementSeq LWW. A distinct node per edit is
        # what makes a two-user same-statement clash surface (EC6) while distinct-statement
        # edits still commute (EC5). See docs/design/2026-06-18-statement-aware-distill.md.
        by_func: dict[tuple[str, str], list] = {}
        for e in (e for e in cl.effects if e.op in STMT_OPS):
            by_func.setdefault((e.file, e.target), []).append(e)
        for (file, func), ops in by_func.items():
            # Use the cluster's intent (e.g. an agent's declared checkpoint intent) so the edit
            # node carries *why*, falling back to a structural label when none was declared.
            # (Local name — the run_sync ``intent`` parameter governs only the --fulfills path.)
            stmt_intent = cl.intent or f"edit {func} ({file})"
            _land(project, ops, NodeKind.FIX, stmt_intent,
                  extend=None, anchors=[owner] if owner else [], report=report)

    # Landing new code may have superseded an earlier hold of the same names — GC the zombies.
    if report.landed or report.extended:
        _sweep_superseded(project, report, {(e.file, e.target) for e in effects})

    landed, extended, quarantined = report.landed, report.extended, report.quarantined
    fulfilled, swept = report.fulfilled, report.swept
    if landed or extended or fulfilled or quarantined or swept:
        project.commit(
            f"sync: {len(landed)} new, {len(fulfilled)} fulfilled, "
            f"{len(extended)} extended, {len(quarantined)} quarantined, {len(swept)} swept"
        )
    msg = f"reconciled {len(landed)} new + {len(extended)} extended node(s)"
    if fulfilled:
        msg += f", {len(fulfilled)} fulfilled"
    if quarantined:
        msg += f", {len(quarantined)} quarantined"
    if swept:
        msg += f", {len(swept)} superseded quarantine(s) swept"
    if notes:
        msg += f"; {len(notes)} change(s) need manual review"
    return SyncReport(True, message=msg, landed=landed, extended=extended,
                      fulfilled=fulfilled, quarantined=quarantined, swept=swept, notes=notes)
