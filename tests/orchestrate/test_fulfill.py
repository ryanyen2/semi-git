"""Phase B: `checkpoint --fulfills` lands the agent's real edits under a PLANNED node.

These drive `run_sync` directly with on-disk files, so they are deterministic and need no
LLM — `--fulfills` skips clustering, and distillation is a pure AST diff.
"""

from sgt.agents.distill import fallback_cluster
from sgt.effects.model import Effect
from sgt.orchestrate.sync import run_sync
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus

_YES = lambda clusters: True  # noqa: E731 - auto-confirm in tests


def _planned(proj, nid, intent, provides=(), needs=()):
    proj.add_plan([Node(id=nid, kind=NodeKind.CAPABILITY, intent=intent,
                        provides=list(provides), needs=list(needs))], edges=[])


def _write(tmp_path, name, src):
    (tmp_path / name).write_text(src, encoding="utf-8")


# -- node model --------------------------------------------------------------
def test_provenance_roundtrips():
    n = Node(id="n", kind=NodeKind.CAPABILITY, intent="now", provenance=["planned: before"])
    assert Node.from_dict(n.to_dict()).provenance == ["planned: before"]


# -- fulfillment -------------------------------------------------------------
def test_fulfill_flips_planned_to_active_and_materializes(tmp_path):
    proj = Project.init(tmp_path)
    _planned(proj, "n1", "add a greet function", provides=["greet"])
    _write(tmp_path, "app.py", "def greet():\n    return 'hi'\n")

    rep = run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="n1")
    assert rep.ok and rep.fulfilled == ["n1"]
    node = proj.graph.get("n1")
    assert node.status is NodeStatus.ACTIVE
    assert "def greet" in proj.materialize()["app.py"]
    assert proj.valid()


def test_fulfill_preserves_planned_intent_as_provenance(tmp_path):
    proj = Project.init(tmp_path)
    _planned(proj, "n1", "add a shortener")
    _write(tmp_path, "u.py", "def shorten(x):\n    return x[:6]\n")

    run_sync(proj, repo_path=str(tmp_path), confirm=_YES,
             fulfills="n1", intent="6-char md5 url shortener")
    node = proj.graph.get("n1")
    assert node.intent == "6-char md5 url shortener"        # reality wins
    assert node.provenance == ["planned: add a shortener"]  # plan kept


def test_fulfill_out_of_dependency_order_holds_the_node(tmp_path):
    # B's code calls foo(), but the node that provides foo is still only PLANNED (no code).
    # The gate refuses the unresolved reference, so --fulfills holds B itself QUARANTINED
    # (atomic per node) — not an anonymous quarantine — recoverable later via reconcile.
    proj = Project.init(tmp_path)
    _planned(proj, "a", "provide foo", provides=["foo"])
    _planned(proj, "b", "register using foo", needs=["foo"])
    _write(tmp_path, "reg.py", "def register():\n    return foo()\n")

    rep = run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="b")
    assert rep.fulfilled == [] and rep.quarantined == ["b"]   # the node itself is held
    assert proj.graph.get("b").status is NodeStatus.QUARANTINED
    assert "b" in proj.witnesses
    assert proj.materialize() == {} and proj.valid()         # held code not materialized


def test_fulfill_atomic_node_then_revert_restores_empty(tmp_path):
    proj = Project.init(tmp_path)
    _planned(proj, "n1", "add greet")
    _write(tmp_path, "app.py", "def greet():\n    return 'hi'\n")
    run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="n1")

    from sgt.lifecycle.algebra import revert
    out = revert(proj, "n1")
    assert out.ok and proj.materialize() == {}


# -- regression: plain checkpoint still behaves like sync --------------------
def test_checkpoint_without_fulfills_creates_new_node(tmp_path):
    proj = Project.init(tmp_path)
    _write(tmp_path, "new.py", "def helper():\n    return 1\n")
    rep = run_sync(proj, repo_path=str(tmp_path), clusterer=fallback_cluster, confirm=_YES)
    assert rep.ok and len(rep.landed) == 1 and rep.fulfilled == []
    assert "def helper" in proj.materialize()["new.py"]


# -- status-aware --fulfills contract ---------------------------------------
def test_fulfill_empty_drift_is_explicit_noop(tmp_path):
    # --fulfills with nothing on disk must NOT silently report success and leave the node
    # PLANNED — the agent has to hear that there is nothing to fulfill yet.
    proj = Project.init(tmp_path)
    _planned(proj, "n1", "add greet")
    rep = run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="n1")
    assert rep.ok is False and "nothing to fulfill" in rep.message
    assert proj.graph.get("n1").status is NodeStatus.PLANNED   # untouched


def test_fulfill_on_active_node_extends_it(tmp_path):
    # --fulfills an ACTIVE node adds the drift to that feature (extend), not a double-fulfill.
    proj = Project.init(tmp_path)
    proj.add_feature(Node("n1", NodeKind.CAPABILITY, "greet"),
                     [Effect.add_def("app.py", "greet", "def greet():\n    return 'hi'")])
    proj.commit("feat: greet", node_id="n1")
    _write(tmp_path, "app.py", "def greet():\n    return 'hi'\n\ndef wave():\n    return 'o/'\n")
    rep = run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="n1", intent="add wave")
    assert rep.ok and rep.extended == ["n1"] and rep.fulfilled == []
    assert "def wave" in proj.materialize()["app.py"]


def test_out_of_order_fulfill_does_not_absorb_held_sibling_then_reconciles(tmp_path):
    # Out-of-order: fulfill b (uses foo) before foo exists -> b held. Implement+fulfill the
    # provider a: a must NOT absorb b's still-held register code (it belongs to b). Once foo is
    # active, b's stored held effects commute, so `reconcile` (rival/provider changed) resolves
    # them — the held code is recovered from the log, not re-typed.
    from sgt.orchestrate.loop import Orchestrator

    proj = Project.init(tmp_path)
    _planned(proj, "a", "provide foo", provides=["foo"])
    _planned(proj, "b", "register using foo", needs=["foo"])
    _write(tmp_path, "m.py", "def register():\n    return foo()\n")
    run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="b")
    assert proj.graph.get("b").status is NodeStatus.QUARANTINED

    # implement the provider in the same module and fulfill it; a must NOT absorb b's register
    _write(tmp_path, "m.py", "def foo():\n    return 1\n")
    rep = run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="a")
    assert rep.fulfilled == ["a"]
    assert proj.bundles["a"] and all(e.target != "register" for e in proj.bundles["a"])  # not absorbed

    # foo is now active -> b's stored held register commutes -> reconcile resolves it from the log
    rep = Orchestrator(proj, repo_path=str(tmp_path)).reconcile("b")
    assert rep.ok and rep.landed == ["b"]
    assert proj.graph.get("b").status is NodeStatus.ACTIVE
    assert "b" not in proj.witnesses and proj.valid()
    assert "def register" in proj.materialize()["m.py"]   # held code restored from the log


def test_refulfill_quarantined_with_revised_code_resolves(tmp_path):
    # b's own first attempt does not commute (calls an undefined name). The agent revises the
    # code on disk and re-checkpoints --fulfills b: the QUARANTINED node re-gates the fresh
    # distilled effects and flips ACTIVE. This is the "I fixed the code, retry" path.
    proj = Project.init(tmp_path)
    _planned(proj, "b", "compute", provides=["compute"])
    _write(tmp_path, "c.py", "def compute():\n    return missing()\n")   # undefined ref
    run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="b")
    assert proj.graph.get("b").status is NodeStatus.QUARANTINED

    _write(tmp_path, "c.py", "def compute():\n    return 42\n")           # agent fixes it
    rep = run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="b", intent="compute 42")
    assert rep.ok and rep.fulfilled == ["b"]
    assert proj.graph.get("b").status is NodeStatus.ACTIVE
    assert "b" not in proj.witnesses
    assert "return 42" in proj.materialize()["c.py"] and proj.valid()


def test_held_fulfill_keeps_declared_intent_and_provenance(tmp_path):
    # A held fulfill must carry the same history a successful one would (KTD3): declared intent
    # adopted, planned intent kept as provenance.
    proj = Project.init(tmp_path)
    _planned(proj, "b", "register using foo", needs=["foo"])
    _write(tmp_path, "reg.py", "def register():\n    return foo()\n")
    run_sync(proj, repo_path=str(tmp_path), confirm=_YES, fulfills="b", intent="register an email")
    node = proj.graph.get("b")
    assert node.status is NodeStatus.QUARANTINED
    assert node.intent == "register an email"                  # reality wins, even when held
    assert node.provenance == ["planned: register using foo"]  # plan kept


# -- CLI wiring (durable across the Phase C refactor) ------------------------
def test_cli_checkpoint_fulfills_flips_planned_active(tmp_path, monkeypatch):
    from sgt import cli

    proj = Project.init(tmp_path)
    _planned(proj, "n1", "add greet")
    proj.save()
    _write(tmp_path, "app.py", "def greet():\n    return 'hi'\n")

    monkeypatch.chdir(tmp_path)
    rc = cli.main(["checkpoint", "--fulfills", "n1", "--intent", "greet returns hi", "--yes"])
    assert rc == 0
    assert Project.open(tmp_path).graph.get("n1").status is NodeStatus.ACTIVE
