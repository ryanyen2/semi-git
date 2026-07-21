"""CLI surface tests (the operation-ideal kernel, plan U7/U8/U9 flipped onto the CLI in U10):
argument parsing, human-readable rendering, and `--json` output for the kernel-backed verbs.
Verb *behavior* (the algebra) is tested in `tests/core/`; this file is the thin CLI layer only.
"""

import json
import os
from types import SimpleNamespace

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
    assert _in(tmp_path, ["log", "--json", "--full"]) == 0
    payload = json.loads(capsys.readouterr().out)
    foo_ops = [op for op in payload["ops"] if "a.py::foo" in [f["symbol"] for f in op["footprint"]]]
    # v1 (pre-horizon) is never mined at all; v2 becomes one root "add", v3 one modify on top --
    # not the 3-op chain a horizon-less init would produce.
    assert len(foo_ops) == 2
    assert {op["kind"] for op in foo_ops} == {"add", "rework"}


def test_init_and_log_roundtrip(tmp_path, capsys):
    _seed(tmp_path, 1)
    capsys.readouterr()  # drain seed commit output (none, but keep symmetry with other tests)
    assert _in(tmp_path, ["log"]) == 0
    out = capsys.readouterr().out
    assert "op(s)" in out and "a.py::foo" in out


def test_log_json_is_machine_readable(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["log", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] >= 1
    assert any("a.py::foo" in op["symbols"] for op in payload["ops"])


def test_log_json_full_is_machine_readable(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["log", "--json", "--full"]) == 0
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


def test_reindex_json_reports_op_count(tmp_path, capsys):
    _seed(tmp_path, 2)
    _in(tmp_path, ["log"])  # mine, so the store isn't empty
    capsys.readouterr()

    assert _in(tmp_path, ["advanced", "reindex", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["op_count"] >= 1


def test_reindex_text_reports_op_count(tmp_path, capsys):
    _seed(tmp_path, 2)
    _in(tmp_path, ["log"])
    capsys.readouterr()

    assert _in(tmp_path, ["advanced", "reindex"]) == 0
    out = capsys.readouterr().out
    assert "reindex" in out and "op(s) indexed" in out


def test_log_json_limit_and_offset_flags(tmp_path, capsys):
    _seed(tmp_path, 3)  # a.py::foo v1/v2/v3, at least 3 distinct ops
    capsys.readouterr()

    assert _in(tmp_path, ["log", "--json"]) == 0
    total = json.loads(capsys.readouterr().out)["count"]
    assert total > 1

    assert _in(tmp_path, ["log", "--json", "--limit", "1", "--offset", "0"]) == 0
    first_page = json.loads(capsys.readouterr().out)
    assert len(first_page["ops"]) == 1

    assert _in(tmp_path, ["log", "--json", "--limit", str(total), "--offset", "1"]) == 0
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


def test_drift_json_cli_matches_view_at_default_and_full(tmp_path, capsys):
    """R21 guardrail for `drift`, mirroring `test_history_json_cli_matches_view_at_default_and_full`."""
    from sgt.api import drift_view
    from sgt.core.lens import get

    _seed(tmp_path, 2)
    get(tmp_path)
    capsys.readouterr()

    assert _in(tmp_path, ["drift", "--json"]) == 0
    assert capsys.readouterr().out.rstrip("\n") == json.dumps(drift_view(str(tmp_path)), indent=2)

    assert _in(tmp_path, ["drift", "--json", "--full"]) == 0
    assert capsys.readouterr().out.rstrip("\n") == json.dumps(drift_view(str(tmp_path), full=True), indent=2)


def test_revert_emit_previews_without_writing(tmp_path, capsys):
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "--emit", "a.py::foo"]) == 0
    out = capsys.readouterr().out
    assert "[revert] a.py::foo" in out
    assert (tmp_path / "a.py").read_text() == "def foo():\n    return 2\n"  # untouched


def test_revert_then_restore_roundtrip(tmp_path, capsys):
    _seed(tmp_path, 2)
    assert _in(tmp_path, ["revert", "a.py::foo"]) == 0
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
    assert _in(tmp_path, ["log", "--json"]) == 0
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
    assert _in(tmp_path, ["revert", "a.py::foo"]) == 0
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
    assert _in(tmp_path, ["revert", "a.py::foo"]) == 0
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


def test_checkpoint_preview_then_confirm(tmp_path, capsys, monkeypatch):
    """A session predicting `a.py::foo` previews a match against a real follow-up edit to it,
    then `--confirm-hollow/--confirm-op` applies exactly that group."""
    from pathlib import Path

    from sgt.core.op import make_op
    from sgt.core.store import Store
    from sgt.loop import plan as plan_mod

    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    gb = _seed(tmp_path, 1)  # a.py::foo == "return 1"
    store = Store(tmp_path)
    baseline = sorted(op.id for op in store.all_ops())

    footprint = {"a.py::foo": (None, plan_mod._PENDING), "__plan__::s1::step0": (None, plan_mod._PENDING)}
    hollow = make_op(footprint, {}, kind="planned", off_chain=True, intent="touch foo")
    store.add_hollow(hollow)
    table = plan_mod._load_sessions(Path(tmp_path))
    table["s1"] = {
        "plan_text": "1. touch foo\n", "created_ts": 0.0, "last_activity_ts": 0.0, "status": "active",
        "baseline_op_ids": baseline,
        "steps": [{
            "hollow_id": hollow.id, "title": "touch foo", "predicted_footprint": ["a.py::foo"],
            "predicted_feature": None, "rationale": "", "status": "pending", "matched_op_ids": [],
        }],
    }
    plan_mod._save_sessions(Path(tmp_path), table)

    (tmp_path / "a.py").write_text("def foo():\n    return 99\n", encoding="utf-8")
    gb.commit_all("touch foo")
    capsys.readouterr()

    assert _in(tmp_path, ["checkpoint", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert len(preview["matches"]) == 1
    group = preview["matches"][0]

    argv = ["checkpoint", "--json"]
    for hid in group["hollow_ids"]:
        argv += ["--confirm-hollow", hid]
    for oid in group["op_ids"]:
        argv += ["--confirm-op", oid]
    assert _in(tmp_path, argv) == 0
    confirmed = json.loads(capsys.readouterr().out)
    assert confirmed["session_id"] == "s1"

    assert _in(tmp_path, ["plan", "status", "--json", "--full"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["sessions"][0]["steps"][0]["status"] == "matched"


def test_drift_json_reports_nothing_with_no_active_session(tmp_path, capsys):
    _seed(tmp_path, 2)
    capsys.readouterr()
    assert _in(tmp_path, ["drift", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["op_ids"] == []


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
    assert _in(tmp_path, ["map"]) == 0
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
    """`sgt map --rebuild` is the escape hatch out of dirty-subtree splicing (Phase 2): it must at
    least run without error and still produce a valid tree, even though the common no-op-edit case
    would otherwise splice everything through verbatim."""
    _seed(tmp_path, n=2)
    capsys.readouterr()
    assert _in(tmp_path, ["map"]) == 0
    capsys.readouterr()
    assert _in(tmp_path, ["map", "--rebuild"]) == 0
    out = capsys.readouterr().out
    assert "feature(s)" in out


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
    # The agentic-loop verbs are re-homed under the `advanced` grouping (KTD2); help advertises
    # them there rather than at the top level.
    main(["help"])
    out = capsys.readouterr().out
    assert "advanced" in out
    assert "plan" in out and "checkpoint" in out and "drift" in out


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
    """R2/KTD2 (re-triaged): the top-level `_VERBS` is exactly the daily spine + the daily
    navigation/inspection/loop/rewrite verbs + the two groupings + the unchanged collaboration/setup
    verbs. Only rare/maintenance verbs live under `advanced`."""
    from sgt.cli import _VERBS

    assert _VERBS == {
        "save", "status", "log", "undo", "revert", "restore", "edit",
        "switch", "diff", "map", "graph", "episodes", "blame", "intent",
        "plan", "checkpoint", "drift",
        "commit", "fulfill",
        "feature", "advanced",
        "sync", "land", "push", "propose", "session", "init", "mcp",
    }


def test_removed_top_level_verb_points_to_its_new_home(capsys):
    """A removed-but-known old verb errors with a clear pointer to its new home (KTD2's hard
    rename, no alias layer); a genuinely unknown token still falls to `_help()`."""
    assert main(["merge-op"]) == 2
    assert "advanced merge-op" in capsys.readouterr().err
    assert main(["reindex"]) == 2
    assert "advanced reindex" in capsys.readouterr().err
    assert main(["state"]) == 2
    assert "advanced state" in capsys.readouterr().err
    assert main(["merge"]) == 2  # re-homed two levels deep under `feature regroup`
    assert "feature regroup merge" in capsys.readouterr().err


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
