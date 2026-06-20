"""T0 merge — one test per edge case in docs/design/2026-06-18-merge-edge-cases.md.

Two replicas R1/R2 (injected ids for deterministic tie-breaks). `sync(src, dst)` ships the
entries `dst` hasn't seen and merges them. Properties asserted throughout: the merged tree is
always invariant-valid (I1) and merge is order-independent (I2).
"""

from __future__ import annotations

from sgt.effects.body import StatementSeq
from sgt.effects.invariants import codebase_valid
from sgt.effects.model import Effect
from sgt.merge import conflicts, export_delta, merge
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus

F_BODY = "a = 1\nb = 2\nreturn a + b"
F_SRC = "def f(u):\n    a = 1\n    b = 2\n    return a + b"


def _replicas(tmp_path):
    a = Project.init(tmp_path / "A", replica_id="R1")
    b = Project.init(tmp_path / "B", replica_id="R2")
    return a, b


def _sync(src, dst):
    """Ship src's unseen entries to dst and merge."""
    return merge(dst, export_delta(src, dst.log.frontier()))


def _f_positions():
    # f's statements, seeded from its defining effect (R1's first effect → eid "R1:0").
    return StatementSeq.from_source(F_BODY, "R1", 0).positions()


def _add(proj, nid, kind, intent, effects):
    proj.add_feature(Node(id=nid, kind=kind, intent=intent), effects)


def _active(proj):
    return {n.id for n in proj.graph.nodes() if n.status is NodeStatus.ACTIVE}


def _quarantined(proj):
    return {n.id for n in proj.graph.nodes() if n.status is NodeStatus.QUARANTINED}


# -- identity & union -------------------------------------------------------
def test_ec1_re_pull_is_idempotent(tmp_path):
    a, b = _replicas(tmp_path)
    _add(a, "f", NodeKind.CAPABILITY, "f", [Effect.add_def("app.py", "f", F_SRC)])
    _sync(a, b)
    before = b.materialize()
    r2 = _sync(a, b)  # pull again
    assert b.materialize() == before
    assert not r2.conflicts and "f" in _active(b)


def test_ec2_diamond_reconvergence(tmp_path):
    a, b = _replicas(tmp_path)
    _add(a, "f", NodeKind.CAPABILITY, "f", [Effect.add_def("app.py", "f", F_SRC)])
    _sync(a, b)                       # B has f
    _add(b, "g", NodeKind.CAPABILITY, "g", [Effect.add_def("g.py", "g", "def g():\n    return 1")])
    _sync(b, a)                       # A gets g
    r = _sync(a, b)                   # B pulls A (which now contains B's own g) → no dup, no new conflict
    assert not r.conflicts
    assert _active(a) == _active(b) == {"f", "g"}


def test_ec3_logical_duplicate_surfaces_conflict(tmp_path):
    a, b = _replicas(tmp_path)
    _add(a, "fa", NodeKind.CAPABILITY, "f", [Effect.add_def("app.py", "f", F_SRC)])
    _add(b, "fb", NodeKind.CAPABILITY, "f", [Effect.add_def("app.py", "f", F_SRC)])
    r = _sync(b, a)  # both define f independently
    assert _active(a) == {"fa"} and _quarantined(a) == {"fb"}   # one wins, other surfaced
    assert codebase_valid(a.materialize())


# -- concurrency & conflict shape -------------------------------------------
def _diverged_edits(tmp_path, pos_b_index):
    """Common base f on both; A edits stmt0, B edits stmt`pos_b_index`."""
    a, b = _replicas(tmp_path)
    _add(a, "f", NodeKind.CAPABILITY, "f", [Effect.add_def("app.py", "f", F_SRC)])
    _sync(a, b)
    p = _f_positions()
    _add(a, "editA", NodeKind.FIX, "edit a", [Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10")])
    _add(b, "editB", NodeKind.FIX, "edit b",
         [Effect.replace_stmt("app.py", "f", p[pos_b_index].to_dict(), "b = 20")])
    return a, b


def test_ec5_distinct_statement_edits_both_land(tmp_path):
    a, b = _diverged_edits(tmp_path, pos_b_index=1)
    _sync(b, a)
    assert _active(a) == {"f", "editA", "editB"} and not _quarantined(a)
    src = a.materialize()["app.py"]
    assert "a = 10" in src and "b = 20" in src
    assert codebase_valid(a.materialize())


def test_ec6_same_statement_edits_hold_one(tmp_path):
    a, b = _diverged_edits(tmp_path, pos_b_index=0)  # both edit stmt0
    _sync(b, a)
    assert _active(a) == {"f", "editA"} and _quarantined(a) == {"editB"}
    assert codebase_valid(a.materialize())
    (c,) = conflicts(a)
    assert c.node_id == "editB" and any(s.node_id == "editA" for s in c.against)


def test_ec7_three_way_same_statement(tmp_path):
    # All three replicas get the base BEFORE anyone edits, so the three edits to stmt0 are
    # genuinely concurrent (none descends from another).
    a, b = _replicas(tmp_path)
    c = Project.init(tmp_path / "C", replica_id="R3")
    _add(a, "f", NodeKind.CAPABILITY, "f", [Effect.add_def("app.py", "f", F_SRC)])
    _sync(a, b)
    _sync(a, c)
    p = _f_positions()
    _add(a, "editA", NodeKind.FIX, "edit a", [Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 10")])
    _add(b, "editB", NodeKind.FIX, "edit b", [Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 20")])
    _add(c, "editC", NodeKind.FIX, "edit c", [Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 30")])
    _sync(b, a)
    _sync(c, a)  # A now has editA (active) plus two concurrent same-statement edits
    assert "editA" in _active(a)
    assert {"editB", "editC"} <= _quarantined(a)   # all losers held, none dropped
    assert codebase_valid(a.materialize())


def test_ec8_mutual_validity_joint_invalidity(tmp_path):
    a, b = _replicas(tmp_path)
    _add(a, "g", NodeKind.CAPABILITY, "g", [Effect.add_def("app.py", "g", "def g():\n    return 1")])
    _sync(a, b)
    _add(a, "rm", NodeKind.FIX, "remove g", [Effect.remove_def("app.py", "g")])
    _add(b, "useg", NodeKind.CAPABILITY, "use g",
         [Effect.add_def("app.py", "user", "def user():\n    return g()")])
    _sync(b, a)
    # each valid alone, invalid together → exactly one held, tree stays valid
    assert len(_quarantined(a)) == 1
    assert codebase_valid(a.materialize())


# -- lifecycle interplay ----------------------------------------------------
def test_ec9_revert_here_edit_there(tmp_path):
    a, b = _replicas(tmp_path)
    _add(a, "f", NodeKind.CAPABILITY, "f", [Effect.add_def("app.py", "f", F_SRC)])
    _sync(a, b)
    a.remove_nodes({"f"})                 # A reverts f (tombstone travels in the delta)
    p = _f_positions()
    _add(b, "editB", NodeKind.FIX, "edit", [Effect.replace_stmt("app.py", "f", p[0].to_dict(), "a = 9")])
    _sync(a, b)                            # B learns f is gone while it edited f
    assert "editB" in _quarantined(b)      # edit of a reverted feature → conflict, not crash
    assert codebase_valid(b.materialize())


# -- re-derivation & closure ------------------------------------------------
def test_ec12_cross_replica_dependency_edge(tmp_path):
    a, b = _replicas(tmp_path)
    _add(a, "base", NodeKind.CAPABILITY, "base", [Effect.add_def("app.py", "base", "def base():\n    return 1")])
    _sync(a, b)
    _add(b, "consumer", NodeKind.CAPABILITY, "consumer",
         [Effect.add_def("app.py", "consumer", "def consumer():\n    return base()")])
    _sync(b, a)
    assert "base" in a.graph.successors("consumer")   # cross-replica DEPENDS_ON edge derived
    assert _active(a) == {"base", "consumer"}


# -- materialization (SEC) --------------------------------------------------
def test_ec14_merge_is_order_independent(tmp_path):
    a, b = _diverged_edits(tmp_path, pos_b_index=0)   # conflicting edits
    # Path 1: merge B's edit into A. Path 2: merge A's edit into B.
    _sync(b, a)
    _sync(a, b)
    assert a.materialize() == b.materialize()                       # I2: same tree
    assert _quarantined(a) == _quarantined(b)                       # same conflict set
    assert codebase_valid(a.materialize())
