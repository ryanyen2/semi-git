"""Tests for ugh.core.lens -- get/put integration with git (plan U6/U7.5, R8/R9/R10/R20).

`get()` mines committed history *and* the current uncommitted working tree (dirty edits land as
pending ops with empty provenance, folded into the returned ideal but never persisted to
`.sgt/local/ideal.json`).
"""

from __future__ import annotations

import pytest

from ugh.core.fold import code
import ugh.core.lens as lens_mod
from ugh.core.lens import (
    _load_ideal_table,
    _ref_key,
    get,
    init,
    ops_with_frontier_images,
    sync_status,
)
from ugh.core.order import is_valid_ideal
from ugh.core.store import Store
from ugh.store.gitbind import GitBinding, init_store
from tests.laws import corpus



def _revert(repo, ref):
    """Take out one op: an op id, or a `file::name` symbol's latest edit."""
    from ugh.core import opindex, planner
    from ugh.core.lens import current_ideal

    if "::" in ref:
        ref = current_ideal(repo).frontier(opindex.index_ops(repo))[ref]
    return planner.apply(repo, planner.plan(repo, [st for st, ops in planner._Ctx(repo).steps.items() if ref in ops], target=ref))

def test_ops_with_frontier_images_skips_a_corrupt_frontier_op(tmp_path, monkeypatch):
    """R1 read-side skip: a truncated/garbled frontier op file must degrade to a drop, not error
    the read views (`status_view`, `fsck_tree`) built on this. The op is
    excluded entirely rather than kept footprint-only -- its `images={}` would fold to silent
    zero-length content for the symbols it produces, a worse failure than its absence.

    The index is pinned to the pre-corruption footprint list so the corrupt file is still named as a
    frontier producer; otherwise `index_ops`' self-heal would drop it before `_safe_get` ever ran
    (that path is itself fine, but it wouldn't exercise the read-side skip this pins)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    ideal = lens_mod.current_ideal(repo)

    store = Store(repo)
    all_ops = store.all_ops()
    frontier_ids = set(ideal.frontier(all_ops).values())
    assert frontier_ids  # the fixture has live symbols at the frontier
    victim = sorted(frontier_ids)[0]

    monkeypatch.setattr("ugh.core.opindex.index_ops", lambda _repo: all_ops)
    (store.ops_dir / victim).write_bytes(b"{ truncated not json")  # corrupt the on-disk full op

    result = ops_with_frontier_images(repo, ideal)  # must not raise
    assert all(op.id != victim for op in result)  # the unreadable frontier op is dropped


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
    """A commit made directly via git (no ugh involvement) is mined on the next `get()` --
    ugh can never be locked out of its own repo."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base")
    ideal1 = get(repo)

    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("hotfix: bump foo (no ugh)")

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
    genesis_after = genesis_ops[0].footprint["a.py::foo"][1]
    assert any(op.footprint.get("a.py::foo", (None,))[0] == genesis_after for op in all_ops)


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


def test_second_get_with_no_new_commits_does_not_rewrite_ideal_and_witness(tmp_path):
    """A `get()` that mines nothing new must not touch `.sgt/local/ideal.json` or
    `.sgt/local/witness.json` on disk -- both are `.sgt/**/*.json` paths the VS Code extension
    watches to invalidate its cache, so an unconditional rewrite on every read (even a no-op one)
    makes every refresh retrigger another refresh, forever."""
    import ugh.state as state

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)  # baseline: mines the commit, seeds ideal_table + witness

    ideal_path = state.path(repo, "ideal_table")
    witness_path = state.path(repo, "witness")
    mtime_before = (ideal_path.stat().st_mtime_ns, witness_path.stat().st_mtime_ns)

    get(repo)  # no new commits, no dirty tree -- should be a pure no-op read

    mtime_after = (ideal_path.stat().st_mtime_ns, witness_path.stat().st_mtime_ns)
    assert mtime_after == mtime_before, "get() rewrote ideal_table/witness with no new state"


def _git(repo, *args):
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_revert_survives_a_history_rewrite(tmp_path):
    """After a take-out, a rebase that replays every commit under a new sha keeps each symbol's
    content sequence, so it re-mines to the same ops and the take-out commit's `Ugh-Out:` trailer
    travels with it: the definition stays out."""
    import json

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    (repo / "a.py").write_text(
        "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )
    gb.commit_all("add bar")
    (repo / ".sgt").mkdir(exist_ok=True)
    (repo / ".sgt" / "oracle.json").write_text(
        json.dumps({"tiers": [{"name": "c", "command": "python -m py_compile a.py"}]}),
        encoding="utf-8",
    )
    get(repo)  # seed

    bar = next(o for o in Store(repo).all_ops() if "a.py::bar" in o.footprint)
    _revert(repo, bar.id)
    assert b"def bar" not in code(get(repo), Store(repo).all_ops())["a.py"]  # revert took effect
    assert b"def bar" not in (repo / "a.py").read_bytes()

    # Rewrite history: reword the root commit and replay the rest onto it, changing every downstream
    # sha (a rebase) -- `bar`'s introducing commit is re-mined under a new sha, content-identical.
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    root = _git(repo, "rev-list", "--max-parents=0", "HEAD")
    _git(repo, "checkout", "--detach", root)
    _git(repo, "commit", "--amend", "-m", "add foo (reworded)")
    c0p = _git(repo, "rev-parse", "HEAD")
    _git(repo, "rebase", "--onto", c0p, root, branch)
    _git(repo, "checkout", branch)

    after = get(repo)
    assert b"def bar" not in code(after, Store(repo).all_ops())["a.py"], \
        "reverted op resurrected by the history rewrite"
    assert b"def bar" not in (repo / "a.py").read_bytes()
    assert any("a.py::foo" in o.footprint and o.id in after.op_ids for o in Store(repo).all_ops())


def test_resync_completes_whatever_the_machine_is_doing(tmp_path, monkeypatch):
    """`resync` promises "the record now matches HEAD", so a wall clock must not decide how much of
    that is true.

    A first contact mines one `_CHUNK_BUDGET_SECONDS` chunk per call, and `mine` resolves a frontier
    sha only when a chunk runs to completion -- so a chunk that hits its deadline records no
    progress at all, and the next call restarts the backward walk from head. Under load that is a
    treadmill: measured on the study's footfall bundle, the same `./stage 3` derived 206 live ops on
    an idle machine and 189 under eight busy cores, and `resync` reported the short one as success.
    A record that short can no longer reproduce the committed tree, so the next `ugh revert` refused
    with `put() would roll back files outside this edit's scope`, naming eight files nobody had
    touched -- on a bundle a participant completes without trouble.

    Pinned with the budget at zero rather than by loading the machine: it is the same failure at its
    limit (every chunk times out), and it is the same answer every time this runs."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    unhurried = get(repo).op_ids
    assert unhurried, "the corpus mined nothing, so this test would pass vacuously"

    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", 0.0)
    res = lens_mod.resync(repo)

    assert res["complete"], "resync returned with the ref still behind and did not say so"
    assert sync_status(repo)["complete"]
    key = _ref_key(GitBinding(repo))
    assert frozenset(_load_ideal_table(repo)[key]) == unhurried, (
        "the re-derived record depends on how fast the machine was")


def test_a_backward_history_rewrite_drops_the_lost_commits_ops_on_next_contact(tmp_path):
    """The ideal is every op mined from the ref's history, so after `git reset --hard` to an
    earlier commit the next `get()` no longer holds what the dropped commit introduced; `resync`
    re-derives the same answer from scratch."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("c1: foo")
    c1 = gb.head()
    (repo / "a.py").write_text(
        "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )
    gb.commit_all("c2: bar")
    seeded = get(repo)
    assert any("a.py::bar" in o.footprint and o.id in seeded.op_ids for o in Store(repo).all_ops())

    _git(repo, "reset", "--hard", c1)
    healed = get(repo)
    assert not any("a.py::bar" in o.footprint and o.id in healed.op_ids for o in Store(repo).all_ops())
    assert any("a.py::foo" in o.footprint and o.id in healed.op_ids for o in Store(repo).all_ops())
    res = lens_mod.resync(repo)
    assert res["after"] == len(healed.op_ids)


def test_mine_on_contact_never_ingests_conflict_marker_bytes(tmp_path):
    """P0-B (launch, F26 lifted into `_sync`): while a git merge/cherry-pick/revert is unresolved,
    the working tree holds `<<<<<<<`/`=======`/`>>>>>>>` conflict markers. Those bytes must never be
    mined into the append-only op store -- once committed they'd be a permanent phantom op. The guard
    (`merge_in_progress` gating the dirty pass) lives in the shared mine-on-contact path, so *every*
    read/navigation `get()` honors it, not just `save`."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base: foo")
    get(repo)
    main = gb.symbolic_ref().rsplit("/", 1)[-1]

    gb._git("checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("feature: foo v2")
    gb._git("checkout", "-q", main)
    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    gb.commit_all("main: foo v3")

    # conflicting merge, left UNRESOLVED: MERGE_HEAD set, markers in the tree
    gb._git("merge", "--no-edit", "feature", check=False)
    assert lens_mod.merge_in_progress(gb) == "merge"
    assert b"<<<<<<<" in (repo / "a.py").read_bytes()

    get(repo)  # a read/navigation contact mid-conflict -- must skip the dirty pass

    marker = b"<<<<<<<"
    assert not any(
        marker in img
        for o in Store(repo).all_ops()
        for img in getattr(o, "images", {}).values()
        if img
    ), "conflict-marker bytes were mined into a persistent op"


def test_sync_status_reports_complete_on_a_fully_synced_ref(tmp_path):
    """A freshly-mined ref: `sync_status` (a pure read -- no mining) reports `complete`."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    assert sync_status(repo) == {"complete": True}


def test_a_first_contact_cut_short_by_its_deadline_is_incomplete_and_resumes(tmp_path, monkeypatch):
    """History is mined oldest first; a first contact whose deadline has passed mines nothing and
    the ref reports incomplete until a later contact finishes it."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", -1.0)
    get(repo)
    assert sync_status(repo)["complete"] is False
    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", 10.0)
    get(repo)
    assert sync_status(repo)["complete"] is True


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
    taken -- so `ugh status` on a real, source-clean repo stops paying the O(files) cost."""
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
    """R16 (U4): churn under `.sgt/` -- ugh's own state, never mined as codebase content -- is not
    counted, whether the changed `.sgt/` file is untracked or tracked. Without this exclusion the
    untracked `.sgt/ops/*` every `get()` writes would keep the guard permanently dirty."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)  # untracked .sgt/ops churn present
    assert not gb.has_dirty_source()

    gb.commit_all("commit ugh state")  # now .sgt/ops/* are tracked
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


def test_unchanged_dirty_tree_skips_the_repeat_dirty_pass(tmp_path, monkeypatch):
    """The no-op gate (perf): a `get()` on a dirty tree that is byte-identical to the last `get()`
    returns the same ideal WITHOUT re-running the O(files) dirty snapshot pass -- the bulk of a warm
    `get()`. R9 holds because *any* real change moves the fingerprint: this test also proves that
    editing the dirty content re-runs the pass, and the persisted-ideal test above proves an ideal
    edit (revert/pin) does too."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")  # dirty edit
    first = get(repo)  # runs the dirty pass once, caches the fingerprint

    calls = _count_snapshot_calls(monkeypatch)
    second = get(repo)  # identical dirty content -> the gate fires
    assert calls["n"] == 0  # the O(files) dirty snapshot pass was skipped
    assert second.op_ids == first.op_ids

    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")  # now it really changed
    get(repo)
    assert calls["n"] >= 1  # a genuine change re-runs the pass (R9 preserved)


def test_clean_merge_does_not_fork_a_twice_edited_branch_symbol(tmp_path):
    """1.3 (F7 root cause): mining a merge commit against its FIRST PARENT ONLY re-attributes the
    second parent's whole cumulative delta as one op whose before_version is the merge-base version.
    When the merged-in branch edited a symbol >=2 times, that cumulative op collides with the
    branch's own first step on `(symbol, base_version)` -> a spurious fork -> `fork_free` drops the
    whole chain -> the merged content silently rolls back to the base. Merge-aware mining skips the
    paths the merge took wholesale from a parent (already mined on that branch), so no cumulative op
    is minted and the merged symbol survives. This is F7's *root cause*, upstream of the Phase-0
    put-refusal guard that only catches the resulting drift."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base: foo v1")
    main = gb.symbolic_ref().rsplit("/", 1)[-1]

    # feature edits foo TWICE -> two chained ops v1->v2->v3
    gb._git("checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("feature: foo v2")
    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    gb.commit_all("feature: foo v3")

    # main diverges on an unrelated file, so `git merge feature` is a real two-parent (non-ff)
    # merge; foo was only touched on feature, so it auto-merges cleanly (no conflict).
    gb._git("checkout", "-q", main)
    (repo / "unrelated.py").write_text("def other():\n    return 0\n", encoding="utf-8")
    gb.commit_all("main: unrelated work")
    gb._git("merge", "-q", "--no-edit", "feature")
    assert (repo / "a.py").read_bytes() == b"def foo():\n    return 3\n"  # git's own merge result

    ideal = get(repo)
    materialized = code(ideal, Store(repo).all_ops())
    assert materialized["a.py"] == b"def foo():\n    return 3\n"  # NOT rolled back to the base v1


def test_init_on_large_corpus_repo_within_budgets(tmp_path):
    """R22/BET-E: opt-in only, skipped unless UGH_LARGE_CORPUS_REPO is set (see tests/laws/corpus.py)."""
    import shutil
    import time

    large_repo = corpus.large_corpus_repo()
    if large_repo is None:
        import pytest

        pytest.skip("UGH_LARGE_CORPUS_REPO not set -- opt-in BET-E large-repo check")

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
    """U22.5 / U9 regression: a single-clone, single-branch history where a file is added, deleted,
    then re-added. Under v2 both births claimed `(symbol, None)` -- a fork whose two tips
    `fork_free` dropped, so the live file vanished from `code(I)` (the ~20% closure loss). Under v3
    (U9) the re-add chains FROM the deletion via a salted bottom, forming ONE valid chain, so the
    file now *materializes completely* -- not merely 'survives on disk via the backstop'. The
    reduction is still surgical: an unrelated single-add symbol is untouched."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)

    (repo / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "notes.txt").write_text("alpha\n", encoding="utf-8")
    gb.commit_all("add keep and notes")

    (repo / "notes.txt").unlink()
    gb.commit_all("delete notes")

    (repo / "notes.txt").write_text("beta\n", encoding="utf-8")  # rebirth: chains from the deletion
    gb.commit_all("re-add notes with different content")

    ideal = get(repo)  # must not raise
    store = Store(repo)
    all_ops = store.all_ops()
    assert is_valid_ideal(all_ops, ideal.op_ids)  # grounded + fork-free

    # U9: the re-added file materializes completely with its live content -- the whole point of the
    # rebirth-chaining fix, upgraded from the pre-U9 "not deleted" assertion.
    assert any("keep.py::keep" in store.get(o).footprint for o in ideal.op_ids)
    materialized = code(ideal, all_ops)
    assert "keep.py" in materialized
    assert materialized["notes.txt"] == b"beta\n"

    # The persisted table is itself a valid ideal -- a second read never re-raises, and the pure
    # read path (which reads the table straight back) constructs cleanly too.
    assert get(repo).op_ids == ideal.op_ids
    from ugh.core.lens import current_ideal

    assert current_ideal(repo).op_ids == ideal.op_ids


# -- U2: fsck --tree classification (code(current_ideal) vs HEAD tree) -------------------------


def test_fsck_tree_clean_after_get_has_no_drift(tmp_path):
    """R2: the mined record reproduces HEAD, so `fsck --tree` finds zero drift."""
    from ugh.core.lens import fsck_tree
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    result = fsck_tree(repo)
    assert result["drift"] == []


def test_fsck_tree_rebirth_file_reproduced_not_drift(tmp_path):
    """R2: an add->delete->re-add file chains through its deletion, so `code(current_ideal)`
    reproduces it and `--tree` does not report it as drift."""
    from ugh.core.lens import fsck_tree
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
    assert "notes.txt" not in result["drift"]


def test_fsck_tree_reports_real_drift_for_a_foreign_edit(tmp_path):
    """R2: a committed edit ugh never absorbed (bytes at HEAD differ from `code(current_ideal)`)
    is real drift -- surfaced with a remedy direction, not hidden."""
    from ugh.core.lens import fsck_tree
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    # a foreign commit that changes a tracked file's bytes without going through ugh; pin the
    # recorded ideal/witness so `fsck_tree` compares the *old* ideal against the drifted HEAD.
    gb = GitBinding(repo)
    tracked = [p for p in _tracked_after(repo) if p.endswith(".py")][0]
    (repo / tracked).write_text("# drifted\n", encoding="utf-8")
    gb.commit_all("foreign edit outside ugh")

    result = fsck_tree(repo)
    assert result["drift"], "a foreign edit at HEAD should surface as real drift"


def _tracked_after(repo):
    import subprocess
    out = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l and not l.startswith(".sgt/")]


def test_a_history_mined_in_chunks_gives_the_ops_of_one_whole_mine(tmp_path, monkeypatch):
    """History is mined oldest first and every version is read from history, so mining it one
    commit per contact gives exactly the ops and the ideal of mining it in one go."""
    from ugh.core.lens import current_ideal

    whole = corpus.CORPUS["linear_history"].build(tmp_path / "whole")
    get(whole, unbounded=True)
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", 0.0)
    chunks = 0
    while not sync_status(repo)["complete"]:
        get(repo)
        chunks += 1
    assert chunks > 1
    assert {o.id for o in Store(repo).all_ops()} == {o.id for o in Store(whole).all_ops()}
    assert current_ideal(repo).op_ids == current_ideal(whole).op_ids


def test_fsck_tree_classifies_a_tracked_path_with_a_non_ascii_name(tmp_path):
    """F72: a tracked, in-scope file ugh cannot reproduce must appear in *some* `fsck --tree` class.
    `_tracked_paths` ran plain `git ls-files`, which C-quotes any path containing non-ASCII bytes
    (`"a/\\346\\234\\272.bin"`). That quoted literal names nothing on disk, so the `is_file()` guard in
    `_status_paths`' `to_delete` filter dropped it, and the path fell out of `backstop_kept`,
    `unmanaged`, and `drift` alike -- reported as nothing at all. On yanshengjia/ml-road that silently
    excused three tracked PDFs (5.6MB, 12MB, 35MB) that ugh never composes. `-z` is the fix."""
    from ugh.core.lens import fsck_tree

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    assert not fsck_tree(repo)["drift"], "fixture should start clean"

    # A binary blob under a non-ASCII name, committed and not mined yet: drift.
    name = "資料/機械学習.bin"
    (repo / "資料").mkdir()
    (repo / name).write_bytes(bytes(range(256)) * 4)
    GitBinding(repo).commit_all("add a blob with a non-ascii name")

    result = fsck_tree(repo)
    assert name in result["drift"], (
        f"tracked non-ASCII path vanished from every fsck class; got "
        f"{ {k: v for k, v in result.items() if v} }"
    )


def test_an_edit_landing_mid_sync_is_not_cached_as_already_mined(tmp_path, monkeypatch):
    """F130: the no-op gate's fingerprint must describe the tree that was actually *mined*, not the
    tree as it stands when the entry is written.

    `_sync` reads the working tree twice: once up front, to decide `include_dirty` (R16), and once
    at the end, inside `_sync_fingerprint`, to key the cache entry. Those are seconds apart on a
    real repo, and an edit landing between them was recorded as "this dirty tree mines to <the
    committed-only ids>" -- a claim that was never true. The gate then served it on every later
    contact, so `status` reported drift while `save` answered "nothing to save", and only a *further*
    edit could move the fingerprint and clear it. A participant whose editor was mid-sync when
    `./stage 1` applied its patch could not record the work at all (2026-09-01).

    The fingerprint now carries the mine-time digest, so a tree that moved mid-sync simply misses
    the gate on the next contact and gets mined."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(repo)  # warm the gate on a clean tree

    # A new commit moves HEAD, so the next `get()` runs the full sync body rather than the gate.
    (repo / "b.py").write_text("def bar():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add bar")

    # Land the edit *during* that sync -- after the `has_dirty_source()` sample that decides
    # `include_dirty`, before the cache entry is fingerprinted. `_reduced_ideal_ids` runs between
    # the two, which is what makes this the real interleaving rather than an approximation of it.
    real_reduce = lens_mod._reduced_ideal_ids

    def land_edit_mid_sync(*args, **kwargs):
        (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
        return real_reduce(*args, **kwargs)

    monkeypatch.setattr(lens_mod, "_reduced_ideal_ids", land_edit_mid_sync)
    raced = get(repo)  # never saw the edit: it arrived after the dirty-source sample
    monkeypatch.undo()

    assert gb.has_dirty_source()  # the edit is on disk and uncommitted

    calls = _count_snapshot_calls(monkeypatch)
    recovered = get(repo)  # the very next contact must mine it (R9)
    assert calls["n"] >= 1, "the gate served a cache entry keyed to a tree it never mined"
    assert recovered.op_ids != raced.op_ids
