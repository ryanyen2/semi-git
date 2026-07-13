"""Tests for sgt.core.lens -- get/put integration with git (plan U6/U7.5, R8/R9/R10/R20).

`get()` mines committed history *and* the current uncommitted working tree (dirty edits land as
pending ops with empty provenance, folded into the returned ideal but never persisted to
`.sgt/local/ideal.json`); `put()` refuses to overwrite an unabsorbed dirty change rather than
clobbering it (U7.5, closing the two gaps FINDINGS.md flagged under its 2026-07-07 U6 entry).
"""

from __future__ import annotations

import pytest

from sgt.core.fold import code
from sgt.core.lens import (
    DirtyWorkingTreeError,
    _load_ideal_table,
    _ref_key,
    _save_ideal_table,
    get,
    init,
    put,
)
from sgt.core.order import chain_edges, is_valid_ideal
from sgt.core.store import Store
from sgt.store.gitbind import GitBinding, init_store
from tests.laws import corpus


def test_put_get_fixed_point(tmp_path):
    """put-get: materializing an ideal and re-mining the witness commit it just wrote yields
    zero new ops."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    put(repo, ideal)
    reidealed = get(repo)
    assert reidealed.op_ids == ideal.op_ids


def test_squash_merge_creates_zero_new_ops(tmp_path):
    """AE1 / the identification law (R8): a squash-merged copy of already-mined work mints no
    new ops -- the existing op just gains the squash commit as an additional witness."""
    repo = corpus.CORPUS["squash_merge"].build(tmp_path / "repo")

    corpus.checkout(repo, "feature")
    feature_ideal = get(repo)
    assert len(feature_ideal.op_ids) > 0

    corpus.checkout(repo, "main")
    main_ideal = get(repo)
    assert main_ideal.op_ids == feature_ideal.op_ids  # literally the same ops, new witness only

    store = Store(repo)
    helper_op = next(store.get(oid) for oid in main_ideal.op_ids if "a.py::helper" in store.get(oid).footprint)
    assert len(helper_op.provenance) == 2  # feature's commit AND the squash commit


def test_rebase_identifies_and_gains_a_witness(tmp_path):
    """A rebase replays the same patch onto a new base -- a rewritten commit SHA, but the same
    net footprint transition, so it identifies with the pre-rebase op rather than re-minting."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base")
    base_branch = gb.symbolic_ref()  # whatever git's default branch name is here

    gb._git("checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    original_sha = gb.commit_all("feature: tweak foo")

    get(repo)
    store = Store(repo)
    # "a.py::foo" now has two chained ops (the base add, then this tweak) -- the tweak is the
    # one with a real before_version.
    op_before = next(
        op for op in store.all_ops()
        if "a.py::foo" in op.footprint and op.footprint["a.py::foo"][0] is not None
    )
    assert original_sha in op_before.provenance

    gb._git("checkout", "-q", base_branch.rsplit("/", 1)[-1])
    (repo / "unrelated.py").write_text("def other():\n    return 0\n", encoding="utf-8")
    gb.commit_all("main: unrelated work")

    gb._git("checkout", "-q", "feature")
    gb._git("rebase", "-q", base_branch.rsplit("/", 1)[-1])
    rebased_sha = gb.head()
    assert rebased_sha != original_sha

    get(repo)
    op_after = next(
        op for op in store.all_ops()
        if "a.py::foo" in op.footprint and op.footprint["a.py::foo"][0] is not None
    )
    assert op_after.id == op_before.id
    assert {original_sha, rebased_sha} <= set(op_after.provenance)


def test_checkout_between_diverged_branches_never_leaks_the_others_op(tmp_path):
    """A `git checkout` between two branches that share a base but diverge on the same symbol
    never fabricates a phantom op, and never leaks one branch's own tip into the other's ideal."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    gb = GitBinding(repo)
    store = Store(repo)

    corpus.checkout(repo, "release")
    release_ideal = get(repo)
    corpus.checkout(repo, "main")
    main_ideal = get(repo)
    main_commits = set(gb.commit_shas())

    for op_id in main_ideal.op_ids:
        assert set(store.get(op_id).provenance) & main_commits

    # "slugify.py::slugify" has two ops per ideal: the shared base add, then this branch's own
    # tweak -- the tweak is the one with a real before_version, and it's what actually diverges.
    def _tweak(ideal):
        for oid in ideal.op_ids:
            op = store.get(oid)
            if "slugify.py::slugify" in op.footprint and op.footprint["slugify.py::slugify"][0] is not None:
                return op
        raise AssertionError("no slugify tweak op found")

    main_slugify = _tweak(main_ideal)
    release_slugify = _tweak(release_ideal)
    assert main_slugify.id != release_slugify.id  # a genuine chain fork, not a shared identity
    assert release_slugify.id not in main_ideal.op_ids  # main's checkout doesn't leak release's tip


def test_foreign_hotfix_commit_mined_on_next_contact(tmp_path):
    """A commit made directly via git (no sgt involvement) is mined on the next `get()` --
    sgt can never be locked out of its own repo."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base")
    ideal1 = get(repo)

    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("hotfix: bump foo (no sgt)")

    ideal2 = get(repo)
    assert ideal2.op_ids != ideal1.op_ids
    store = Store(repo)
    foo_ops = [store.get(oid) for oid in ideal2.op_ids if "a.py::foo" in store.get(oid).footprint]
    # the ideal is downward-closed, so it holds the *whole* chain: the original add plus the
    # hotfix's new rework -- exactly one of them is the new, hotfix-witnessed step.
    assert len(foo_ops) == 2
    new_step = [op for op in foo_ops if op.footprint["a.py::foo"][0] is not None]
    assert len(new_step) == 1


def test_horizon_init_chains_onto_its_genesis_op(tmp_path):
    """R10: pre-horizon history compresses to one genesis op per symbol; a post-horizon edit
    chains directly onto it (no gap, no separate re-derivation of pre-horizon history)."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("pre-horizon: add foo")
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    horizon_sha = gb.commit_all("pre-horizon: tweak foo")

    init(repo, horizon=horizon_sha)
    store = Store(repo)
    genesis_ops = [op for op in store.all_ops() if "a.py::foo" in op.footprint]
    assert len(genesis_ops) == 1
    assert genesis_ops[0].footprint["a.py::foo"][0] is None  # a genesis op has no real predecessor
    assert horizon_sha in genesis_ops[0].provenance

    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    gb.commit_all("post-horizon: tweak foo again")
    ideal = get(repo)

    all_ops = store.all_ops()
    assert is_valid_ideal(all_ops, ideal.op_ids)
    edges = chain_edges(all_ops)
    assert any(a == genesis_ops[0].id for a, _b in edges)  # something chains onto the genesis op


def test_get_on_empty_repo_returns_empty_ideal(tmp_path):
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    ideal = get(repo)
    assert ideal.op_ids == frozenset()


def test_dirty_edit_is_mined_as_pending_and_visible_but_not_persisted(tmp_path):
    """Gap 2 (U7.5): an uncommitted working-tree edit is mined with empty provenance, folded into
    `get()`'s returned ideal (so `code()` reproduces the dirty bytes), yet never written to the
    durable `.sgt/local/ideal.json` -- discarding the edit simply stops it appearing next time."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)  # baseline, clean tree

    (repo / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")  # dirty, uncommitted
    ideal = get(repo)

    store = Store(repo)
    pending_ids = {op.id for op in store.all_ops() if not op.provenance}
    assert pending_ids, "the dirty edit was not mined as a pending (empty-provenance) op"
    assert pending_ids <= ideal.op_ids  # the overlay is visible in the returned ideal

    materialized = code(ideal, store.all_ops())
    assert materialized["a.py"] == (repo / "a.py").read_bytes()  # reproduces the dirty bytes
    assert b"return 42" in materialized["a.py"]

    persisted = set(_load_ideal_table(repo)[_ref_key(gb)])
    assert not (pending_ids & persisted), "a pending op leaked into .sgt/local/ideal.json"


def _count_snapshot_calls(monkeypatch):
    """Spy on `working_tree_snapshot` -- taken only when the dirty mining pass runs (R16), so its
    call count is a direct probe of whether the O(tracked files) pending pass fired."""
    calls = {"n": 0}
    real = GitBinding.working_tree_snapshot

    def counting(self):
        calls["n"] += 1
        return real(self)

    monkeypatch.setattr(GitBinding, "working_tree_snapshot", counting)
    return calls


def test_clean_tree_skips_the_dirty_mining_pass(tmp_path, monkeypatch):
    """R16 (U4): on a tree whose only working-tree churn is untracked `.sgt/ops/*` (what every
    `get()` leaves behind), the pending pass never runs -- the snapshot it begins with is never
    taken -- so `sgt status` on a real, source-clean repo stops paying the O(files) cost."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)  # mines ops into .sgt/ops/, left untracked -- the realistic "clean source" state
    assert any((repo / ".sgt" / "ops").iterdir()), "expected untracked .sgt/ops churn"

    calls = _count_snapshot_calls(monkeypatch)
    get(repo)  # source-clean tree
    assert calls["n"] == 0
    assert not gb.has_dirty_source()


def test_untracked_source_file_triggers_the_dirty_pass(tmp_path, monkeypatch):
    """R16 (U4): an untracked *source* file is a genuine pending add and must still be mined -- it
    reads dirty and the pass runs, so it lands as a pending (empty-provenance) op in the overlay."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)

    (repo / "new.py").write_text("def bar():\n    return 2\n", encoding="utf-8")  # untracked source
    assert gb.has_dirty_source()
    calls = _count_snapshot_calls(monkeypatch)
    ideal = get(repo)
    assert calls["n"] >= 1
    pending = {op.id for op in Store(repo).all_ops() if not op.provenance}
    assert pending and pending <= ideal.op_ids  # the new file is visible in the overlay


def test_sgt_dir_change_is_not_mineable_dirt(tmp_path):
    """R16 (U4): churn under `.sgt/` -- sgt's own state, never mined as codebase content -- is not
    counted, whether the changed `.sgt/` file is untracked or tracked. Without this exclusion the
    untracked `.sgt/ops/*` every `get()` writes would keep the guard permanently dirty."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)  # untracked .sgt/ops churn present
    assert not gb.has_dirty_source()

    gb.commit_all("commit sgt state")  # now .sgt/ops/* are tracked
    op_files = sorted((repo / ".sgt" / "ops").iterdir())
    assert op_files, "expected committed .sgt/ops files"
    with op_files[0].open("a", encoding="utf-8") as f:
        f.write("\n")  # a *tracked* .sgt/ file now differs from HEAD
    assert not gb.has_dirty_source()


def test_tracked_edit_triggers_the_dirty_pass(tmp_path, monkeypatch):
    """R16 (U4), the other side: a genuine tracked-file edit reads dirty, so the pending pass runs
    and the pending overlay behavior is preserved unchanged."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)

    (repo / "a.py").write_text("def foo():\n    return 42\n", encoding="utf-8")  # tracked edit
    assert gb.has_dirty_source()
    calls = _count_snapshot_calls(monkeypatch)
    get(repo)
    assert calls["n"] >= 1


def test_put_refuses_to_clobber_an_unabsorbed_dirty_edit(tmp_path):
    """R9 (U7.5): `put()` of an ideal that targets *different* bytes than an uncommitted edit on
    disk raises rather than silently reverting the edit; it refuses before touching the tree."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    baseline = get(repo)  # materializes foo == 1

    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")  # dirty, uncommitted

    with pytest.raises(DirtyWorkingTreeError):
        put(repo, baseline)  # would overwrite the uncommitted foo == 2 with foo == 1

    assert (repo / "a.py").read_bytes() == b"def foo():\n    return 2\n"  # tree left untouched


def test_persisted_ideal_survives_re_get_without_resurrecting_excluded_ops(tmp_path):
    """Gap 1 (U7.5): once `.sgt/local/ideal.json` holds an explicit (smaller) ideal for a ref, a
    re-`get()` with no new commits returns exactly that set -- an intentionally excluded op is
    not re-derived back in by a provenance scan of git history (the durability U8's revert/pin
    verbs will depend on)."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("modify foo")
    full = get(repo)
    store = Store(repo)

    # The modify op (a chain tip, so dropping it keeps a valid ideal) is what we exclude.
    def _is_foo_modify(op):
        fp = op.footprint.get("a.py::foo")
        return fp is not None and fp[0] is not None

    modify_id = next(oid for oid in full.op_ids if _is_foo_modify(store.get(oid)))
    subset = full.op_ids - {modify_id}

    table = _load_ideal_table(repo)
    table[_ref_key(gb)] = sorted(subset)
    _save_ideal_table(repo, table)

    reideal = get(repo)  # no new commits since the witness
    assert reideal.op_ids == subset  # exactly the persisted subset, not the full history
    assert modify_id not in reideal.op_ids

    materialized = code(reideal, store.all_ops())
    assert b"return 1" in materialized["a.py"]  # the excluded modify really is gone from the fold
    assert b"return 2" not in materialized["a.py"]


def test_init_on_large_corpus_repo_within_budgets(tmp_path):
    """R22/BET-E: opt-in only, skipped unless SGT_LARGE_CORPUS_REPO is set (see tests/laws/corpus.py)."""
    import shutil
    import time

    large_repo = corpus.large_corpus_repo()
    if large_repo is None:
        import pytest

        pytest.skip("SGT_LARGE_CORPUS_REPO not set -- opt-in BET-E large-repo check")

    dest = tmp_path / "large"
    shutil.copytree(large_repo, dest)
    gb = GitBinding(dest)
    n_commits = len(gb.commit_shas())

    t0 = time.time()
    init(dest)
    elapsed = time.time() - t0

    budget = corpus.MAX_INIT_SECONDS_PER_1K_COMMITS * (n_commits / 1000)
    assert elapsed <= budget, f"init took {elapsed:.1f}s, budget {budget:.1f}s for {n_commits} commits"


def test_get_survives_add_delete_readd_fork_in_linear_history(tmp_path):
    """U22.5 regression: a single-clone, single-branch history where a file is added, deleted,
    then re-added rebirths `(symbol, None)` twice -- two ops claim the same chain step, a fork by
    `is_fork_free`'s definition. `get()` must reduce to a *valid* ideal (drop the forked file's
    tips) rather than raise from `Ideal.from_ops`, and the reduction must be surgical: an unrelated
    file added once still surfaces. Before the fix, `sgt state` on any such repo crashed with
    'not a valid ideal', after having already persisted the invalid table to disk."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)

    (repo / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gb.commit_all("add keep and notes")

    (repo / "notes.txt").unlink()
    gb.commit_all("delete notes")

    (repo / "notes.txt").write_text("beta\n", encoding="utf-8")  # rebirth: before=None again
    gb.commit_all("re-add notes with different content")

    ideal = get(repo)  # must not raise
    store = Store(repo)
    all_ops = store.all_ops()
    assert is_valid_ideal(all_ops, ideal.op_ids)  # grounded + fork-free

    # The reduction is surgical: the unrelated single-add symbol survives, only the forked file's
    # tips were dropped, and the materialized tree agrees (notes.txt does not resurrect).
    assert any("keep.py::keep" in store.get(o).footprint for o in ideal.op_ids)
    materialized = code(ideal, all_ops)
    assert "keep.py" in materialized
    assert "notes.txt" not in materialized

    # The persisted table is itself a valid ideal -- a second read never re-raises, and the pure
    # read path (which reads the table straight back) constructs cleanly too.
    assert get(repo).op_ids == ideal.op_ids
    from sgt.core.lens import current_ideal

    assert current_ideal(repo).op_ids == ideal.op_ids


# -- U1: safe materialization (symlink guard + deletion backstop) -----------------------------


def test_put_does_not_write_or_delete_through_symlink(tmp_path):
    """Review reproduction (data loss): a symlink pointing outside the repo must never be written
    through nor removed by materialization. Mining leaves the link unmanaged (R3), and
    `_write_working_tree`'s lstat guard refuses any write/delete at a symlink leaf or ancestor, so
    an unrelated `put` leaves both the link and its external target intact."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET\n", encoding="utf-8")

    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    gb.commit_all("add a")
    (repo / "link.txt").symlink_to(outside)
    gb.commit_all("add link")

    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")  # unrelated edit
    ideal = get(repo)
    put(repo, ideal)

    assert outside.read_text() == "SECRET\n"          # external target never written through
    assert (repo / "link.txt").is_symlink()           # link itself never deleted


def test_write_working_tree_refuses_symlinked_ancestor(tmp_path):
    """Defense in depth: even if a materialized path's ancestor directory is a symlink on disk,
    the write is refused rather than following the link out of the repo."""
    from sgt.core.lens import _write_working_tree

    repo = tmp_path / "repo"
    init_store(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "pkg").symlink_to(outside, target_is_directory=True)

    _write_working_tree(repo, {"pkg/mod.py": b"data\n"}, [])

    assert not (outside / "mod.py").exists()          # never wrote through the symlinked dir


def test_put_backstop_keeps_unreproducible_rebirth_file(tmp_path):
    """AE1 / R4 (pre-U9): an add->delete->re-add history drops both `notes.txt` births as a fork,
    so `code(ideal)` no longer covers the path. Its live bytes are *not* reproducible from any
    valid ideal over the store, so `put` must NOT delete the working file -- the deletion is
    surfaced (backstop-kept), never silent. A legitimately reproducible file is unaffected."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)

    (repo / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gb.commit_all("add keep and notes")
    (repo / "notes.txt").unlink()
    gb.commit_all("delete notes")
    (repo / "notes.txt").write_text("beta\n", encoding="utf-8")
    gb.commit_all("re-add notes")

    ideal = get(repo)
    assert "notes.txt" not in code(ideal, Store(repo).all_ops())  # dropped from the ideal
    put(repo, ideal)

    assert (repo / "notes.txt").exists()                          # live file survives
    assert (repo / "notes.txt").read_text() == "beta\n"           # with its live content


# -- U2: fsck --tree classification (code(current_ideal) vs HEAD tree) -------------------------


def test_fsck_tree_clean_after_put_has_no_drift(tmp_path):
    """R2: after `put` writes `code(ideal)` and commits, the HEAD tree *is* `code(current_ideal)`,
    so `fsck --tree` finds zero real drift (the self-hosting invariant in miniature)."""
    from sgt.core.lens import fsck_tree
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    put(repo, ideal)
    result = fsck_tree(repo)
    assert result["drift"] == []


def test_fsck_tree_classifies_rebirth_file_as_backstop_kept_not_drift(tmp_path):
    """R2: an add->delete->re-add file is dropped from `code(I)` but kept on disk by the backstop.
    `--tree` classifies it as backstop-kept planned divergence, never as real drift (AE1)."""
    from sgt.core.lens import fsck_tree
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gb.commit_all("add keep and notes")
    (repo / "notes.txt").unlink()
    gb.commit_all("delete notes")
    (repo / "notes.txt").write_text("beta\n", encoding="utf-8")
    gb.commit_all("re-add notes")

    get(repo)
    result = fsck_tree(repo)
    assert "notes.txt" in result["backstop_kept"]
    assert "notes.txt" not in result["drift"]


def test_fsck_tree_reports_real_drift_for_a_foreign_edit(tmp_path):
    """R2: a committed edit sgt never absorbed (bytes at HEAD differ from `code(current_ideal)`)
    is real drift -- surfaced with a remedy direction, not hidden."""
    from sgt.core.lens import fsck_tree, current_ideal, record_ideal
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    put(repo, ideal)
    # a foreign commit that changes a tracked file's bytes without going through sgt; pin the
    # recorded ideal/witness so `fsck_tree` compares the *old* ideal against the drifted HEAD.
    gb = GitBinding(repo)
    tracked = [p for p in _tracked_after(repo) if p.endswith(".py")][0]
    (repo / tracked).write_text("# drifted\n", encoding="utf-8")
    gb.commit_all("foreign edit outside sgt")

    result = fsck_tree(repo)
    assert result["drift"], "a foreign edit at HEAD should surface as real drift"


def _tracked_after(repo):
    import subprocess
    out = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l and not l.startswith(".sgt/")]
