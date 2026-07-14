"""P0 tests: ``GitBinding.diff_name_and_text`` — name-status + new-file hunk ranges.

This is the diff helper P1 (git ``-M`` rename detection) and P3 (``analyze()`` intersecting
hunk ranges against unit line spans) build on. It parses ``git diff -M parent..sha`` for the
status letter, the rename old→new paths, and the ``@@`` line ranges in the post-commit file.

Tests diff from a baseline commit (an empty placeholder file, since a commit needs something
staged) so the parent-vs-child diff isolates exactly the change under test.
"""

from sgt.store.gitbind import FileChange, init_store


def _write(gb, path, text):
    (gb.repo / path).write_text(text, encoding="utf-8")


def test_added_file_reports_A_and_full_range(tmp_path):
    gb, _ = init_store(tmp_path)
    _write(gb, "README.md", "placeholder\n")
    base = gb.commit_all("baseline")
    _write(gb, "a.py", "x = 1\ny = 2\nz = 3\n")
    sha = gb.commit_all("add a")
    changes = gb.diff_name_and_text(base, sha)
    assert changes == [FileChange(status="A", path="a.py", old_path=None, new_ranges=((1, 3),))]


def test_root_commit_diffs_against_empty_tree(tmp_path):
    gb, _ = init_store(tmp_path)
    _write(gb, "a.py", "x = 1\n")
    root = gb.commit_all("root")
    changes = gb.diff_name_and_text(None, root)
    by_path = {c.path: c for c in changes}
    assert by_path["a.py"] == FileChange(status="A", path="a.py", old_path=None, new_ranges=((1, 1),))
    assert all(c.status == "A" for c in changes)  # everything is "added" vs the empty tree


def test_modified_file_reports_touched_ranges(tmp_path):
    gb, _ = init_store(tmp_path)
    _write(gb, "a.py", "one\ntwo\nthree\nfour\n")
    first = gb.commit_all("add a")
    _write(gb, "a.py", "one\nTWO\nthree\nfour\nfive\n")  # change line 2, add line 5
    second = gb.commit_all("edit a")
    changes = gb.diff_name_and_text(first, second)
    assert len(changes) == 1
    c = changes[0]
    assert c.status == "M" and c.path == "a.py" and c.old_path is None
    assert (2, 2) in c.new_ranges and (5, 5) in c.new_ranges


def test_rename_detected_via_M_keeps_one_change_with_old_path(tmp_path):
    gb, _ = init_store(tmp_path)
    body = "def greet(name):\n    return f'hi {name}'\n" * 3  # enough body to score as a rename
    _write(gb, "old.py", body)
    first = gb.commit_all("add old")
    (gb.repo / "old.py").unlink()
    _write(gb, "new.py", body)
    second = gb.commit_all("rename to new")
    changes = gb.diff_name_and_text(first, second, find_renames=True)
    assert len(changes) == 1
    c = changes[0]
    assert c.status == "R" and c.path == "new.py" and c.old_path == "old.py"
    assert c.new_ranges == ()  # a pure rename (identical bytes) touches no new lines


def test_rename_disabled_reports_delete_plus_add(tmp_path):
    gb, _ = init_store(tmp_path)
    body = "def greet(name):\n    return f'hi {name}'\n" * 3
    _write(gb, "old.py", body)
    first = gb.commit_all("add old")
    (gb.repo / "old.py").unlink()
    _write(gb, "new.py", body)
    second = gb.commit_all("rename to new")
    changes = gb.diff_name_and_text(first, second, find_renames=False)
    statuses = sorted((c.status, c.path) for c in changes)
    assert statuses == [("A", "new.py"), ("D", "old.py")]


def test_deleted_file_reports_D_and_no_new_ranges(tmp_path):
    gb, _ = init_store(tmp_path)
    _write(gb, "b.py", "gone\n")
    first = gb.commit_all("add b")
    (gb.repo / "b.py").unlink()
    second = gb.commit_all("drop b")
    changes = gb.diff_name_and_text(first, second)
    assert changes == [FileChange(status="D", path="b.py", old_path=None, new_ranges=())]
