"""CLI surface tests (the operation-ideal kernel, plan U7/U8/U9 flipped onto the CLI in U10):
argument parsing, human-readable rendering, and `--json` output for the kernel-backed verbs.
Verb *behavior* (the algebra) is tested in `tests/core/`; this file is the thin CLI layer only.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

from sgt.cli import main
from sgt.intent import resolve as resolve_mod
from sgt.store.gitbind import init_store


def _boom(*args, **kwargs):
    raise AssertionError("the NL intent resolver must never be called when a deterministic rung matches")


def _in(tmp_path, argv):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _seed(tmp_path, n: int = 2):
    """A repo whose a.py::foo is a linear chain of `n` versions, one per commit."""
    gb, _ = init_store(tmp_path)
    for i in range(1, n + 1):
        (tmp_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"foo v{i}")
    return gb


def test_init_with_horizon_mines_only_from_that_commit_forward(tmp_path, capsys):
    """`sgt init --horizon <ref>` must reach `lens.init`'s `horizon` param, not get swallowed
    as a positional path -- the escape hatch for a repo whose pre-horizon history is unminable
    (e.g. self-referential rename/delete cycles the miner can't represent, R10)."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("foo v1")
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    horizon_sha = gb.commit_all("foo v2")
    (tmp_path / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    gb.commit_all("foo v3")

    capsys.readouterr()
    assert _in(tmp_path, ["init", "--horizon", horizon_sha]) == 0
    assert not (tmp_path / "--horizon").exists()  # not misparsed as the repo path

    capsys.readouterr()
    assert _in(tmp_path, ["log", "--ops", "--json", "--full"]) == 0
    payload = json.loads(capsys.readouterr().out)
    foo_ops = [op for op in payload["ops"] if "a.py::foo" in [f["symbol"] for f in op["footprint"]]]
    # v1 (pre-horizon) is never mined at all; v2 becomes one root "add", v3 one modify on top --
    # not the 3-op chain a horizon-less init would produce.
    assert len(foo_ops) == 2
    assert {op["kind"] for op in foo_ops} == {"add", "rework"}


def test_init_and_log_roundtrip(tmp_path, capsys):
    # U1 moved the raw op-DAG dump under `sgt log --ops` (bare `sgt log` is now the lane×commit
    # grid, which doesn't print raw `file::symbol` names).
    _seed(tmp_path, 1)
    capsys.readouterr()  # drain seed commit output (none, but keep symmetry with other tests)
    assert _in(tmp_path, ["log", "--ops"]) == 0
    out = capsys.readouterr().out
    assert "op(s)" in out and "a.py::foo" in out


def test_log_human_rail_and_map_wire_state_projections(tmp_path, capsys):
    # The rail (bare `sgt log`) and the map (`--map`) both build `states` from forks_view/
    # rewrite_view and read grid_view ghosts. A clean repo has no forks/drafts/plans -> no banner,
    # but the projections must still wire through without error and render the tree.
    _seed(tmp_path, 3)
    capsys.readouterr()
    assert _in(tmp_path, ["log"]) == 0
    assert "save(s)" in capsys.readouterr().out
    assert _in(tmp_path, ["log", "--map"]) == 0
    assert "feature(s)" in capsys.readouterr().out


def test_log_focus_unknown_group_falls_through_to_the_map(tmp_path, capsys):
    # An unresolvable --focus arg names no group -> resolve_focus_group None -> the single-lane map
    # path, which reports the missing lane rather than crashing or hanging on the vertical view.
    _seed(tmp_path, 2)
    capsys.readouterr()
    assert _in(tmp_path, ["log", "--focus", "nope-no-such"]) == 0
    assert capsys.readouterr().out.strip()  # renders something, doesn't crash


def test_log_json_is_machine_readable(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["log", "--ops", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] >= 1
    assert any("a.py::foo" in op["symbols"] for op in payload["ops"])


def test_log_json_full_is_machine_readable(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["log", "--ops", "--json", "--full"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] >= 1
    assert any("a.py::foo" in [f["symbol"] for f in op["footprint"]] for op in payload["ops"])


def test_state_shows_frontier_and_coverage(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["advanced", "state", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["covered_paths"] == ["a.py"]
    assert payload["oracle_configured"] is False
    assert payload["oracle_verdict"] is None


def test_state_json_full_shows_frontier(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["advanced", "state", "--json", "--full"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "a.py::foo" in payload["frontier"]


def test_log_json_limit_and_offset_flags(tmp_path, capsys):
    _seed(tmp_path, 3)  # a.py::foo v1/v2/v3, at least 3 distinct ops
    capsys.readouterr()

    assert _in(tmp_path, ["log", "--ops", "--json"]) == 0
    total = json.loads(capsys.readouterr().out)["count"]
    assert total > 1

    assert _in(tmp_path, ["log", "--ops", "--json", "--limit", "1", "--offset", "0"]) == 0
    first_page = json.loads(capsys.readouterr().out)
    assert len(first_page["ops"]) == 1

    assert _in(tmp_path, ["log", "--ops", "--json", "--limit", str(total), "--offset", "1"]) == 0
    rest = json.loads(capsys.readouterr().out)
    assert len(rest["ops"]) == total - 1
    assert rest["ops"][0]["id"] != first_page["ops"][0]["id"]


def test_history_json_cli_matches_view_at_default_and_full(tmp_path, capsys):
    """R21: `sgt history --json` (and `--full`) is byte-identical to `sgt.api.history_view` at
    the same mode -- the compact default gained CLI-side limit/offset forwarding in this diff,
    so it needs its own guardrail alongside `log`/`state`/`compose`'s existing ones."""
    from sgt.api import history_view
    from sgt.core.lens import get

    _seed(tmp_path, 2)
    get(tmp_path)  # mine before computing the expected payload directly
    capsys.readouterr()

    assert _in(tmp_path, ["advanced", "history", "--json"]) == 0
    assert capsys.readouterr().out.rstrip("\n") == json.dumps(history_view(str(tmp_path)), indent=2)

    assert _in(tmp_path, ["advanced", "history", "--json", "--full"]) == 0
    assert capsys.readouterr().out.rstrip("\n") == json.dumps(history_view(str(tmp_path), full=True), indent=2)


def test_revert_emit_previews_without_writing(tmp_path, capsys):
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "--emit", "a.py::foo"]) == 0
    out = capsys.readouterr().out
    assert "[revert] a.py::foo" in out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_revert_then_restore_roundtrip(tmp_path, capsys):
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "a.py::foo", "--yes"]) == 0
    capsys.readouterr()
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"

    assert _in(tmp_path, ["restore", "--json", "a.py::foo"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["added"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"


def test_revert_unknown_ref_fails_with_message(tmp_path, monkeypatch, capsys):
    # An unresolved ref now falls to the NL rung (`_resolve_via_intent`) once every deterministic
    # rung fails; force it offline so this stays the deterministic-failure case it always was,
    # independent of whether some earlier test in the same process populated OPENAI_API_KEY.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["revert", "nope::nothing"]) == 1
    assert "✗" in capsys.readouterr().out


# -- NL intent-resolution rung (fallback ladder's last step: op-id -> prefix -> file::symbol ->
# feature label -> NL intent) -------------------------------------------------------------------

class _FakeResponses:
    def __init__(self, output_parsed):
        self._output_parsed = output_parsed
        self.calls = 0

    def parse(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(output_parsed=self._output_parsed)


class FakeClient:
    """Stands in for `get_client(repo).responses.parse(...)` -- the `tests/loop/test_plan.py`
    idiom, applied to `sgt.intent.resolve`."""

    def __init__(self, output_parsed):
        self.responses = _FakeResponses(output_parsed)


def test_revert_by_op_id_never_touches_the_intent_resolver(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(resolve_mod, "get_client", _boom)
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["log", "--ops", "--json"]) == 0
    op_id = json.loads(capsys.readouterr().out)["ops"][0]["id"]

    assert _in(tmp_path, ["revert", "--emit", op_id]) == 0  # resolves as an op-id; NL rung never runs


def test_revert_by_feature_label_never_touches_the_intent_resolver(tmp_path, monkeypatch, capsys):
    from sgt.api import map_view
    from sgt.lens.map import build_map

    monkeypatch.setattr(resolve_mod, "get_client", _boom)
    _seed(tmp_path, 2)
    _in(tmp_path, ["log"])
    build_map(tmp_path)
    label = next(n["label"] for n in map_view(tmp_path)["nodes"] if n["kind"] == "feature")
    capsys.readouterr()

    assert _in(tmp_path, ["revert", "--emit", label]) == 0  # resolves via resolve_feature; NL rung never runs


def test_revert_nl_query_prints_candidates_and_leaves_ideal_unchanged(tmp_path, monkeypatch, capsys):
    from sgt.core.lens import current_ideal, get

    _seed(tmp_path, 2)
    get(tmp_path)  # mine, so "before" reflects the real ideal the command itself would mine
    before = current_ideal(tmp_path).op_ids
    candidate = resolve_mod.Candidate(ref="a.py::foo", kind="symbol", rationale="matches the query")
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: FakeClient(
        resolve_mod.IntentResolution(candidates=[candidate])
    ))
    capsys.readouterr()

    assert _in(tmp_path, ["revert", "the foo logic"]) == 2
    out = capsys.readouterr().out
    assert "a.py::foo" in out
    assert "sgt revert a.py::foo" in out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched
    assert current_ideal(tmp_path).op_ids == before


def test_revert_nl_yes_applies_top_candidate(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, 2)
    candidate = resolve_mod.Candidate(ref="a.py::foo", kind="symbol", rationale="matches the query")
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: FakeClient(
        resolve_mod.IntentResolution(candidates=[candidate])
    ))
    capsys.readouterr()

    assert _in(tmp_path, ["revert", "--yes", "the foo logic"]) == 0
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"


def test_revert_nl_drops_hallucinated_candidate(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, 2)
    hallucinated = resolve_mod.Candidate(ref="ghost.py::nope", kind="symbol", rationale="a guess")
    real = resolve_mod.Candidate(ref="a.py::foo", kind="symbol", rationale="matches the query")
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: FakeClient(
        resolve_mod.IntentResolution(candidates=[hallucinated, real])
    ))
    capsys.readouterr()

    assert _in(tmp_path, ["revert", "--json", "the foo logic"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert [c["ref"] for c in payload["candidates"]] == ["a.py::foo"]  # hallucinated ref dropped
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # never applied


def test_revert_nl_abstains_when_llm_returns_no_candidates(tmp_path, monkeypatch, capsys):
    """An empty candidate list (the LLM declining to guess on an out-of-domain query) is a clean
    exit 1, not a crash and never an applied edit -- the safety valve against confidently
    reverting real work for a query that names nothing in this codebase."""
    _seed(tmp_path, 2)
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: FakeClient(
        resolve_mod.IntentResolution(candidates=[])
    ))
    capsys.readouterr()

    assert _in(tmp_path, ["revert", "--yes", "the blockchain consensus module"]) == 1
    out = capsys.readouterr().out
    assert "plausibly matches" in out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # never applied


def test_revert_nl_dedups_candidates_with_identical_effect(tmp_path, monkeypatch, capsys):
    """An op-id and its `file::symbol` that re-plan to the same edit are one choice, not two --
    the user sees one entry per distinct outcome, not the same revert spelled several ways."""
    _seed(tmp_path, 2)
    dup_a = resolve_mod.Candidate(ref="a.py::foo", kind="symbol", rationale="one phrasing")
    dup_b = resolve_mod.Candidate(ref="a.py::foo", kind="op", rationale="same effect, other phrasing")
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: FakeClient(
        resolve_mod.IntentResolution(candidates=[dup_a, dup_b])
    ))
    capsys.readouterr()

    assert _in(tmp_path, ["revert", "--json", "the foo logic"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["candidates"]) == 1


def test_restore_nl_drops_noop_candidate(tmp_path, monkeypatch, capsys):
    """A `restore` of an already-live symbol adds nothing; it must be dropped, not offered as a
    choice the user can't tell apart from doing nothing. `a.py::foo` is live, so it was never in
    `restore`'s shown vocabulary (only *removed* symbols are, per `_context`'s verb-aware pool) --
    the shared confinement guard (U7/R9) now drops it before `resolve_intent` even returns, one
    stage earlier than the caller's own re-plan-and-drop safety net."""
    _seed(tmp_path, 2)  # nothing reverted -- a.py::foo is live
    candidate = resolve_mod.Candidate(ref="a.py::foo", kind="symbol", rationale="already present")
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: FakeClient(
        resolve_mod.IntentResolution(candidates=[candidate])
    ))
    capsys.readouterr()

    assert _in(tmp_path, ["restore", "bring back foo"]) == 1
    assert "plausibly matches" in capsys.readouterr().out


def test_revert_nl_feature_candidate_routes_through_feature_plan(tmp_path, monkeypatch, capsys):
    """A `feature`-kind NL candidate must re-plan through `plan_revert_feature` (as the
    deterministic feature rung does), not a single-op `plan_revert` that can't resolve a feature
    id -- otherwise the prompt invites feature ids the survivor filter then silently drops."""
    from sgt.api import map_view
    from sgt.lens.map import build_map

    _seed(tmp_path, 2)
    _in(tmp_path, ["log"])  # mine
    build_map(tmp_path)
    fid = next(n["id"] for n in map_view(tmp_path)["nodes"] if n["kind"] == "feature")
    candidate = resolve_mod.Candidate(ref=fid, kind="feature", rationale="the whole foo feature")
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: FakeClient(
        resolve_mod.IntentResolution(candidates=[candidate])
    ))
    capsys.readouterr()

    assert _in(tmp_path, ["revert", "--json", "the entire foo feature"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"] and payload["candidates"][0]["removed"] >= 1  # survived + actionable


def test_revert_nl_offline_reports_clear_message(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed(tmp_path, 2)
    capsys.readouterr()

    assert _in(tmp_path, ["revert", "something vague"]) == 1
    out = capsys.readouterr().out
    assert "✗" in out and "OPENAI_API_KEY" in out


def test_restore_nl_query_prints_candidates_and_leaves_ideal_unchanged(tmp_path, monkeypatch, capsys):
    from sgt.core.lens import current_ideal

    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "a.py::foo", "--yes"]) == 0
    capsys.readouterr()
    before = current_ideal(tmp_path).op_ids

    candidate = resolve_mod.Candidate(ref="a.py::foo", kind="symbol", rationale="the old foo")
    monkeypatch.setattr(resolve_mod, "get_client", lambda repo: FakeClient(
        resolve_mod.IntentResolution(candidates=[candidate])
    ))

    assert _in(tmp_path, ["restore", "bring back the old foo logic"]) == 2
    out = capsys.readouterr().out
    assert "sgt restore a.py::foo" in out
    assert current_ideal(tmp_path).op_ids == before


def test_restore_nl_offline_reports_clear_message(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "a.py::foo", "--yes"]) == 0
    capsys.readouterr()

    assert _in(tmp_path, ["restore", "something vague"]) == 1
    out = capsys.readouterr().out
    assert "✗" in out and "OPENAI_API_KEY" in out


def test_diff_between_refs(tmp_path, capsys):
    gb = _seed(tmp_path, 1)
    base = gb.symbolic_ref().rsplit("/", 1)[-1]
    gb._git("checkout", "-q", "-b", "feature")
    (tmp_path / "b.py").write_text("def bar():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feature: add bar")
    _in(tmp_path, ["log"])  # mine feature
    gb._git("checkout", "-q", base)
    capsys.readouterr()  # drain the priming `log` output

    assert _in(tmp_path, ["diff", "--json", base, "feature"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "b.py::bar" in payload["by_symbol"]


def test_fsck_reports_clean_store(tmp_path, capsys):
    _seed(tmp_path, 1)
    _in(tmp_path, ["log"])  # mine, so the store isn't empty
    capsys.readouterr()  # drain the priming `log` output
    assert _in(tmp_path, ["advanced", "fsck", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["checked"] >= 1


def test_oracle_run_with_no_config_warns(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["advanced", "oracle", "run"]) == 0
    assert "no oracle configured" in capsys.readouterr().out


def test_oracle_override_then_state_shows_verdict(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["advanced", "oracle", "override", "--status", "pass", "--reason", "manual check"]) == 0
    capsys.readouterr()

    assert _in(tmp_path, ["advanced", "state", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    # No `.sgt/oracle.json` -> not "configured", so state doesn't surface the override either
    # (a repo can still call `oracle override` without any tier config; `state` only surfaces
    # the verdict once an oracle is declared, per R13's "no config" degrade).
    assert payload["oracle_configured"] is False


def _no_client(*args, **kwargs):
    raise RuntimeError("no client")


def test_plan_intake_and_status_json(tmp_path, capsys, monkeypatch):
    from sgt.loop import plan as plan_mod

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    _seed(tmp_path, 1)
    capsys.readouterr()

    assert _in(tmp_path, ["plan", "intake", "1. step one\n2. step two"]) == 0
    assert "step one" in capsys.readouterr().out

    assert _in(tmp_path, ["plan", "status", "--json", "--full"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["sessions"]) == 1
    assert [s["title"] for s in payload["sessions"][0]["steps"]] == ["step one", "step two"]


def test_plan_abandon(tmp_path, capsys, monkeypatch):
    from sgt.loop import plan as plan_mod

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    _seed(tmp_path, 1)
    _in(tmp_path, ["plan", "intake", "1. step one", "--json"])
    session_id = json.loads(capsys.readouterr().out)["session_id"]

    assert _in(tmp_path, ["plan", "abandon", session_id]) == 0
    capsys.readouterr()
    assert _in(tmp_path, ["plan", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sessions"] == []

    assert _in(tmp_path, ["plan", "abandon", "no-such-session"]) == 1


def test_log_summary_surfaces_the_residual_of_open_intents(tmp_path, capsys, monkeypatch):
    """`sgt log --summary` folds in the residual (intent-ledger P1): a plan step stated but never
    landed (its session abandoned with the step pending) resurfaces here as "what needs attention",
    so an unfinished intention isn't lost -- and without a separate open/done queue to groom."""
    from sgt.loop import plan as plan_mod

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    _seed(tmp_path, 1)
    _in(tmp_path, ["plan", "intake", "1. wire the retry backoff", "--json"])
    session_id = json.loads(capsys.readouterr().out)["session_id"]
    _in(tmp_path, ["plan", "abandon", session_id])
    capsys.readouterr()

    assert _in(tmp_path, ["log", "--summary"]) == 0
    out = capsys.readouterr().out
    assert "never landed" in out
    assert "wire the retry backoff" in out


def test_log_summary_omits_the_residual_when_nothing_is_open(tmp_path, capsys):
    """No open intents -> no residual noise in the summary (the section prints only when there is
    something to attend to, like every other warning in `_status`)."""
    _seed(tmp_path, 1)
    capsys.readouterr()
    assert _in(tmp_path, ["log", "--summary"]) == 0
    assert "never landed" not in capsys.readouterr().out


def test_fmt_age_is_coarse():
    """The residual age is a coarse day/hour/minute string, never a raw timestamp."""
    from sgt.cli.inspect import _fmt_age

    assert _fmt_age(0) == "just now"
    assert _fmt_age(120) == "2m ago"
    assert _fmt_age(7200) == "2h ago"
    assert _fmt_age(2 * 86400) == "2d ago"


def _seed_pending_step(tmp_path, plan_mod, hollow_id_suffix: str, *, session="s1"):
    """A one-step active plan session predicting `a.py::foo`, its baseline being the current op set.
    Returns the session's single hollow id."""
    from pathlib import Path

    from sgt.core.op import make_op
    from sgt.core.store import Store

    store = Store(tmp_path)
    baseline = sorted(op.id for op in store.all_ops())
    footprint = {"a.py::foo": (None, plan_mod._PENDING),
                 f"__plan__::{session}::{hollow_id_suffix}": (None, plan_mod._PENDING)}
    hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent="touch foo")
    store.add_hollow(hollow)
    table = plan_mod._load_sessions(Path(tmp_path))
    table.setdefault(session, {
        "plan_text": "1. touch foo\n", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": baseline, "steps": [],
    })
    table[session]["steps"].append({
        "hollow_id": hollow.id, "title": "touch foo", "predicted_footprint": ["a.py::foo"],
        "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
    })
    plan_mod._save_sessions(Path(tmp_path), table)
    return hollow.id


def test_save_auto_confirms_a_single_plan_step(tmp_path, capsys, monkeypatch):
    """A save fulfilling exactly one pending plan step auto-confirms it -- no separate `checkpoint`
    verb (U12/R10). The save reports the fold and the step flips to `matched`."""
    from sgt.loop import plan as plan_mod

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    _seed(tmp_path, 1)  # a.py::foo == "return 1"
    _seed_pending_step(tmp_path, plan_mod, "step0")

    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")
    capsys.readouterr()
    assert _in(tmp_path, ["save", "-m", "touch foo", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["saved"] is True
    assert len(out["plan"]["auto_confirmed"]) == 1
    assert out["plan"]["auto_confirmed"][0]["session_id"] == "s1"
    assert not out["plan"]["ambiguous"]

    # Auto-confirming the only step completes the one-step session: it leaves the active
    # `plan status` surface, and its step is recorded matched in the full table.
    assert _in(tmp_path, ["plan", "status", "--json", "--full"]) == 0
    assert json.loads(capsys.readouterr().out)["sessions"] == []
    table = plan_mod._load_sessions(tmp_path)
    assert table["s1"]["status"] == "completed"
    assert table["s1"]["steps"][0]["status"] == "matched"


def test_save_resolve_plan_settles_an_ambiguous_match(tmp_path, capsys, monkeypatch):
    """Two pending steps both predicting `a.py::foo` tangle into one op cluster on a save -- an n:m
    match that does NOT auto-confirm. `save --resolve-plan --confirm-hollow/--confirm-op` settles
    one group by name (the standalone-on-a-clean-tree resolution path)."""
    from sgt.loop import plan as plan_mod

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    _seed(tmp_path, 1)
    h0 = _seed_pending_step(tmp_path, plan_mod, "step0")
    _seed_pending_step(tmp_path, plan_mod, "step1")  # second step, same predicted symbol -> n:m

    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")
    capsys.readouterr()
    assert _in(tmp_path, ["save", "-m", "touch foo", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["saved"] is True
    assert not out["plan"]["auto_confirmed"]  # two steps -> ambiguous, nothing auto-confirmed
    ambiguous = out["plan"]["ambiguous"]
    assert len(ambiguous) == 1 and len(ambiguous[0]["hollow_ids"]) == 2
    op_id = ambiguous[0]["op_ids"][0]

    # Resolve one step by name, standalone on the now-clean tree (nothing new to save).
    argv = ["save", "--resolve-plan", "--confirm-hollow", h0, "--confirm-op", op_id, "--json"]
    assert _in(tmp_path, argv) == 0
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["ok"] is True and confirmed["session_id"] == "s1"

    assert _in(tmp_path, ["plan", "status", "--json", "--full"]) == 0
    steps = json.loads(capsys.readouterr().out)["sessions"][0]["steps"]
    by_hollow = {s["hollow_id"]: s["status"] for s in steps}
    assert by_hollow[h0] == "matched"  # exactly the named step resolved; the other stays pending


def test_resolve_plan_confirm_resolves_truncated_refs_to_full_ids(tmp_path, capsys, monkeypatch):
    """`--resolve-plan` prints truncated (12-char) ids, so a user pasting one back must resolve to
    the full canonical id -- and the recorded match must key on the *full* op id, never the prefix.
    Keying on a prefix would leave the real op absent from `recorded_matches`, so it would resurface
    as drift on the next checkpoint. Also asserts unknown/ambiguous refs fail cleanly."""
    from sgt.loop import plan as plan_mod
    from sgt.loop.match import recorded_matches

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    _seed(tmp_path, 1)
    h0 = _seed_pending_step(tmp_path, plan_mod, "step0")
    _seed_pending_step(tmp_path, plan_mod, "step1")  # second step, same symbol -> n:m, no auto-confirm

    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")
    capsys.readouterr()
    assert _in(tmp_path, ["save", "-m", "touch foo", "--json"]) == 0
    full_op = json.loads(capsys.readouterr().out)["plan"]["ambiguous"][0]["op_ids"][0]

    # Confirm using the *truncated* ids the preview prints (h0/full_op are 64-char; feed 12).
    argv = ["save", "--resolve-plan", "--confirm-hollow", h0[:12], "--confirm-op", full_op[:12], "--json"]
    assert _in(tmp_path, argv) == 0
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["op_ids"] == [full_op] and confirmed["hollow_ids"] == [h0]  # resolved, not echoed
    assert full_op in recorded_matches(tmp_path)  # keyed on the FULL id -> never resurfaces as drift
    assert full_op[:12] not in recorded_matches(tmp_path)  # the prefix is NOT an orphan key

    capsys.readouterr()
    assert _in(tmp_path, ["save", "--resolve-plan", "--confirm-hollow", h0[:12],
                          "--confirm-op", "deadbeef", "--json"]) == 1  # unknown op ref fails cleanly


def test_history_json_lists_commits_and_places_ops_on_the_axis(tmp_path, capsys):
    _seed(tmp_path, n=2)
    capsys.readouterr()
    assert _in(tmp_path, ["advanced", "history", "--json", "--full"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert [c["index"] for c in view["commits"]] == [0, 1]
    assert view["ops"]
    assert all(op["commit_index"] in (0, 1) for op in view["ops"])


def test_compose_json_matches_the_api_view_byte_for_byte(tmp_path, capsys):
    """R21: `sgt compose --json` is byte-identical to `sgt.api.compose_view` -- no CLI-side
    reshaping of the workbench's single-call refresh."""
    from sgt.api import compose_view

    _seed(tmp_path, n=2)
    capsys.readouterr()
    assert _in(tmp_path, ["advanced", "compose", "--json"]) == 0
    out = capsys.readouterr().out
    assert out.rstrip("\n") == json.dumps(compose_view(str(tmp_path)), indent=2)


def test_compose_text_summarizes_status_and_oracle(tmp_path, capsys):
    _seed(tmp_path, n=1)
    capsys.readouterr()
    assert _in(tmp_path, ["advanced", "compose"]) == 0
    out = capsys.readouterr().out
    assert "file(s)" in out and "symbol(s)" in out and "feature(s)" in out
    assert "oracle: unconfigured" in out


def test_fold_at_commit_index_returns_that_frontiers_files(tmp_path, capsys):
    _seed(tmp_path, n=2)  # a.py::foo v1 (index 0), then v2 (index 1)
    capsys.readouterr()

    assert _in(tmp_path, ["advanced", "fold", "--at", "0", "--json"]) == 0
    early = json.loads(capsys.readouterr().out)
    assert early["files"]["a.py"] == "def foo():\n    return 1\n"

    assert _in(tmp_path, ["advanced", "fold", "--at", "1", "--json"]) == 0
    later = json.loads(capsys.readouterr().out)
    assert later["files"]["a.py"] == "def foo():\n    return 2\n"
    assert later["op_count"] > early["op_count"]


def test_fold_at_ref_folds_head(tmp_path, capsys):
    _seed(tmp_path, n=1)
    capsys.readouterr()
    assert _in(tmp_path, ["advanced", "fold", "--at", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert "a.py" in out and "oracle verdict: pending" in out


def test_fold_at_bad_op_id_set_reports_forked_not_a_crash(tmp_path, capsys):
    _seed(tmp_path, n=2)
    capsys.readouterr()
    assert _in(tmp_path, ["advanced", "history", "--json", "--full"]) == 0
    hist = json.loads(capsys.readouterr().out)
    non_root = next(o["id"] for o in hist["ops"] if o["commit_index"] > 0)

    assert _in(tmp_path, ["advanced", "fold", "--at", f"op:{non_root}"]) == 1
    assert "fold --at" in capsys.readouterr().out


def test_split_without_apply_previews_groups_and_writes_nothing(tmp_path, capsys):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add foo and bar")
    assert _in(tmp_path, ["log", "--tree", "--refresh"]) == 0  # U14: `map` is now a `log` mode
    capsys.readouterr()
    tree_before = (tmp_path / ".sgt" / "tree" / "tree.json").read_text()

    # feature ids are now content-addressed (`f-<founding op>`, U21) rather than a hardcoded `F0`,
    # so the target is derived from the built tree; a manual split still mints the next `F<n>`.
    from sgt.lens.tree import load as _load_tree
    feature_id = next(nid for nid, nd in _load_tree(tmp_path)["nodes"].items() if not nd["children"])

    # `sgt split <feature>` with no `--apply` *is* the preview -- there is no separate
    # `sgt preview split` path (removed: it duplicated this exact read, see `_preview_verb`).
    assert _in(tmp_path, ["feature", "regroup", "split", feature_id, "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["ok"] is True
    assert view["applied"] is False
    assert (tmp_path / ".sgt" / "tree" / "tree.json").read_text() == tree_before  # preview writes nothing


def test_map_rebuild_forces_a_full_recluster(tmp_path, capsys):
    """`sgt log --tree --rebuild` (the former `sgt map --rebuild`, folded into `log` in U14) is the
    escape hatch out of dirty-subtree splicing (Phase 2): it must at least run without error and
    still produce a valid tree, even though the common no-op-edit case would otherwise splice
    everything through verbatim."""
    _seed(tmp_path, n=2)
    capsys.readouterr()
    assert _in(tmp_path, ["log", "--tree", "--refresh"]) == 0
    capsys.readouterr()
    assert _in(tmp_path, ["log", "--tree", "--rebuild"]) == 0
    out = capsys.readouterr().out
    assert "feature(s)" in out


def test_print_map_tree_drops_phantom_empty_member_leaves(capsys):
    """A feature leaf with no members is a clustering-split artifact (empty child), not a real
    feature -- it and any subsystem left with nothing else to show must not print. A leaf with
    real members but 0 ops (pre-existing code sgt never mined an op for) is still a real feature
    and must still print -- `sgt map` shows what exists, not just what has history."""
    from sgt.cli.inspect import _print_map_tree

    view = {
        "roots": ["N0"],
        "nodes": [
            {"id": "N0", "kind": "subsystem", "label": "root", "op_count": 3,
             "children": ["F1", "N1"], "members": []},
            {"id": "F1", "kind": "feature", "label": "Real Feature", "op_count": 3,
             "children": [], "members": ["a.py::foo"]},
            {"id": "N1", "kind": "subsystem", "label": "phantom subsystem", "op_count": 0,
             "children": ["F2"], "members": []},
            {"id": "F2", "kind": "feature", "label": " ·  2", "op_count": 0,
             "children": [], "members": []},
        ],
        "feature_count": 2,
    }
    _print_map_tree(view)
    out = capsys.readouterr().out
    assert "Real Feature" in out
    assert "phantom subsystem" not in out and "F2" not in out
    assert "1 feature(s)" in out  # not the inflated view["feature_count"] of 2


def test_preview_unknown_verb_or_bad_arity_prints_usage(tmp_path, capsys):
    _seed(tmp_path, n=1)
    capsys.readouterr()
    assert _in(tmp_path, ["advanced", "preview", "not-a-verb"]) == 2
    assert "usage: sgt preview" in capsys.readouterr().out
    assert _in(tmp_path, ["advanced", "preview", "merge", "only-one-arg"]) == 2
    assert "usage: sgt preview" in capsys.readouterr().out


def test_preview_split_is_no_longer_a_verb(tmp_path, capsys):
    """Removed: bare `sgt split <feature>` already previews without `--apply` (see above)."""
    _seed(tmp_path, n=1)
    capsys.readouterr()
    assert _in(tmp_path, ["advanced", "preview", "split", "whatever"]) == 2
    assert "usage: sgt preview" in capsys.readouterr().out


def test_help_mentions_kernel_verbs(capsys):
    main(["help"])
    out = capsys.readouterr().out
    assert "revert" in out and "restore" in out and "oracle" in out and "fsck" in out
    assert "--json" in out


def test_help_mentions_agentic_loop_verbs(capsys):
    # The agentic loop is `sgt plan` + `sgt save --resolve-plan`; checkpoint/drift folded into
    # `save` (U12), so help advertises the loop, not those (now-removed) verbs.
    main(["help"])
    out = capsys.readouterr().out
    assert "advanced" in out
    assert "plan" in out and "resolve-plan" in out


def test_help_mentions_history_and_preview_verbs(capsys):
    # `history`/`preview` moved under the `advanced` grouping; help lists them there.
    main(["help"])
    out = capsys.readouterr().out
    assert "advanced" in out
    assert "history" in out and "preview" in out


def test_unknown_verb_falls_back_to_help(capsys):
    assert main(["nonsense"]) == 0
    assert "sgt —" in capsys.readouterr().out


def test_verbs_is_exactly_the_spine_groupings_and_collaboration_set():
    """R12/KTD8/KTD9 (U14): the grid is the only inspection surface, so the top-level `_VERBS` is
    the daily spine + `log` + the two groupings + the unchanged collaboration/setup verbs.
    `status`/`map`/`graph`/`episodes` collapsed onto `sgt log` render modes; `blame`/`edit`/
    `commit`/`fulfill` demoted under `advanced`. `intent` stays top-level (its subcommands don't
    map to a `log` mode; re-promoted in c4f9966/KTD8)."""
    from sgt.cli import _VERBS

    assert _VERBS == {
        "save", "log", "undo", "revert", "restore", "resolve",
        "switch", "diff", "intent", "now",  # `now` = state-of-actions orient (state block + what-next)
        "plan",  # checkpoint/drift folded into `save` (U12)
        "feature", "advanced",
        "sync", "land", "push", "propose", "session", "init", "mcp",
    }


def test_grouping_verbs_resolve(tmp_path, capsys):
    """Happy path: a bare grouping prints its own subhelp and exits cleanly, and a re-homed verb
    resolves at its new path."""
    assert main(["feature"]) == 0
    assert main(["feature", "regroup"]) == 0
    assert main(["advanced"]) == 0
    _seed(tmp_path, 1)
    _in(tmp_path, ["log"])  # mine so the store isn't empty
    capsys.readouterr()
    assert _in(tmp_path, ["advanced", "fsck", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_revert_keep_selects_which_frontier_dependents_to_retain(tmp_path, capsys):
    """CLI plumbing for the interactive revert frontier (U3/R4): `--keep <id>` retains exactly the
    named dependent, drafting a continuation hollow for its symbol, while `--keep ""` keeps none (a
    plain revert, no hollow). Exercises the argparse `--keep` parsing that the core-level
    `revert_keep_dependents` behavior (tests/core/test_rewrite.py) never sees."""
    from sgt.core.store import Store

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add helper")
    (tmp_path / "b.py").write_text(
        "from a import helper\n\ndef user():\n    return helper() + 1\n", encoding="utf-8"
    )
    gb.commit_all("add user, depending on helper")
    assert _in(tmp_path, ["init", "."]) == 0
    capsys.readouterr()

    ops = Store(tmp_path).all_ops()
    helper_op = next(o for o in ops if "a.py::helper" in o.footprint)
    user_op = next(o for o in ops if "b.py::user" in o.footprint)

    # `--keep <user-op>`: retain the direct dependent -> exactly one continuation hollow.
    assert _in(tmp_path, ["revert", helper_op.id, "--keep", user_op.id, "--json"]) == 0
    kept = json.loads(capsys.readouterr().out)
    assert kept["ok"] and len(kept["hollow_ids"]) == 1

    # `--keep ""`: keep none -> a plain revert, no continuation hollow drafted.
    assert _in(tmp_path, ["revert", helper_op.id, "--keep", "", "--json"]) == 0
    none = json.loads(capsys.readouterr().out)
    assert none["ok"] and none["hollow_ids"] == []


# -- inline revert confirm gate (feedforward graph + `[y/N]` in the normal log flow) -------------
#
# A bare `sgt revert <ref>` on an interactive tty draws the feedforward inline (no modal pane) and
# gates on `[y/N]`. Both isattys are forced so the gate is entered deterministically regardless of
# how pytest was invoked, and `input` is monkeypatched so no real prompt blocks the suite.

def _force_tty(monkeypatch, stdin=True, stdout=True):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: stdout)


def test_revert_on_a_tty_applies_when_the_user_confirms(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, 2)
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "y")

    assert _in(tmp_path, ["revert", "a.py::foo"]) == 0
    assert "applied" in capsys.readouterr().out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"  # reverted


def test_revert_on_a_tty_changes_nothing_when_the_user_declines(tmp_path, monkeypatch, capsys):
    _seed(tmp_path, 2)
    _force_tty(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *a: "n")

    assert _in(tmp_path, ["revert", "a.py::foo"]) == 1
    assert "skipped" in capsys.readouterr().out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_revert_without_tty_or_yes_still_refuses_with_exit_2(tmp_path, monkeypatch, capsys):
    """The machine/degrade contract is byte-unchanged: no tty and no `--yes` prints the feedforward
    and refuses (exit 2), never launching the pane."""
    _seed(tmp_path, 2)
    _force_tty(monkeypatch, stdin=False)

    assert _in(tmp_path, ["revert", "a.py::foo"]) == 2
    assert "not applied — this was the preview" in capsys.readouterr().out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_revert_json_still_applies_immediately_without_the_pane(tmp_path, capsys):
    """`--json` is the immediate-apply machine contract VS Code/tests depend on -- unchanged."""
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "a.py::foo", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["removed"]
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 1\n"  # reverted


# -- feature-reorg gate (merge/rename/move share `_confirm`; split has its own inline path) ------
#
# Metadata verbs touch no code, so their pane renders a `summary` rather than a code rail; the gate
# is `_common.maybe_confirm`, monkeypatched here so no TUI launches. `--json` and the non-tty case
# (maybe_confirm -> None) must keep the immediate-apply / preview-only behaviour byte-for-byte.

def _feature_repo(tmp_path):
    """A repo with one mined feature; returns its content-addressed feature id."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add foo and bar")
    assert _in(tmp_path, ["log", "--tree", "--refresh"]) == 0
    from sgt.lens.tree import load as _load_tree
    return next(nid for nid, nd in _load_tree(tmp_path)["nodes"].items() if not nd["children"])


def test_rename_on_a_tty_applies_when_the_pane_confirms(tmp_path, monkeypatch, capsys):
    from sgt.cli import _common
    from sgt.tui.consequence import Decision

    fid = _feature_repo(tmp_path)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: Decision(True))
    capsys.readouterr()

    assert _in(tmp_path, ["feature", "rename", fid, "Renamed"]) == 0
    assert "renamed" in capsys.readouterr().out
    from sgt.lens.pins import load_pins
    assert load_pins(tmp_path).labels[fid] == "Renamed"


def test_rename_on_a_tty_changes_nothing_when_the_pane_aborts(tmp_path, monkeypatch, capsys):
    from sgt.cli import _common
    from sgt.tui.consequence import Decision

    fid = _feature_repo(tmp_path)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: Decision(False))
    capsys.readouterr()

    assert _in(tmp_path, ["feature", "rename", fid, "Renamed"]) == 1
    assert "skipped" in capsys.readouterr().out
    from sgt.lens.pins import load_pins
    assert "Renamed" not in load_pins(tmp_path).labels.values()  # not applied


def test_rename_json_never_launches_the_pane_and_applies(tmp_path, monkeypatch, capsys):
    from sgt.cli import _common

    fid = _feature_repo(tmp_path)

    def boom(*a, **k):
        raise AssertionError("--json must not launch the consequence pane")

    monkeypatch.setattr(_common, "maybe_confirm", boom)
    capsys.readouterr()

    assert _in(tmp_path, ["feature", "rename", fid, "Renamed", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["new_label"] == "Renamed"


def test_rename_non_tty_keeps_immediate_apply(tmp_path, monkeypatch, capsys):
    """maybe_confirm -> None (no tty / no textual) means proceed: the metadata verb still applies
    immediately, exactly as it did before the pane existed."""
    from sgt.cli import _common

    fid = _feature_repo(tmp_path)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: None)
    capsys.readouterr()

    assert _in(tmp_path, ["feature", "rename", fid, "Renamed"]) == 0
    from sgt.lens.pins import load_pins
    assert load_pins(tmp_path).labels[fid] == "Renamed"


def test_split_on_a_tty_applies_when_the_pane_confirms(tmp_path, monkeypatch, capsys):
    from sgt.cli import _common
    from sgt.tui.consequence import Decision

    fid = _feature_repo(tmp_path)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: Decision(True))
    capsys.readouterr()

    # No --apply: on a tty the pane *is* the confirm, so a confirm splits.
    assert _in(tmp_path, ["feature", "regroup", "split", fid]) == 0
    assert "split" in capsys.readouterr().out
    from sgt.lens.tree import load as _load_tree
    leaves = [nid for nid, nd in _load_tree(tmp_path)["nodes"].items() if not nd["children"]]
    assert len(leaves) == 2  # actually split


def test_split_non_tty_still_prints_the_preview_only(tmp_path, monkeypatch, capsys):
    from sgt.cli import _common

    fid = _feature_repo(tmp_path)
    monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: None)
    tree_before = (tmp_path / ".sgt" / "tree" / "tree.json").read_text()
    capsys.readouterr()

    assert _in(tmp_path, ["feature", "regroup", "split", fid]) == 0
    assert "preview only" in capsys.readouterr().out
    assert (tmp_path / ".sgt" / "tree" / "tree.json").read_text() == tree_before  # nothing applied
