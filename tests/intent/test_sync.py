"""Tests for the intent overlay's sync reconciliation (plan U5, KTD7): the prompt sidecar unions
by key as a real part of the sync commit (transactional, like pins/tree/aliases), while
`themes.json` is deliberately left to the next explicit `sgt intent build` -- rebuilding it needs
`GitBinding.history()`, which only reflects the merged history once the sync/land commit actually
exists, so a transactional rebuild would either see stale history or write a committed artifact
into the tree *after* it was already committed. `build_themes`, called as a normal follow-up once
the sync commit is real, correctly re-derives over the merged op union.

Fixture mirrors `tests/core/test_sync.py`'s two-clone idiom (a bare remote + two working clones)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sgt.core import lens, sync
from sgt.core.store import Store
from sgt.intent import group, prompts, theme
from sgt.store.gitbind import GitBinding


def _init_bare(root: Path) -> Path:
    remote = root / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    return remote


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    GitBinding(dest).init()
    return dest


def _push(repo: Path, branch: str = "main") -> None:
    subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "origin", branch], check=True, capture_output=True
    )


def _edit_and_commit(repo: Path, path: str, content: str, message: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    content_sha = GitBinding(repo).commit_all(message)
    ideal = lens.get(repo)
    put_sha = lens.put(repo, ideal, message=f"sgt: mine {message}")
    lens.record_ideal(repo, ideal, put_sha)
    return content_sha


def _two_clones(tmp_path: Path, main_py: str) -> tuple[Path, Path]:
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", main_py, "init")
    _push(a)
    b = _clone(remote, tmp_path / "b")
    lens.get(b)
    return a, b


_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"


def test_prompt_sidecars_union_across_a_sync_with_distinct_keys(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    prompts.record_prompt(a, "plan-a", "fix foo")
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)

    prompts.record_prompt(b, "plan-b", "fix bar")
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "bump bar")

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged

    assert prompts.prompt_for(b, "plan-a") == "fix foo"
    assert prompts.prompt_for(b, "plan-b") == "fix bar"


def test_prompt_sidecar_same_key_both_sides_merges_deterministically_no_crash(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    prompts.record_prompt(a, "shared-key", "a's version")
    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)

    prompts.record_prompt(b, "shared-key", "b's version")
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "bump bar")

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged

    result = prompts.prompt_for(b, "shared-key")
    assert result in ("a's version", "b's version")  # deterministic pick, no crash, no duplicate


def test_build_themes_after_sync_rederives_over_the_merged_op_union(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "fix(foo): bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "fix(bar): bump bar")

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged

    themes = theme.build_themes(b)
    all_op_ids = {op.id for op in Store(b).all_ops()}
    all_shas = {sha for t in themes.values() for sha in t["atom_shas"]}

    # every theme's members are commit shas, never op-ids, and no theme-id repeats for the same
    # member set (content-addressed minting, U4) -- rebuilding twice in a row proves it.
    assert all_shas.isdisjoint(all_op_ids)
    assert theme.build_themes(b) == themes


def test_sync_with_no_new_ops_leaves_themes_json_byte_identical(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)
    theme.build_themes(b)
    before = (b / ".sgt" / "intent" / "themes.json").read_text(encoding="utf-8")
    GitBinding(b).commit_all("build intent themes")  # a clean tree is sync's precondition

    # A no-op sync (nothing new on either side) followed by rebuilding themes again must not
    # churn the file.
    sync.sync(b, remote="origin", branch="main")
    theme.build_themes(b)
    after = (b / ".sgt" / "intent" / "themes.json").read_text(encoding="utf-8")

    assert before == after


def test_two_clones_diverge_then_sync_atoms_partition_the_full_union(tmp_path):
    """Rung-0/1 determinism carries through a sync: after the union, `group.atoms` still forms a
    total partition of the merged store with no dropped or duplicated op."""
    a, b = _two_clones(tmp_path, _BASE)

    _edit_and_commit(a, "main.py", "def foo():\n    return 100\n\n\ndef bar():\n    return 2\n", "bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 1\n\n\ndef bar():\n    return 200\n", "bump bar")

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged

    all_op_ids = {op.id for op in Store(b).all_ops()}
    partitioned = {op_id for atom in group.atoms(b) for op_id in atom.op_ids}
    assert partitioned == all_op_ids
