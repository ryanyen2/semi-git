"""Phase 1.2 §F: `sgt migrate state-ref` moves a pre-1.2 repo's committed `.sgt/**` state off the
branch tree onto `refs/sgt/state`. The one irreversible tree change of Phase 1.2 -- a single
migration commit that `git rm --cached`s the moved paths -- is what kills F1 (saves stop dirtying
the tree) and F10 (that state stops colliding on merge/rebase).

A pre-1.2 repo is synthesized by deleting the `.sgt/.gitignore` a fresh 1.2 store writes and
force-committing the `.sgt/**` files, reproducing exactly the tracked layout an un-migrated clone
carries. Hermetic: real `git`, no network, no LLM.
"""

from __future__ import annotations

from pathlib import Path

from sgt import state
from sgt.core import migrate
from sgt.core.store import Store
from sgt.core.sync import state_ref
from sgt.store.gitbind import GitBinding, init_store


def _pre_1_2_repo(root: Path, *, local_exclusions: bytes | None = None) -> tuple[GitBinding, Path]:
    """A repo whose `.sgt/**` state is TRACKED in the branch tree, as before Phase 1.2. Seeds a
    couple of traveling artifacts (an op file + two tables), the stay-tracked config (`tiers.json`,
    `oracle.json`), and optionally a local (never-tracked) exclusion log to promote. `.sgt/local/`
    keeps its long-standing `*` gitignore; only the 1.2-era top-level `.sgt/.gitignore` is absent,
    which is exactly what leaves the traveling paths tracked."""
    gb, _ = init_store(root)
    repo = root
    Store(repo).init()  # writes both .sgt/.gitignore and .sgt/local/.gitignore

    ops = repo / ".sgt" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    (ops / "op-aaa").write_bytes(b'{"id": "op-aaa"}\n')
    state.save_json(repo, "pins", {"f-0001": "auth"})
    state.save_json(repo, "declared_orset", {"adds": [], "tombstones": []})
    state.save_json(repo, "tiers", {"entity": []})          # stays tracked (LAW-0)
    state.save_json(repo, "oracle_config", {"model": "x"})  # stays tracked (team config)
    if local_exclusions is not None:
        old = repo / ".sgt" / "local" / "exclusions.json"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(local_exclusions)

    (repo / "README").write_text("hello\n")  # a source file, so a normal commit is non-empty
    (repo / ".sgt" / ".gitignore").unlink()  # pre-1.2: nothing under .sgt is ignored
    gb._git("add", "-A")
    gb._git("commit", "-q", "-m", "seed pre-1.2 tracked state")
    return gb, repo


def test_dry_run_reports_moved_paths_and_writes_nothing(tmp_path):
    gb, repo = _pre_1_2_repo(tmp_path)
    head_before = gb.head()

    report = migrate.migrate_to_state_ref(repo, dry_run=True)

    assert report.dry_run and not report.changed and not report.already_migrated
    assert ".sgt/ops/op-aaa" in report.untracked
    assert ".sgt/pins/pins.json" in report.untracked
    assert ".sgt/declared_edges.json" in report.untracked
    # The stay-set never moves.
    assert ".sgt/tiers.json" not in report.untracked
    assert ".sgt/oracle.json" not in report.untracked

    # Nothing was written: the paths are still tracked, no commit, no ref.
    assert ".sgt/ops/op-aaa" in gb.tracked_paths(".sgt")
    assert gb.head() == head_before
    assert state_ref.read_sha(gb) is None


def test_apply_untracks_state_seeds_ref_and_keeps_files(tmp_path):
    gb, repo = _pre_1_2_repo(tmp_path)
    head_before = gb.head()

    report = migrate.migrate_to_state_ref(repo, dry_run=False)
    assert report.changed and not report.already_migrated

    # The moved paths are untracked; the stay-set is still tracked.
    tracked = set(gb.tracked_paths(".sgt"))
    assert ".sgt/ops/op-aaa" not in tracked
    assert ".sgt/pins/pins.json" not in tracked
    assert ".sgt/declared_edges.json" not in tracked
    assert ".sgt/tiers.json" in tracked
    assert ".sgt/oracle.json" in tracked

    # Files still exist on disk (rm --cached keeps the working tree).
    assert (repo / ".sgt" / "ops" / "op-aaa").is_file()
    assert (repo / ".sgt" / "pins" / "pins.json").is_file()

    # The ref now carries the traveling state.
    tree = state_ref.read_tree(gb)
    assert ".sgt/ops/op-aaa" in tree
    assert ".sgt/pins/pins.json" in tree
    assert ".sgt/tiers.json" not in tree  # config never travels

    # One migration commit, and `.sgt` no longer dirties the tree (the F1/F10 win).
    assert gb.head() != head_before
    status = gb._git("status", "--porcelain", "--", ".sgt", check=False)
    assert status.stdout.strip() == ""


def test_apply_is_idempotent(tmp_path):
    gb, repo = _pre_1_2_repo(tmp_path)
    migrate.migrate_to_state_ref(repo, dry_run=False)
    head_after_first = gb.head()

    again = migrate.migrate_to_state_ref(repo, dry_run=False)
    assert again.already_migrated and not again.changed
    assert again.untracked == ()
    assert gb.head() == head_after_first  # no second (empty) commit


def test_promotes_local_exclusions_to_shared(tmp_path):
    body = b'{"refs/heads/main": {"adds": [["op-x", "t1"]], "tombstones": []}}\n'
    gb, repo = _pre_1_2_repo(tmp_path, local_exclusions=body)
    old = repo / ".sgt" / "local" / "exclusions.json"
    assert old.is_file()

    report = migrate.migrate_to_state_ref(repo, dry_run=False)
    assert report.exclusions_promoted

    # The log moved to its shared home and now travels on the ref.
    new = state.path(repo, "exclusions")
    assert new == repo / ".sgt" / "exclusions.json"
    assert new.read_bytes() == body
    assert not old.exists()
    assert ".sgt/exclusions.json" in state_ref.read_tree(gb)
