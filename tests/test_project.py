"""End-to-end runtime test WITHOUT the LLM: build features from hand-made effects,
exercise materialize / commit / revert / switch, and verify the working tree and
the git history reflect intent-level versioning."""

from sgt.effects.model import Effect
from sgt.lifecycle.algebra import revert_feature, switch_feature
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


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


def test_modify_replaces_behavior_in_place(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "shorten", NodeKind.CAPABILITY, "url shortener",
         [Effect.add_def("app.py", "shorten", "def shorten(u):\n    return u[:6]")])

    # Iterate on the feature: rewrite its body via replace_def (the `sgt modify` shape).
    proj.extend_feature("shorten",
                        [Effect.replace_def("app.py", "shorten", "def shorten(u):\n    return u[:8]")])
    proj.commit("refine: shorten uses 8 chars", node_id="shorten")

    app = (tmp_path / "app.py").read_text()
    assert "u[:8]" in app and "u[:6]" not in app
    assert proj.valid()
    # Reverting the feature removes the whole bundle (add + replace) cleanly.
    out = revert_feature(proj, "shorten")
    assert out.ok and out.removed == ["shorten"]


def test_replace_def_introduced_dependency_is_closure_correct(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "helper", NodeKind.CONCEPT, "helper",
         [Effect.add_def("app.py", "helper", "def helper(x):\n    return x * 2")])
    _add(proj, "calc", NodeKind.CAPABILITY, "calc",
         [Effect.add_def("app.py", "calc", "def calc(x):\n    return x")])

    # Modify calc so its body now CALLS helper -> a new dependency edge must form.
    proj.extend_feature("calc",
                        [Effect.replace_def("app.py", "calc", "def calc(x):\n    return helper(x)")])
    assert "helper" in proj.graph.successors("calc")
    proj.commit("refine: calc uses helper", node_id="calc")

    # Reverting helper must now also take calc (whose body depends on it).
    out = revert_feature(proj, "helper")
    assert out.ok
    assert set(out.removed) == {"helper", "calc"}
    assert proj.valid()


def test_quarantine_excluded_from_materialize_until_resolved(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "base", NodeKind.CAPABILITY, "base",
         [Effect.add_def("app.py", "base", "def base():\n    return 1")])

    qnode = Node(id="q1", kind=NodeKind.CAPABILITY, intent="held feature")
    held = [Effect.add_def("app.py", "extra", "def extra():\n    return base()")]
    proj.quarantine(qnode, held, reason="invariant_violated",
                    held_descs=["add_def extra (app.py)"], against_ids=["base"])

    # held effects are NOT materialized while quarantined
    assert "extra" not in proj.materialize().get("app.py", "")
    assert proj.graph.get("q1").status is NodeStatus.QUARANTINED
    # witness + dependency edge recorded
    assert proj.witnesses["q1"]["reason"] == "invariant_violated"
    assert "base" in proj.graph.successors("q1")

    # resolving flips it ACTIVE and its effects join the replay
    proj.resolve_quarantine("q1", held)
    cb = proj.materialize()
    assert "def extra" in cb["app.py"]
    assert proj.graph.get("q1").status is NodeStatus.ACTIVE
    assert "q1" not in proj.witnesses
    assert proj.valid()


def test_quarantine_persists_and_reloads(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "base", NodeKind.CAPABILITY, "base",
         [Effect.add_def("a.py", "base", "def base():\n    return 1")])
    qnode = Node(id="q1", kind=NodeKind.FIX, intent="held")
    proj.quarantine(qnode, [Effect.add_def("a.py", "x", "def x():\n    return 1")],
                    reason="non_commuting_with:base", held_descs=["add_def x (a.py)"],
                    against_ids=["base"])
    proj.save()

    re = Project.open(tmp_path)
    assert re.graph.get("q1").status is NodeStatus.QUARANTINED
    assert re.witnesses["q1"]["reason"] == "non_commuting_with:base"
    assert "x" not in re.materialize().get("a.py", "")


def test_quarantine_anchors_via_inferred_deps_when_against_empty(tmp_path):
    # A held task with against_ids=[] but whose effects reference an existing node
    # must still be anchored to that node (no orphan), so revert closure reaches it.
    proj = Project.init(tmp_path)
    _add(proj, "base", NodeKind.CAPABILITY, "base",
         [Effect.add_def("a.py", "base", "def base():\n    return 1")])
    qnode = Node(id="q1", kind=NodeKind.CAPABILITY, intent="held")
    proj.quarantine(qnode, [Effect.add_def("a.py", "x", "def x():\n    return base()")],
                    reason="invariant_violated", held_descs=["add_def x (a.py)"],
                    against_ids=[])  # nothing landed this run
    # inferred from the held effect's call to base()
    assert "base" in proj.graph.successors("q1")
    out = revert_feature(proj, "base")
    assert out.ok and set(out.removed) == {"base", "q1"}


def test_reverting_against_node_gcs_quarantine(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "base", NodeKind.CAPABILITY, "base",
         [Effect.add_def("a.py", "base", "def base():\n    return 1")])
    qnode = Node(id="q1", kind=NodeKind.CAPABILITY, intent="held")
    proj.quarantine(qnode, [Effect.add_def("a.py", "x", "def x():\n    return base()")],
                    reason="invariant_violated", held_descs=["add_def x (a.py)"],
                    against_ids=["base"])

    # q1 depends on base, so reverting base takes q1 with it (R35)
    out = revert_feature(proj, "base")
    assert out.ok
    assert set(out.removed) == {"base", "q1"}
    assert "q1" not in proj.witnesses


def test_reopen_persists_state(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "f", NodeKind.CAPABILITY, "feature f",
         [Effect.add_def("a.py", "f", "def f():\n    return 1")])
    reopened = Project.open(tmp_path)
    assert reopened.graph.has("f")
    assert "def f" in reopened.materialize()["a.py"]
