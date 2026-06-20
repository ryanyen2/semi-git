"""Phase 3 tests: bidirectional reconcile (`sgt sync`) + the pre-flight drift guard.

Driven by the deterministic `fallback_cluster` so no LLM is required.
"""

from sgt.agents.distill import fallback_cluster
from sgt.effects.model import Effect
from sgt.orchestrate.loop import Orchestrator
from sgt.orchestrate.sync import run_sync
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


def _seed_shorten(tmp_path) -> Project:
    proj = Project.init(tmp_path)
    proj.add_feature(Node("shorten", NodeKind.CAPABILITY, "url shortener"),
                     [Effect.add_def("app.py", "shorten", "def shorten(u):\n    return u[:6]")])
    proj.commit("feat: shorten", node_id="shorten")
    return proj


def _det(effects, project):
    return fallback_cluster(effects, project)


def test_no_drift_after_commit(tmp_path):
    proj = _seed_shorten(tmp_path)
    assert proj.check_drift().any is False


def test_hand_edit_is_detected_as_drift(tmp_path):
    proj = _seed_shorten(tmp_path)
    (tmp_path / "app.py").write_text("def shorten(u):\n    return u[:8]\n")  # out-of-band edit
    drift = proj.check_drift()
    assert drift.any and drift.modified == ["app.py"]


def test_preflight_guard_blocks_mutation_on_drift(tmp_path):
    proj = _seed_shorten(tmp_path)
    (tmp_path / "app.py").write_text("def shorten(u):\n    return u[:8]\n")
    orch = Orchestrator(proj, repo_path=str(tmp_path))
    rep = orch.revert("shorten")  # guard runs before any graph mutation
    assert rep.ok is False and "checkpoint" in rep.message
    # --force bypasses the guard
    orch_f = Orchestrator(proj, repo_path=str(tmp_path), force=True)
    assert orch_f.revert("shorten").ok is True


def test_sync_distills_hand_edit_into_fix_node(tmp_path):
    # A body edit now distills to statement ops landed as their OWN fix node (anchored to the
    # owner), not an extend — so two replicas editing one function merge at statement
    # granularity. See docs/design/2026-06-18-statement-aware-distill.md.
    proj = _seed_shorten(tmp_path)
    (tmp_path / "app.py").write_text("def shorten(u):\n    return u[:8]\n")
    rep = run_sync(proj, clusterer=_det, confirm=lambda c: True)
    assert rep.ok and rep.extended == [] and len(rep.landed) == 1
    assert "shorten" in proj.graph.successors(rep.landed[0])  # fix node depends on the function
    # the edit is now in the graph and re-materialization REPRODUCES it (no clobber)
    assert "u[:8]" in proj.materialize()["app.py"]
    assert proj.check_drift().any is False
    # and a fresh open sees the reconciled state persisted
    assert "u[:8]" in Project.open(tmp_path).materialize()["app.py"]


def test_sync_creates_new_node_for_new_file(tmp_path):
    proj = _seed_shorten(tmp_path)
    (tmp_path / "util.py").write_text("def helper(x):\n    return x * 2\n")  # brand-new file
    rep = run_sync(proj, clusterer=_det, confirm=lambda c: True)
    assert rep.ok and len(rep.landed) == 1
    assert "def helper" in proj.materialize()["util.py"]
    assert proj.check_drift().any is False


def test_sync_rejected_at_checkpoint_lands_nothing(tmp_path):
    proj = _seed_shorten(tmp_path)
    (tmp_path / "app.py").write_text("def shorten(u):\n    return u[:8]\n")
    rep = run_sync(proj, clusterer=_det, confirm=lambda c: False)
    assert rep.ok is False
    assert "u[:8]" not in proj.materialize()["app.py"]  # unchanged


def test_sync_quarantines_invalid_hand_edit(tmp_path):
    proj = _seed_shorten(tmp_path)
    # hand edit introduces a call to an undefined function -> invariant-invalid
    (tmp_path / "app.py").write_text("def shorten(u):\n    return missing(u)\n")
    rep = run_sync(proj, clusterer=_det, confirm=lambda c: True)
    assert len(rep.quarantined) == 1
    qid = rep.quarantined[0]
    assert proj.graph.get(qid).status is NodeStatus.QUARANTINED


def test_sync_reports_unparseable_file_as_note_without_crashing(tmp_path):
    proj = _seed_shorten(tmp_path)
    (tmp_path / "app.py").write_text("def shorten(u):\n    return u[:8\n")  # syntax error
    rep = run_sync(proj, clusterer=_det, confirm=lambda c: True)
    assert rep.ok and any("does not parse" in n for n in rep.notes)
