"""Tests for published oracle claims (plan U22, D8).

A verdict a clone records is private (`.sgt/local/oracle.json`). Publishing it writes an immutable,
committed `.sgt/claims/<ideal_key>.<runner_fp>.json` carrying runner identity; sync unions that
directory as a trivial file-level G-Set, so a teammate reads the claim after syncing. The two-clone
rig (bare remote + two working clones) is reused from `test_sync`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sgt import state
from sgt.core import lens, oracle, sync
from sgt.store.gitbind import GitBinding
from tests.core.test_sync import _BASE, _push, _two_clones


def _configure_oracle(repo: Path, tiers: list[tuple[str, str]]) -> None:
    (repo / ".sgt").mkdir(exist_ok=True)
    payload = {"tiers": [{"name": name, "command": command} for name, command in tiers]}
    (repo / ".sgt" / "oracle.json").write_text(json.dumps(payload), encoding="utf-8")


def test_a_published_claim_travels_to_a_syncing_clone(tmp_path):
    a, b = _two_clones(tmp_path, _BASE)

    # A records a passing verdict for its current ideal, publishes it, then commits + pushes.
    _configure_oracle(a, [("build", "exit 0")])
    ideal_a = lens.get(a)
    oracle.run(a)
    claim = oracle.publish(a)
    GitBinding(a).commit_all("A: configure oracle + publish claim")
    _push(a)

    assert oracle.overall_status(oracle.verdict_for(a, ideal_a)) == "pass"
    assert claim["status"] == "pass"
    assert claim["runner"]["host"]  # runner identity recorded on the claim

    assert state.list_claim_files(b) == []  # B has nothing published before syncing

    report = sync.sync(b, remote="origin", branch="main")
    assert report.merged

    # The claim file traveled verbatim as a G-Set member and reads back with A's runner identity.
    files = state.list_claim_files(b)
    assert len(files) == 1
    body = state.load_claim(b, files[0])
    assert body["ideal_key"] == claim["ideal_key"]
    assert body["status"] == "pass"
    assert body["runner"] == claim["runner"]

    # ...and is readable via the ideal-keyed lookup (A's ideal == B's post-sync ideal here).
    got = oracle.claim_for(b, lens.current_ideal(b))
    assert [c["ideal_key"] for c in got] == [claim["ideal_key"]]


def test_claim_for_is_empty_without_a_published_claim(tmp_path):
    a, _b = _two_clones(tmp_path, _BASE)
    assert oracle.claim_for(a, lens.current_ideal(a)) == []


def test_publish_refuses_without_a_recorded_verdict(tmp_path):
    a, _b = _two_clones(tmp_path, _BASE)
    lens.get(a)
    with pytest.raises(ValueError, match="no verdict recorded"):
        oracle.publish(a)
