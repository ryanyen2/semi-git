"""Recovering decisions from the log: grouping, footprint, lifecycle, and frontier."""

from sgt.decisions.model import DecisionStatus, Frontier, LifecycleKind
from sgt.decisions.store import (
    build_decisions,
    load_frontier,
    save_frontier,
    save_meta,
)
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import EdgeType, Node, NodeKind


def _proj(tmp_path):
    """base (checkpoint 1) then user (checkpoint 2) — two lanes, two landings."""
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base capability"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    proj.log.stamp_committed()  # base lands at frame 1
    proj.add_feature(
        Node(id="user", kind=NodeKind.CAPABILITY, intent="uses base"),
        [Effect.add_def("m.py", "user", "def user():\n    return base()")],
    )
    proj.log.stamp_committed()  # user lands at frame 2
    proj.save()
    return proj


def test_one_decision_per_node_per_checkpoint(tmp_path):
    decisions = build_decisions(_proj(tmp_path))
    by_id = {d.id: d for d in decisions}
    assert set(by_id) == {"base@1", "user@2"}
    assert by_id["base@1"].footprint == ["m.py::base"]
    assert by_id["user@2"].intent.decision == "uses base"
    # both are lane-introducing; depends-on (user->base) is NOT a lifecycle edge
    assert by_id["base@1"].lifecycle_kind is LifecycleKind.INTRODUCE
    assert by_id["user@2"].lifecycle_kind is LifecycleKind.INTRODUCE
    assert by_id["user@2"].feature == "user"


def test_accretion_is_a_revise_on_the_same_lane(tmp_path):
    proj = _proj(tmp_path)
    # extend `base` at a later checkpoint -> a second decision on the base lane
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()  # frame 3
    decisions = {d.id: d for d in build_decisions(proj)}
    assert "base@3" in decisions
    rev = decisions["base@3"]
    assert rev.lifecycle_kind is LifecycleKind.REVISE
    assert rev.lifecycle_of == "base@1"
    assert rev.feature == "base"  # same lane


def test_fork_starts_a_new_lane(tmp_path):
    proj = _proj(tmp_path)
    proj.add_feature(
        Node(id="base2", kind=NodeKind.CAPABILITY, intent="alt base"),
        [Effect.add_def("n.py", "base2", "def base2():\n    return 9")],
    )
    proj.graph.add_edge("base2", "base", EdgeType.DERIVES_FROM)  # base2 forks base
    proj.log.stamp_committed()
    decisions = {d.id: d for d in build_decisions(proj)}
    fork = decisions["base2@3"]
    assert fork.lifecycle_kind is LifecycleKind.FORK
    assert fork.lifecycle_of == "base@1"
    assert fork.feature == "base2"  # a fork is its own lane


def test_default_frontier_is_the_tip_per_lane(tmp_path):
    proj = _proj(tmp_path)
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()
    decisions = build_decisions(proj)
    f = load_frontier(proj, decisions)
    assert f.selection == {"base": "base@3", "user": "user@2"}


def test_pinned_frontier_persists_and_stale_pins_fall_back(tmp_path):
    proj = _proj(tmp_path)
    proj.extend_feature("base", [Effect.add_def("m.py", "helper", "def helper():\n    return 2")])
    proj.log.stamp_committed()
    decisions = build_decisions(proj)
    # pin base back to its first decision (compose-feature-versions)
    save_frontier(proj, Frontier({"base": "base@1", "user": "user@2"}))
    assert load_frontier(proj, decisions).selection["base"] == "base@1"
    # a pin to a decision that doesn't exist falls back to the lane tip
    save_frontier(proj, Frontier({"base": "base@999"}))
    assert load_frontier(proj, decisions).selection["base"] == "base@3"


def test_fix_node_sharing_an_entity_folds_into_the_same_lane(tmp_path):
    # The distiller can split a def-rewrite into a separate fix node that re-owns the same entity.
    # Footprint-grounded lane assignment (R13) must fold it into the original lane as a revise,
    # not spawn a phantom lane + a duplicate-owner clash.
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="embedding", kind=NodeKind.CAPABILITY, intent="embedding"),
        [Effect.add_def("e.py", "embed", "def embed(t):\n    return [t]")],
    )
    proj.log.stamp_committed()  # embedding@1 owns e.py::embed
    # a separate node re-owns the SAME entity (what a fix-node split looks like)
    proj.add_feature(
        Node(id="fix0001", kind=NodeKind.FIX, intent="rewrite embed"),
        [Effect.add_def("e.py", "embed", "def embed(t):\n    return [t, t]")],
    )
    proj.log.stamp_committed()  # fix0001@2 also owns e.py::embed
    decisions = {d.id: d for d in build_decisions(proj)}
    # both decisions live on the embedding lane (earliest node id), the later one a revise
    assert decisions["embedding@1"].feature == "embedding"
    assert decisions["fix0001@2"].feature == "embedding"
    assert decisions["fix0001@2"].lifecycle_kind is LifecycleKind.REVISE


def test_co_editing_two_existing_defs_does_not_weld_their_lanes(tmp_path):
    # The regression behind the "flattened graph": a checkpoint that ALSO edits a second existing def
    # (e.g. a generate-dispatch that forwards through run_pipeline) must NOT fuse the two lanes. The
    # node introduces a fresh helper, so it is its own lane; generate and run_pipeline stay distinct.
    proj = Project.init(tmp_path)
    proj.add_feature(Node(id="generate", kind=NodeKind.CAPABILITY, intent="call the LLM"),
                     [Effect.add_def("rag.py", "generate", "def generate(c):\n    return c")])
    proj.log.stamp_committed()  # generate@1 owns rag.py::generate
    proj.add_feature(Node(id="run_pipeline", kind=NodeKind.CAPABILITY, intent="orchestrate"),
                     [Effect.add_def("rag.py", "run_pipeline", "def run_pipeline(q):\n    return generate(q)")])
    proj.log.stamp_committed()  # run_pipeline@2 owns rag.py::run_pipeline
    # a node that adds a NEW helper and co-edits BOTH generate and run_pipeline
    proj.add_feature(
        Node(id="openai", kind=NodeKind.CAPABILITY, intent="dispatch to openai"),
        [Effect.add_def("rag.py", "_generate_openai", "def _generate_openai(c):\n    return c"),
         Effect.replace_def("rag.py", "generate", "def generate(c, p='anthropic'):\n    return c"),
         Effect.replace_def("rag.py", "run_pipeline", "def run_pipeline(q, p='anthropic'):\n    return generate(q, p)")],
    )
    proj.log.stamp_committed()
    feats = {d.id: d.feature for d in build_decisions(proj)}
    assert feats["generate@1"] == "generate"
    assert feats["run_pipeline@2"] == "run_pipeline"      # NOT welded into generate
    assert feats["generate@1"] != feats["run_pipeline@2"]  # two distinct lanes survive
    assert feats["openai@3"] == "openai"                   # fresh-def node is its own lane, no weld


def test_pure_multi_def_revise_folds_into_one_lane_not_both(tmp_path):
    # A node that modifies two existing defs and adds nothing new folds into a SINGLE lane (the one it
    # declares it provides), leaving the other lane intact — one fold, never a transitive weld.
    proj = Project.init(tmp_path)
    proj.add_feature(Node(id="preprocess", kind=NodeKind.CAPABILITY, intent="format ctx"),
                     [Effect.add_def("rag.py", "preprocess", "def preprocess(d):\n    return d")])
    proj.log.stamp_committed()
    proj.add_feature(Node(id="run_pipeline", kind=NodeKind.CAPABILITY, intent="orchestrate"),
                     [Effect.add_def("rag.py", "run_pipeline", "def run_pipeline(q):\n    return preprocess(q)")])
    proj.log.stamp_committed()
    # remove-preprocess: rewrites run_pipeline (which it provides) and deletes preprocess — no new def
    proj.add_feature(
        Node(id="rmpre", kind=NodeKind.CAPABILITY, intent="inline preprocess", provides=["run_pipeline"]),
        [Effect.replace_def("rag.py", "run_pipeline", "def run_pipeline(q):\n    return q"),
         Effect.remove_def("rag.py", "preprocess")],
    )
    proj.log.stamp_committed()
    feats = {d.id: d.feature for d in build_decisions(proj)}
    assert feats["rmpre@3"] == "run_pipeline"     # folds into the lane it provides
    assert feats["preprocess@1"] == "preprocess"  # the other lane is NOT welded in


def test_import_only_targets_do_not_create_a_lane(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="base", kind=NodeKind.CAPABILITY, intent="base"),
        [Effect.add_def("m.py", "base", "def base():\n    return 1")],
    )
    proj.log.stamp_committed()
    # an import-only node (what the distiller emits for `from x import y`) shares no entity, so it
    # stays its own (degenerate) lane but never steals/merges a real feature's entity ownership.
    from sgt.decisions.store import _is_entity_key
    assert _is_entity_key("m.py::base") is True
    assert _is_entity_key("m.py::from x import y") is False
    assert _is_entity_key("m.py::import os") is False


def _planned_proj(tmp_path):
    """A freshly-planned, never-checkpointed workspace: 4 PLANNED nodes with depends_on."""
    proj = Project.init(tmp_path)
    proj.add_plan(
        [
            Node(id="retrieve", kind=NodeKind.CAPABILITY, intent="retrieve data"),
            Node(id="preprocess", kind=NodeKind.CAPABILITY, intent="preprocess data"),
            Node(id="generate", kind=NodeKind.CAPABILITY, intent="call the LLM"),
            Node(id="orchestrate", kind=NodeKind.CAPABILITY, intent="run the pipeline"),
        ],
        # dependent -> dependency
        edges=[
            ("preprocess", "retrieve"),
            ("generate", "preprocess"),
            ("orchestrate", "preprocess"),
            ("orchestrate", "retrieve"),
            ("orchestrate", "generate"),
        ],
    )
    proj.save()
    return proj


def test_planned_only_workspace_yields_planned_decisions(tmp_path):
    decisions = {d.id: d for d in build_decisions(_planned_proj(tmp_path))}
    assert set(decisions) == {"retrieve", "preprocess", "generate", "orchestrate"}
    for d in decisions.values():
        assert d.status is DecisionStatus.PLANNED
        assert d.id == d.node_id  # no "@landing" suffix
        assert d.feature == d.node_id  # each capability is its own lane
        assert d.footprint == []
        assert d.commits == []
        assert d.alternatives == []
        assert d.lifecycle_kind is LifecycleKind.INTRODUCE
        assert d.lifecycle_of is None
    # intent.decision carries the node intent
    assert decisions["retrieve"].intent.decision == "retrieve data"


def test_planned_landing_is_topological_deps_first(tmp_path):
    decisions = {d.id: d for d in build_decisions(_planned_proj(tmp_path))}
    # a dependency must sort before every dependent on the time axis
    assert decisions["retrieve"].landing < decisions["preprocess"].landing
    assert decisions["preprocess"].landing < decisions["generate"].landing
    assert decisions["preprocess"].landing < decisions["orchestrate"].landing
    assert decisions["generate"].landing < decisions["orchestrate"].landing
    # 1..N contiguous
    assert sorted(d.landing for d in decisions.values()) == [1, 2, 3, 4]


def test_planned_decisions_never_enter_the_frontier(tmp_path):
    proj = _planned_proj(tmp_path)
    decisions = build_decisions(proj)
    assert load_frontier(proj, decisions).selection == {}
    assert Frontier.tip_of(decisions).selection == {}


def test_mixed_landed_and_planned(tmp_path):
    """A landed lane coexists with a still-planned capability."""
    proj = _proj(tmp_path)  # base@1, user@2 landed
    proj.add_plan([Node(id="future", kind=NodeKind.CAPABILITY, intent="not built yet")], edges=[])
    proj.save()
    decisions = build_decisions(proj)
    by_id = {d.id: d for d in decisions}
    assert by_id["base@1"].status is DecisionStatus.LANDED
    assert by_id["user@2"].status is DecisionStatus.LANDED
    assert by_id["future"].status is DecisionStatus.PLANNED
    # the planned node must not pollute the (landed-only) frontier
    f = load_frontier(proj, decisions)
    assert f.selection == {"base": "base@1", "user": "user@2"}


def test_landed_node_is_not_also_listed_as_planned(tmp_path):
    """A node that landed effects must not be re-emitted as a planned decision."""
    proj = _proj(tmp_path)
    decisions = build_decisions(proj)
    # exactly one decision per landed node id; no bare-id (planned) duplicate
    assert {d.id for d in decisions} == {"base@1", "user@2"}
    assert all(d.status is DecisionStatus.LANDED for d in decisions)


def test_authored_metadata_merges_from_sidecar(tmp_path):
    proj = _proj(tmp_path)
    save_meta(
        proj.sgt_dir,
        {"base@1": {"context": "needed a base", "consequence": "base() exists",
                    "alternatives": [{"option": "inline it", "why_rejected": "duplication"}]}},
    )
    base = {d.id: d for d in build_decisions(proj)}["base@1"]
    assert base.intent.context == "needed a base"
    assert base.intent.decision == "base capability"  # node intent retained as the decision
    assert base.alternatives[0].option == "inline it"
