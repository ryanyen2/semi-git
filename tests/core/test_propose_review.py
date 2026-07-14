"""Tests for the U32 review surface: `sgt.api.proposal_review_view`'s feature checklist, `sgt
propose land --subset`'s partial-accept CLI, and `sgt propose publish`'s `gh`-porcelain behavior.

Reuses the two-clone rig from `test_sync` and `_configure_oracle` from `test_claims`, exactly like
`test_propose.py`. Two disjoint additions -- each a brand-new file -- are hand-assigned to two
distinct feature ids so the checklist's feature split is deterministic rather than riding on
Leiden clustering's arity choice for a tiny corpus. New *files* (not two functions in one file) are
essential here, not just convenient: two functions appended to the same file share a residue chain
(the whitespace gap between them is its own op), so "accept just one function's bare op" is not a
self-consistent, independently-landable slice -- confirmed empirically, materializing it alone
produced a corrupt file. Two disjoint files have no such coupling.
"""

from __future__ import annotations

import shutil

import pytest

from sgt import api
from sgt.core import lens, propose
from sgt.core.store import Store
from sgt.lens import tree
from sgt.store.gitbind import GitBinding, parse_op_ids
from tests.core.test_claims import _configure_oracle
from tests.core.test_sync import _BASE, _two_clones
from tests.core.test_propose import _branch


def _two_feature_proposal(tmp_path, *, oracle_cmd="exit 0"):
    """Two disjoint new files, each hand-assigned to its own feature by writing `.sgt/tree/tree.json`
    directly rather than through `build_map`'s real clustering (the split itself isn't what this
    test suite is verifying)."""
    a, _b = _two_clones(tmp_path, _BASE)
    _configure_oracle(a, [("build", oracle_cmd)])
    _branch(a, "feature")
    (a / "baz.py").write_text("def baz():\n    return 3\n", encoding="utf-8")
    (a / "qux.py").write_text("def qux():\n    return 4\n", encoding="utf-8")
    GitBinding(a).commit_all("add baz.py and qux.py")
    ideal = lens.get(a)
    put_sha = lens.put(a, ideal, message="sgt: mine add baz/qux")
    lens.record_ideal(a, ideal, put_sha)

    op_leaf = {}
    for op in Store(a).all_ops():
        keys = op.footprint.keys()
        if any(k.startswith("baz.py::") for k in keys):
            op_leaf[op.id] = "F-baz"
        elif any(k.startswith("qux.py::") for k in keys):
            op_leaf[op.id] = "F-qux"
    assert {"F-baz", "F-qux"} <= set(op_leaf.values())
    tree.save(a, {
        "roots": ["ROOT"],
        "nodes": {
            "ROOT": {"children": ["F-baz", "F-qux"], "members": [], "label": "root"},
            "F-baz": {"children": [], "members": ["baz.py::baz"], "label": "Baz"},
            "F-qux": {"children": [], "members": ["qux.py::qux"], "label": "Qux"},
        },
        "op_leaf": op_leaf,
    })

    p = propose.create(a, base_ref="main", title="Add baz and qux")
    GitBinding(a).commit_all("A: oracle config + tree + proposal")
    return a, p


def test_feature_checklist_has_two_disjoint_features_with_no_requires(tmp_path):
    """Two unrelated additions pinned to distinct features: the checklist lists both, each with
    its own `op_ids` and an empty `requires` (no chain/reference/declared coupling between them)."""
    a, p = _two_feature_proposal(tmp_path)

    review = api.proposal_review_view(a, p.id)
    assert review["approvals"] == []
    fids = {f["feature_id"] for f in review["feature_checklist"]}
    assert fids == {"F-baz", "F-qux"}
    for f in review["feature_checklist"]:
        assert f["requires"] == []
        assert f["op_ids"], "each feature should own at least one delta op"

    baz = next(f for f in review["feature_checklist"] if f["feature_id"] == "F-baz")
    qux = next(f for f in review["feature_checklist"] if f["feature_id"] == "F-qux")
    assert not (set(baz["op_ids"]) & set(qux["op_ids"]))


def test_proposal_review_view_reports_error_for_unknown_id(tmp_path):
    a, _p = _two_feature_proposal(tmp_path)
    assert api.proposal_review_view(a, "nope") == {"error": "no proposal 'nope'", "id": "nope"}


def test_land_subset_lands_exactly_the_chosen_feature_and_the_remainder_still_applies(tmp_path):
    """S8 / plan U32: `propose.land(..., accept_ids=<one feature's op_ids>)` advances the base with
    only that feature's ops; the other feature's ops never reach the base. The original proposal
    -- Δ unchanged -- still re-unions without a fork against the now-advanced base (the remainder
    "survives as a valid proposal" per the plan's acceptance wording, without any new object)."""
    a, p = _two_feature_proposal(tmp_path)
    review = api.proposal_review_view(a, p.id)
    baz = next(f for f in review["feature_checklist"] if f["feature_id"] == "F-baz")
    qux = next(f for f in review["feature_checklist"] if f["feature_id"] == "F-qux")

    report = propose.land(a, p.id, accept_ids=baz["op_ids"])
    assert report.landed, report.blocked_reason

    # `lens.ideal_for_ref` is a coarse provenance-scan over a ref's *whole* ancestry (its own
    # docstring: it never consults the persisted ideal table) -- with HEAD left on `feature` and
    # `main` CAS-advanced to a 2-parent merge, that ancestry still reaches back through the original
    # unsplit commit, so it over-reports. The landing commit's own trailers are the authoritative
    # record of what actually landed (`land.py`'s `format_op_trailers(sorted(res.merged_ideal...))`).
    gb = GitBinding(a)
    main_sha = gb.rev_parse("refs/heads/main")
    landed_ids = set(parse_op_ids(gb.commit_message(main_sha)))
    assert set(baz["op_ids"]) <= landed_ids
    assert not (set(qux["op_ids"]) & landed_ids)
    assert gb.file_at(main_sha, "baz.py") is not None
    assert gb.file_at(main_sha, "qux.py") is None

    st = propose.status(a, p.id)
    assert st["state"] != "fork"


def test_land_subset_refuses_an_unknown_feature_ref(tmp_path):
    a, p = _two_feature_proposal(tmp_path)
    with pytest.raises(ValueError):
        # accept_ids not a subset of Δ -> propose.land's own guard (CLI resolves refs earlier).
        propose.land(a, p.id, accept_ids=["not-a-real-op-id"])


def test_cli_land_subset_refuses_when_a_required_feature_is_left_out(tmp_path, monkeypatch, capsys):
    """The CLI's `--subset` resolution (not `propose.land` itself) refuses a subset that would
    make `base ∪ accept` fork-unsafe by naming the omitted required feature. Exercised here by
    monkeypatching `proposal_review_view` to report a synthetic `requires` edge, since two disjoint
    pinned features never actually require one another."""
    a, p = _two_feature_proposal(tmp_path)

    from sgt.cli import propose as propose_cli

    real_view = api.proposal_review_view

    def _fake_view(repo, pid):
        view = real_view(repo, pid)
        for f in view["feature_checklist"]:
            if f["feature_id"] == "F-baz":
                f["requires"] = ["F-qux"]
        return view

    monkeypatch.setattr("sgt.api.proposal_review_view", _fake_view)
    rc = propose_cli._propose(str(a), "land", p.id, "main", None, None, False, ["F-baz"], "origin", False)
    assert rc == 1
    out = capsys.readouterr().out
    assert "Qux" in out


def test_cli_publish_refuses_cleanly_when_gh_is_absent(tmp_path, monkeypatch, capsys):
    a, p = _two_feature_proposal(tmp_path)
    from sgt.cli import propose as propose_cli

    monkeypatch.setattr("shutil.which", lambda _name: None)
    rc = propose_cli._propose(str(a), "publish", p.id, "main", None, None, False, None, "origin", False)
    assert rc == 1
    assert "gh" in capsys.readouterr().out


def test_cli_publish_edits_an_existing_pr_instead_of_creating_a_second_one(tmp_path, monkeypatch):
    """Plan U32 test scenario: "published PR body updates when the proposal does" -- when `gh pr
    list --head <branch>` already reports an open PR, `publish` calls `gh pr edit` on it rather
    than `gh pr create`, so a later render (Δ changed, a claim landed) updates the PR in place.
    `gh`'s own network calls are faked (no real GitHub needed); the real `git push` underneath
    `GitBinding.push_head_as` still runs, against the local `origin` `_two_clones` sets up."""
    import subprocess

    a, p = _two_feature_proposal(tmp_path)
    from sgt.cli import propose as propose_cli

    real_run = subprocess.run
    calls = []

    def _fake_run(cmd, *args, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, *args, **kwargs)
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout='[{"number": 42}]', stderr="")
        assert cmd[:3] == ["gh", "pr", "edit"], f"expected an edit of the existing PR, got {cmd}"
        return subprocess.CompletedProcess(cmd, 0, stdout="https://github.com/o/r/pull/42\n", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr("subprocess.run", _fake_run)

    rc = propose_cli._propose(str(a), "publish", p.id, "main", None, None, False, None, "origin", False)
    assert rc == 0
    assert any(c[:3] == ["gh", "pr", "edit"] for c in calls)
    assert not any(c[:3] == ["gh", "pr", "create"] for c in calls)


@pytest.mark.skipif(shutil.which("gh") is None, reason="requires the gh CLI on PATH")
def test_cli_publish_pushes_the_pr_branch_and_reports_create_failure_off_github(tmp_path):
    """Integration guard (plan U32): with a real `gh` binary but a plain local `origin` (not an
    actual GitHub remote), `publish` still pushes the rendered branch -- proving
    `GitBinding.push_head_as` reaches the remote -- and then fails cleanly when `gh pr create`
    can't resolve a GitHub repo for it, rather than crashing."""
    a, p = _two_feature_proposal(tmp_path)
    from sgt.cli import propose as propose_cli

    rc = propose_cli._propose(str(a), "publish", p.id, "main", None, None, False, None, "origin", False)
    assert rc == 1  # gh has no GitHub host to create a PR against for a local-only remote

    view = api.proposal_view(a, p.id)
    rendered = propose.render_github(view)
    # the push itself succeeded before gh pr create was attempted
    out = GitBinding(a)._git("ls-remote", "origin", f"refs/heads/{rendered['branch']}", check=True)
    assert rendered["branch"] in out.stdout
