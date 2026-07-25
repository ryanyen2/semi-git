"""Tests for the proposal object + GitHub rendering (plan U24, C10; matrix rows 6, 7, 12).

A proposal is a base+Δ review object: creatable (rejecting a base∪Δ that isn't a valid ideal),
checkable for staleness by re-union against the moved base (current / clean-reunion / fork), and
renderable as a GitHub PR body a reviewer without sgt can act on. Like a claim (D8), it is a
committed, immutable G-Set file that travels on sync. The two-clone rig (bare remote + two working
clones) is reused from `test_sync`; `_configure_oracle` from `test_claims`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sgt import api, state
from sgt.core import lens, oracle, propose, sync
from sgt.core.mine import mine
from sgt.core.store import Store
from sgt.lens.map import build_map
from sgt.store.gitbind import GitBinding
from tests.core.test_claims import _configure_oracle
from tests.core.test_sync import _BASE, _edit_and_commit, _push, _two_clones


def _branch(repo: Path, name: str) -> None:
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", name], check=True, capture_output=True)


def _fetch(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "fetch", "-q", "origin"], check=True, capture_output=True)


def _ingest_ref_ops(repo: Path, ref: str) -> None:
    """Mine `ref`'s ops into the local store *without* advancing the current branch's ideal -- the
    op-store half of a sync, so a base ref's conflicting op is knowable at create/status time. (The
    committed `.sgt/ops/` dir is swapped by a plain `git checkout`, so two divergent branches' ops
    only ever coexist in one store through this union, exactly as sync's ingest does.)"""
    gb = GitBinding(repo)
    store = Store(repo)
    store.init()
    mined_ops, _last_sha = mine(repo, since=gb.merge_base("HEAD", ref), target=gb.rev_parse(ref))
    for op in mined_ops:
        store.add(op)


_WITH_BAZ = _BASE + "\n\ndef baz():\n    return 3\n"


def test_create_load_roundtrip_and_status_current(tmp_path):
    """create -> load round-trips exactly, and an un-moved base reports `current`."""
    a, _b = _two_clones(tmp_path, _BASE)
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _WITH_BAZ, "add baz")

    p = propose.create(a, base_ref="main", title="add baz")
    assert p.delta_ids  # Δ is the baz op, non-empty
    assert propose.load(a, p.id) == p  # exact round-trip through the JSON codec
    assert propose.all_proposals(a) == [p]

    st = propose.status(a, p.id)
    assert st["state"] == "current"
    assert st["base_moved"] is False
    assert st["forks"] == []


def test_create_rejects_a_delta_whose_base_union_is_not_a_valid_ideal(tmp_path):
    """Validity (C10): a Δ whose base∪Δ isn't a valid ideal -- here the branch forks the base on the
    same symbol -- is refused by `create`. The base's conflicting op is unioned into the store (as a
    real fork only arises through a store union, never a single branch's linear history)."""
    a, b = _two_clones(tmp_path, _BASE)
    # the base (origin/main) reworks foo one way...
    _edit_and_commit(b, "main.py", _BASE.replace("return 1", "return 999"), "main: rework foo")
    _push(b)
    # ...the contributor reworks the same symbol differently on a feature branch...
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 42"), "feature: rework foo")
    # ...and learns of the base's op (fetch + mine into the store) without advancing feature.
    _fetch(a)
    _ingest_ref_ops(a, "origin/main")

    with pytest.raises(ValueError, match="not a valid ideal"):
        propose.create(a, base_ref="origin/main")


def test_row6_fork_based_contributor_status_reports_fork(tmp_path):
    """Row 6: a proposal created cleanly over the base goes stale as a `fork` once the base reworks a
    symbol Δ also touches (surfaced when the contributor syncs the moved base in), and `status`
    reports the `sgt merge-op` remedy."""
    a, b = _two_clones(tmp_path, _BASE)
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 42"), "feature: rework foo")

    p = propose.create(a, base_ref="origin/main")  # origin/main is still a clean ancestor here
    assert propose.status(a, p.id)["state"] == "current"
    GitBinding(a).commit_all("A: proposal")

    # the base now reworks the same symbol; the contributor syncs it in (store gains the fork tip).
    _edit_and_commit(b, "main.py", _BASE.replace("return 1", "return 999"), "main: rework foo")
    _push(b)
    report = sync.sync(a, remote="origin", branch="main")
    assert report.forks  # sync surfaced the same-symbol fork

    st = propose.status(a, p.id)
    assert st["state"] == "fork"
    assert st["base_moved"] is True
    assert st["forks"], "a same-symbol fork should be surfaced"
    assert st["forks"][0]["remedy"].startswith("sgt merge-op ")
    assert st["remedy"].startswith("sgt merge-op ")


def test_plan_land_predicts_a_proposals_advance_and_leaves_no_trace(tmp_path):
    """The `propose land` feedforward dry run: over a clean, landable proposal it delegates to
    `sync.plan_land` and reports the op count the base branch would advance by and that the oracle is
    configured (LAW-G's gate), without moving the base tip -- a pure read behind the pane."""
    a, _b = _two_clones(tmp_path, _BASE)
    _configure_oracle(a, [("build", "exit 0")])
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _WITH_BAZ, "add baz")
    p = propose.create(a, base_ref="main", title="add baz")
    GitBinding(a).commit_all("A: oracle config + proposal")  # a real land needs a clean tree

    before_tip = GitBinding(a).rev_parse("refs/heads/main")
    plan = propose.plan_land(a, p.id)

    assert plan.clean and not plan.forks
    assert plan.ops_added > 0 and plan.oracle_configured is True
    assert GitBinding(a).rev_parse("refs/heads/main") == before_tip  # nothing advanced


def test_plan_land_of_a_stale_forked_proposal_surfaces_the_blocker(tmp_path):
    """A proposal gone stale as a fork surfaces that fork as the blocker in the dry run (so the pane
    refuses before the CAS would), without needing an oracle run."""
    a, b = _two_clones(tmp_path, _BASE)
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 42"), "feature: rework foo")
    p = propose.create(a, base_ref="origin/main")
    GitBinding(a).commit_all("A: proposal")

    _edit_and_commit(b, "main.py", _BASE.replace("return 1", "return 999"), "main: rework foo")
    _push(b)
    assert sync.sync(a, remote="origin", branch="main").forks  # the fork is now shared state

    plan = propose.plan_land(a, p.id)
    assert plan.forks, "a stale-forked proposal's land dry run must surface the fork blocker"


def test_status_clean_reunion_when_base_advances_disjointly(tmp_path):
    """A base that advances on a *different* symbol leaves Δ applying cleanly: `clean-reunion`."""
    a, b = _two_clones(tmp_path, _BASE)
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _BASE.replace("return 1", "return 42"), "feature: rework foo")
    p = propose.create(a, base_ref="origin/main")
    GitBinding(a).commit_all("A: proposal")

    _edit_and_commit(b, "main.py", _BASE.replace("return 2", "return 22"), "main: rework bar")
    _push(b)
    sync.sync(a, remote="origin", branch="main")

    st = propose.status(a, p.id)
    assert st["state"] == "clean-reunion"
    assert st["base_moved"] is True
    assert st["note"] == "base advanced; Δ still applies"


def test_row7_render_github_pr_body_is_readable_without_sgt(tmp_path):
    """Row 7: `render_github` emits a suggested branch, a PR title, and a PR body in plain markdown
    -- a feature-delta table, the oracle claim (status + runner identity), and provenance -- that a
    reviewer without sgt can act on."""
    a, _b = _two_clones(tmp_path, _BASE)
    _configure_oracle(a, [("build", "exit 0")])
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _WITH_BAZ, "add baz")
    build_map(a)  # populate the feature tree so Δ maps to real feature ids (offline fallback labels)

    oracle.run(a)
    claim = oracle.publish(a)
    p = propose.create(a, base_ref="main", title="Add baz helper", description="Adds a baz function.")

    view = api.proposal_view(a, p.id)
    rendered = propose.render_github(view)

    assert rendered["pr_title"] == "Add baz helper"
    assert rendered["branch"].startswith("sgt/")
    body = rendered["pr_body"]
    # plain-markdown structure a bare GitHub reviewer sees
    assert "Adds a baz function." in body
    assert "| Feature | Label | Ops |" in body and "| --- | --- | --- |" in body
    assert "### Oracle claim" in body
    assert claim["status"] in body  # "pass"
    assert claim["runner"]["host"] in body  # runner identity is legible in the body
    assert "### Provenance" in body
    # the feature delta rendered at least one real feature row (not the empty placeholder)
    assert view["feature_delta"], "a built tree should assign Δ's op to a feature"
    assert "_(none)_" not in body


def test_a_proposal_travels_to_a_syncing_clone(tmp_path):
    """G-Set travel: a committed proposal file unions to a syncing reviewer clone, byte-for-byte,
    exactly like a claim."""
    a, b = _two_clones(tmp_path, _BASE)
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _WITH_BAZ, "add baz")
    p = propose.create(a, base_ref="main")
    GitBinding(a).commit_all("A: publish proposal")  # commit the .sgt/proposals/ file
    _push(a, "feature")

    assert state.list_proposal_files(b) == []  # nothing before syncing
    report = sync.sync(b, remote="origin", branch="feature")
    assert report.merge_sha is not None  # a merge landed (disjoint add)

    files = state.list_proposal_files(b)
    assert files == [f"{p.id}.json"]
    assert propose.load(b, p.id) == p  # round-trips as an identical Proposal on the reviewer clone


def test_row12_claim_reverified_by_reviewer_clone(tmp_path):
    """Row 12: a proposal carrying a published claim travels to a reviewer clone, which reads the
    claim (runner identity intact) via the proposal view and re-runs the oracle on the same
    `ideal_key` to confirm green."""
    a, b = _two_clones(tmp_path, _BASE)
    _configure_oracle(a, [("build", "exit 0")])
    _branch(a, "feature")
    _edit_and_commit(a, "main.py", _WITH_BAZ, "add baz")

    oracle.run(a)
    claim = oracle.publish(a)
    p = propose.create(a, base_ref="main")
    GitBinding(a).commit_all("A: oracle config + claim + proposal")
    _push(a, "feature")

    report = sync.sync(b, remote="origin", branch="feature")
    assert report.merge_sha is not None

    # the claim traveled and resolves on B via the proposal view, with A's runner identity intact.
    view = api.proposal_view(b, p.id)
    assert view["claim"], "the published claim should travel and resolve on the reviewer clone"
    assert view["claim"][0]["runner"] == claim["runner"]
    assert view["claim"][0]["status"] == "pass"

    # B re-runs the oracle against the same ideal_key the claim was published for -- confirms green.
    ideal_b = lens.current_ideal(b)
    assert oracle.ideal_key(ideal_b) == p.claim_key
    _configure_oracle(b, [("build", "exit 0")])
    rerun = oracle.run(b, ideal=ideal_b)
    assert oracle.overall_status(rerun) == "pass"
