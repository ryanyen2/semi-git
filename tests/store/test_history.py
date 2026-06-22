"""U8 — historical frame reconstruction: per-entry landing stamp, materialize_at, git tree reads."""

from __future__ import annotations

from sgt.cli import main
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.clock import VersionVector
from sgt.store.gitbind import GitBinding
from sgt.store.graph import Node, NodeKind
from sgt.store.oplog import EffectLog, LogEntry


def _entry(eid, node, name, src):
    return LogEntry(
        eid=eid, node_id=node, author="r", vv=VersionVector(),
        effect=Effect.add_def("m.py", name, src),
    )


def test_stamp_committed_is_monotonic_and_idempotent_per_entry():
    log = EffectLog()
    e1 = _entry("r:1", "a", "a", "def a():\n    return 1")
    log.append(e1)
    assert log.stamp_committed() == 1 and e1.landing == 1
    e2 = _entry("r:2", "b", "b", "def b():\n    return 2")
    log.append(e2)
    assert log.stamp_committed() == 2
    assert e1.landing == 1 and e2.landing == 2  # e1 is not re-stamped
    # landing + landing_seq survive a persistence round-trip.
    log2 = EffectLog.from_dict(log.to_dict())
    assert log2.landing_seq == 2 and log2.entries[0].landing == 1


def _two_checkpoints(tmp_path):
    main(["init", str(tmp_path)])
    proj = Project.open(tmp_path)
    proj.add_feature(
        Node(id="foo", kind=NodeKind.CAPABILITY, intent="foo"),
        [Effect.add_def("m.py", "foo", "def foo():\n    return 1")],
    )
    sha1 = proj.commit("add foo", node_id="foo")
    proj.add_feature(
        Node(id="bar", kind=NodeKind.CAPABILITY, intent="bar"),
        [Effect.add_def("m.py", "bar", "def bar():\n    return 2")],
    )
    sha2 = proj.commit("add bar", node_id="bar")
    return proj, sha1, sha2


def test_materialize_at_replays_per_entry(tmp_path):
    proj, _, _ = _two_checkpoints(tmp_path)
    at1 = proj.materialize_at(1)
    at2 = proj.materialize_at(2)
    # Frame 1 has only foo; frame 2 has both — per-entry, the later add isn't pulled back.
    assert "def foo" in at1["m.py"] and "def bar" not in at1["m.py"]
    assert "def foo" in at2["m.py"] and "def bar" in at2["m.py"]
    # The latest frame equals the live materialization.
    assert at2 == proj.materialize()


def test_timeframe_view_morphs_and_attributes(tmp_path):
    from sgt.api import timeframe_view

    proj, _, _ = _two_checkpoints(tmp_path)
    f1 = timeframe_view(proj, 1)
    f2 = timeframe_view(proj, 2)
    assert f1["frame"] == 1 and f2["frame"] == 2
    # The map grows across frames.
    assert {e["name"] for e in f1["entities"]} == {"foo"}
    assert {e["name"] for e in f2["entities"]} == {"foo", "bar"}
    # Frame-accurate overlay: foo is owned by its feature at frame 1.
    foo = next(e for e in f1["entities"] if e["name"] == "foo")
    assert foo["node_id"] == "foo"
    # Shape parity with entity_graph_view, plus the frame ref.
    assert set(f2) == {
        "entities", "edges", "reduced_edges", "components", "clusters", "count", "frame"
    }


def test_tree_at_and_file_at_read_past_snapshots(tmp_path):
    _, sha1, sha2 = _two_checkpoints(tmp_path)
    git = GitBinding(tmp_path)
    tree1 = git.tree_at(sha1)
    assert "m.py" in tree1 and "def foo" in tree1["m.py"] and "def bar" not in tree1["m.py"]
    assert "def bar" in (git.file_at(sha2, "m.py") or "")
    assert git.file_at(sha1, "does/not/exist.py") is None
