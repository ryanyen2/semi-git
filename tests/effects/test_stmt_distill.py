"""Statement-aware distillation — alignment, promotion, and the multi-agent merge payoff.

The headline: two users running *file-editing* coding agents (Gemini/Cursor/Claude Code/Codex/…)
edit one function locally, `sgt sync` distills their edits, and the T0 merge engine gives
statement granularity — distinct-statement edits both land (EC5), same-statement edits surface as
a conflict (EC6) — regardless of which agent produced the edit. See
docs/design/2026-06-18-statement-aware-distill.md.
"""

from __future__ import annotations

from sgt.agents.distill import fallback_cluster
from sgt.effects.body import StatementSeq
from sgt.effects.invariants import codebase_valid, normalize
from sgt.effects.model import Effect, EffectOp, STMT_OPS
from sgt.effects.stmt_distill import diff_statements, promote_body_rewrites
from sgt.merge import conflicts, export_delta, merge
from sgt.orchestrate.sync import run_sync
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus

F_SRC = "def f(u):\n    a = 1\n    b = 2\n    return a + b"


def _seed_f(path, rid="R1") -> Project:
    p = Project.init(path, replica_id=rid)
    p.add_feature(Node("f", NodeKind.CAPABILITY, "f"), [Effect.add_def("app.py", "f", F_SRC)])
    p.commit("feat: f", node_id="f")
    return p


def _det(effects, project):
    return fallback_cluster(effects, project)


# -- pure alignment (diff_statements) ---------------------------------------
def test_replace_reuses_existing_posid():
    """D4: an in-place change reuses the slot's PosId — the conflict-detection-critical case."""
    live = StatementSeq.from_source("a = 1\nb = 2\nreturn a + b", "R1", 0)
    ops = diff_statements(live, "a = 10\nb = 2\nreturn a + b", "app.py", "f")
    assert len(ops) == 1 and ops[0].op is EffectOp.REPLACE_STMT
    assert ops[0].payload["pos"] == live.ordered()[0].pos.to_dict()  # identity preserved
    assert ops[0].payload["source"] == "a = 10"


def test_pure_insert_positions_between_anchors():
    """D5: a new statement becomes insert_stmt between the surrounding kept PosIds."""
    live = StatementSeq.from_source("a = 1\nb = 2", "R1", 0)
    ops = diff_statements(live, "a = 1\nx = 9\nb = 2", "app.py", "f")
    assert len(ops) == 1 and ops[0].op is EffectOp.INSERT_STMT
    assert ops[0].payload["source"] == "x = 9"
    assert ops[0].payload["after"] == live.ordered()[0].pos.to_dict()
    assert ops[0].payload["before"] == live.ordered()[1].pos.to_dict()


def test_pure_remove_tombstones_the_slot():
    """D6: a deleted statement becomes remove_stmt at its PosId."""
    live = StatementSeq.from_source("a = 1\nb = 2\nc = 3", "R1", 0)
    ops = diff_statements(live, "a = 1\nc = 3", "app.py", "f")
    assert len(ops) == 1 and ops[0].op is EffectOp.REMOVE_STMT
    assert ops[0].payload["pos"] == live.ordered()[1].pos.to_dict()


def test_unchanged_body_yields_no_ops():
    live = StatementSeq.from_source("a = 1\nb = 2", "R1", 0)
    assert diff_statements(live, "a = 1\nb = 2", "app.py", "f") == []


def test_multiline_statement_is_one_slot():
    """D10: a nested block is a single statement slot, aligned as a whole."""
    live = StatementSeq.from_source("if u:\n    return 1\nreturn 0", "R1", 0)
    ops = diff_statements(live, "if u:\n    return 2\nreturn 0", "app.py", "f")
    assert len(ops) == 1 and ops[0].op is EffectOp.REPLACE_STMT
    assert normalize(ops[0].payload["source"]) == normalize("if u:\n    return 2")


# -- log-aware promotion ----------------------------------------------------
def test_new_function_stays_add_def():
    """D1: a function with no defining effect cannot be seeded → keep the whole unit."""
    active = [Effect.add_def("app.py", "f", "def f():\n    return 1", eid="R1:0")]
    coarse = [Effect.add_def("app.py", "g", "def g():\n    return 2")]
    actual = {"app.py": "def f():\n    return 1\ndef g():\n    return 2"}
    out, notes = promote_body_rewrites(active, coarse, actual)
    assert out == coarse and not notes


def test_signature_change_falls_back_to_replace_def():
    """D3: a header change is coarser than statements → whole-unit replace_def + a note."""
    active = [Effect.add_def("app.py", "f", "def f(u):\n    return u", eid="R1:0")]
    coarse = [Effect.replace_def("app.py", "f", "def f(u, v):\n    return u")]
    actual = {"app.py": "def f(u, v):\n    return u"}
    out, notes = promote_body_rewrites(active, coarse, actual)
    assert out[0].op is EffectOp.REPLACE_DEF and any("signature" in n for n in notes)


def test_body_change_promotes_to_statement_ops():
    """D8: the first body edit promotes a function to statement-managed."""
    active = [Effect.add_def("app.py", "f", "def f(u):\n    a = 1\n    return a", eid="R1:0")]
    coarse = [Effect.replace_def("app.py", "f", "def f(u):\n    a = 2\n    return a")]
    actual = {"app.py": "def f(u):\n    a = 2\n    return a"}
    out, notes = promote_body_rewrites(active, coarse, actual)
    assert out and all(e.op in STMT_OPS for e in out)
    assert out[0].op is EffectOp.REPLACE_STMT and out[0].payload["source"] == "a = 2"


# -- end-to-end sync: a body edit lands as its own fix node (round-trip) -----
def test_sync_body_edit_lands_as_fix_node_and_reproduces(tmp_path):
    p = _seed_f(tmp_path)
    (tmp_path / "app.py").write_text("def f(u):\n    a = 1\n    b = 2\n    return a * b\n")
    rep = run_sync(p, clusterer=_det, confirm=lambda c: True)
    assert rep.extended == [] and len(rep.landed) == 1      # a fix node, not an extend
    fix = rep.landed[0]
    assert "f" in p.graph.successors(fix)                   # depends on the function's owner
    assert normalize(p.materialize()["app.py"]) == normalize(
        "def f(u):\n    a = 1\n    b = 2\n    return a * b")
    assert p.check_drift().any is False                     # round-trip: no clobber


# -- the multi-agent payoff (EC5 / EC6 through the real file-editing path) ---
def _agents_edit(tmp_path, a_src, b_src):
    """Seed f on A, replicate to B, then each 'agent' edits app.py on disk and syncs."""
    from sgt.orchestrate.sync import run_sync

    a = _seed_f(tmp_path / "A", "R1")
    b = Project.init(tmp_path / "B", replica_id="R2")
    merge(b, export_delta(a, b.log.frontier()))             # B learns f (defining eid travels)
    b.write_working_tree()
    (tmp_path / "A" / "app.py").write_text(a_src)
    (tmp_path / "B" / "app.py").write_text(b_src)
    run_sync(a, clusterer=_det, confirm=lambda c: True)
    run_sync(b, clusterer=_det, confirm=lambda c: True)
    merge(b, export_delta(a, b.log.frontier()))             # bring A's edit into B
    return a, b


def test_distinct_statement_edits_from_two_agents_both_land(tmp_path):
    a, b = _agents_edit(
        tmp_path,
        "def f(u):\n    a = 10\n    b = 2\n    return a + b\n",   # A edits statement 0
        "def f(u):\n    a = 1\n    b = 20\n    return a + b\n",   # B edits statement 1
    )
    src = b.materialize()["app.py"]
    assert "a = 10" in src and "b = 20" in src               # EC5 reached via real agents
    assert not conflicts(b)
    assert codebase_valid(b.materialize())


def test_same_statement_edits_from_two_agents_surface_conflict(tmp_path):
    a, b = _agents_edit(
        tmp_path,
        "def f(u):\n    a = 10\n    b = 2\n    return a + b\n",   # A edits statement 0
        "def f(u):\n    a = 99\n    b = 2\n    return a + b\n",   # B edits the SAME statement 0
    )
    quarantined = {n.id for n in b.graph.nodes() if n.status is NodeStatus.QUARANTINED}
    assert len(quarantined) == 1                              # EC6: one edit surfaced, not lost
    assert len(conflicts(b)) == 1
    assert codebase_valid(b.materialize())
