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

from sgt.core import lens, sync
from sgt.core.op import make_op
from sgt.core.store import Store, _serialize
from sgt.core.sync import MinerVersionMismatch
from sgt.store.gitbind import GitBinding

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
