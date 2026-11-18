"""The collaboration laws (design doc 2026-07-10-collaboration-and-review.md §8, plan C12).

`tests/core/test_sync.py` proves one two-clone sync does the right thing on a handful of hand-built
scenarios. This module generalizes that rig to N replicas (2-3) over a bare remote and asserts the
*algebraic* laws the design doc names -- that sync's outcome is a function of the delivered op and
name sets and nothing else (order, schedule, or which replica initiated). Each law from §8 has a named,
findable home here from this freeze unit (U16) onward, so every later behavior change is measured
against a red or green test that already exists:

- LAW-0 (replica determinism): same history, same miner -> byte-identical op stores. GREEN.
- LAW-U (order independence): any two schedules delivering the same op sets converge to identical
  `.sgt/` state (stores, orders, names). GREEN.
- LAW-I (idempotence): sync twice = sync once. GREEN.
- LAW-F (fork completeness & soundness): sync reports a fork iff the union forks a chain. GREEN.
- LAW-R (resolutions travel): a resolved fork stays resolved on every replica. Red today -- forks
  abort sync and no durable shared fork record exists to hang a resolution on (xfail, U20/U21).
- LAW-G (green-to-green): a shared tip only advances to an oracle-green op-set. GREEN as of U23 --
  `sync` advances a branch ungated *by design*, so enforcement is land-mediated: `ugh land`'s CAS
  advances `refs/heads/<branch>` only after the exact resulting op-set passes the oracle.
- LAW-L (locality): sync never moves a replica's HEAD selection out from under it. GREEN.

Hermetic discipline matches `tests/laws/corpus.py` and `tests/core/test_sync.py`: real ``git``
subprocess calls, no mocks, no network, no wall-clock/LLM dependency. Where a law compares op-store
*bytes* across replicas (LAW-0), the history is built from `tests/laws/corpus.py`'s pinned-SHA
fixtures so provenance (commit shas, folded into the op file) is itself deterministic.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from ugh import state
from ugh.core import lens, sync
from ugh.core.lens import get
from ugh.core.store import Store
from ugh.store.gitbind import GitBinding
from tests.conftest import _clone, _init_bare, _push
from tests.laws import corpus


# --- N-replica rig (generalizes tests/core/test_sync.py's two-clone helpers) -------------------
# The git-plumbing primitives (`_init_bare`/`_clone`/`_push`) are the shared, state-ref-aware
# fixtures from `tests/conftest.py` (Phase 1.2 Step 3): `_push` publishes + pushes `refs/ugh/state`
# alongside the branch and `_clone` fetches + materializes it, so the op store and committed tables --
# which now live on the ref, off the branch tree -- actually travel between these replicas. The
# higher-level `_replicas`/`_edit_and_commit` stay local (their shapes are suite-specific).



def _revert(repo, op_id):
    from ugh.core import planner

    steps = [st for st, ops in planner._Ctx(repo).steps.items() if op_id in ops]
    return planner.apply(repo, planner.plan(repo, steps, target=op_id))

def _edit_and_commit(repo: Path, path: str, content: str, message: str) -> str:
    """A real commit that carries `Sgt-Op:` trailers, exactly like `lens.put` does outside tests
    (a ref's tip must carry them for `sync` to read its ideal without a checkout -- see
    `tests/core/test_sync.py`)."""
    (repo / path).write_text(content, encoding="utf-8")
    content_sha = GitBinding(repo).commit_all(message)
    ideal = lens.get(repo)
    lens.put(repo, ideal, message=f"ugh: mine {message}")
    return content_sha


def _replicas(tmp_path: Path, base: str, n: int) -> tuple[Path, list[Path]]:
    """A bare remote plus `n` clones, all past one shared init commit that writes `main.py`."""
    remote = _init_bare(tmp_path)
    first = _clone(remote, tmp_path / "r0")
    lens.init(first)
    _edit_and_commit(first, "main.py", base, "init")
    _push(first)
    clones = [first]
    for i in range(1, n):
        c = _clone(remote, tmp_path / f"r{i}")
        lens.get(c)  # baseline mine, mirrors a fresh teammate clone
        clones.append(c)
    return remote, clones


def _ops_bytes(repo: Path) -> dict[str, bytes]:
    """Every committed op file (`.sgt/ops/<id>`) as raw bytes -- the content LAW-0/LAW-U compare."""
    ops_dir = repo / ".sgt" / "ops"
    if not ops_dir.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in ops_dir.iterdir() if p.is_file()}


def _sync_and_push(repo: Path) -> sync.SyncReport:
    """One replica absorbs the shared branch and, if it advanced, publishes the result. Sequential
    use (one replica at a time) is race-free: origin only moves via the replica currently holding
    the floor, so a push is never rejected for concurrency."""
    report = sync.sync(repo, remote="origin", branch="main")
    ahead = report.merged or GitBinding(repo).head() != GitBinding(repo).rev_parse("origin/main")
    if ahead:
        _push(repo)
    return report


_BASE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
_THREE = "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n\n\ndef baz():\n    return 3\n"


# --- LAW-0: replica determinism (GREEN) --------------------------------------------------------


@pytest.mark.parametrize("case", ["linear_history", "class_with_methods", "commuting_features"])
def test_law0_independent_clones_mine_to_byte_identical_op_stores(case, tmp_path):
    """§2.1 / LAW-0: two independent miners over the *same* history produce byte-identical op
    stores. Built from `tests/laws/corpus.py`'s pinned-SHA fixtures so even provenance (the witness
    commit shas folded into each op file) is deterministic -- the whole `.sgt/ops/` tree matches
    file-name-for-file-name and byte-for-byte, which is what makes op-store union meaningful."""
    repo_a = corpus.CORPUS[case].build(tmp_path / "a")
    repo_b = corpus.CORPUS[case].build(tmp_path / "b")
    get(repo_a)
    get(repo_b)

    ops_a, ops_b = _ops_bytes(repo_a), _ops_bytes(repo_b)
    assert ops_a and ops_a.keys() == ops_b.keys()  # same op ids mined on both
    assert ops_a == ops_b  # ... down to the last byte, provenance included


# --- LAW-U: order independence -----------------------------------------------------------------


def test_law_u_disjoint_edits_converge_under_a_randomized_schedule(tmp_path):
    """LAW-U (the half that holds today): three replicas each edit a *different* symbol, then sync
    in a randomized, seeded round-robin over the shared remote. The commutative core -- op-store
    union plus a re-fold -- must land every replica on byte-identical op stores and an identical
    working tree regardless of the delivery order."""
    _remote, (a, b, c) = _replicas(tmp_path, _THREE, 3)

    _edit_and_commit(a, "main.py", _THREE.replace("return 1", "return 100"), "A: bump foo")
    _edit_and_commit(b, "main.py", _THREE.replace("return 2", "return 200"), "B: bump bar")
    _edit_and_commit(c, "main.py", _THREE.replace("return 3", "return 300"), "C: bump baz")

    replicas = [a, b, c]
    rng = random.Random(20260710)
    for _round in range(4):  # a few gossip passes in a shuffled order reach the fixpoint
        for repo in rng.sample(replicas, len(replicas)):
            _sync_and_push(repo)

    ops = [_ops_bytes(r) for r in replicas]
    assert ops[0] and ops[0] == ops[1] == ops[2]  # identical op stores, schedule-independent
    trees = [(r / "main.py").read_text(encoding="utf-8") for r in replicas]
    assert trees[0] == trees[1] == trees[2]
    for want in ("return 100", "return 200", "return 300"):
        assert want in trees[0]  # all three disjoint edits survived the union


def test_law_u_names_converge_across_a_two_clone_sync(tmp_path):
    """LAW-U for the names table: two replicas each name a different step of the shared base; after
    each has synced the other's state, both hold the identical table."""
    from ugh import names

    _remote, (a, b) = _replicas(tmp_path, _BASE, 2)
    foo, bar = (next(op.id for op in Store(a).all_ops() if f"main.py::{n}" in op.footprint)
                for n in ("foo", "bar"))
    names.name(a, [foo], "checkout")
    _push(a)
    names.name(b, [bar], "billing")
    sync.sync(b, remote="origin", branch="main")
    _push(b)
    sync.sync(a, remote="origin", branch="main")
    assert names.load(a) == names.load(b) == {foo: "checkout", bar: "billing"}


# --- LAW-I: idempotence (GREEN) ----------------------------------------------------------------


def test_law_i_sync_twice_equals_sync_once(tmp_path):
    """LAW-I: after a schedule delivers a teammate's disjoint edit, re-running the sync is a pure
    no-op -- 'already up to date', with the op store and working tree byte-for-byte unchanged. This
    is what lets a sync loop run to a fixpoint (design doc §8; mirrors test_sync's idempotence)."""
    _remote, (a, b) = _replicas(tmp_path, _BASE, 2)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 100"), "A: bump foo")
    _push(a)

    first = sync.sync(b, remote="origin", branch="main")
    assert first.merged
    ops_once = _ops_bytes(b)
    tree_once = (b / "main.py").read_text(encoding="utf-8")

    second = sync.sync(b, remote="origin", branch="main")
    assert not second.merged and second.message == "already up to date"
    assert _ops_bytes(b) == ops_once  # nothing appended
    assert (b / "main.py").read_text(encoding="utf-8") == tree_once  # nothing re-folded differently


# --- LAW-F: fork completeness & soundness (GREEN) ----------------------------------------------


def test_law_f_sync_reports_a_fork_iff_the_union_forks_a_chain(tmp_path):
    """LAW-F: sync reports a fork *iff* two in-ideal ops claim the same chain step. Both halves:
    disjoint edits union with an *empty* fork set (soundness -- no phantom forks), while two
    replicas reworking the *same* symbol from the same base surface exactly one fork on that symbol
    (completeness -- the real divergence is caught)."""
    # Soundness: footprint-disjoint edits are not a fork.
    _remote, (a, b) = _replicas(tmp_path, _BASE, 2)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 100"), "A: bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", _BASE.replace("return 2", "return 200"), "B: bump bar")
    clean = sync.sync(b, remote="origin", branch="main")
    assert clean.merged and clean.forks == ()

    # Completeness: same-symbol divergence surfaces exactly one fork, naming that symbol.
    remote2 = tmp_path / "fork"
    _remote2, (c, d) = _replicas(remote2, _BASE, 2)
    _edit_and_commit(c, "main.py", _BASE.replace("return 1", "return 999"), "C: rework foo")
    _push(c)
    _edit_and_commit(d, "main.py", _BASE.replace("return 1", "return 42"), "D: rework foo")
    forked = sync.sync(d, remote="origin", branch="main")
    assert not forked.merged
    assert len(forked.forks) == 1
    symbol, _tip_c, _tip_d = forked.forks[0]
    assert symbol == "main.py::foo"


# --- LAW-R: resolutions travel (GREEN as of U20 -- the fork is durable shared state) -----------


def test_law_r_a_surfaced_fork_is_durable_shared_state(tmp_path):
    """LAW-R precondition -- 'a fork resolved on any replica is resolved on every replica' can only
    hold if the fork is durable, committed, shared state a resolution attaches to. As of U20's
    divergence-as-state, sync records the surfaced fork in committed `.sgt/forks.json` (travelling
    with the repo) instead of aborting, so a resolution computed on one replica has a shared object
    to record against. This asserts the fork is durably recorded; travelling resolutions (LAW-R's
    second half) build on it in U21."""
    _remote, (a, b) = _replicas(tmp_path, _BASE, 2)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 999"), "A: rework foo")
    _push(a)
    _edit_and_commit(b, "main.py", _BASE.replace("return 1", "return 42"), "B: rework foo")

    report = sync.sync(b, remote="origin", branch="main")
    assert report.forks  # the fork is seen...
    assert (b / ".sgt" / "forks.json").is_file()  # ...and recorded, so it is resolvable-shared


# --- LAW-G: green-to-green (GREEN as of U23 -- land is the enforcement point) -------------------


def _configure_oracle(repo, command: str) -> None:
    """Commit a one-tier oracle at `.sgt/oracle.json` (committed, team-shared) whose verdict is the
    tier's exit code -- `exit 0` green, `exit 1` red. Committed so `land`'s clean-tree precondition
    holds and the config rides in the branch tree every worktree/clone shares."""
    state.save_json(repo, "oracle_config", {"tiers": [{"name": "gate", "command": command}]})
    GitBinding(repo).commit_all("configure oracle")


# --- LAW-L: locality (GREEN) -------------------------------------------------------------------


def test_law_l_sync_does_not_move_a_bystander_or_switch_selection(tmp_path):
    """LAW-L: sync changes no replica's HEAD selection unless it landed a merge on that replica's
    own branch. The syncing replica's HEAD advances only via its own merge commit (its pre-sync
    HEAD stays an ancestor -- the selection is not switched to theirs); a bystander replica not
    party to the sync has its HEAD untouched."""
    _remote, (a, b, c) = _replicas(tmp_path, _BASE, 3)  # c is the bystander
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 100"), "A: bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", _BASE.replace("return 2", "return 200"), "B: bump bar")

    b_head_before = GitBinding(b).head()
    c_head_before = GitBinding(c).head()

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged

    b_head_after = GitBinding(b).head()
    assert b_head_after == report.merge_sha  # b advanced to its own merge commit, not to theirs
    assert b_head_before in set(GitBinding(b).commit_shas(b_head_after))  # old selection still an ancestor
    assert GitBinding(c).head() == c_head_before  # the bystander never moved


# --- U8: reverts travel across replicas (three-way base subtraction) ----------------------------


def test_u8_a_revert_travels_to_every_replica(tmp_path):
    """U8/LAW-R: a revert computed on one replica removes the op on every other replica's next sync.
    The three-way base subtraction stops the blind `ours ∪ theirs` from resurrecting what a teammate
    removed -- the convergence-suite form of the two-clone reproduction in `test_sync.py`, and
    idempotent (a second sync with the removal already applied is a no-op, LAW-I)."""
    _, (r0, r1) = _replicas(tmp_path, _THREE, 2)
    baz = next(o for o in Store(r0).all_ops() if "main.py::baz" in o.footprint)

    taken = _revert(r0, baz.id)  # r0 reverts baz on its own clone...
    _push(r0)

    _sync_and_push(r1)  # ...and the revert travels on r1's next sync
    from ugh.core import oplog

    sha = oplog.tail(r0)["sha"]
    mined = {op.id for op in Store(r0).all_ops() if sha in op.provenance}
    assert taken.undone and mined and mined <= lens.current_ideal(r1).op_ids
    assert "def baz" not in (r1 / "main.py").read_text(encoding="utf-8")
    assert _sync_and_push(r1).message == "already up to date"  # idempotent with a removal in play
