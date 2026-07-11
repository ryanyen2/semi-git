"""The collaboration laws (design doc 2026-07-10-collaboration-and-review.md §8, plan C12).

`tests/core/test_sync.py` proves one two-clone sync does the right thing on a handful of hand-built
scenarios. This module generalizes that rig to N replicas (2-3) over a bare remote and asserts the
*algebraic* laws the design doc names -- that sync's outcome is a function of the delivered op/pin
sets and nothing else (order, schedule, or which replica initiated). Each law from §8 has a named,
findable home here from this freeze unit (U16) onward, so every later behavior change is measured
against a red or green test that already exists:

- LAW-0 (replica determinism): same history, same miner -> byte-identical op stores. GREEN.
- LAW-U (order independence): any two schedules delivering the same op sets converge to identical
  `.sgt/` state (stores, orders, pins, feature trees, ids included). Disjoint edits converge today
  (GREEN); contradicting pins and replica-local feature ids do NOT (xfail, U21).
- LAW-I (idempotence): sync twice = sync once. GREEN.
- LAW-F (fork completeness & soundness): sync reports a fork iff the union forks a chain. GREEN.
- LAW-R (resolutions travel): a resolved fork stays resolved on every replica. Red today -- forks
  abort sync and no durable shared fork record exists to hang a resolution on (xfail, U20/U21).
- LAW-G (green-to-green): a shared tip only advances to an oracle-green op-set. Red today -- sync
  advances the branch ungated; the gate ships with U23's `land` (xfail, out of scope for U16).
- LAW-L (locality): sync never moves a replica's HEAD selection out from under it. GREEN.

Hermetic discipline matches `tests/laws/corpus.py` and `tests/core/test_sync.py`: real ``git``
subprocess calls, no mocks, no network, no wall-clock/LLM dependency. Where a law compares op-store
*bytes* across replicas (LAW-0), the history is built from `tests/laws/corpus.py`'s pinned-SHA
fixtures so provenance (commit shas, folded into the op file) is itself deterministic.
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

import pytest

from sgt.core import lens, sync
from sgt.core.lens import get
from sgt.core.store import Store
from sgt.lens import tree
from sgt.lens.pins import Pins, load_pins, save_pins
from sgt.store.gitbind import GitBinding
from tests.laws import corpus


# --- N-replica rig (generalizes tests/core/test_sync.py's two-clone helpers) -------------------


def _init_bare(root: Path) -> Path:
    remote = root / "remote.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, capture_output=True
    )
    return remote


def _clone(remote: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    GitBinding(dest).init()  # repo-scope identity, matches every other fixture in this suite
    return dest


def _push(repo: Path, branch: str = "main") -> None:
    subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "origin", branch], check=True, capture_output=True
    )


def _edit_and_commit(repo: Path, path: str, content: str, message: str) -> str:
    """A real commit that carries `Sgt-Op:` trailers, exactly like `lens.put` does outside tests
    (a ref's tip must carry them for `sync` to read its ideal without a checkout -- see
    `tests/core/test_sync.py`)."""
    (repo / path).write_text(content, encoding="utf-8")
    content_sha = GitBinding(repo).commit_all(message)
    ideal = lens.get(repo)
    put_sha = lens.put(repo, ideal, message=f"sgt: mine {message}")
    lens.record_ideal(repo, ideal, put_sha)
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


def test_law_u_contradicting_pins_converge_across_schedules(tmp_path):
    """LAW-U: two replicas assign the *same* member to *different* features -- a genuine pin
    contradiction. Two schedules deliver the identical pin facts but let a different side be
    `theirs` in the reconciling sync. Before U21 `union_pins` was latest-wins by merge order, so the
    schedules converged to *different* committed `assign` maps; U21's witness-topo + hash tie-break
    makes the winner a pure function of the pin facts (here: witness-less, so the content-hash
    fallback), so both schedules must now converge to the identical `assign`.

    In each schedule the side that publishes *first* is the one that pushes -- the other reconciles
    it as `theirs` in a fast-forward-free sync (the second side never force-pushes over the first;
    it absorbs and, in real use, would push the merge). This makes a genuinely different side
    `theirs` across the two schedules while every push is a legal fast-forward from the shared
    base."""
    def _world(sub: Path, theirs_side: str) -> dict[str, str]:
        _remote, (a, b) = _replicas(sub, _BASE, 2)
        save_pins(a, Pins(assign={"m1": "featureA"}))
        GitBinding(a).commit_all("A: pin m1 -> featureA")
        save_pins(b, Pins(assign={"m1": "featureB"}))
        GitBinding(b).commit_all("B: pin m1 -> featureB")
        if theirs_side == "a":
            _push(a)  # a publishes first; b reconciles a-as-theirs
            sync.sync(b, remote="origin", branch="main")
            return load_pins(b).assign
        _push(b)  # b publishes first; a reconciles b-as-theirs
        sync.sync(a, remote="origin", branch="main")
        return load_pins(a).assign

    schedule_1 = _world(tmp_path / "w1", theirs_side="a")
    schedule_2 = _world(tmp_path / "w2", theirs_side="b")
    assert schedule_1 == schedule_2  # same pin facts, two schedules -> identical assign (LAW-U)


@pytest.mark.xfail(
    strict=True,
    reason="LAW-U: feature ids are replica-local (sgt/lens/tree.py mints F<n> via Greene "
    "member-overlap against each replica's own `previous` tree, so independently-curated replicas "
    "adopt different ids for the same feature). Birth-minted, replica-independent ids land in U21.",
)
def test_law_u_feature_ids_are_replica_independent(tmp_path):
    """LAW-U (feature-tree half): the *same* feature (same member set, same ops) must carry the
    same id on every replica. Today `tree.build` carries ids across a run by matching against that
    replica's own last tree, so two replicas whose prior curation minted different ids for the
    feature keep diverging ids even after their op stores are identical. Modelled with two
    replica-local `previous` trees over one shared op store; asserts the ids agree (they must,
    under LAW-U) and so fails until U21's birth-minted ids."""
    repo = corpus.CORPUS["class_with_methods"].build(tmp_path / "repo")
    ideal = get(repo)
    ops = Store(repo).all_ops()

    natural = tree.build(repo, ops, ideal, pins=Pins(), previous=None)
    members = sorted({m for nd in natural["nodes"].values() if not nd["children"] for m in nd["members"]})
    assert members  # the fixture produced at least one alive feature to disagree about
    probe = members[0]

    # Two replicas that curated independently: each already knows this feature under its own id.
    prev_a = {"nodes": {"F7": {"members": members, "children": [], "parent": None, "depth": 0}}, "roots": ["F7"]}
    prev_b = {"nodes": {"F42": {"members": members, "children": [], "parent": None, "depth": 0}}, "roots": ["F42"]}
    tree_a = tree.build(repo, ops, ideal, pins=Pins(), previous=prev_a)
    tree_b = tree.build(repo, ops, ideal, pins=Pins(), previous=prev_b)

    def _feature_of(built: dict, member: str) -> str:
        return next(nid for nid, nd in built["nodes"].items() if not nd["children"] and member in nd["members"])

    assert _feature_of(tree_a, probe) == _feature_of(tree_b, probe)  # same feature, one id (LAW-U)


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


# --- LAW-G: green-to-green (RED, U23 -- out of scope for U16) -----------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="LAW-G: sync advances the shared branch with no oracle gate; the green-to-green "
    "guarantee's enforcement point is U23's `land` (CAS + oracle-green on the exact op-set). Out "
    "of scope for U16 -- kept red as a named home per C12.",
)
def test_law_g_shared_tip_advances_only_on_an_oracle_green(tmp_path):
    """LAW-G: a shared tip only ever points at an op-set with an oracle pass. Today `sgt sync`
    merges and advances the branch tip with no verdict consulted at all, so the tip can advance to
    an unverified (here: no oracle configured) op-set. This asserts sync refuses to advance without
    a green verdict, and so fails until U23's land-mediated advance ships the gate."""
    _remote, (a, b) = _replicas(tmp_path, _BASE, 2)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 100"), "A: bump foo")
    _push(a)
    _edit_and_commit(b, "main.py", _BASE.replace("return 2", "return 200"), "B: bump bar")

    report = sync.sync(b, remote="origin", branch="main")  # no oracle configured -> no green verdict
    assert not report.merged  # LAW-G: an ungated advance must not have happened


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
