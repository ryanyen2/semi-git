"""Tests for sgt.core.lens -- get/put integration with git (plan U6/U7.5, R8/R9/R10/R20).

`get()` mines committed history *and* the current uncommitted working tree (dirty edits land as
pending ops with empty provenance, folded into the returned ideal but never persisted to
`.sgt/local/ideal.json`); `put()` refuses to overwrite an unabsorbed dirty change rather than
clobbering it (U7.5, closing the two gaps FINDINGS.md flagged under its 2026-07-07 U6 entry).
"""

from __future__ import annotations

import time

import pytest

from sgt.core.fold import code
from sgt.core.ideal import Ideal
import sgt.core.lens as lens_mod
from sgt.core.lens import (
    DirtyWorkingTreeError,
    _load_backfill_state,
    _load_ideal_table,
    _ref_key,
    _save_backfill_state,
    _save_ideal_table,
    get,
    init,
    ops_with_frontier_images,
    put,
    sync_status,
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


def test_ops_with_frontier_images_skips_a_corrupt_frontier_op(tmp_path, monkeypatch):
    """R1 read-side skip: a truncated/garbled frontier op file must degrade to a drop, not error
    the read views (`status_view`, `fsck_tree`, `_reproducible_content`) built on this. The op is
    excluded entirely rather than kept footprint-only -- its `images={}` would fold to silent
    zero-length content for the symbols it produces, a worse failure than its absence.

    The index is pinned to the pre-corruption footprint list so the corrupt file is still named as a
    frontier producer; otherwise `index_ops`' self-heal would drop it before `_safe_get` ever ran
    (that path is itself fine, but it wouldn't exercise the read-side skip this pins)."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    put(repo, ideal)
    ideal = lens_mod.current_ideal(repo)

    store = Store(repo)
    all_ops = store.all_ops()
    frontier_ids = set(ideal.frontier(all_ops).values())
    assert frontier_ids  # the fixture has live symbols at the frontier
    victim = sorted(frontier_ids)[0]

    monkeypatch.setattr("sgt.core.opindex.index_ops", lambda _repo: all_ops)
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


def test_second_get_with_no_new_commits_does_not_rewrite_ideal_and_witness(tmp_path):
    """A `get()` that mines nothing new must not touch `.sgt/local/ideal.json` or
    `.sgt/local/witness.json` on disk -- both are `.sgt/**/*.json` paths the VS Code extension
    watches to invalidate its cache, so an unconditional rewrite on every read (even a no-op one)
    makes every refresh retrigger another refresh, forever."""
    import sgt.state as state

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
    """1.1 (F11/F20): after reverting an op, rewriting git history so the reverted op's content is
    re-mined under a *new* commit sha (a rebase) must not silently resurrect it. Before 1.1, `_sync`
    trusted the persisted table as a base and only *unioned* freshly-mined provenance onto it, so
    the re-mined op came back. Now the ideal is derived as `reduce(provenance-in-ancestry −
    exclusions)`, and the revert is a positive exclusion that survives the sha rewrite."""
    import json

    from sgt.core import verbs

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
    verbs.revert(repo, bar.id)
    assert bar.id not in get(repo).op_ids  # revert took effect
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
    assert bar.id not in after.op_ids, "reverted op resurrected by the history rewrite"
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
    A record that short can no longer reproduce the committed tree, so the next `sgt revert` refused
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


def test_resync_re_derives_the_ideal_after_a_backward_history_rewrite(tmp_path):
    """P0-B (launch): a *backward* rewrite (`git reset --hard` to an earlier commit, `branch -f`)
    drops commits from HEAD's ancestry, but the persisted `.sgt/local/ideal.json` still names the
    ops those commits witnessed -- so `get()` keeps returning the vanished symbols (`log`/`--map`
    show ghosts, a later `save` can dead-end). `get()` alone does NOT self-heal here (it unions
    freshly-mined provenance onto the persisted base). `resync` drops just this ref's derived local
    state and re-mines, so the ideal re-derives from what HEAD actually reaches now."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("c1: foo")
    c1 = gb.head()
    (repo / "a.py").write_text(
        "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n", encoding="utf-8"
    )
    gb.commit_all("c2: bar")

    seeded = get(repo)  # ideal knows both foo and bar
    key = _ref_key(gb) or gb.head()
    assert any("a.py::bar" in o.footprint and o.id in seeded.op_ids for o in Store(repo).all_ops())
    before_count = len(_load_ideal_table(repo)[key])

    # backward rewrite: drop c2 entirely, so bar's introducing commit leaves HEAD's ancestry
    _git(repo, "reset", "--hard", c1)

    # get() does not self-heal a backward desync -- the dropped op is still in the ideal
    stale = get(repo)
    assert any(
        "a.py::bar" in o.footprint and o.id in stale.op_ids for o in Store(repo).all_ops()
    ), "expected the pre-fix desync: bar still present before resync"

    res = lens_mod.resync(repo)
    assert res["key"] == key
    assert res["after"] < res["before"]  # ops shrank: the dropped commit's ops fell out

    healed = get(repo)
    assert not any(
        "a.py::bar" in o.footprint and o.id in healed.op_ids for o in Store(repo).all_ops()
    ), "resync must re-derive the ideal from current HEAD -- bar's commit is gone"
    assert any("a.py::foo" in o.footprint and o.id in healed.op_ids for o in Store(repo).all_ops())
    assert len(_load_ideal_table(repo)[key]) < before_count
    assert b"def bar" not in (repo / "a.py").read_bytes()


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


def test_backfill_state_round_trips(tmp_path):
    """`_save_backfill_state`/`_load_backfill_state` are pure passthrough plumbing over
    `.sgt/local/backfill.json` -- a later unit reads/writes the genesis-backfill frontier through
    them, but this unit only wires the persistence, not any mining behavior."""
    repo = tmp_path / "repo"
    table = {"refs/heads/main": {"genesis_frontier": "abc123", "reached_genesis": False}}
    _save_backfill_state(repo, table)
    assert _load_backfill_state(repo) == table


def test_backfill_state_missing_file_returns_empty_dict(tmp_path):
    """Symmetric with `_load_witnesses`'s missing-file behavior: a fresh repo where
    `_save_backfill_state` was never called loads as `{}`, not an error."""
    repo = tmp_path / "repo"
    assert _load_backfill_state(repo) == {}


def test_sync_status_reports_complete_on_a_fully_synced_ref(tmp_path):
    """A freshly-mined ref with nothing left to backfill: `sync_status` (U6, a pure read -- no
    mining) reports both `complete` and `reached_genesis` true."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    assert sync_status(repo) == {"complete": True, "reached_genesis": True,
                                "history_rewritten": False}


def test_sync_status_reports_incomplete_while_a_first_contact_chunk_is_still_backfilling(tmp_path, monkeypatch):
    """A ref whose very first `get()` chunk gets deadline-cut short (U1/U4's chunking) has its
    witness bootstrapped to head immediately but its genesis backfill still open -- `sync_status`
    must report `complete=False` even though the ref is already forward-current."""
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", -1.0)  # deadline already past every check

    get(repo)

    status = sync_status(repo)
    assert status["reached_genesis"] is False
    assert status["complete"] is False


def test_sync_survives_a_witness_planted_without_backfill_state(tmp_path):
    """A witness can land at a ref-key that never went through `_sync`'s own backward walk: every
    materializing verb (`put()` + `record_ideal`, mirrored here) advances `witness[key]` straight
    to the commit `put()` just made, and `record_ideal` never touches `backfill.json`. The next
    ordinary `get()` on that same key then finds `prev_head == head` with a *missing* backfill
    entry -- defaulted to `{"genesis_frontier": None, "reached_genesis": False}`. This key shape is
    a detached-HEAD/throwaway-key artifact (every commit under a detached HEAD mints its own
    never-reused `_ref_key`, e.g. `land`'s per-session worktrees): nothing ever queries
    `sync_status` for a discarded commit sha, so `_sync` deliberately leaves it alone rather than
    paying for a backward walk that would also have to survive `land`'s R7 rollback (which restores
    the git-tracked worktree but never touches gitignored `.sgt/local/*.json`, see
    `test_cas_exhaustion_restores_and_persists_nothing`). The one hard requirement is that this
    shape must not crash `_sync` (no `gb.parent_of(None)` TypeError from mistaking a missing
    backfill record for one that needs a fresh genesis walk)."""
    from sgt.core.lens import record_ideal

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    gb = GitBinding(repo)
    gb._git("checkout", "-q", "--detach")

    ideal = get(repo)  # bootstraps *this* detached head's own witness + backfill state
    put_sha = put(repo, ideal, message="materialize")  # a new commit; HEAD moves past it
    record_ideal(repo, ideal, put_sha)  # plants witness[put_sha] = put_sha directly (no ref_key)

    key = _ref_key(gb)
    assert key == put_sha
    assert key not in _load_backfill_state(repo), "record_ideal should not seed backfill state"

    get(repo)  # must not raise TypeError from gb.parent_of(None)

    assert key not in _load_backfill_state(repo), "a throwaway detached key stays a no-op, not a walk"


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


def test_a_dirty_ignored_tier_file_does_not_block_a_materializing_edit(tmp_path):
    """A tracked path sgt deliberately never mines -- a dot-path, a gitignored path, a lockfile:
    the `ignored` tier -- is outside sgt's remit, not a path the ideal dropped. `code()` never
    produces it, so the old guard read "tracked but absent from `materialized`" as "the ideal
    deletes this" and refused every `put()` while it was dirty. One uncommitted `.gitignore` line
    then blocked `save`, `undo`, `revert --yes` and `restore` alike, and the remedy the error named
    (`sgt save`) answered `nothing to save` because an ignored path mints no op -- a loop with no
    way out that does not go through git."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    gb.commit_all("add foo and a gitignore")
    baseline = get(repo)

    (repo / ".gitignore").write_text("__pycache__/\n.DS_Store\n", encoding="utf-8")  # dirty, unminable

    put(repo, baseline, message="an edit while the gitignore is dirty")

    # The fold neither refused nor touched it: an ignored path is sgt's to leave alone entirely.
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "__pycache__/\n.DS_Store\n"
    assert (repo / "a.py").is_file()


def test_put_refuses_to_roll_back_committed_drift_outside_the_edit_delta(tmp_path):
    """Phase-0 0.1 (F7/F9): a one-symbol edit's fold rewrites *every* covered path, so a file whose
    committed on-disk bytes drifted from sgt's recorded ideal (a merge/cherry-pick the miner
    mis-attributed) is silently rolled back to the stale ideal's content. The delta-scoped guard
    refuses when a path OUTSIDE the `before Δ after` op-delta would be rewritten, and names it. The
    drift here is committed (on-disk == HEAD), so `_dirty_conflicts` structurally cannot catch it --
    only the delta guard does."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def bar():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo and bar")
    (repo / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")  # bar gains a modify op
    gb.commit_all("modify bar")
    full = get(repo)
    store = Store(repo)

    # Pin the recorded ideal to EXCLUDE bar's modify op: now `code(current_ideal)` for b.py is the
    # stale `return 1`, while b.py on disk (and at HEAD) is the committed `return 2`. This is the
    # committed drift F7 leaves after a mis-mined merge -- reconstructed here at the unit boundary.
    def _is_bar_modify(op):
        fp = op.footprint.get("b.py::bar")
        return fp is not None and fp[0] is not None

    bar_modify = next(oid for oid in full.op_ids if _is_bar_modify(store.get(oid)))
    foo_add = next(oid for oid in full.op_ids if "a.py::foo" in store.get(oid).footprint)
    pinned = full.op_ids - {bar_modify}
    table = _load_ideal_table(repo)
    table[_ref_key(gb)] = sorted(pinned)
    _save_ideal_table(repo, table)

    # Now edit only a.py's scope (drop foo). b.py is OUTSIDE this delta, and its on-disk bytes
    # differ from what the (pinned, stale) ideal materializes -> put must refuse rather than roll
    # b.py back to `return 1`.
    edited = Ideal.from_ops(pinned - {foo_add}, store.all_ops())
    with pytest.raises(DirtyWorkingTreeError) as exc:
        put(repo, edited)
    assert "b.py" in str(exc.value)
    assert (repo / "b.py").read_bytes() == b"def bar():\n    return 2\n"  # never rolled back


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


def test_put_does_not_false_refuse_an_in_sync_unrelated_file(tmp_path):
    """The 0.1 guard is delta-scoped: on an in-sync repo an unrelated file's on-disk bytes already
    equal what the ideal materializes, so a one-symbol revert never trips the drift guard for it.
    Guards against over-refusal breaking every normal materializing verb."""
    from sgt.core import verbs

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (repo / "b.py").write_text("def bar():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo and bar")
    ideal = get(repo)
    put(repo, ideal)  # in sync: disk == code(current_ideal) everywhere

    verbs.revert(repo, "a.py::foo")  # a one-file edit; must not refuse over the in-sync b.py

    assert (repo / "b.py").read_bytes() == b"def bar():\n    return 1\n"  # unrelated file untouched


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
    from sgt.core.lens import current_ideal

    assert current_ideal(repo).op_ids == ideal.op_ids


def test_local_rebuild_records_a_genuine_fork_in_the_shared_store(tmp_path):
    """1.4 (F7/F8 residual): merge-aware mining (1.3) stops a *clean* merge from forking, but a merge
    that genuinely *conflicts* on one symbol still lands both divergent tips in committed history -- a
    real fork. `fork_free` parks the symbol at the common ancestor by dropping both tips, and used to
    do so *silently* (`order.py`), so `sgt forks`/`resolve` saw nothing to reconcile. The local
    rebuild must now record what it parked in the one shared fork store, exactly as sync/land do --
    park AND report, never silently exclude."""
    from sgt import state

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("base: foo v1")
    get(repo)  # mine the base
    main = gb.symbolic_ref().rsplit("/", 1)[-1]

    # feature and main each edit foo from the SAME base version -> a real conflict on merge
    gb._git("checkout", "-q", "-b", "feature")
    (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    gb.commit_all("feature: foo v2")

    gb._git("checkout", "-q", main)
    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    gb.commit_all("main: foo v3")

    # a conflicting merge; resolve to main's side and complete the two-parent merge commit by hand.
    gb._git("merge", "--no-edit", "feature", check=False)  # exits non-zero: conflict
    (repo / "a.py").write_text("def foo():\n    return 3\n", encoding="utf-8")
    gb._git("add", "a.py")
    gb._git("commit", "--no-edit")

    ideal = get(repo)  # cold rebuild encounters the genuine fork
    all_ops = Store(repo).all_ops()
    assert is_valid_ideal(all_ops, ideal.op_ids)  # parked -> still a valid ideal, never a crash
    # the symbol parked at its common ancestor: both divergent tips dropped, so foo reads v1.
    assert code(ideal, all_ops)["a.py"] == b"def foo():\n    return 1\n"

    # ...and the parked fork is surfaced in the one shared store, not silently excluded.
    records = state.load_json(repo, "forks", default=[])
    assert any(r["symbol"] == "a.py::foo" for r in records)


# -- U2: mining-fidelity marks (which commits reduce_to_ideal could not fully reconstruct) -----

def test_fidelity_marks_the_commits_of_a_reduction_drop(tmp_path):
    """U2/R6: a two-clone same-symbol fork lands both tips in one ref's history but neither in the
    reduced ideal (`reduce_to_ideal`/`fork_free` drops them). `_record_fidelity` marks the commits
    that witnessed the dropped ops, so `grid_view` flags them "partial" rather than silently
    omitting the loss. Deterministic: the recorded set is stable across repeated `get()`s."""
    from sgt import state
    from sgt.core import sync
    from tests.core.test_sync import _BASE, _edit_and_commit, _push, _two_clones

    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", "def foo():\n    return 999\n\n\ndef bar():\n    return 2\n", "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", "def foo():\n    return 42\n\n\ndef bar():\n    return 2\n", "B: rework foo")
    report = sync.sync(b, remote="origin", branch="main")
    assert report.forks  # the fork was surfaced (both tips excluded from the ideal)

    get(b)  # records fidelity for the post-fork ideal (sync's own get() ran before the fork existed)
    key = _ref_key(GitBinding(b))
    marks = state.load_json(b, "fidelity", default={})
    assert marks[key]["shas"]  # the dropped fork tips' witnessing commits are recorded, non-empty

    get(b)  # deterministic — a second reduction records the identical set
    assert state.load_json(b, "fidelity", default={}) == marks


def test_fidelity_is_empty_on_clean_linear_history(tmp_path):
    """A single-clone linear history reconstructs fully -- no fork, nothing ungrounded -- so
    `reduce_to_ideal` drops nothing and no commit is marked partial."""
    from sgt import state

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    marks = state.load_json(repo, "fidelity", default={})
    assert all(not e["shas"] for e in marks.values())


def test_fidelity_does_not_mark_a_user_revert(tmp_path):
    """The correctness distinction (R6): a revert removes an op from the *persisted* ideal but not
    from the ref's raw provenance union, so `included \\ reduce_to_ideal(included)` never contains
    it -- an intentional edit is never mistaken for a reconstruction loss. Reverting on clean
    history leaves the marks empty."""
    from sgt import state
    from sgt.core import verbs

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    verbs.revert(repo, "c.py::qux")  # a leaf symbol — removes it from the ideal
    get(repo)
    marks = state.load_json(repo, "fidelity", default={})
    assert all(not e["shas"] for e in marks.values())  # the revert created no spurious partial mark


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


def test_put_reproduces_a_rebirth_file_via_its_chain(tmp_path):
    """AE1 across the Phase-A -> Phase-D transition: pre-U9 an add->delete->re-add history dropped
    both `notes.txt` births as a fork and the U1 *backstop* kept the live file on disk; post-U9 the
    re-add chains FROM the deletion into one valid chain, so the path is now reproducible from the
    ideal and `put` reproduces it directly -- rebirth no longer needs the backstop at all. (The
    backstop mechanism itself is now only reachable via a genuine multi-clone content fork -- a
    dedicated fixture for it is a U1/U2 follow-up; see FINDINGS.) `put` leaves the live bytes intact."""
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
    assert code(ideal, Store(repo).all_ops())["notes.txt"] == b"beta\n"  # U9: reproduced via the chain
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


def test_fsck_tree_rebirth_file_reproduced_not_drift_or_backstop(tmp_path):
    """R2 across the Phase-A -> Phase-D transition: pre-U9 an add->delete->re-add file was dropped
    from `code(I)` and `--tree` classified it backstop-kept; post-U9 (U9) the re-add chains into one
    valid chain, so the file is reproduced by `code(current_ideal)` and matches the HEAD tree -- no
    drift and no backstop entry. `--tree` must not misreport the now-reproducible file as drift (AE1)."""
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
    assert "notes.txt" not in result["drift"]           # reproduced cleanly, never misreported
    assert "notes.txt" not in result["backstop_kept"]   # U9: no longer needs the backstop


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


def test_a_chunked_backfill_admits_every_op_the_finished_store_can_ground(tmp_path, monkeypatch):
    """A history mined in several deadline-cut chunks must end at the same ideal as one mined whole.

    Backward backfill reduces after every chunk, and an op whose producer is older than the chunk
    boundary is legitimately ungrounded *at that moment* -- so reduction drops it. The drop has to be
    reconsidered once the producer lands, and it was not: `base_ids` is the previous reduced answer, so
    the dropped op is in neither `base_ids` nor `new_committed_ids` on the next chunk and never comes
    back (F68 layer 2). Worse, once `reached_genesis` flips, the no-op gate's fingerprint covers HEAD,
    the working tree, and the ideal entry but *not* the op store -- so no later read recomputes either
    (F68 layer 1). Measured on a real corpus repo this cost ~17 points of byte-exact reconstruction at
    the median.

    The invariant is chunk-invariance of the *result*, so this asserts against `reduce_to_ideal` over
    the finished store rather than a hardcoded count: whatever the store can ground, the persisted
    ideal must admit.
    """
    from sgt.core import order
    from sgt.core.lens import current_ideal

    # Self-calibrate the chunk budget instead of hardcoding one: a fixed number starves the walk on a
    # slow machine (below one commit's cost, no chunk ever mines anything and the frontier never moves)
    # and lets a fast one finish in a single chunk, which would make this test pass while testing
    # nothing. Time one whole mine on a throwaway copy, then give each chunk a third of it.
    warmup = corpus.CORPUS["linear_history"].build(tmp_path / "warmup")
    started = time.monotonic()
    get(warmup)
    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", (time.monotonic() - started) / 3)

    # Shrink the budget until the walk actually takes more than one chunk, rather than calibrating
    # once and asserting. The warm-up mine leaves the page cache hot, so the timed run is routinely
    # faster than the run it was timing, and a third of it can still swallow the whole history in
    # one go. That is not a failure of the invariant under test, it is the fixture failing to set
    # itself up, and it was failing CI on whichever Python version happened to get the fastest
    # runner while the other two passed.
    budget = lens_mod._CHUNK_BUDGET_SECONDS
    for attempt in range(6):
        monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", budget / (4 ** attempt))
        repo = corpus.CORPUS["linear_history"].build(tmp_path / f"repo{attempt}")
        chunks = 0
        for _ in range(60):
            get(repo)
            chunks += 1
            if sync_status(repo)["reached_genesis"]:
                break
        if sync_status(repo)["reached_genesis"] and chunks > 1:
            break
    else:
        pytest.skip("could not force a multi-chunk mine on this machine; the invariant is untested "
                    "here rather than violated")

    assert sync_status(repo)["reached_genesis"], "chunked walk never finished; test cannot conclude"
    assert chunks > 1, "history mined in one chunk; test cannot conclude"

    ops = Store(repo).all_ops()
    groundable = set(order.reduce_to_ideal(frozenset(o.id for o in ops), ops))
    assert groundable, "no groundable ops; fixture mined nothing"
    admitted = set(current_ideal(repo).op_ids)
    assert admitted == groundable, (
        f"chunked mining left {len(groundable - admitted)} groundable ops out of the ideal "
        f"(admitted {len(admitted)} of {len(groundable)})"
    )


def test_fsck_tree_classifies_a_tracked_path_with_a_non_ascii_name(tmp_path):
    """F72: a tracked, in-scope file sgt cannot reproduce must appear in *some* `fsck --tree` class.
    `_tracked_paths` ran plain `git ls-files`, which C-quotes any path containing non-ASCII bytes
    (`"a/\\346\\234\\272.bin"`). That quoted literal names nothing on disk, so the `is_file()` guard in
    `_status_paths`' `to_delete` filter dropped it, and the path fell out of `backstop_kept`,
    `unmanaged`, and `drift` alike -- reported as nothing at all. On yanshengjia/ml-road that silently
    excused three tracked PDFs (5.6MB, 12MB, 35MB) that sgt never composes, and because the evaluation's
    denominator counts them as in scope, they scored as *successes*: 10 points of inflation on a 30-file
    repo. Same `is_file()`-swallows-a-misparsed-path shape as the V4 harness defect, in the one function
    whose job is to report what sgt cannot reproduce. `-z` is the fix; symlink entries stay in the list
    because `unmanaged` is built from them."""
    from sgt.core.lens import fsck_tree

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    ideal = get(repo)
    put(repo, ideal)
    assert not fsck_tree(repo)["drift"], "fixture should start clean"

    # A binary blob under a non-ASCII name, committed outside sgt: nothing in the store can
    # regenerate its bytes, so it is exactly a `backstop_kept` path.
    name = "資料/機械学習.bin"
    (repo / "資料").mkdir()
    (repo / name).write_bytes(bytes(range(256)) * 4)
    GitBinding(repo).commit_all("add a blob with a non-ascii name")

    result = fsck_tree(repo)
    assert name in result["backstop_kept"], (
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
    # `include_dirty`, before the cache entry is fingerprinted. `_record_parked_forks` sits between
    # the two, which is what makes this the real interleaving rather than an approximation of it.
    real_parked = lens_mod._record_parked_forks

    def land_edit_mid_sync(*args, **kwargs):
        (repo / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
        return real_parked(*args, **kwargs)

    monkeypatch.setattr(lens_mod, "_record_parked_forks", land_edit_mid_sync)
    raced = get(repo)  # never saw the edit: it arrived after the dirty-source sample
    monkeypatch.undo()

    assert gb.has_dirty_source()  # the edit is on disk and uncommitted

    calls = _count_snapshot_calls(monkeypatch)
    recovered = get(repo)  # the very next contact must mine it (R9)
    assert calls["n"] >= 1, "the gate served a cache entry keyed to a tree it never mined"
    assert recovered.op_ids != raced.op_ids
