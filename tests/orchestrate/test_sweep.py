"""Accretion fix: a fixed re-checkpoint sweeps the superseded (zombie) quarantine it replaced,
so recovery needs no manual revert + replan — while a legitimately held conflict against a
pre-existing active rival is preserved (only names re-landed *this run* are swept)."""

from sgt.agents.distill import fallback_cluster
from sgt.effects.model import Effect
from sgt.orchestrate.sync import run_sync
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


def _checkpoint(tmp_path, intent):
    proj = Project.open(tmp_path)

    def cluster(effects, project):
        cls = fallback_cluster(effects, project)
        for c in cls:
            c.intent = intent
        return cls

    rep = run_sync(proj, repo_path=str(tmp_path), clusterer=cluster, confirm=lambda c: True)
    return Project.open(tmp_path), rep


def _quarantined(proj):
    return [n.id for n in proj.graph.nodes() if n.status is NodeStatus.QUARANTINED]


def test_fixed_recheckpoint_sweeps_the_superseded_zombie(tmp_path):
    Project.init(tmp_path)
    # 1. Invalid code (main calls an undefined helper) is held.
    (tmp_path / "app.py").write_text("def main():\n    return helper()\n")
    proj, _ = _checkpoint(tmp_path, "add main")
    assert len(_quarantined(proj)) == 1

    # 2. The agent fixes the code; the good version lands AND the prior hold is swept.
    (tmp_path / "app.py").write_text("def helper():\n    return 1\n\ndef main():\n    return helper()\n")
    proj, rep = _checkpoint(tmp_path, "add main + helper")
    assert rep.landed and rep.swept                # something landed and a zombie was reclaimed
    assert _quarantined(proj) == []                # no lingering quarantine — no revert+replan
    assert proj.valid()


def test_unrelated_checkpoint_preserves_a_recorded_conflict(tmp_path):
    """A hold against a pre-existing active rival (a recorded uniqueness conflict) must survive an
    unrelated checkpoint — the sweep only reclaims names re-landed in the same run."""
    proj = Project.init(tmp_path)
    proj.add_feature(Node("af", NodeKind.CAPABILITY, "f=1"),
                     [Effect.add_def("lib.py", "f", "def f():\n    return 1")])
    proj.quarantine(Node("bf", NodeKind.CAPABILITY, "f=2"),
                    [Effect.add_def("lib.py", "f", "def f():\n    return 2")],
                    reason="uniqueness: f", held_descs=["add_def f (lib.py)"], against_ids=["af"])
    proj.commit("seed conflict")

    (tmp_path / "other.py").write_text("def g():\n    return 0\n")  # unrelated work
    proj, rep = _checkpoint(tmp_path, "add g")
    assert rep.swept == []
    assert "bf" in _quarantined(proj)  # the recorded conflict is still recoverable via reconcile
