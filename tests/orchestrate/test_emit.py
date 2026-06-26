"""`--emit` previews a revert/restore without touching the tree or the frontier."""

from sgt.effects.model import Effect
from sgt.orchestrate.loop import Orchestrator
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _seed(tmp_path) -> Project:
    proj = Project.init(tmp_path)
    proj.add_feature(Node("greet", NodeKind.CAPABILITY, "greet"),
                     [Effect.add_def("app.py", "greet", "def greet():\n    return 'hi'")])
    proj.commit("feat: greet", node_id="greet")
    return proj


def test_revert_emit_previews_without_mutating(tmp_path):
    proj = _seed(tmp_path)
    rep = Orchestrator(proj, repo_path=str(tmp_path)).revert("greet", emit=True)
    assert rep.ok and rep.landed == ["greet"]
    assert "dry-run" in rep.message and "app.py: removed" in rep.message
    # nothing changed: node still present, file still materializes, on disk untouched
    assert proj.graph.has("greet")
    assert "def greet" in proj.materialize()["app.py"]
    assert (tmp_path / "app.py").read_text().count("def greet") == 1


def test_restore_emit_previews_without_mutating(tmp_path):
    proj = _seed(tmp_path)
    Orchestrator(proj, repo_path=str(tmp_path)).revert("greet")  # greet now out of force
    proj = Project.open(str(tmp_path))
    rep = Orchestrator(proj, repo_path=str(tmp_path)).restore("greet", emit=True)
    assert rep.ok and "dry-run" in rep.message and "app.py: added" in rep.message
    # still off: the dry-run wrote nothing to the frontier
    assert "def greet" not in proj.materialize().get("app.py", "")


def test_emit_leaves_sgt_files_byte_identical(tmp_path):
    # The strongest no-mutation guarantee: the persisted .sgt store is untouched by a dry run.
    proj = _seed(tmp_path)
    sgt = tmp_path / ".sgt"
    before = {p.name: p.read_text() for p in sorted(sgt.glob("*.json"))}
    Orchestrator(proj, repo_path=str(tmp_path)).revert("greet", emit=True)
    after = {p.name: p.read_text() for p in sorted(sgt.glob("*.json"))}
    assert after == before


def test_emit_previews_cascade_without_mutating(tmp_path):
    # B depends on A; reverting A cascades to B (downward closure) so nothing dangles.
    # --emit must preview the full cascade, still without mutating.
    proj = Project.init(tmp_path)
    proj.add_feature(Node("a", NodeKind.CAPABILITY, "a"),
                     [Effect.add_def("m.py", "a", "def a():\n    return 1")])
    proj.add_feature(Node("b", NodeKind.CAPABILITY, "b uses a"),
                     [Effect.add_def("m.py", "b", "def b():\n    return a()")])
    proj.commit("feat")
    rep = Orchestrator(proj, repo_path=str(tmp_path)).revert("a", emit=True)
    assert rep.ok and set(rep.landed) == {"a", "b"}
    assert "dry-run" in rep.message
    # nothing mutated: both lanes still materialize
    assert "def a" in proj.materialize().get("m.py", "")
