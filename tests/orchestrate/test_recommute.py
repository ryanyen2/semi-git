"""Graph-only reconcile: a quarantine's held effects are re-gated (no re-authoring).

When a node is held because its effects conflict with a rival, reverting or suspending the
rival should let `reconcile` resolve the held node deterministically — sgt never calls a
backend to rewrite the code.
"""

from sgt.effects.model import Effect
from sgt.orchestrate.loop import Orchestrator
from sgt.orchestrate.quarantine import attempt_recommute
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


def _seed_conflict(tmp_path) -> Project:
    """Land `f` one way, then quarantine a second node that defines `f` differently."""
    proj = Project.init(tmp_path)
    proj.add_feature(Node("a", NodeKind.CAPABILITY, "f returns 1"),
                     [Effect.add_def("m.py", "f", "def f():\n    return 1")])
    proj.quarantine(
        Node("b", NodeKind.CAPABILITY, "f returns 2"),
        [Effect.add_def("m.py", "f", "def f():\n    return 2")],
        reason="uniqueness: f", held_descs=["add_def f (m.py)"], against_ids=["a"],
    )
    proj.commit("seed conflict")
    return proj


def test_recommute_blocked_while_rival_active(tmp_path):
    proj = _seed_conflict(tmp_path)
    ok, effects, reason = attempt_recommute(proj, "b")
    assert ok is False and effects is None and reason


# NOTE: the old "suspend the rival, then reconcile the held same-name def" recovery has no
# direct analog in the one-frontier model: a quarantine and its same-name rival are the *same
# lane* (footprint-union in `_assign_lanes`), and a lossless frontier cannot hold "a off but b on"
# within one lane. Same-name rivalry is now a single-lane pick, not a toggle. The general reconcile
# paths — provider-lands (test_fulfill), empty-bundle and blocked-while-active (below) — are
# unaffected. See docs/design/2026-06-25-one-frontier-minimal-verbs.md (open questions).


def test_reconcile_no_pending_is_ok(tmp_path):
    proj = Project.init(tmp_path)
    rep = Orchestrator(proj, repo_path=str(tmp_path)).reconcile()
    assert rep.ok and "no pending" in rep.message


def test_reconcile_blocked_by_out_of_band_drift(tmp_path):
    # reconcile re-materializes and commits, so it must refuse to run over un-checkpointed hand
    # edits (it would clobber them) — same drift guard as every other mutating verb.
    proj = _seed_conflict(tmp_path)
    (tmp_path / "hand.py").write_text("def hand():\n    return 0\n")
    rep = Orchestrator(proj, repo_path=str(tmp_path)).reconcile()
    assert rep.ok is False and "out-of-band" in rep.message
    assert proj.graph.get("b").status is NodeStatus.QUARANTINED   # untouched


def test_recommute_empty_bundle_resolves(tmp_path):
    # An empty held bundle has nothing to conflict with — it must resolve, not wedge forever.
    proj = Project.init(tmp_path)
    proj.quarantine(Node("q", NodeKind.CAPABILITY, "empty"), [],
                    reason="seed", held_descs=[], against_ids=[])
    proj.commit("seed empty quarantine")
    ok, effects, _ = attempt_recommute(proj, "q")
    assert ok and effects == []
    rep = Orchestrator(proj, repo_path=str(tmp_path)).reconcile("q")
    assert rep.ok and rep.landed == ["q"]
    assert proj.graph.get("q").status is NodeStatus.ACTIVE
