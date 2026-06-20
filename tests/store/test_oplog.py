"""Effect log: projections, stamping, persistence round-trip, and legacy migration."""

from __future__ import annotations

import json

from sgt.effects.model import Effect
from sgt.project import EFFECTS_FILE, GRAPH_FILE, Project
from sgt.store.clock import VersionVector
from sgt.store.graph import Node, NodeKind, SemanticGraph
from sgt.store.oplog import EffectLog, LogEntry


def _entry(eid, nid, n):
    return LogEntry(eid=eid, node_id=nid, effect=Effect.add_import("a.py", "import os"),
                    author=eid.split(":")[0], vv=VersionVector({eid.split(":")[0]: n}))


def test_log_bundles_and_node_order_projections():
    log = EffectLog()
    log.append(_entry("R:0", "A", 1))
    log.append(_entry("R:1", "B", 2))
    log.append(_entry("R:2", "A", 3))  # extend A
    assert list(log.node_ids()) == ["A", "B"]          # first-appearance order
    assert len(log.bundles()["A"]) == 2 and len(log.bundles()["B"]) == 1


def test_tombstone_drops_node_from_projections():
    log = EffectLog()
    log.append(_entry("R:0", "A", 1))
    log.append(_entry("R:1", "B", 2))
    log.tombstone({"A"})
    assert log.node_ids() == ["B"]
    assert "A" not in log.bundles()


def test_frontier_is_merge_of_all_vvs():
    log = EffectLog()
    log.append(_entry("R1:0", "A", 1))
    log.append(_entry("R2:0", "B", 1))
    assert log.frontier().counts == {"R1": 1, "R2": 1}


def test_log_round_trips():
    log = EffectLog()
    log.append(_entry("R:0", "A", 1))
    log.tombstone({"Z"})
    assert EffectLog.from_dict(log.to_dict()).to_dict() == log.to_dict()


def test_add_feature_stamps_effects_with_eids(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(Node(id="f", kind=NodeKind.CAPABILITY, intent="x"),
                     [Effect.add_def("a.py", "f", "def f():\n    return 1")])
    (eff,) = proj.bundles["f"]
    assert eff.eid and ":" in eff.eid              # globally-unique author-stamped id
    assert proj.vv.rank >= 1                        # version vector advanced


def test_legacy_store_migrates_into_log(tmp_path):
    # Write a pre-redesign .sgt (order + bundles, no "log") and confirm it opens,
    # migrates, materializes identically, and effects gain eids.
    Project.init(tmp_path)
    # Build the legacy payload by hand.
    g = SemanticGraph()
    g.add_node(Node(id="shorten", kind=NodeKind.CAPABILITY, intent="url shortener"))
    g.save(tmp_path / ".sgt" / GRAPH_FILE)
    legacy = {
        "order": ["shorten"],
        "bundles": {"shorten": [Effect.add_def("app.py", "shorten",
                    "def shorten(u):\n    return u[:6]").to_dict()]},
        "managed_files": [],
        "witnesses": {},
    }
    (tmp_path / ".sgt" / EFFECTS_FILE).write_text(json.dumps(legacy), encoding="utf-8")

    re = Project.open(tmp_path)
    assert "def shorten" in re.materialize()["app.py"]   # replays identically
    assert re.bundles["shorten"][0].eid                  # gained an identity
    assert re.log.frontier().rank == 1                   # one migrated effect
