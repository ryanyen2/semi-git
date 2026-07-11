"""SYNC-1 hardening tests (plan U20; design doc §5.1, workflow-matrix rows 3/4/5/10).

The 6 integration tests in `test_sync.py` are the pre-hardening contract; these cover the five
behaviors U20 adds, each mapped to its acceptance example: mine-on-contact (C3/AE8), the
miner-version handshake (C6, row 10), committed-ideal recovery after a squash (C5/AE10, row 4),
`sgt push` rejection routing (C7), and divergence-as-state (C4/AE9). Same hermetic two-clone rig
as `test_sync.py`: real `git`, no network, no wall-clock/LLM dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sgt.api import forks_view, status_view
from sgt.cli.sync import _push as cli_push
from sgt.core import lens, order, sync
from sgt.core.ideal import Ideal
from sgt.core.op import make_op
from sgt.core.store import Store, _serialize
from sgt.core.sync import MinerVersionMismatch
from sgt.core.sync.ingest import Ingested
from sgt.core.sync.resolve import resolve
from sgt.lens.pins import Pins
from sgt.store.gitbind import GitBinding, PushRejected

from tests.core.test_sync import (
    _BASE,
    _clone,
    _edit_and_commit,
    _init_bare,
    _push,
    _two_clones,
)


def _ops_bytes(repo: Path) -> dict[str, bytes]:
    ops_dir = repo / ".sgt" / "ops"
    if not ops_dir.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in ops_dir.iterdir() if p.is_file()}


# --- C3: mine-on-contact (AE8, rows 3/5) -------------------------------------------------------


def test_c3_plain_git_teammate_work_unions_then_self_dedups_on_adoption(tmp_path):
    """AE8: a plain-git teammate (no sgt) commits to the shared branch; `sgt sync` mines their
    foreign commits into the union. When that teammate later adopts sgt over their *own* identical
    history, LAW-0 makes the mined ids byte-identical -- the op stores match and nothing
    double-mints."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")  # the plain-git teammate
    b = _clone(remote, tmp_path / "b")  # the sgt-native teammate

    (a / "main.py").write_text(_BASE, encoding="utf-8")
    GitBinding(a).commit_all("base (plain git)")
    _push(a)

    subprocess.run(["git", "-C", str(b), "pull", "-q", "origin", "main"], check=True, capture_output=True)
    lens.init(b)
    GitBinding(b).commit_all("B: adopt sgt")  # commit the mined store so the tree is clean for sync

    a_baz = _BASE + "\n\ndef baz():\n    return 42\n"
    (a / "main.py").write_text(a_baz, encoding="utf-8")
    baz_sha = GitBinding(a).commit_all("A: add baz (plain git, no trailers, no op files)")
    _push(a)

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged
    assert report.ops_added > 0  # A's foreign work contributed to the union
    b_ops = _ops_bytes(b)
    baz_ids = [oid for oid, raw in b_ops.items() if b"main.py::baz" in raw]
    assert len(baz_ids) == 1  # mined exactly once
    assert baz_sha.encode() in b_ops[baz_ids[0]]  # provenance points at A's real commit

    lens.init(a)  # A finally adopts sgt over its own history
    assert _ops_bytes(a) == b_ops  # byte-identical stores, zero duplicates (LAW-0)


def test_c3_hotfix_committed_on_top_of_an_sgt_branch_is_mined(tmp_path):
    """Row 5: a foreign hotfix lands directly at the tip (e.g. a GitHub web-UI edit) over an sgt
    history. The tip has no trailers, so sync mines `merge_base..theirs` -- picking up both the
    absorbed sgt ops and the new hotfix -- rather than reading a stale trailer set."""
    a, b = _two_clones(tmp_path, _BASE)
    # A adds a foreign hotfix at the tip with plain git (no sgt).
    (a / "main.py").write_text(_BASE + "\n\ndef hotfix():\n    return 7\n", encoding="utf-8")
    GitBinding(a).commit_all("A: hotfix (plain git, at the tip)")
    _push(a)

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged
    assert "def hotfix" in (b / "main.py").read_text(encoding="utf-8")
    assert any("main.py::hotfix" in op.footprint for op in Store(b).all_ops())


# --- C6: miner-version handshake (row 10) ------------------------------------------------------


def test_c6_sync_refuses_to_union_across_miner_versions(tmp_path):
    """Row 10 / §5.1.5: a teammate on a different sgt miner version mints different ids for the
    same edit, so uniting the stores would alias incompatible semantics. Sync refuses the whole
    union with an instruction naming which side is behind, before any merge is attempted."""
    a, b = _two_clones(tmp_path, _BASE)  # B's op store arrives committed via clone -- tree is clean

    # A publishes an op file stamped with an older miner version.
    stale = make_op(
        {"main.py::foo": ("v1", "v2")}, {"main.py::foo": b"x"}, kind="rework", miner_version="1"
    )
    (a / ".sgt" / "ops" / stale.id).write_bytes(_serialize(stale))
    GitBinding(a).commit_all("A: op minted by an older miner")
    _push(a)

    with pytest.raises(MinerVersionMismatch) as exc:
        sync.sync(b, remote="origin", branch="main")
    assert "theirs" in str(exc.value)  # A (version 1) is the side behind ours (version 2)
    assert GitBinding(b).is_clean()  # refused before any tree mutation


# --- C5: committed-ideal recovery after a squash-merge (AE10, row 4) ----------------------------


def _squash_commit(repo: Path, tip_sha: str, parent_sha: str, message: str) -> str:
    """A single commit whose *tree* is `tip_sha`'s but whose message is plain (no `Sgt-Op:`
    trailers) and whose parent is `parent_sha` -- exactly what GitHub's squash-merge produces."""
    tree = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{tip_sha}^{{tree}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return subprocess.run(
        ["git", "-C", str(repo), "commit-tree", tree, "-p", parent_sha, "-m", message],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_c5_squash_merge_ideal_is_recovered_from_the_committed_file(tmp_path):
    """AE10 / row 4: GitHub squash-merges an sgt branch -- the `Sgt-Op:` trailers are destroyed but
    the tree (including `.sgt/ideal.json` and the fine-grained `.sgt/ops/` blobs) survives. Sync
    recovers the exact ideal from the committed file and identifies rather than re-mining the
    coarse squash (which would fork against the fine ops)."""
    remote = _init_bare(tmp_path)
    a = _clone(remote, tmp_path / "a")
    lens.init(a)
    _edit_and_commit(a, "main.py", _BASE, "init")
    _push(a)
    base_sha = GitBinding(a).head()

    b = _clone(remote, tmp_path / "b")
    lens.get(b)  # a fresh teammate at the base, never having witnessed a's later work

    _edit_and_commit(a, "main.py", _BASE + "\n\ndef baz():\n    return 42\n", "A: add baz")
    fine_ideal = lens.current_ideal(a).op_ids  # a's exact fine-grained ideal

    squash = _squash_commit(a, GitBinding(a).head(), base_sha, "Squash merge #1 (no trailers)")
    subprocess.run(
        ["git", "-C", str(a), "push", "-q", "-f", "origin", f"{squash}:main"],
        check=True, capture_output=True,
    )

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged
    assert lens.current_ideal(b).op_ids == fine_ideal  # recovered fine ideal, no coarse re-mine
    assert "def baz" in (b / "main.py").read_text(encoding="utf-8")


# --- C7: sgt push (never forces; rejection routes to sync) --------------------------------------


def test_c7_push_succeeds_and_reports_the_pushed_sha(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(b, "main.py", _BASE.replace("return 1", "return 5"), "B: bump foo")
    gb = GitBinding(b)

    pushed = gb.push("origin", "main")
    assert pushed == gb.head()
    assert gb.rev_parse("origin/main") == pushed  # the remote-tracking ref advanced


def test_c7_push_rejected_on_non_fast_forward_routes_to_sync(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 9"), "A: bump foo")
    _push(a)  # origin advances past B
    _edit_and_commit(b, "main.py", _BASE.replace("return 2", "return 8"), "B: bump bar")  # divergent
    gb = GitBinding(b)

    with pytest.raises(PushRejected):
        gb.push("origin", "main")  # never forces -- the remote moved

    rc = cli_push(str(b), "origin", "main", as_json=False)
    assert rc == 1  # the CLI reports the rejection and routes to `sgt sync`
    assert gb.head() != gb.rev_parse("origin/main")  # nothing was forced over the remote


# --- C4: divergence-as-state (AE9) -------------------------------------------------------------


def _six(overrides: dict[int, int]) -> str:
    return "".join(f"def f{i}():\n    return {overrides.get(i, i)}\n\n\n" for i in range(6))


def test_c4_fork_free_five_land_while_one_symbol_forks(tmp_path):
    """AE9: five ops sync cleanly while one symbol forks. The branch advances by the clean five
    (their effects are in the post-sync tree), the forked symbol's content stays at the pre-fork
    common ancestor (never either tip), `.sgt/forks.json` records the fork, and `sgt status`/
    `sgt forks` report exactly one open fork."""
    a, b = _two_clones(tmp_path, _six({}))

    # A advances five disjoint symbols (f1..f5) *and* reworks f0.
    _edit_and_commit(a, "main.py", _six({0: 900, 1: 101, 2: 102, 3: 103, 4: 104, 5: 105}), "A: five + f0")
    _push(a)
    # B reworks only f0 -- so f0 forks, but f1..f5 are fork-free new work from A.
    _edit_and_commit(b, "main.py", _six({0: 42}), "B: rework f0")

    report = sync.sync(b, remote="origin", branch="main")

    assert not report.merged  # an open fork
    assert len(report.forks) == 1
    assert report.forks[0][0] == "main.py::f0"

    text = (b / "main.py").read_text(encoding="utf-8")
    for i in range(1, 6):  # the clean five landed
        assert f"return {100 + i}" in text
    assert "return 0" in text  # f0 sits at the common ancestor...
    assert "return 900" not in text and "return 42" not in text  # ...never either tip

    assert (b / ".sgt" / "forks.json").is_file()
    assert forks_view(b)["open"] == 1
    assert status_view(b)["forks"]["open"] == 1  # loud status (D5)


def test_c4_fork_free_construction_is_a_valid_ideal(tmp_path):
    """The soundness the D5 construction rests on: `union \\ (both tips' up-sets)` is still a valid
    ideal (a downward-closed set minus an upward-closed set stays downward-closed *and* fork-free).
    Verified directly with `order.is_valid_ideal` rather than trusted -- and the forked tips are
    genuinely absent from the resulting ideal, while an unrelated fork-free op survives."""
    add_foo = make_op({"m.py::foo": (None, "v1")}, {"m.py::foo": b"1"}, kind="add")
    rework_a = make_op({"m.py::foo": ("v1", "v2a")}, {"m.py::foo": b"a"}, kind="rework")
    rework_b = make_op({"m.py::foo": ("v1", "v2b")}, {"m.py::foo": b"b"}, kind="rework")
    add_bar = make_op({"m.py::bar": (None, "w1")}, {"m.py::bar": b"x"}, kind="add")  # unrelated
    all_ops = [add_foo, rework_a, rework_b, add_bar]

    ing = Ingested(
        ours_pins=Pins(), theirs_pins=Pins(),
        ours_declared=frozenset(), theirs_declared=frozenset(),
        ours_tree=None,
        ours_ideal=Ideal.from_ops({add_foo.id, rework_b.id, add_bar.id}, all_ops),
        theirs_ideal_ids=frozenset({add_foo.id, rework_a.id, add_bar.id}),
        all_ops=all_ops, theirs_ops=[rework_a], mined_ops=[], ops_added=1,
    )

    res = resolve(tmp_path, ing)

    assert len(res.forks) == 1
    fork_free = res.merged_ideal.op_ids
    assert order.is_valid_ideal(all_ops, fork_free)  # the construction is a valid ideal
    assert rework_a.id not in fork_free and rework_b.id not in fork_free  # both tips excluded
    assert {add_foo.id, add_bar.id} <= fork_free  # ancestor + unrelated fork-free op survive
