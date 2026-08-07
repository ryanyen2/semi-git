"""Plan-session ownership and concurrency (Phase 1 items 4/6/7).

Several coding agents share one repo, and every plan id is readable by all of them
(`sgt plan status`). Two failures follow from that, and both are silent:

* **clobbering** -- agent B closes or abandons A's plan. `mark_done`/`abandon` unlink the pending
  hollows, so A keeps working, its checkpoints stop matching, and nothing says why.
* **lost writes** -- `plan_sessions.json` is one whole-table JSON file mutated read-modify-write, so
  two simultaneous intakes drop one of the two plans entirely.

The ownership check fixes the first and `plan_lock` the second. `adopt` is what keeps the check from
converting every crashed agent into a plan nobody may ever close.
"""

from __future__ import annotations

import json
import os

import pytest

from sgt.cli import main
from sgt.loop import plan as plan_mod
from sgt.loop.match import confirm_match
from sgt.store.gitbind import init_store


def _no_client(*args, **kwargs):
    raise RuntimeError("offline: force the deterministic fallback decomposition")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod, "get_client", _no_client)
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("foo v1")
    return tmp_path


def _intake(repo, owner: str | None, text: str = "1. do a thing\n"):
    return plan_mod.intake(repo, text, claude_session_id=owner)


# -- the check ---------------------------------------------------------------------------------


def test_another_agent_cannot_close_or_abandon_your_plan(repo):
    """The core protection. Both verbs unlink pending hollows, so both must refuse."""
    session = _intake(repo, "agent-A")

    for verb in (plan_mod.mark_done, plan_mod.abandon):
        with pytest.raises(plan_mod.PlanOwnershipError) as excinfo:
            verb(repo, session.session_id, actor="agent-B")
        # The error has to be actionable: it names who owns it and how to proceed.
        assert "agent-A" in str(excinfo.value)
        assert "sgt plan adopt" in str(excinfo.value)

    # And nothing was destroyed by the refused call.
    assert session.session_id in plan_mod.active_sessions(repo)
    assert plan_mod.owner_of(repo, session.session_id) == "agent-A"


def test_another_agent_cannot_confirm_into_your_plan(repo):
    """A confirm consumes the hollow and credits the plan with work -- so it is destructive to the
    owner in both directions, not just bookkeeping."""
    session = _intake(repo, "agent-A")
    hollow = session.steps[0]["hollow_id"]

    with pytest.raises(plan_mod.PlanOwnershipError):
        confirm_match(repo, session.session_id, [hollow], ["deadbeef"], actor="agent-B")

    steps = plan_mod._load_sessions(repo)[session.session_id]["steps"]
    assert steps[0]["status"] == "pending"  # untouched


def test_the_owner_is_allowed(repo):
    session = _intake(repo, "agent-A")
    assert plan_mod.mark_done(repo, session.session_id, actor="agent-A") is True


def test_a_human_with_no_session_id_is_allowed(repo):
    """The CLI has no Claude session id. Refusing there would mean a human could never close an
    agent's plan in their own repo -- so `actor=None` is always permitted."""
    session = _intake(repo, "agent-A")
    assert plan_mod.abandon(repo, session.session_id, actor=None) is True


def test_an_unowned_plan_is_claimable_by_anyone(repo):
    """A plan intook from the CLI (or by an agent that couldn't read its own id) has no owner, so
    there is no one to protect it from."""
    session = _intake(repo, None)
    assert plan_mod.mark_done(repo, session.session_id, actor="agent-B") is True


def test_housekeeping_sweeps_are_not_ownership_checked(repo):
    """The sweeps have no session identity to present. If the check applied to them, an owned plan
    could never be reaped and the stale-plan cleanup would silently stop working."""
    session = _intake(repo, "agent-A")
    table = plan_mod._load_sessions(repo)
    table[session.session_id]["last_activity_ts"] = 0.0
    plan_mod._save_sessions(repo, table)

    assert plan_mod.sweep_stale_sessions(repo, plan_mod.STALE_SECONDS) == [session.session_id]


# -- adopt -------------------------------------------------------------------------------------


def test_adopt_transfers_ownership_without_destroying_progress(repo):
    """The whole point of adopt: a dead agent's plan becomes workable again, with its steps and
    confirmed matches intact. Re-intaking instead would mint duplicate hollows for done work."""
    session = _intake(repo, "agent-A", "1. first thing\n2. second thing\n")
    before = plan_mod._load_sessions(repo)[session.session_id]["steps"]

    ok, previous = plan_mod.adopt(repo, session.session_id, "agent-B")
    assert ok and previous == "agent-A"
    assert plan_mod.owner_of(repo, session.session_id) == "agent-B"

    after = plan_mod._load_sessions(repo)[session.session_id]["steps"]
    assert [s["hollow_id"] for s in after] == [s["hollow_id"] for s in before]
    assert [s["status"] for s in after] == [s["status"] for s in before]
    # And the new owner may now do what it was refused before.
    assert plan_mod.mark_done(repo, session.session_id, actor="agent-B") is True


def test_adopt_reports_an_unknown_session_rather_than_inventing_one(repo):
    ok, previous = plan_mod.adopt(repo, "no-such-session", "agent-B")
    assert ok is False and previous is None


# -- the lock ----------------------------------------------------------------------------------


def test_concurrent_intakes_do_not_lose_a_plan(repo):
    """Two agents intaking at once. Without the lock, the second load-mutate-save overwrites the
    first and one agent's plan simply never existed. Serialized through the real flock in
    subprocess-free form: threads, since flock is per-open-file-description and the failure being
    guarded is the read-modify-write interleave."""
    import threading

    ids, errors = [], []

    def worker(n: int):
        try:
            ids.append(plan_mod.intake(repo, f"{n}. step\n",
                                       claude_session_id=f"agent-{n}").session_id)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    table = plan_mod._load_sessions(repo)
    assert len(ids) == 6
    for sid in ids:
        assert sid in table, "an intake was lost to a concurrent write"


def test_sweeps_do_not_deadlock_on_the_non_reentrant_lock(repo):
    """`intake` sweeps stale sessions, and the sweeps mutate the same table. flock is
    non-reentrant, so a nested public call would hang forever rather than fail -- which is why the
    sweeps use the `_*_locked` internals. A hang here is the regression."""
    old = _intake(repo, "agent-A")
    table = plan_mod._load_sessions(repo)
    table[old.session_id]["last_activity_ts"] = 0.0
    plan_mod._save_sessions(repo, table)

    fresh = _intake(repo, "agent-B")  # triggers sweep_stale_sessions from inside intake
    assert fresh.session_id in plan_mod.active_sessions(repo)
    assert old.session_id not in plan_mod._load_sessions(repo)

    # `sweep_built_sessions` -> the mark_done body is likewise inlined under one lock.
    assert isinstance(plan_mod.sweep_built_sessions(repo), list)


# -- the CLI surface ---------------------------------------------------------------------------


def _cli(repo, argv):
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def test_cli_close_refuses_with_the_owner_named(repo, capsys):
    session = _intake(repo, "agent-A")
    capsys.readouterr()
    rc = _cli(repo, ["plan", "done", session.session_id, "--claude-session", "agent-B"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "agent-A" in out and "sgt plan adopt" in out


def test_cli_adopt_then_close_succeeds(repo, capsys):
    session = _intake(repo, "agent-A")
    capsys.readouterr()
    assert _cli(repo, ["plan", "adopt", session.session_id,
                       "--claude-session", "agent-B", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["previous_owner"] == "agent-A"

    assert _cli(repo, ["plan", "done", session.session_id, "--claude-session", "agent-B"]) == 0


def test_cli_resume_shows_remaining_steps_and_how_to_get_back(repo, capsys):
    """What was missing for a stalled plan was orientation, not machinery: which steps remain, and
    the handle for the conversation that was building it."""
    session = _intake(repo, "chat-uuid-123", "1. first thing\n2. second thing\n")
    capsys.readouterr()

    assert _cli(repo, ["plan", "resume", session.session_id]) == 0
    out = capsys.readouterr().out
    assert "first thing" in out and "second thing" in out
    assert "claude --resume chat-uuid-123" in out


def test_cli_resume_with_no_argument_picks_the_only_plan(repo, capsys):
    _intake(repo, "chat-1")
    capsys.readouterr()
    assert _cli(repo, ["plan", "resume"]) == 0
    assert "next:" in capsys.readouterr().out


def test_cli_resume_asks_which_when_several_are_active(repo, capsys):
    """Ambiguity must be an error that lists the choices -- not a silent pick of one."""
    a = _intake(repo, "chat-1", "1. alpha\n")
    b = _intake(repo, "chat-2", "1. beta\n")
    capsys.readouterr()
    rc = _cli(repo, ["plan", "resume"])
    assert rc == 2
    out = capsys.readouterr().out
    assert a.session_id in out and b.session_id in out


def test_plan_usage_lists_every_subcommand_it_accepts(repo, capsys):
    """A bad subcommand prints usage and exits 2 (never 0), and the usage must name the verbs that
    actually dispatch -- `resume`/`adopt` included."""
    capsys.readouterr()
    assert _cli(repo, ["plan", "bogus"]) == 2
    usage = capsys.readouterr().out
    for sub in ("intake", "status", "resume", "adopt", "done", "abandon"):
        assert sub in usage
