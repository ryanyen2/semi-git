"""Tests for sgt.core.oracle -- async tiered build/test verdicts (plan U9, R13).

Materialization never calls this module -- that's the whole of the "async" requirement, and is
exercised directly below (a `verbs.revert` with no `.sgt/oracle.json` writes nothing to the
verdict table). A verdict is keyed to the exact ideal it was run against, so an edit produces a
fresh key rather than resetting anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from sgt.core import oracle, verbs
from sgt.core.lens import get
from sgt.core.store import Store
from sgt.store.gitbind import init_store


def _foo_chain(repo_path, n: int):
    """A repo whose a.py::foo is a linear chain of `n` versions, one per commit."""
    gb, _ = init_store(repo_path)
    for i in range(1, n + 1):
        (repo_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"foo v{i}")
    return repo_path


def _configure(repo: Path, tiers: list[tuple[str, str]]) -> None:
    payload = {"tiers": [{"name": name, "command": command} for name, command in tiers]}
    (repo / ".sgt").mkdir(exist_ok=True)
    (repo / ".sgt" / "oracle.json").write_text(json.dumps(payload), encoding="utf-8")


def test_no_config_warns_and_proceeds(tmp_path):
    repo = _foo_chain(tmp_path / "repo", 1)
    get(repo)

    result = oracle.run(repo)

    assert result == {"configured": False, "tiers": {}, "override": None}
    assert not (repo / ".sgt" / "local" / "oracle.json").is_file()


def test_failing_tier_records_fail_with_exit_code_and_tail(tmp_path):
    repo = _foo_chain(tmp_path / "repo", 1)
    _configure(repo, [("build", "echo boom && exit 7")])
    ideal = get(repo)

    result = oracle.run(repo)

    assert result["configured"] is True
    tier = result["tiers"]["build"]
    assert tier["status"] == "fail"
    assert tier["exit_code"] == 7
    assert "boom" in tier["output_tail"]
    assert oracle.overall_status(oracle.verdict_for(repo, ideal)) == "fail"


def test_pipeline_stops_at_first_failure(tmp_path):
    repo = _foo_chain(tmp_path / "repo", 1)
    _configure(repo, [("parse", "exit 1"), ("build", "exit 0")])
    get(repo)

    result = oracle.run(repo)

    assert result["tiers"]["parse"]["status"] == "fail"
    assert "build" not in result["tiers"]  # never reached


def test_override_supersedes_with_attribution(tmp_path):
    repo = _foo_chain(tmp_path / "repo", 1)
    _configure(repo, [("build", "exit 1")])
    ideal = get(repo)
    oracle.run(repo)
    assert oracle.overall_status(oracle.verdict_for(repo, ideal)) == "fail"

    record = oracle.override(repo, "pass", "flaky runner", by="alice")

    assert record["override"] == {
        "status": "pass", "reason": "flaky runner", "by": "alice", "ts": record["override"]["ts"],
    }
    assert oracle.overall_status(oracle.verdict_for(repo, ideal)) == "pass"


def test_rerun_replaces_a_stale_record(tmp_path):
    repo = _foo_chain(tmp_path / "repo", 1)
    flag = repo / "ready"
    _configure(repo, [("build", f"test -f {flag.name}")])
    ideal = get(repo)

    first = oracle.run(repo)
    assert first["tiers"]["build"]["status"] == "fail"

    flag.write_text("go", encoding="utf-8")
    second = oracle.run(repo)

    assert second["tiers"]["build"]["status"] == "pass"
    assert oracle.overall_status(oracle.verdict_for(repo, ideal)) == "pass"


def test_verdict_is_keyed_to_the_ideal_and_resets_on_edit(tmp_path):
    repo = _foo_chain(tmp_path / "repo", 3)
    _configure(repo, [("build", "exit 0")])
    before = get(repo)
    oracle.run(repo)
    assert oracle.overall_status(oracle.verdict_for(repo, before)) == "pass"

    tip = before.frontier(Store(repo).all_ops())["a.py::foo"]
    verbs.revert(repo, tip)  # apply -- edits the ideal
    after = get(repo)

    assert after.op_ids != before.op_ids
    assert oracle.verdict_for(repo, after) is None
    assert oracle.overall_status(oracle.verdict_for(repo, after)) == "pending"
    # The stale record for the old ideal is untouched (nothing resets it explicitly).
    assert oracle.overall_status(oracle.verdict_for(repo, before)) == "pass"


def test_apply_with_no_oracle_configured_never_touches_the_verdict_table(tmp_path):
    repo = _foo_chain(tmp_path / "repo", 2)
    ideal = get(repo)
    tip = ideal.frontier(Store(repo).all_ops())["a.py::foo"]

    verbs.revert(repo, tip)  # a materializing verb -- must never invoke the oracle

    assert not (repo / ".sgt" / "local" / "oracle.json").is_file()
