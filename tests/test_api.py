"""The canonical JSON projection (ugh.api) consumed by the CLI --json mode and MCP. Fixtures are
deterministic git repos (tests/laws/corpus.py, pinned SHAs) mined by `ugh.core.lens.get`."""

import json

from ugh.api import log_view, state_view, status_view
from ugh.core.lens import get
from ugh.core.store import Store
from tests.laws import corpus


def _take_out(repo, op_ids):
    from ugh.core import planner

    steps = [st for st, ops in planner._Ctx(repo).steps.items() if set(ops) & set(op_ids)]
    return planner.apply(repo, planner.plan(repo, steps, target="t"))


def _head_ops(repo):
    from ugh.core.opindex import index_ops
    from ugh.store.gitbind import GitBinding

    head = GitBinding(repo).head()
    return {o.id for o in index_ops(repo) if head in o.provenance}


def _mined(tmp_path, name):
    """Build a deterministic kernel git fixture and mine it (get) so the store is populated."""
    repo = corpus.CORPUS[name].build(tmp_path / "repo")
    get(repo)
    return repo


def test_state_view_coverage_fraction_on_mixed_fixture(tmp_path):
    """R7: on a tree of two Python files + a YAML + a Markdown file, exactly the two code paths
    are entity-granular and the two non-parseable paths get whole-file coverage -- so the honest
    entity-granularity coverage fraction is 2/4 = 0.5."""
    v = state_view(_mined(tmp_path, "mixed_coverage"), full=True)
    assert v["covered_paths"] == ["config.yaml", "notes.md", "pkg.py", "util.py"]
    assert v["entity_paths"] == ["pkg.py", "util.py"]
    assert v["coverage_fraction"] == 0.5
    # the frontier is the per-chain vector: it names entity, residue, anchor, and whole-file syms
    assert "pkg.py::compute" in v["frontier"]
    assert "config.yaml" in v["frontier"]


def test_kernel_views_are_pure(tmp_path):
    """Views are side-effect-free reads: called twice over a freshly-mined store they produce
    byte-identical output and mint no new ops (no network/timestamp leakage)."""
    repo = _mined(tmp_path, "mixed_coverage")
    first = json.dumps([state_view(repo), log_view(repo)], sort_keys=True)
    op_count = len(Store(repo).all_ops())
    second = json.dumps([state_view(repo), log_view(repo)], sort_keys=True)
    assert first == second
    assert len(Store(repo).all_ops()) == op_count


def test_log_cli_json_matches_the_view_byte_for_byte(tmp_path, capsys, monkeypatch):
    """R21: the CLI `--json` surface is byte-identical to the api view -- one projection, no drift."""
    from ugh.cli import main

    repo = _mined(tmp_path, "mixed_coverage")
    want = json.dumps(log_view(repo), indent=2)
    monkeypatch.chdir(repo)
    assert main(["log", "--json"]) == 0
    assert capsys.readouterr().out.rstrip("\n") == want


def test_sync_status_reports_complete_on_a_fresh_fully_synced_fixture(tmp_path):
    repo = _mined(tmp_path, "mixed_coverage")
    assert status_view(repo)["sync_status"] == {"complete": True}


def test_sync_status_reports_incomplete_and_the_view_does_not_mine(tmp_path, monkeypatch):
    """A ref whose first-contact `_sync()` chunk is cut short by a deadline reports `complete ==
    False`, and reading the view triggers no additional mining."""
    import ugh.core.lens as lens_mod

    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")  # a chunk mines one commit
    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", -1.0)
    get(repo)

    op_count_before = len(Store(repo).all_ops())
    assert status_view(repo)["sync_status"] == {"complete": False}
    assert len(Store(repo).all_ops()) == op_count_before


# -- compact-by-default view contracts (plan: optimize the ugh agent surface for context +
# retrieval speed, Part B) -----------------------------------------------------------------------


def test_state_view_default_is_compact_and_full_restores_frontier(tmp_path):
    repo = _mined(tmp_path, "mixed_coverage")
    compact = state_view(repo)
    full = state_view(repo, full=True)

    assert "frontier" not in compact and "entity_paths" not in compact
    assert compact["frontier_count"] == len(full["frontier"])
    assert compact["entity_path_count"] == len(full["entity_paths"])
    # fields status_view depends on stay present at the default
    for key in ("covered_paths", "coverage_fraction", "derived_paths"):
        assert compact[key] == full[key]


# -- bookkeeping folding: ugh's own commits are not the developer's work ---------------------------

def test_log_view_leaves_out_ugh_s_own_take_out_commits(tmp_path):
    """History is append-only, so a revert is itself a forward commit; `log` lists the saves."""
    repo = _mined(tmp_path, "linear_history")
    before = log_view(repo)
    _take_out(repo, _head_ops(repo))  # materializes a real commit for its own mechanics

    after = log_view(repo)
    assert [s["sha"] for s in after["saves"]] == [s["sha"] for s in before["saves"]]
    assert any(st["out"] for st in after["saves"][0]["steps"])


def test_a_user_save_is_never_marked_as_bookkeeping(tmp_path):
    """The mark must follow ugh's own mechanics only -- a save carrying the user's message is the
    work itself, and folding it would hide exactly what the developer wants to see."""
    from ugh.cli.porcelain import save

    repo = _mined(tmp_path, "linear_history")
    (repo / "new.py").write_text("def thing():\n    return 1\n", encoding="utf-8")
    assert save(str(repo), message="add the thing I meant to add")["saved"]

    assert log_view(repo)["saves"][0]["subject"] == "add the thing I meant to add"
