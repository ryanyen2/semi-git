"""Phase D: `--emit` previews a revert/switch without touching the tree or the graph."""

from sgt.effects.model import Effect
from sgt.orchestrate.loop import Orchestrator
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


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


def test_switch_emit_previews_without_mutating(tmp_path):
    proj = _seed(tmp_path)
    rep = Orchestrator(proj, repo_path=str(tmp_path)).switch("greet", on=False, emit=True)
    assert rep.ok and "dry-run" in rep.message
    assert proj.graph.get("greet").status is NodeStatus.ACTIVE  # not actually suspended


def test_emit_leaves_sgt_files_byte_identical(tmp_path):
    # The strongest no-mutation guarantee: the persisted .sgt store is untouched by a dry run.
    proj = _seed(tmp_path)
    sgt = tmp_path / ".sgt"
    before = {p.name: p.read_text() for p in sorted(sgt.glob("*.json"))}
    Orchestrator(proj, repo_path=str(tmp_path)).revert("greet", emit=True)
    after = {p.name: p.read_text() for p in sorted(sgt.glob("*.json"))}
    assert after == before


def test_emit_reports_refusal_without_mutating(tmp_path):
    # B depends on A; suspending A would dangle B's reference -> the real op is refused.
    # --emit must report that refusal, still without mutating.
    proj = Project.init(tmp_path)
    proj.add_feature(Node("a", NodeKind.CAPABILITY, "a"),
                     [Effect.add_def("m.py", "a", "def a():\n    return 1")])
    proj.add_feature(Node("b", NodeKind.CAPABILITY, "b uses a"),
                     [Effect.add_def("m.py", "b", "def b():\n    return a()")])
    proj.commit("feat")
    rep = Orchestrator(proj, repo_path=str(tmp_path)).switch("a", on=False, emit=True)
    assert rep.ok is False and "would be refused" in rep.message
    assert proj.graph.get("a").status is NodeStatus.ACTIVE
