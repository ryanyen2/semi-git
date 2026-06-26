"""End-to-end runtime test WITHOUT the LLM: build features from hand-made effects,
exercise materialize / commit / revert / restore, and verify the working tree and
the git history reflect intent-level versioning.

Recompose is frontier-based and lossless: ``revert`` sets a lane (and its dependents) OFF; the log
and graph are untouched, so ``restore`` brings it back. Closure pulls dependents down; there is no
concept-GC and no surgical suspend-refuse (those were node-status artifacts)."""

import pytest

from sgt.effects.model import Effect, EffectError
from sgt.lifecycle.algebra import restore, revert
from sgt.project import Project
from sgt.store.gitbind import GitError
from sgt.store.graph import Node, NodeKind, NodeStatus


def _add(project, nid, kind, intent, effects):
    project.add_feature(Node(id=nid, kind=kind, intent=intent), effects)
    project.commit(f"feat: {intent}", node_id=nid)


def test_commit_rolls_back_sgt_on_git_failure(tmp_path, monkeypatch):
    # The split-brain guard: if the git commit fails, .sgt must roll back to its prior state so
    # the semantic record never advances past git.
    proj = Project.init(tmp_path)
    proj.add_feature(Node("n", NodeKind.CAPABILITY, "n"),
                     [Effect.add_def("a.py", "n", "def n():\n    return 1")])
    proj.commit("feat n", node_id="n")
    sgt = tmp_path / ".sgt"
    # The semantic state (graph + effect log) is what must roll back; the replica's monotonic
    # counter is intentionally *not* rewound (reusing ids risks collisions, a gap is harmless).
    semantic = ("graph.json", "effects.json")
    before = {n: (sgt / n).read_text() for n in semantic}

    proj.add_feature(Node("m", NodeKind.CAPABILITY, "m"),
                     [Effect.add_def("b.py", "m", "def m():\n    return 2")])

    def boom(*a, **k):
        raise GitError("commit failed")
    monkeypatch.setattr(proj.git, "commit_all", boom)
    with pytest.raises(GitError):
        proj.commit("feat m")
    after = {n: (sgt / n).read_text() for n in semantic}
    assert after == before   # graph + log restored; no half-written node 'm' persisted


def test_write_working_tree_refuses_path_escape(tmp_path):
    # Defense-in-depth: an effect path that escapes the repo root is refused, never written.
    proj = Project.init(tmp_path)
    proj.add_feature(Node("evil", NodeKind.CAPABILITY, "evil"),
                     [Effect.add_def("../evil.py", "x", "def x():\n    return 1")])
    with pytest.raises(EffectError):
        proj.write_working_tree()
    assert not (tmp_path.parent / "evil.py").exists()


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
    outcome = revert(proj, "redirect")
    assert outcome.ok and outcome.changed == ["redirect"]
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
    outcome = revert(proj, "shorten")
    assert outcome.ok
    assert set(outcome.changed) == {"shorten", "redirect"}
    proj.commit("revert: shorten")
    assert "app.py" not in proj.materialize() or proj.materialize().get("app.py", "").strip() == ""
    assert proj.valid()


def test_revert_then_restore_round_trips_the_working_tree(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "banner", NodeKind.CAPABILITY, "banner",
         [Effect.add_def("ui.py", "banner", "def banner():\n    return 'hi'")])

    off = revert(proj, "banner")
    assert off.ok
    proj.commit("revert: banner")
    assert "ui.py" not in proj.materialize() or "banner" not in proj.materialize().get("ui.py", "")

    # lossless: the lane is still in the graph, restore brings it back
    on = restore(proj, "banner")
    assert on.ok
    proj.commit("restore: banner")
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
    # Reverting the feature drops the whole lane (add + replace) from the tree cleanly.
    out = revert(proj, "shorten")
    assert out.ok and out.changed == ["shorten"]
    assert "def shorten" not in proj.materialize().get("app.py", "")


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
    out = revert(proj, "helper")
    assert out.ok
    assert set(out.changed) == {"helper", "calc"}
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
    # inferred from the held effect's call to base() — the anchor edge is recorded so closure
    # *could* reach the quarantine, but revert only cascades to live (materializing) lanes, so a
    # held node is left untouched (it isn't in the tree to dangle).
    assert "base" in proj.graph.successors("q1")
    out = revert(proj, "base")
    assert out.ok and set(out.changed) == {"base"}
    assert proj.graph.get("q1").status is NodeStatus.QUARANTINED


def test_reverting_a_lane_takes_its_quarantine_dependent(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "base", NodeKind.CAPABILITY, "base",
         [Effect.add_def("a.py", "base", "def base():\n    return 1")])
    qnode = Node(id="q1", kind=NodeKind.CAPABILITY, intent="held")
    proj.quarantine(qnode, [Effect.add_def("a.py", "x", "def x():\n    return base()")],
                    reason="invariant_violated", held_descs=["add_def x (a.py)"],
                    against_ids=["base"])

    # Reverting base sets only its live lane off; the held q1 isn't materializing, so it is not
    # cascaded — and it is losslessly retained (node + witness), recoverable via reconcile.
    out = revert(proj, "base")
    assert out.ok
    assert set(out.changed) == {"base"}
    assert proj.graph.has("q1") and proj.witnesses.get("q1")
    assert proj.graph.get("q1").status is NodeStatus.QUARANTINED


def test_same_name_in_different_files_does_not_create_false_dependency(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "a", NodeKind.CAPABILITY, "a", [Effect.add_def("a.py", "main", "def main():\n    return 1")])
    _add(proj, "b", NodeKind.CAPABILITY, "b", [Effect.add_def("b.py", "main", "def main():\n    return 2")])
    # Two unrelated `main`s in different files must not be linked.
    assert "a" not in proj.graph.successors("b")
    assert "b" not in proj.graph.successors("a")


def test_cross_file_import_creates_dependency(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "lib", NodeKind.CONCEPT, "lib", [Effect.add_def("lib.py", "foo", "def foo():\n    return 1")])
    _add(proj, "user", NodeKind.CAPABILITY, "user",
         [Effect.add_def("app.py", "use", "from lib import foo\n\ndef use():\n    return foo()")])
    assert "lib" in proj.graph.successors("user")


def test_method_level_feature_and_revert(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "svc", NodeKind.CAPABILITY, "service class",
         [Effect.add_def("app.py", "Svc", "class Svc:\n    def a(self):\n        return 1")])
    # A second feature adds a METHOD to the existing class, not a whole new class.
    proj.extend_feature("svc", [Effect.add_def("app.py", "Svc.b", "def b(self):\n    return 2")])
    proj.commit("refine: add Svc.b", node_id="svc")
    app = (tmp_path / "app.py").read_text()
    assert "def a(self)" in app and "def b(self)" in app
    assert proj.valid()


def test_revert_cascades_to_caller_rather_than_dangling(tmp_path):
    # Reverting `helper` while `caller` calls it must take `caller` too (downward closure),
    # so the tree never references an absent `helper` — it cascades, it does not refuse.
    proj = Project.init(tmp_path)
    _add(proj, "helper", NodeKind.CONCEPT, "helper",
         [Effect.add_def("m.py", "helper", "def helper():\n    return 1")])
    _add(proj, "caller", NodeKind.CAPABILITY, "caller",
         [Effect.add_def("m.py", "caller", "def caller():\n    return 1"),
          Effect.add_call("m.py", "caller", "helper")])
    out = revert(proj, "helper")
    assert out.ok
    assert set(out.changed) == {"helper", "caller"}
    proj.commit("revert: helper")
    assert proj.valid()
    assert "def helper" not in proj.materialize().get("m.py", "")


def test_reopen_persists_state(tmp_path):
    proj = Project.init(tmp_path)
    _add(proj, "f", NodeKind.CAPABILITY, "feature f",
         [Effect.add_def("a.py", "f", "def f():\n    return 1")])
    reopened = Project.open(tmp_path)
    assert reopened.graph.has("f")
    assert "def f" in reopened.materialize()["a.py"]
