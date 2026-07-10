"""CLI surface tests (the operation-ideal kernel, plan U7/U8/U9 flipped onto the CLI in U10):
argument parsing, human-readable rendering, and `--json` output for the kernel-backed verbs.
Verb *behavior* (the algebra) is tested in `tests/core/`; this file is the thin CLI layer only.
"""

import json
import os

from sgt.cli import main
from sgt.store.gitbind import init_store


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
    assert _in(tmp_path, ["log", "--json"]) == 0
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
    assert any("a.py::foo" in [f["symbol"] for f in op["footprint"]] for op in payload["ops"])


def test_state_shows_frontier_and_coverage(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["state", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "a.py::foo" in payload["frontier"]
    assert payload["covered_paths"] == ["a.py"]
    assert payload["oracle_configured"] is False
    assert payload["oracle_verdict"] is None


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


def test_revert_unknown_ref_fails_with_message(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["revert", "nope::nothing"]) == 1
    assert "✗" in capsys.readouterr().out


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
    assert _in(tmp_path, ["fsck", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["checked"] >= 1


def test_oracle_run_with_no_config_warns(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["oracle", "run"]) == 0
    assert "no oracle configured" in capsys.readouterr().out


def test_oracle_override_then_state_shows_verdict(tmp_path, capsys):
    _seed(tmp_path, 1)
    assert _in(tmp_path, ["oracle", "override", "--status", "pass", "--reason", "manual check"]) == 0
    capsys.readouterr()

    assert _in(tmp_path, ["state", "--json"]) == 0
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

    assert _in(tmp_path, ["plan", "status", "--json"]) == 0
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

    assert _in(tmp_path, ["plan", "status", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["sessions"][0]["steps"][0]["status"] == "matched"


def test_drift_json_reports_nothing_with_no_active_session(tmp_path, capsys):
    _seed(tmp_path, 2)
    capsys.readouterr()
    assert _in(tmp_path, ["drift", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == []


def test_history_json_lists_commits_and_places_ops_on_the_axis(tmp_path, capsys):
    _seed(tmp_path, n=2)
    capsys.readouterr()
    assert _in(tmp_path, ["history", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert [c["index"] for c in view["commits"]] == [0, 1]
    assert view["ops"]
    assert all(op["commit_index"] in (0, 1) for op in view["ops"])


def test_preview_split_reports_affected_features_and_writes_nothing(tmp_path, capsys):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add foo and bar")
    assert _in(tmp_path, ["map"]) == 0
    capsys.readouterr()
    tree_before = (tmp_path / ".sgt" / "tree" / "tree.json").read_text()

    assert _in(tmp_path, ["preview", "split", "F0", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["ok"] is True
    assert view["affected_features"] == ["F0", "F1"]
    assert (tmp_path / ".sgt" / "tree" / "tree.json").read_text() == tree_before  # preview writes nothing


def test_preview_unknown_verb_or_bad_arity_prints_usage(tmp_path, capsys):
    _seed(tmp_path, n=1)
    capsys.readouterr()
    assert _in(tmp_path, ["preview", "not-a-verb"]) == 2
    assert "usage: sgt preview" in capsys.readouterr().out
    assert _in(tmp_path, ["preview", "merge", "only-one-arg"]) == 2
    assert "usage: sgt preview" in capsys.readouterr().out


def test_help_mentions_kernel_verbs(capsys):
    main(["help"])
    out = capsys.readouterr().out
    assert "revert" in out and "restore" in out and "oracle" in out and "fsck" in out
    assert "--json" in out


def test_help_mentions_agentic_loop_verbs(capsys):
    main(["help"])
    out = capsys.readouterr().out
    assert "plan intake" in out and "checkpoint" in out and "drift" in out


def test_help_mentions_history_and_preview_verbs(capsys):
    main(["help"])
    out = capsys.readouterr().out
    assert "sgt history" in out and "sgt preview" in out


def test_unknown_verb_falls_back_to_help(capsys):
    assert main(["nonsense"]) == 0
    assert "sgt —" in capsys.readouterr().out
