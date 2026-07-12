"""Tests for `sgt.core.session` -- the U30/D5 scratch-tree session layer.

Each session is a real `git worktree` nested under `.sgt/local/sessions/<name>` (gitignored, so
it never shows up in the main tree's own `git status`), checked out on its own
`sgt-session/<name>` branch. `land` reuses the U23 CAS `sgt.core.sync.land` verbatim, run against
the scratch tree (worktrees share one ref store, so the CAS is against the one shared branch
record regardless of which worktree issues it); `gc` reaps sessions whose owning pid has died.
"""

from __future__ import annotations

import os

from sgt import state
from sgt.core import lens
from sgt.core import session as session_mod
from sgt.core.lens import get
from sgt.core.store import Store, fsck
from sgt.store.gitbind import init_store


def _seed_repo(root, oracle_cmd="exit 0"):
    gb, _ = init_store(root)
    if oracle_cmd is not None:
        state.save_json(root, "oracle_config", {"tiers": [{"name": "gate", "command": oracle_cmd}]})
    (root / "main.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("init")
    ideal = get(root)
    put_sha = lens.put(root, ideal, message="sgt: init")
    lens.record_ideal(root, ideal, put_sha)
    return gb


def _write_and_commit(scratch, path, content):
    from sgt.store.gitbind import GitBinding

    (scratch / path).write_text(content, encoding="utf-8")
    GitBinding(scratch).commit_all(f"edit {path}")
    ideal = get(scratch)
    put_sha = lens.put(scratch, ideal, message=f"sgt: edit {path}")
    lens.record_ideal(scratch, ideal, put_sha)


def test_start_creates_a_worktree_on_its_own_branch(tmp_path):
    _seed_repo(tmp_path)

    session = session_mod.start(tmp_path, "s1")

    assert session.name == "s1"
    assert session.branch == "sgt-session/s1"
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
    op = next(op for op in store.all_ops() if "b.py::bar" in op.footprint)
    assert any(a.session == "s1" for a in op.attribution)

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
