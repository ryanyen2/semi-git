"""End-to-end runtime test WITHOUT the LLM: build features from hand-made effects,
exercise materialize / commit / revert / switch, and verify the working tree and
the git history reflect intent-level versioning."""

from sgt.effects.model import Effect
from sgt.lifecycle.algebra import revert_feature, switch_feature
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _add(project, nid, kind, intent, effects):
    project.add_feature(Node(id=nid, kind=kind, intent=intent), effects)
    project.commit(f"feat: {intent}", node_id=nid)


def test_add_two_features_then_revert_one(tmp_path):
    proj = Project.init(tmp_path)

    # Feature 1: shorten
    _add(proj, "shorten", NodeKind.CAPABILITY, "url shortener",
         [Effect.add_def("app.py", "shorten", "def shorten(u):\n    return u[:6]")])
    # Feature 2: redirect, which CALLS shorten -> dependency inferred
    _add(proj, "redirect", NodeKind.CAPABILITY, "redirect handler",
         [Effect.add_def("app.py", "redirect", "def redirect(u):\n    return shorten(u)")])

    # both materialized to disk
    app = (tmp_path / "app.py").read_text()
    assert "def shorten" in app and "def redirect" in app
    # dependency inferred from the call to shorten
    assert "shorten" in proj.graph.successors("redirect")
    # git history has two feature commits with node trailers
    assert len(proj.git.commit_shas()) >= 2
    assert proj.git.node_id_for_commit(proj.git.head()) == "redirect"

    # Revert the dependent (redirect): shorten survives, file stays valid
    outcome = revert_feature(proj, "redirect")
    assert outcome.ok and outcome.removed == ["redirect"]
    proj.commit("revert: redirect")
    app = (tmp_path / "app.py").read_text()
    assert "def shorten" in app
    assert "def redirect" not in app
    assert proj.valid()


def test_revert_dependency_takes_dependent_with_it(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "shorten", NodeKind.CAPABILITY, "url shortener",
         [Effect.add_def("app.py", "shorten", "def shorten(u):\n    return u[:6]")])
    _add(proj, "redirect", NodeKind.CAPABILITY, "redirect handler",
         [Effect.add_def("app.py", "redirect", "def redirect(u):\n    return shorten(u)")])

    # Reverting shorten must take redirect (which depends on it) too — else the
    # remaining code would call an undefined shorten().
    outcome = revert_feature(proj, "shorten")
    assert outcome.ok
    assert set(outcome.removed) == {"shorten", "redirect"}
    proj.commit("revert: shorten")
    assert "app.py" not in proj.materialize() or proj.materialize().get("app.py", "").strip() == ""
    assert proj.valid()


def test_switch_off_removes_from_working_tree_then_restores(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "banner", NodeKind.CAPABILITY, "banner",
         [Effect.add_def("ui.py", "banner", "def banner():\n    return 'hi'")])

    off = switch_feature(proj, "banner", on=False)
    assert off.ok
    proj.commit("switch: banner off")
    assert "ui.py" not in proj.materialize() or "banner" not in proj.materialize().get("ui.py", "")

    on = switch_feature(proj, "banner", on=True)
    assert on.ok
    proj.commit("switch: banner on")
    assert "def banner" in proj.materialize()["ui.py"]


def test_reopen_persists_state(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "f", NodeKind.CAPABILITY, "feature f",
         [Effect.add_def("a.py", "f", "def f():\n    return 1")])
    reopened = Project.open(tmp_path)
    assert reopened.graph.has("f")
    assert "def f" in reopened.materialize()["a.py"]
