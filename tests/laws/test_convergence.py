"""The collaboration laws over N replicas (2-3) of one bare remote, now that a save is a git
commit, ops are mined on every clone, and sync merges unit by unit
(docs/design/2026-11-18-b4-save-sync-brief.md):

- LAW-0 (replica determinism): same history, same miner -> byte-identical op stores.
- LAW-U (order independence): any schedule delivering the same commits converges to the same HEAD,
  tree, record and names.
- LAW-I (idempotence): sync twice = sync once.
- LAW-F (choices exactly where the same lines differ): a sync leaves a two-version choice iff both
  sides changed the same lines of one unit differently.
- LAW-S (a merge settles): after any merge the record reproduces the merged tree, so no symbol
  has two tips.
- LAW-R (resolutions travel): a choice resolved on one replica is resolved on every replica.
- LAW-L (locality): sync never moves a bystander or switches a replica's selection.

Real `git`, no mocks, no network, no wall-clock/LLM dependency.
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


# --- N-replica rig ------------------------------------------------------------------------------



def _revert(repo, op_id):
    from ugh.core import planner

    steps = [st for st, ops in planner._Ctx(repo).steps.items() if op_id in ops]
    return planner.apply(repo, planner.plan(repo, steps, target=op_id))

def _edit_and_commit(repo: Path, path: str, content: str, message: str) -> str:
    (repo / path).write_text(content, encoding="utf-8")
    sha = GitBinding(repo).commit_all(message)
    lens.get(repo)
    return sha


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
    ahead = GitBinding(repo).head() != GitBinding(repo).rev_parse("origin/main")
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
    """LAW-U: three replicas each edit a different definition, then sync in a seeded random
    round-robin over the shared remote; all land on one HEAD, tree and record."""
    _remote, (a, b, c) = _replicas(tmp_path, _THREE, 3)

    _edit_and_commit(a, "main.py", _THREE.replace("return 1", "return 100"), "A: bump foo")
    _edit_and_commit(b, "main.py", _THREE.replace("return 2", "return 200"), "B: bump bar")
    _edit_and_commit(c, "main.py", _THREE.replace("return 3", "return 300"), "C: bump baz")

    replicas = [a, b, c]
    rng = random.Random(20260710)
    for _round in range(4):
        for repo in rng.sample(replicas, len(replicas)):
            _sync_and_push(repo)

    assert len({GitBinding(r).head() for r in replicas}) == 1
    assert len({lens.current_ideal(r).op_ids for r in replicas}) == 1
    trees = {(r / "main.py").read_text(encoding="utf-8") for r in replicas}
    assert trees == {_THREE.replace("return 1", "return 100").replace("return 2", "return 200")
                     .replace("return 3", "return 300")}


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
    """LAW-I: once a teammate's edit is in, a second sync is 'already up to date' and changes
    nothing: HEAD, tree and op store stay byte for byte."""
    _remote, (a, b) = _replicas(tmp_path, _BASE, 2)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 100"), "A: bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", _BASE.replace("return 2", "return 200"), "B: bump bar")

    assert sync.sync(b, remote="origin", branch="main").message == "merged"
    head, ops, tree = GitBinding(b).head(), _ops_bytes(b), (b / "main.py").read_bytes()

    assert sync.sync(b, remote="origin", branch="main").message == "already up to date"
    assert (GitBinding(b).head(), _ops_bytes(b), (b / "main.py").read_bytes()) == (head, ops, tree)


# --- LAW-F: choices exactly where the same lines differ ----------------------------------------


@pytest.mark.parametrize("theirs, ours, units", [
    (_BASE.replace("return 1", "return 100"), _BASE.replace("return 2", "return 200"), ()),
    (_BASE.replace("return 1", "return 999"), _BASE.replace("return 1", "return 42"), ("main.py::foo",)),
])
def test_law_f_a_choice_iff_both_sides_changed_the_same_lines(tmp_path, theirs, ours, units):
    _remote, (a, b) = _replicas(tmp_path, _BASE, 2)
    _edit_and_commit(a, "main.py", theirs, "A")
    _push(a)
    _edit_and_commit(b, "main.py", ours, "B")
    assert sync.sync(b, remote="origin", branch="main").choices == units
    assert tuple(c.unit for c in sync.choices(b)) == units


# --- LAW-S: a merge settles what it contains ---------------------------------------------------


@pytest.mark.parametrize("ours", [_BASE.replace("return 1", "return 42"),
                                  _BASE.replace("return 2", "return 200"),
                                  "def foo():\n    return 1\n"])
def test_law_s_after_a_merge_the_record_reproduces_the_merged_tree(tmp_path, ours):
    _remote, (a, b) = _replicas(tmp_path, _BASE, 2)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 999"), "A")
    _push(a)
    _edit_and_commit(b, "main.py", ours, "B")
    sync.sync(b, remote="origin", branch="main")
    assert lens.fsck_tree(b)["drift"] == []


# --- LAW-R: resolutions travel -----------------------------------------------------------------


def test_law_r_a_resolved_choice_is_resolved_on_every_replica(tmp_path):
    _remote, (a, b, c) = _replicas(tmp_path, _BASE, 3)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 999"), "A")
    _push(a)
    _edit_and_commit(b, "main.py", _BASE.replace("return 1", "return 42"), "B")
    sync.sync(b, remote="origin", branch="main")
    sync.resolve(b, sync.choices(b), "theirs", "main.py::foo")
    _push(b)
    for repo in (a, c):
        sync.sync(repo, remote="origin", branch="main")
        assert sync.choices(repo) == []
        assert (repo / "main.py").read_bytes() == (b / "main.py").read_bytes()


# --- LAW-G: green-to-green (GREEN as of U23 -- land is the enforcement point) -------------------


def _configure_oracle(repo, command: str) -> None:
    """Commit a one-tier oracle at `.sgt/oracle.json` (committed, team-shared) whose verdict is the
    tier's exit code -- `exit 0` green, `exit 1` red. Committed so `land`'s clean-tree precondition
    holds and the config rides in the branch tree every worktree/clone shares."""
    state.save_json(repo, "oracle_config", {"tiers": [{"name": "gate", "command": command}]})
    GitBinding(repo).commit_all("configure oracle")


# --- LAW-L: locality ---------------------------------------------------------------------------


def test_law_l_sync_does_not_move_a_bystander_or_switch_selection(tmp_path):
    """LAW-L: the syncing replica's HEAD advances only by its own merge commit (its old HEAD stays
    the first parent); a bystander's HEAD is untouched."""
    _remote, (a, b, c) = _replicas(tmp_path, _BASE, 3)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 100"), "A: bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", _BASE.replace("return 2", "return 200"), "B: bump bar")

    b_before, c_before = GitBinding(b).head(), GitBinding(c).head()
    report = sync.sync(b, remote="origin", branch="main")

    assert report.message == "merged" and GitBinding(b).head() == report.head
    assert GitBinding(b).parents(report.head)[0] == b_before
    assert GitBinding(c).head() == c_before


# --- U8: reverts travel across replicas (three-way base subtraction) ----------------------------


def test_u8_a_revert_travels_to_every_replica(tmp_path):
    """A take-out made on one replica is a commit, so the next sync brings it to every other
    replica, and a second sync is a no-op."""
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
