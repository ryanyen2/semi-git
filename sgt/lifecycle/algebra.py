"""Recompose the working tree by editing the frontier — the one in-force axis.

The frontier selects, per lane, the in-force decision (or ``OFF``). These two operations are the
whole recompose surface:

* ``revert`` sets a lane ``OFF`` and, with it, every lane that builds on it (downward closure), so
  no dependent is left referencing absent code. Lossless: the log and graph are untouched, so
  ``restore`` brings the feature back.
* ``restore`` sets a lane's in-force decision — its tip ("on") or an earlier one (a pin: hold
  feature-A@v3 beside feature-B@latest) — and turns its ``OFF`` build-on dependencies back on
  (upward closure) so the composition resolves.

Each verb is a pure ``plan_*`` (compute the candidate selection + closure, no I/O) plus ``apply``
(persist, gate, roll back if invalid). The pure plan lets ``--dry-run`` preview a recompose without
touching disk. See docs/design/2026-06-25-one-frontier-minimal-verbs.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sgt.decisions.model import Frontier
from sgt.decisions.store import OFF, build_decisions, load_frontier, save_frontier
from sgt.project import Project
from sgt.store.graph import EdgeType, NodeStatus

_ACTIVE = NodeStatus.ACTIVE


@dataclass
class RecomposeOutcome:
    ok: bool
    changed: list[str] = field(default_factory=list)  # lanes whose in-force selection changed
    message: str = ""
    selection: dict[str, str] = field(default_factory=dict)  # the candidate frontier (for dry-run)


def _lane_deps(project, decisions) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``(dependents, dependencies)`` at lane granularity, lifted from graph ``DEPENDS_ON`` edges.

    ``dependents[L]`` = lanes that build on ``L`` (must go off when ``L`` does);
    ``dependencies[L]`` = lanes ``L`` builds on (must be on for ``L`` to resolve). A node-level
    ``src DEPENDS_ON dst`` edge maps to its lanes (``build_decisions`` assigns each node a lane);
    intra-lane edges are dropped. This is the same edge set the old node-level revert closed over,
    so closure behavior carries over — now expressed per lane.
    """
    lane_of: dict[str, str] = {}
    for d in decisions:
        lane_of.setdefault(d.node_id, d.feature)
    dependents: dict[str, set[str]] = {}
    dependencies: dict[str, set[str]] = {}
    for e in project.graph.edges():
        if e.type is EdgeType.DEPENDS_ON:
            s, t = lane_of.get(e.src), lane_of.get(e.dst)  # s depends on t
            if s and t and s != t:
                dependents.setdefault(t, set()).add(s)
                dependencies.setdefault(s, set()).add(t)
    return dependents, dependencies


def _close(seed: str, edges: dict[str, set[str]]) -> set[str]:
    """Transitive closure of ``seed`` over ``edges`` (inclusive of ``seed``)."""
    seen: set[str] = set()
    stack = [seed]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(edges.get(cur, ()))
    return seen


def plan_revert(project: Project, lane: str) -> tuple[dict[str, str] | None, list[str], str]:
    """Candidate frontier for reverting ``lane`` (lane + dependents -> OFF). No I/O."""
    decisions = build_decisions(project)
    if lane not in {d.feature for d in decisions}:
        return None, [], f"unknown lane {lane!r}"
    frontier = load_frontier(project, decisions)
    dependents, _ = _lane_deps(project, decisions)
    # Cascade only to *live* dependents (lanes with an ACTIVE node, i.e. actually materializing).
    # A quarantined/planned dependent isn't in the tree, so reverting its dependency can't dangle
    # it — and a held rival (anchored by a uniqueness clash, not a true need) must survive so it
    # can be reconciled in. So they are never pulled off; only the seed + live dependents go OFF.
    live = {d.feature for d in decisions
            if project.graph.has(d.node_id) and project.graph.get(d.node_id).status is _ACTIVE}
    close = {ln for ln in _close(lane, dependents) if ln == lane or ln in live}
    changed = [ln for ln in close if frontier.selection.get(ln) != OFF]
    sel = dict(frontier.selection)
    for ln in close:
        sel[ln] = OFF
    return sel, sorted(changed), ""


def plan_restore(project: Project, lane: str,
                 decision_id: str | None = None) -> tuple[dict[str, str] | None, list[str], str]:
    """Candidate frontier for restoring ``lane`` to a decision (tip if None) + on its OFF deps."""
    decisions = build_decisions(project)
    by_lane: dict[str, list] = {}
    for d in decisions:
        by_lane.setdefault(d.feature, []).append(d)
    if lane not in by_lane:
        return None, [], f"unknown lane {lane!r}"
    target = decision_id or max(by_lane[lane], key=lambda d: d.landing).id
    frontier = load_frontier(project, decisions)
    _, dependencies = _lane_deps(project, decisions)
    sel = dict(frontier.selection)
    changed: list[str] = []
    if sel.get(lane) != target:
        changed.append(lane)
    sel[lane] = target
    for dep in _close(lane, dependencies) - {lane}:
        if sel.get(dep) == OFF:  # an explicitly pinned dep is left at its pin; only OFF deps wake
            sel[dep] = max(by_lane[dep], key=lambda d: d.landing).id
            changed.append(dep)
    return sel, sorted(set(changed)), ""


def apply(project: Project, selection: dict[str, str], changed: list[str],
          refuse_msg: str) -> RecomposeOutcome:
    """Persist a candidate frontier, gate the resulting tree, and roll back if it is invalid."""
    if not changed:
        return RecomposeOutcome(True, changed=[], message="no change", selection=selection)
    prev = load_frontier(project, build_decisions(project))
    save_frontier(project, Frontier(selection=selection))
    if not project.valid():
        save_frontier(project, prev)
        return RecomposeOutcome(False, changed=changed, message=refuse_msg, selection=selection)
    return RecomposeOutcome(True, changed=changed, selection=selection)


def revert(project: Project, lane: str) -> RecomposeOutcome:
    sel, changed, err = plan_revert(project, lane)
    if err:
        return RecomposeOutcome(False, message=err)
    return apply(project, sel, changed, "revert would leave the codebase invalid; aborted")


def restore(project: Project, lane: str, decision_id: str | None = None) -> RecomposeOutcome:
    sel, changed, err = plan_restore(project, lane, decision_id)
    if err:
        return RecomposeOutcome(False, message=err)
    return apply(project, sel, changed, "restore would leave the codebase invalid; aborted")
