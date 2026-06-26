"""Plan-editing verbs: merge/split reshape PLANNED drafts (inert — gate nothing)."""

from sgt.effects.model import Effect
from sgt.orchestrate.loop import Orchestrator
from sgt.project import Project
from sgt.store.graph import Node, NodeKind, NodeStatus


def _planned(proj, nid, intent, provides=(), needs=()):
    return Node(id=nid, kind=NodeKind.CAPABILITY, intent=intent,
                status=NodeStatus.PLANNED, provides=list(provides), needs=list(needs))


# -- merge ------------------------------------------------------------------
def test_merge_folds_interface_and_redirects_edges(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_plan([
        _planned(proj, "a", "make a", provides=["a"]),
        _planned(proj, "b", "make b", needs=["a"]),
        _planned(proj, "c", "make c", provides=["c"]),
        _planned(proj, "d", "make d", needs=["c"]),
    ], edges=[("b", "a"), ("d", "c")])
    proj.commit("plan a,b,c,d")

    rep = Orchestrator(proj, repo_path=str(tmp_path)).merge(["a", "c"])
    assert rep.ok and rep.node_id == "a"

    reopened = Project.open(tmp_path)
    g = reopened.graph
    assert not g.has("c")                              # merged draft removed
    sv = g.get("a")
    assert sv.provides == ["a", "c"]                   # absorbed c's interface
    assert any("make c" in p for p in sv.provenance)   # kept its intent in provenance
    # b still depends on the survivor; d's edge to c was redirected onto the survivor
    assert "a" in g.successors("b") and "a" in g.successors("d")
    assert reopened.materialize() == {}                # drafts are inert


def test_merge_unions_alternatives(tmp_path):
    from sgt.decisions.store import build_decisions, load_meta, save_meta

    proj = Project.init(tmp_path)
    proj.add_plan([_planned(proj, "a", "make a"), _planned(proj, "b", "make b")], edges=[])
    meta = load_meta(proj.sgt_dir)
    meta["b"] = {"alternatives": [{"option": "old", "why_rejected": "slow",
                                   "source": "user", "confidence": "high"}]}
    save_meta(proj.sgt_dir, meta)
    proj.commit("plan a,b")

    assert Orchestrator(proj, repo_path=str(tmp_path)).merge(["a", "b"]).ok
    dec = {d.node_id: d for d in build_decisions(Project.open(tmp_path))}["a"]
    assert [a.option for a in dec.alternatives] == ["old"]   # b's alternative followed onto survivor


def test_merge_refuses_realized_node(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(Node("real", NodeKind.CAPABILITY, "real"),
                     [Effect.add_def("m.py", "real", "def real():\n    return 1")])
    proj.add_plan([_planned(proj, "draft", "a draft")], edges=[])
    proj.commit("feat + draft")

    rep = Orchestrator(proj, repo_path=str(tmp_path)).merge(["draft", "real"])
    assert not rep.ok and "not a plan draft" in rep.message
    assert Project.open(tmp_path).graph.has("draft")        # nothing mangled on refusal


def test_merge_needs_two_distinct_drafts(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_plan([_planned(proj, "a", "a")], edges=[])
    proj.commit("plan a")
    orch = Orchestrator(proj, repo_path=str(tmp_path))
    assert not orch.merge(["a"]).ok                          # one ref
    assert not orch.merge(["a", "a"]).ok                     # same draft twice -> nothing to merge


# -- split ------------------------------------------------------------------
def test_split_replaces_and_relinks_by_interface(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_plan([
        _planned(proj, "x", "make x1 and x2", provides=["x1", "x2"]),
        _planned(proj, "p", "needs x1", needs=["x1"]),
    ], edges=[("p", "x")])
    proj.commit("plan x,p")

    rep = Orchestrator(proj, repo_path=str(tmp_path)).split("x", ["ADD x1", "ADD x2"])
    assert rep.ok and len(rep.landed) == 2

    reopened = Project.open(tmp_path)
    g = reopened.graph
    assert not g.has("x")                                    # original replaced
    provides = {tuple(g.get(nid).provides)[0] for nid in rep.landed}
    assert provides == {"x1", "x2"}
    # p depended on x for the name x1; it reconnects to whichever piece now provides x1
    piece_x1 = next(nid for nid in rep.landed if g.get(nid).provides == ["x1"])
    assert g.successors("p") == [piece_x1]
    assert "unassigned" not in rep.message                   # both names claimed


def test_split_reports_unassigned_provides_for_freeform_pieces(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_plan([_planned(proj, "x", "make x1 x2", provides=["x1", "x2"])], edges=[])
    proj.commit("plan x")

    rep = Orchestrator(proj, repo_path=str(tmp_path)).split(
        "x", ["split off the parsing half", "split off the rendering half"])
    assert rep.ok and "unassigned provides" in rep.message
    assert "x1" in rep.message and "x2" in rep.message       # freeform pieces claim nothing


def test_split_refuses_realized_and_too_few_pieces(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(Node("real", NodeKind.CAPABILITY, "real"),
                     [Effect.add_def("m.py", "real", "def real():\n    return 1")])
    proj.add_plan([_planned(proj, "draft", "a draft")], edges=[])
    proj.commit("feat + draft")
    orch = Orchestrator(proj, repo_path=str(tmp_path))
    assert not orch.split("draft", ["ADD only_one"]).ok      # < 2 pieces
    assert not orch.split("real", ["ADD a", "ADD b"]).ok     # realized node refused
