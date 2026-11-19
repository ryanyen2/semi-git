"""``GitBinding.diff_paths``: the paths a commit changed, a rename as one change with its old path.

Tests diff from a baseline commit (an empty placeholder file, since a commit needs something
staged) so the parent-vs-child diff isolates exactly the change under test.
"""

from ugh.store.gitbind import FileChange, init_store


def _write(gb, path, text):
    (gb.repo / path).write_text(text, encoding="utf-8")


def test_added_file_is_one_change(tmp_path):
    gb, _ = init_store(tmp_path)
    _write(gb, "README.md", "placeholder\n")
    base = gb.commit_all("baseline")
    _write(gb, "a.py", "x = 1\ny = 2\nz = 3\n")
    sha = gb.commit_all("add a")
    assert gb.diff_paths(base, sha) == [FileChange(path="a.py", old_path=None)]


def test_root_commit_diffs_against_empty_tree(tmp_path):
    gb, _ = init_store(tmp_path)
    _write(gb, "a.py", "x = 1\n")
    root = gb.commit_all("root")
    by_path = {c.path: c for c in gb.diff_paths(None, root)}
    assert by_path["a.py"] == FileChange(path="a.py", old_path=None)


def test_rename_detected_via_M_keeps_one_change_with_old_path(tmp_path):
    gb, _ = init_store(tmp_path)
    body = "def greet(name):\n    return f'hi {name}'\n" * 3  # enough body to score as a rename
    _write(gb, "old.py", body)
    first = gb.commit_all("add old")
    (gb.repo / "old.py").unlink()
    _write(gb, "new.py", body)
    second = gb.commit_all("rename to new")
    assert gb.diff_paths(first, second) == [FileChange(path="new.py", old_path="old.py")]


def test_deleted_file_is_one_change(tmp_path):
    gb, _ = init_store(tmp_path)
    _write(gb, "b.py", "gone\n")
    first = gb.commit_all("add b")
    (gb.repo / "b.py").unlink()
    second = gb.commit_all("drop b")
    assert gb.diff_paths(first, second) == [FileChange(path="b.py", old_path=None)]
