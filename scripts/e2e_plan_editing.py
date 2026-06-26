"""Live end-to-end for plan-editing + the intent DSL — entirely offline (no LLM, no API key).

Proves the UC1/UC5 robustness story is real, not toy: a plan you can reshape (split/merge) and a
controlled-NL intent surface that parses deterministically and captures user-asserted rationale.
We stand in for the user at the keyboard, driving the orchestrator the way the CLI/MCP do.

Run: uv run python scripts/e2e_plan_editing.py
"""
from __future__ import annotations

import sys
import tempfile

REPO = "/Users/ryanyen2/repos/semi-git"
sys.path.insert(0, REPO)
from sgt.decisions.store import build_decisions  # noqa: E402
from sgt.effects.model import Effect  # noqa: E402
from sgt.orchestrate.loop import Orchestrator  # noqa: E402
from sgt.project import Project  # noqa: E402
from sgt.store.graph import Node, NodeKind, NodeStatus  # noqa: E402

PASS, FAIL = "✅", "❌"
_results: list[bool] = []


def check(label: str, cond: bool) -> None:
    _results.append(bool(cond))
    print(f"   {PASS if cond else FAIL} {label}")


def _boom(*a, **k):  # the LLM planner must never run on this offline path
    raise AssertionError("planner ran — canonical DSL should be deterministic")


def main() -> int:
    wd = tempfile.mkdtemp(prefix="sgt-planedit-e2e-")
    print(f"workdir: {wd}")
    proj = Project.init(wd)
    orch = lambda: Orchestrator(Project.open(wd), repo_path=wd, decomposer=_boom)

    # ---- Phase 1: canonical-DSL plan, offline, with rationale capture ----
    rep = orch().plan("ADD validate_email, normalize_email USING re BECAUSE inline regex was brittle")
    print("\n=== Phase 1 — canonical-DSL plan (no key) ===")
    check("planned one node deterministically (planner never ran)", rep.ok and len(rep.landed) == 1)
    node = Project.open(wd).graph.get(rep.landed[0])
    check("declared provides parsed from the DSL", node.provides == ["validate_email", "normalize_email"])
    check("declared needs parsed from USING", node.needs == ["re"])
    dec = next(d for d in build_decisions(Project.open(wd)) if d.node_id == rep.landed[0])
    check("BECAUSE became the decision's context", dec.intent.context == "inline regex was brittle")

    # a second draft that USES one of the first draft's names (a real dependency)
    rep2 = orch().plan("ADD send_welcome USING validate_email")
    welcome = rep2.landed[0]

    # ---- Phase 2: split one draft into two; dependents relink by interface ----
    rep3 = orch().split(node.id, ["ADD validate_email", "ADD normalize_email"])
    print("\n=== Phase 2 — split a draft; relink by declared interface ===")
    check("split replaced the draft with two pieces", rep3.ok and len(rep3.landed) == 2)
    g = Project.open(wd).graph
    check("original draft is gone", not g.has(node.id))
    piece_validate = next(nid for nid in rep3.landed if g.get(nid).provides == ["validate_email"])
    piece_normalize = next(nid for nid in rep3.landed if g.get(nid).provides == ["normalize_email"])
    check("send_welcome reconnected to the piece that provides validate_email",
          piece_validate in g.successors(welcome))
    check("no provides left unassigned (both claimed by DSL pieces)", "unassigned" not in rep3.message)

    # ---- Phase 3: merge the two pieces back; the dependent's edge redirects ----
    rep4 = orch().merge([piece_validate, piece_normalize])
    print("\n=== Phase 3 — merge drafts; edges redirect onto the survivor ===")
    check("merge folded the pieces into one survivor", rep4.ok and rep4.node_id == piece_validate)
    g = Project.open(wd).graph
    check("merged draft removed", not g.has(piece_normalize))
    check("survivor absorbed both names", g.get(piece_validate).provides == ["validate_email", "normalize_email"])
    check("send_welcome still depends on the survivor (edge preserved through merge)",
          piece_validate in g.successors(welcome))

    # a freeform split has no declared interface — we report what it orphans rather than hide it
    rep_ff = orch().split(piece_validate, ["the parsing half", "the rendering half"])
    check("freeform split reports unassigned provides honestly",
          rep_ff.ok and "unassigned provides" in rep_ff.message and "validate_email" in rep_ff.message)
    check("the dependent is now an honest island (no phantom edge to a freeform piece)",
          Project.open(wd).graph.successors(welcome) == [])

    # ---- Phase 4: EXTEND folds onto a realized lane as a REVISE ----
    print("\n=== Phase 4 — EXTEND a realized lane (fold-as-revise) ===")
    p = Project.open(wd)
    p.add_feature(Node("auth", NodeKind.CAPABILITY, "auth", provides=["login"]),
                  [Effect.add_def("auth.py", "login", "def login():\n    return 1")])
    p.commit("feat auth")
    rep5 = Orchestrator(Project.open(wd), repo_path=wd, decomposer=_boom).plan("EXTEND login TO add logout")
    check("EXTEND planned a node", rep5.ok)
    folded = next(d for d in build_decisions(Project.open(wd)) if d.node_id == rep5.landed[0])
    check("folded onto the auth lane as a revise", folded.feature == "auth"
          and folded.lifecycle_kind.value == "revise")

    # ---- Phase 5: revert drops a draft (no separate `drop` verb needed) ----
    print("\n=== Phase 5 — revert discards a draft ===")
    rep6 = Orchestrator(Project.open(wd), repo_path=wd).revert(welcome)
    check("revert dropped the planned draft", rep6.ok and not Project.open(wd).graph.has(welcome))

    # ---- invariant: plan-editing never touches the materialized tree ----
    check("materialized tree only has realized code (drafts stayed inert)",
          set(Project.open(wd).materialize()) == {"auth.py"})

    print("\n" + "=" * 60)
    ok = sum(_results)
    print(f"{ok}/{len(_results)} checks passed")
    print(f"workdir kept for inspection: {wd}")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
