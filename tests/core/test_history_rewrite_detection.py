"""A backward git history rewrite heals on the next contact.

The ideal is every op mined from the ref's history, so after `git reset --hard` / `commit
--amend` / `branch -f` the next `get()` holds exactly what HEAD reaches: nothing to detect and no
remedy to route to.
"""

from __future__ import annotations

import subprocess

from ugh import api
from ugh.core import lens
from ugh.store.gitbind import init_store


def _repo(tmp_path):
    gb, _ = init_store(tmp_path)
    for i in (1, 2, 3):
        (tmp_path / "a.py").write_text(f"def foo():\n    return {i}\n", encoding="utf-8")
        (tmp_path / f"m{i}.py").write_text(f"def m{i}():\n    return {i}\n", encoding="utf-8")
        gb.commit_all(f"v{i}")
    lens.get(tmp_path)
    return tmp_path


def test_a_backward_reset_is_absorbed_on_the_next_contact(tmp_path):
    repo = _repo(tmp_path)
    before = api.status_view(repo)["symbols"]
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", "HEAD~2"], capture_output=True, check=True)
    lens.get(repo)
    view = api.status_view(repo)
    assert view["symbols"] < before
    assert view["files"] == 2 and not view["drift"]["any"]


def test_saving_re_authored_work_after_a_backward_move_is_clean(tmp_path):
    """Re-authoring dropped content by hand and saving it is an ordinary save."""
    from ugh.cli.porcelain import save

    repo = _repo(tmp_path)
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", "HEAD~2"], capture_output=True, check=True)
    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    (repo / "m2.py").write_text("def m2():\n    return 2\n", encoding="utf-8")
    assert save(str(repo), message="record the replayed work")["saved"] is True
    assert not api.status_view(repo)["drift"]["any"]
