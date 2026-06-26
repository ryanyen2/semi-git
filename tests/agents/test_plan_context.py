"""Graph-driven planner context: a capability map (HEAD) + retrieved relevant code, bounded."""

from sgt.agents.plan_context import build_plan_context
from sgt.effects.model import Effect
from sgt.project import Project
from sgt.store.graph import Node, NodeKind


def _proj(tmp_path):
    """m.py with two landed capabilities: loader() and, separately, ranker() calling score()."""
    proj = Project.init(tmp_path)
    proj.add_feature(
        Node(id="loader", kind=NodeKind.CAPABILITY, intent="load rows"),
        [Effect.add_def("m.py", "load_rows", "def load_rows(path):\n    return []")],
    )
    proj.log.stamp_committed()
    proj.add_feature(
        Node(id="ranker", kind=NodeKind.CAPABILITY, intent="rank documents"),
        [Effect.add_def("m.py", "score", "def score(doc):\n    return 0"),
         Effect.add_def("m.py", "rank_documents", "def rank_documents(docs):\n    return sorted(docs, key=lambda d: score(d))")],
    )
    proj.log.stamp_committed()
    (tmp_path / "m.py").write_text(
        "def load_rows(path):\n    return []\ndef score(doc):\n    return 0\n"
        "def rank_documents(docs):\n    return sorted(docs, key=lambda d: score(d))\n",
        encoding="utf-8",
    )
    proj.save()
    return proj


def test_context_includes_a_capability_map_of_head(tmp_path):
    ctx = build_plan_context(_proj(tmp_path), "tweak ranking")
    assert "Existing capabilities (HEAD):" in ctx
    # in-force decisions are listed with the names they provide
    assert "load_rows" in ctx and "rank_documents" in ctx


def test_retrieval_pulls_intent_relevant_code_and_call_graph_neighbors(tmp_path):
    # "ranking" seeds rank_documents; score() is a 1-hop call-graph neighbor and should ride along.
    ctx = build_plan_context(_proj(tmp_path), "improve the ranking of documents")
    assert "rank_documents" in ctx
    assert "def score" in ctx  # neighbor expansion brought in the called function


def test_context_is_bounded_by_budget(tmp_path):
    # A tiny budget still returns the capability map + at least one chunk, but stays small.
    ctx = build_plan_context(_proj(tmp_path), "improve ranking", budget_chars=20)
    assert "Existing capabilities (HEAD):" in ctx
    assert len(ctx) < 1500  # nowhere near a full-tree dump


def test_context_stays_bounded_as_the_codebase_grows(tmp_path):
    # The whole point: with 40 unrelated capabilities on disk, a full-tree render is huge, but the
    # graph-driven context retrieves only the intent-relevant slice and stays near the budget.
    proj = Project.init(tmp_path)
    src_lines = []
    for i in range(40):
        body = f"def feature_{i}(x):\n    # capability number {i} with some body text to add bulk\n    return x + {i}\n"
        proj.add_feature(Node(id=f"f{i}", kind=NodeKind.CAPABILITY, intent=f"feature {i}"),
                         [Effect.add_def("big.py", f"feature_{i}", body.strip())])
        src_lines.append(body)
    proj.log.stamp_committed()
    (tmp_path / "big.py").write_text("\n".join(src_lines), encoding="utf-8")
    proj.save()

    full_render_chars = len("\n".join(src_lines))
    ctx = build_plan_context(proj, "tweak feature_7 behavior", budget_chars=2000)
    # bounded near the budget, and a small fraction of dumping all 40 defs' bodies
    assert len(ctx) < full_render_chars
    # the relevant capability is retrieved
    assert "feature_7" in ctx


def test_capability_map_is_capped_at_scale(tmp_path):
    # With 40 in-force capabilities and a cap of 5, the map lists at most 5 + a "(+N more)" note,
    # prioritizing the ones relevant to the intent (so the planner still sees what it's enhancing).
    proj = Project.init(tmp_path)
    defs = []
    for i in range(39):
        proj.add_feature(Node(id=f"f{i}", kind=NodeKind.CAPABILITY, intent=f"widget {i}"),
                         [Effect.add_def("big.py", f"widget_{i}", f"def widget_{i}():\n    return {i}")])
        defs.append(f"def widget_{i}():\n    return {i}")
    # one distinctly-named, intent-relevant capability
    proj.add_feature(Node(id="rank", kind=NodeKind.CAPABILITY, intent="rank search results"),
                     [Effect.add_def("big.py", "rank_results", "def rank_results(xs):\n    return xs")])
    defs.append("def rank_results(xs):\n    return xs")
    proj.log.stamp_committed()
    (tmp_path / "big.py").write_text("\n".join(defs), encoding="utf-8")
    proj.save()

    ctx = build_plan_context(proj, "improve how we rank search results", budget_chars=300, cap_features=5)
    cap_block = ctx.split("Code relevant")[0]
    assert cap_block.count("(provides:") <= 5            # capped
    assert "more capabilities, not shown" in cap_block   # the rest are summarized
    assert "rank_results" in cap_block                   # the intent-relevant one survives the cap


def test_empty_project_degrades_to_codebase_render(tmp_path):
    proj = Project.init(tmp_path)
    proj.save()
    ctx = build_plan_context(proj, "add a thing")
    assert "nothing built yet" in ctx  # empty capability map, no crash
