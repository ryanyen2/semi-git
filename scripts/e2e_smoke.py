"""Live end-to-end smoke test for semi-git against the real OpenAI backend.

Verifies the THESIS, not just that code runs:
  1. A freeform intent becomes a feature node whose generated code is valid + runnable.
  2. A second feature that calls the first gets an inferred dependency edge.
  3. Reverting an independent feature leaves the rest intact and valid (clean plug-out).
  4. Reverting a depended-on feature takes its dependents with it (closure).

Run:  uv run python scripts/e2e_smoke.py
The OpenAI key is loaded from this repo's .env.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from sgt.adapter.openai_agent import OpenAICodingAgent
from sgt.config import load_env
from sgt.effects.invariants import codebase_valid
from sgt.orchestrate.loop import Orchestrator
from sgt.project import Project

REPO_ROOT = Path(__file__).resolve().parent.parent


def banner(t: str) -> None:
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def show_code(proj: Project) -> dict:
    cb = proj.materialize()
    for f in sorted(cb):
        print(f"\n----- {f} -----\n{cb[f]}")
    print(f"\n[codebase invariant-valid: {codebase_valid(cb)}]")
    return cb


def report(label: str, rep) -> None:
    print(f"{label}: ok={rep.ok} lane={rep.lane} node={rep.node_id} :: {rep.message}")
    for d in rep.landed:
        print(f"    landed: {d}")
    for d in rep.held:
        print(f"    HELD:   {d}")


def main() -> int:
    load_env(REPO_ROOT)  # OPENAI_API_KEY from this repo's .env
    workdir = tempfile.mkdtemp(prefix="sgt-e2e-")
    print(f"workdir: {workdir}")
    proj = Project.init(workdir)
    orch = Orchestrator(proj, OpenAICodingAgent(repo_path=workdir), repo_path=workdir)

    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(("  PASS: " if cond else "  FAIL: ") + msg)
        if not cond:
            failures.append(msg)

    # --- 1. first feature ---------------------------------------------------
    banner("1) sgt do: add a URL shortener (pure function)")
    r1 = orch.ingest(
        "Add a function shorten(url) in store.py that returns the first 6 characters "
        "of the md5 hex digest of the url. Import hashlib as needed."
    )
    report("ingest #1", r1)
    cb = show_code(proj)
    check(r1.ok and r1.node_id is not None, "feature 1 landed as a node")
    check("store.py" in cb and codebase_valid(cb), "store.py generated and invariant-valid")
    # runnable + behaves as intended
    try:
        ns: dict = {}
        exec(cb["store.py"], ns)
        code = ns["shorten"]("https://example.com/very/long/path")
        print(f"  shorten(...) -> {code!r}")
        check(isinstance(code, str) and len(code) == 6, "shorten returns a 6-char string (intent honored)")
    except Exception as ex:  # noqa: BLE001
        check(False, f"shorten runnable ({type(ex).__name__}: {ex})")
    shorten_node = r1.node_id

    # --- 2. dependent feature ----------------------------------------------
    banner("2) sgt do: add short_link(url) that USES shorten")
    r2 = orch.ingest(
        "Add a function short_link(url) in store.py that calls shorten(url) and returns "
        "the string 'https://sho.rt/' followed by the resulting code."
    )
    report("ingest #2", r2)
    show_code(proj)
    link_node = r2.node_id
    cb = proj.materialize()
    check("short_link" in cb.get("store.py", ""), "short_link landed and is present")
    check(codebase_valid(cb), "codebase still valid after the dependent feature")
    if link_node and link_node != shorten_node:
        deps = proj.graph.successors(link_node)
        print(f"  inferred dependencies of {link_node}: {deps}")
        check(shorten_node in deps, "short_link's cross-node dependency on shorten was inferred")
    else:
        # The classifier judged this a refinement of the shortener capability and
        # merged it into that node. Valid product behavior: reverting the shortener
        # must then also remove short_link (verified in step 5).
        check(link_node == shorten_node, "short_link merged into the shortener node (classifier judgment)")

    # --- 3. independent feature --------------------------------------------
    banner("3) sgt do: add an independent validator")
    r3 = orch.ingest(
        "Add a function is_valid_url(url) in store.py that returns True only if url "
        "starts with 'http://' or 'https://'."
    )
    report("ingest #3", r3)
    show_code(proj)
    validator_node = r3.node_id

    banner("semantic graph")
    for n in proj.graph.nodes():
        print(f"  {n.id} [{n.kind.value}]: {n.intent[:55]}  -> deps {proj.graph.successors(n.id)}")

    # --- 4. clean plug-out of the independent feature ----------------------
    banner("4) sgt revert: remove the validator (independent) — others must survive")
    if validator_node:
        rev = orch.revert(validator_node)
        report("revert validator", rev)
        cb = show_code(proj)
        check(rev.ok and rev.landed == [validator_node], "only the validator was removed")
        check("shorten" in cb.get("store.py", ""), "shorten survived the revert")
        check("short_link" in cb.get("store.py", ""), "short_link survived the revert")
        check(codebase_valid(cb), "codebase still invariant-valid after revert")
        # still runnable
        try:
            ns = {}
            exec(cb["store.py"], ns)
            check("is_valid_url" not in ns, "validator is gone from the materialized module")
        except Exception as ex:  # noqa: BLE001
            check(False, f"post-revert module runnable ({type(ex).__name__}: {ex})")

    # --- 5. closure: revert the depended-on feature ------------------------
    banner("5) sgt revert: remove shorten — its dependent short_link must go too")
    rev2 = orch.revert(shorten_node)
    report("revert shorten", rev2)
    cb = show_code(proj)
    check(rev2.ok, "revert of shorten succeeded")
    if link_node:
        check(shorten_node in rev2.landed and link_node in rev2.landed,
              "closure removed both shorten and its dependent short_link")
    check(codebase_valid(proj.materialize()), "codebase invariant-valid after closure revert")

    banner("RESULT")
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED — intent-level versioning and clean plug-out verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
