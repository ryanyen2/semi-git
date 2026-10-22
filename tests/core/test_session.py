"""Tests for `ugh.core.session` -- the U30/D5 scratch-tree session layer.

Each session is a real `git worktree` nested under `.sgt/local/sessions/<name>` (gitignored, so
it never shows up in the main tree's own `git status`), checked out on its own
`ugh-session/<name>` branch. `land` reuses the U23 CAS `ugh.core.sync.land` verbatim, run against
the scratch tree (worktrees share one ref store, so the CAS is against the one shared branch
record regardless of which worktree issues it); `gc` reaps sessions whose owning pid has died.
"""

from __future__ import annotations

import os

from ugh import state
from ugh.core import lens
from ugh.core import session as session_mod
from ugh.core.lens import get
from ugh.core.store import Store, fsck
from ugh.store.gitbind import init_store


def _seed_repo(root, oracle_cmd="exit 0"):
    gb, _ = init_store(root)
    if oracle_cmd is not None:
        state.save_json(root, "oracle_config", {"tiers": [{"name": "gate", "command": oracle_cmd}]})
    (root / "main.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("init")
    ideal = get(root)
    put_sha = lens.put(root, ideal, message="ugh: init")
    lens.record_ideal(root, ideal, put_sha)
    return gb


def _write_and_commit(scratch, path, content):
    from ugh.store.gitbind import GitBinding

    (scratch / path).write_text(content, encoding="utf-8")
    GitBinding(scratch).commit_all(f"edit {path}")
    ideal = get(scratch)
    put_sha = lens.put(scratch, ideal, message=f"ugh: edit {path}")
    lens.record_ideal(scratch, ideal, put_sha)


def test_start_creates_a_worktree_on_its_own_branch(tmp_path):
    _seed_repo(tmp_path)

    session = session_mod.start(tmp_path, "s1")

    assert session.name == "s1"
    assert session.branch == "ugh-session/s1"
    assert session.target_branch == "main"
    assert os.path.isdir(session.scratch)
    assert session.scratch.startswith(str(tmp_path / ".sgt" / "local" / "sessions"))
    assert session_mod.list_sessions(tmp_path) == (session,)


def test_start_refuses_a_name_collision(tmp_path):
    _seed_repo(tmp_path)
    session_mod.start(tmp_path, "s1")

    try:
        session_mod.start(tmp_path, "s1")
        assert False, "expected a SessionError"
    except session_mod.SessionError as e:
        assert "already exists" in str(e)


def test_two_sessions_touching_the_same_symbol_are_reported_as_overlapping(tmp_path):
    """The early-fork warning (D5): two sessions edit the same symbol independently, and
    `overlaps` names them and the shared symbol *before* either lands -- a report, not a lock."""
    _seed_repo(tmp_path)
    from pathlib import Path

    s1 = session_mod.start(tmp_path, "s1")
    s2 = session_mod.start(tmp_path, "s2")

    _write_and_commit(Path(s1.scratch), "main.py", "def foo():\n    return 2\n")
    _write_and_commit(Path(s2.scratch), "main.py", "def foo():\n    return 3\n")

    found = session_mod.overlaps(tmp_path)
    assert len(found) == 1
    pair = found[0]
    assert {pair["a"], pair["b"]} == {"s1", "s2"}
    assert pair["symbols"] == ["main.py::foo"]


def test_independent_sessions_do_not_overlap(tmp_path):
    _seed_repo(tmp_path)
    from pathlib import Path

    s1 = session_mod.start(tmp_path, "s1")
    s2 = session_mod.start(tmp_path, "s2")

    _write_and_commit(Path(s1.scratch), "a.py", "def alpha():\n    return 1\n")
    _write_and_commit(Path(s2.scratch), "b.py", "def beta():\n    return 2\n")

    assert session_mod.overlaps(tmp_path) == ()


def test_land_advances_the_target_branch_and_stamps_session_attribution(tmp_path):
    """Provenance of a landed op names the session (the plan's own test scenario): once `land`
    succeeds, the *main* repo's own store -- not just the scratch tree's -- carries
    `Attribution(session="s1")` on the op the session minted."""
    from pathlib import Path

    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")
    expected_new_ops = len(session_mod.new_op_ids(session))

    report = session_mod.land(tmp_path, "s1")

    assert report.landed, report.blocked_reason
    assert report.ops_added == expected_new_ops

    get(tmp_path)  # absorb the landing commit into the main repo's store
    store = Store(tmp_path)
    # The landed *add* op carries the attribution. Match by the attribution itself, not by
    # `next(any b.py::bar op)`: mining the main repo's still-stale working tree (the land advanced
    # the ref, not this checkout) also mints a transient pending drift-prune of the symbol, and
    # which of the two sorts first by content-address id is not something to assert on.
    attributed = [
        op for op in store.all_ops()
        if "b.py::bar" in op.footprint and any(a.session == "s1" for a in op.attribution)
    ]
    assert attributed, "the landed op should carry the session's attribution in the main store"

    # land is the terminal step: the scratch worktree and session record are both gone.
    assert session_mod.list_sessions(tmp_path) == ()
    assert not Path(session.scratch).exists()


def test_land_refused_by_a_red_oracle_leaves_the_session_intact(tmp_path):
    _seed_repo(tmp_path, oracle_cmd="exit 1")
    from pathlib import Path

    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")

    report = session_mod.land(tmp_path, "s1")

    assert not report.landed
    assert session_mod.list_sessions(tmp_path) == (session,)
    assert Path(session.scratch).is_dir()


def test_gc_reaps_a_session_whose_owning_pid_is_dead_and_fsck_stays_clean(tmp_path):
    import subprocess
    import sys
    from dataclasses import replace
    from pathlib import Path

    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    dead_pid = dead.pid
    sessions = session_mod._load(tmp_path)
    sessions["s1"] = replace(sessions["s1"], owner_pid=dead_pid)
    session_mod._save(tmp_path, sessions)

    reaped = session_mod.gc(tmp_path)

    assert reaped == ("s1",)
    assert session_mod.list_sessions(tmp_path) == ()
    assert not Path(session.scratch).exists()
    assert fsck(tmp_path).ok


def test_gc_leaves_a_live_session_alone_unless_forced(tmp_path):
    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")

    assert session_mod.gc(tmp_path) == ()
    assert session_mod.list_sessions(tmp_path) == (session,)

    assert session_mod.gc(tmp_path, force=True) == ("s1",)
    assert session_mod.list_sessions(tmp_path) == ()


def _kill_pid_of(tmp_path, name):
    """Point a session's owning pid at a process that has already exited, so `is_alive` reads it
    as dead -- the crashed-agent state `gc` triages against."""
    import subprocess
    import sys
    from dataclasses import replace

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    sessions = session_mod._load(tmp_path)
    sessions[name] = replace(sessions[name], owner_pid=dead.pid)
    session_mod._save(tmp_path, sessions)


def test_gc_refuses_to_reap_a_dead_session_with_uncommitted_work(tmp_path):
    """F19 safety (0.4): liveness=getppid can wrongly mark a live agent's session dead, so `gc`
    must not destroy a scratch tree that still holds uncommitted edits -- only `--force` reaps it."""
    from pathlib import Path

    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    (Path(session.scratch) / "wip.py").write_text("def wip():\n    return 0\n", encoding="utf-8")
    _kill_pid_of(tmp_path, "s1")

    assert session_mod.pending_work(session_mod._require(tmp_path, "s1")) == ("uncommitted changes",)

    assert session_mod.gc(tmp_path) == ()  # dirty -> refused
    assert [s.name for s in session_mod.list_sessions(tmp_path)] == ["s1"]
    assert Path(session.scratch).exists()

    assert session_mod.gc(tmp_path, force=True) == ("s1",)
    assert not Path(session.scratch).exists()


def test_gc_refuses_to_reap_a_dead_session_with_unlanded_commits(tmp_path):
    """F19 safety (0.4): a committed-but-not-yet-landed scratch tree is also work worth keeping."""
    from pathlib import Path

    _seed_repo(tmp_path)
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "b.py", "def bar():\n    return 5\n")
    _kill_pid_of(tmp_path, "s1")

    assert "unlanded commits" in session_mod.pending_work(session_mod._require(tmp_path, "s1"))

    assert session_mod.gc(tmp_path) == ()  # unlanded -> refused
    assert Path(session.scratch).exists()

    assert session_mod.gc(tmp_path, force=True) == ("s1",)
    assert not Path(session.scratch).exists()


def test_stale_sessions_reports_without_reaping(tmp_path):
    import subprocess
    import sys
    from dataclasses import replace

    _seed_repo(tmp_path)
    session_mod.start(tmp_path, "s1")

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    sessions = session_mod._load(tmp_path)
    sessions["s1"] = replace(sessions["s1"], owner_pid=dead.pid)
    session_mod._save(tmp_path, sessions)

    stale = session_mod.stale_sessions(tmp_path)
    assert [s.name for s in stale] == ["s1"]
    assert len(session_mod.list_sessions(tmp_path)) == 1  # a read view, never reaps


def test_a_session_started_after_a_revert_on_its_branch_owns_none_of_the_base_history(tmp_path):
    """The scratch tree re-mines the shared history into its own store, and an ideal is not a
    function of the code alone: after `ugh revert` on the target branch the main ideal holds the
    verb's authored ops, while the scratch holds what mining that commit yields. Diffing the two
    reported five ops nobody in the session wrote (`main.py::bar` among them), and `land` would
    have stamped the session's name onto them."""
    from ugh.core import verbs

    gb = _seed_repo(tmp_path)
    (tmp_path / "main.py").write_text("def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("add bar")
    get(tmp_path)
    assert verbs.revert(tmp_path, "main.py::bar").ok

    session = session_mod.start(tmp_path, "s1")

    assert session_mod.new_op_ids(session) == frozenset()


def test_sessions_view_names_the_lanes_a_session_touched_without_sentinels(tmp_path):
    """K9: each session lists the map lanes its own work touched, by the symbols a reader sees.
    `foo` is on a lane of the main map; `baz` is new, so no lane owns it yet (`feature_id` None);
    the import the edit adds is bookkeeping and never reaches the list. `change_count` counts a
    symbol once per save that touched it, the scratch tree's uncommitted work being one save."""
    from pathlib import Path

    from ugh.api import map_view, sessions_view
    from ugh.lens.map import build_map

    _seed_repo(tmp_path)
    build_map(tmp_path)
    foo_lane = next(n["id"] for n in map_view(tmp_path)["nodes"] if "main.py::foo" in n["own_symbols"])

    session = session_mod.start(tmp_path, "s1")
    scratch = Path(session.scratch)
    _write_and_commit(scratch, "main.py",
                      "import os\n\n\ndef foo():\n    return 2\n\n\ndef baz():\n    return os.sep\n")
    (scratch / "main.py").write_text(
        "import os\n\n\ndef foo():\n    return 3\n\n\ndef baz():\n    return os.sep\n", encoding="utf-8")

    (row,) = sessions_view(tmp_path)["sessions"]

    assert row["touched"] == [
        {"feature_id": foo_lane, "symbols": ["main.py::foo"]},
        {"feature_id": None, "symbols": ["main.py::baz"]},
    ]
    assert row["symbols_truncated"] is False
    assert row["change_count"] == 3  # foo: the commit + the uncommitted edit; baz: the commit
    assert row["new_op_count"] > row["change_count"]  # the rest is layout bookkeeping


def test_a_session_started_under_claude_code_lives_as_long_as_claude_and_names_its_chat(tmp_path, monkeypatch):
    """An agent runs `ugh session start` from a tool shell that exits with the command, so
    `getppid()` read dead at once. Under Claude Code the owner is `CLAUDE_PID` (the long-lived
    `claude` process), and `CLAUDE_CODE_SESSION_ID` is kept as the key a plan records too. A record
    written before the field existed still loads."""
    import subprocess
    import sys

    from ugh.api import sessions_view

    _seed_repo(tmp_path)
    claude = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        monkeypatch.setenv("CLAUDE_PID", str(claude.pid))
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "chat-uuid-1")
        session_mod.start(tmp_path, "s1")

        (row,) = sessions_view(tmp_path)["sessions"]
        assert (row["owner_pid"], row["alive"], row["claude_session_id"]) == (claude.pid, True, "chat-uuid-1")
    finally:
        claude.kill()
        claude.wait()
    assert sessions_view(tmp_path)["sessions"][0]["alive"] is False

    raw = state.load_json(tmp_path, "sessions", {})
    del raw["s1"]["claude_session_id"]
    state.save_json(tmp_path, "sessions", raw)
    assert session_mod.list_sessions(tmp_path)[0].claude_session_id is None


def test_sessions_view_reports_a_symbol_the_target_branch_changed_under_a_session(tmp_path):
    """`overlaps` also names what `land` will have to reconcile with the branch itself: a symbol
    the session changed that main changed too since the session's base, as `b: <branch>` with
    `branch: true`. A symbol only main changed is no collision. A report, never a lock (S6)."""
    from pathlib import Path

    from ugh.api import sessions_view

    def main_entries():
        return [o for o in sessions_view(tmp_path)["overlaps"] if o.get("branch")]

    _seed_repo(tmp_path)
    _write_and_commit(tmp_path, "main.py", "def f():\n    return 1\n\n\ndef g():\n    return 1\n")
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "main.py", "def f():\n    return 2\n\n\ndef g():\n    return 1\n")

    _write_and_commit(tmp_path, "main.py", "def f():\n    return 1\n\n\ndef g():\n    return 3\n")
    assert main_entries() == []

    _write_and_commit(tmp_path, "main.py", "def f():\n    return 4\n\n\ndef g():\n    return 3\n")
    assert main_entries() == [{"a": "s1", "b": "main", "branch": True, "symbols": ["main.py::f"]}]


def test_sessions_view_says_who_started_a_session_what_it_holds_and_when_it_last_changed(tmp_path, monkeypatch):
    """`alive` is only the process fact, so each row also says who started the session (Claude Code
    or a terminal), what it holds that its branch lacks (unlanded commits, uncommitted source), and
    when it last changed -- the activity a surface marks. An older record has no `started_by`."""
    import time
    from pathlib import Path

    from ugh.api import sessions_view

    _seed_repo(tmp_path)
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
    agent = session_mod.start(tmp_path, "agent")
    monkeypatch.delenv("CLAUDE_PID")
    session_mod.start(tmp_path, "human")
    _write_and_commit(Path(agent.scratch), "b.py", "def bar():\n    return 5\n")
    time.sleep(1.1)  # past the 1 s commit-time granularity, so the edit is strictly newest
    (Path(agent.scratch) / "b.py").write_text("def bar():\n    return 6\n", encoding="utf-8")
    edited = (Path(agent.scratch) / "b.py").stat().st_mtime

    rows = {r["name"]: r for r in sessions_view(tmp_path)["sessions"]}

    assert rows["agent"]["started_by"] == "claude" and rows["human"]["started_by"] == "terminal"
    assert rows["agent"]["unlanded"] >= 1 and rows["agent"]["uncommitted"] is True
    assert rows["human"]["unlanded"] == 0 and rows["human"]["uncommitted"] is False
    assert rows["agent"]["last_change_ts"] == edited
    assert rows["human"]["last_change_ts"] == session_mod.list_sessions(tmp_path)[1].started_at

    raw = state.load_json(tmp_path, "sessions", {})
    del raw["human"]["started_by"]
    state.save_json(tmp_path, "sessions", raw)
    assert session_mod.list_sessions(tmp_path)[1].started_by is None


def test_a_plan_joined_to_a_session_by_its_claude_id_matches_the_sessions_work(tmp_path, monkeypatch):
    """An agent plans on the main repo and builds in a session it started from the same Claude Code
    session. Its step matches the session's change before anything lands, and the plan says so with
    `matched_in_session`. A plan from another Claude session does not see that work, and the
    session's ops are never main's drift."""
    from pathlib import Path

    from ugh.api import plan_view
    from ugh.loop import plan as plan_mod
    from ugh.loop.match import compute_checkpoint

    def decompose(repo, plan_text):
        return plan_mod.PlanDecomposition(
            steps=[plan_mod.PlanStep(title="change foo", predicted_footprint=["main.py::foo"])])

    _seed_repo(tmp_path)
    monkeypatch.setattr(plan_mod, "_llm_decompose", decompose)
    plan_mod.intake(tmp_path, "1. change foo", session_id="joined", claude_session_id="chat-1")
    plan_mod.intake(tmp_path, "1. change foo", session_id="other", claude_session_id="chat-2")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "chat-1")
    session = session_mod.start(tmp_path, "s1")
    _write_and_commit(Path(session.scratch), "main.py", "def foo():\n    return 2\n")
    session_ops = session_mod.new_op_ids(session)

    result = compute_checkpoint(tmp_path)

    groups = {g.session_id: set(g.op_ids) for g in result.matches}
    assert set(groups) == {"joined"} and groups["joined"] and groups["joined"] <= session_ops
    assert not set(result.drift_op_ids) & session_ops
    flags = {p["session_id"]: p["matched_in_session"] for p in plan_view(tmp_path)["sessions"]}
    assert flags == {"joined": True, "other": False}


def test_session_status_says_what_a_dead_session_holds_instead_of_promising_a_reap(tmp_path, capsys):
    """`gc` reaps a dead session only when it holds nothing (`pending_work`), so `ugh session status`
    keeps "gc will reap" for exactly those and names what the others hold."""
    import subprocess
    import sys
    from dataclasses import replace
    from pathlib import Path

    from ugh.cli import main

    _seed_repo(tmp_path)
    busy = session_mod.start(tmp_path, "busy")
    session_mod.start(tmp_path, "idle")
    _write_and_commit(Path(busy.scratch), "b.py", "def bar():\n    return 5\n")
    (Path(busy.scratch) / "b.py").write_text("def bar():\n    return 6\n", encoding="utf-8")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    sessions = session_mod._load(tmp_path)
    for name in sessions:
        sessions[name] = replace(sessions[name], owner_pid=dead.pid)
    session_mod._save(tmp_path, sessions)

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert main(["session", "status"]) == 0
    finally:
        os.chdir(cwd)
    lines = {line.split(":")[0].strip(): line for line in capsys.readouterr().out.splitlines()}

    unlanded, _, _ = session_mod.activity(sessions["busy"])
    assert f"(DEAD, holds {unlanded} unlanded commits and uncommitted edits -- gc keeps it)" in lines["busy"]
    assert "(DEAD (gc will reap))" in lines["idle"]
