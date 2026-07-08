"""The canonical JSON projection (sgt.api) consumed by the CLI --json mode and MCP.

The operation-ideal kernel's read surface: the op DAG, the current ideal, and ideal-vs-ideal
semantic diffs. Fixtures are deterministic git repos (tests/laws/corpus.py, pinned SHAs) mined by
`sgt.core.lens.get`.
"""

import json

from sgt.api import ideal_diff_view, oplog_view, state_view
from sgt.core.lens import get
from tests.laws import corpus


def _mined(tmp_path, name):
    """Build a deterministic kernel git fixture and mine it (get) so the store is populated."""
    repo = corpus.CORPUS[name].build(tmp_path / "repo")
    get(repo)
    return repo


def test_state_view_coverage_fraction_on_mixed_fixture(tmp_path):
    """R7: on a tree of two Python files + a YAML + a Markdown file, exactly the two code paths
    are entity-granular and the two non-parseable paths get whole-file coverage -- so the honest
    entity-granularity coverage fraction is 2/4 = 0.5."""
    v = state_view(_mined(tmp_path, "mixed_coverage"))
    assert v["covered_paths"] == ["config.yaml", "notes.md", "pkg.py", "util.py"]
    assert v["entity_paths"] == ["pkg.py", "util.py"]
    assert v["coverage_fraction"] == 0.5
    assert v["oracle_configured"] is False  # no `.sgt/oracle.json` in this fixture
    assert v["oracle_verdict"] is None
    # the frontier is the per-chain vector: it names entity, residue, anchor, and whole-file syms
    assert "pkg.py::compute" in v["frontier"]
    assert "config.yaml" in v["frontier"]


def test_ideal_diff_view_lists_symmetric_difference_grouped_by_symbol(tmp_path):
    """`ideal_diff_view` between two diverged branch ideals lists exactly the symmetric-difference
    ops, grouped by the symbol whose chain forked, labeled by side."""
    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    get(repo)
    corpus.checkout(repo, "main")
    get(repo)

    v = ideal_diff_view(repo, "main", "release")
    assert v["ref_a"] == "main" and v["ref_b"] == "release"
    assert v["count"] == 2  # each branch's own tweak, and nothing else
    assert list(v["by_symbol"]) == ["slugify.py::slugify"]  # only the forked chain differs
    sides = v["by_symbol"]["slugify.py::slugify"]
    assert len(sides["only_in_a"]) == 1 and len(sides["only_in_b"]) == 1
    assert sides["only_in_a"] != sides["only_in_b"]  # a genuine fork, two distinct op ids


def test_oplog_view_is_sorted_and_carries_op_fields(tmp_path):
    """The op DAG is emitted in a deterministic (id-sorted) order with each op's kind, footprint,
    provenance, and intent -- no set-iteration leakage."""
    v = oplog_view(_mined(tmp_path, "mixed_coverage"))
    assert v["count"] == len(v["ops"]) > 0
    assert [op["id"] for op in v["ops"]] == sorted(op["id"] for op in v["ops"])
    op = v["ops"][0]
    assert set(op) == {"id", "kind", "footprint", "provenance", "intent"}
    assert op["footprint"] and all({"symbol", "before", "after"} == set(f) for f in op["footprint"])
    assert op["provenance"]  # every mined op carries at least its witnessing commit


def test_kernel_views_are_pure(tmp_path):
    """Views are side-effect-free reads: called twice over a freshly-mined store they produce
    byte-identical output and mint no new ops (no network/timestamp leakage)."""
    repo = _mined(tmp_path, "mixed_coverage")
    first = json.dumps(state_view(repo), sort_keys=True)
    op_count = oplog_view(repo)["count"]
    second = json.dumps(state_view(repo), sort_keys=True)
    assert first == second
    assert oplog_view(repo)["count"] == op_count


def test_log_and_state_cli_json_match_views_byte_for_byte(tmp_path, capsys, monkeypatch):
    """R21: `sgt log/state --json` output is byte-identical to the api views -- the single
    projection, no drift between the CLI surface and the api."""
    from sgt.cli import main

    repo = _mined(tmp_path, "mixed_coverage")
    expected = {"log": json.dumps(oplog_view(repo), indent=2),
                "state": json.dumps(state_view(repo), indent=2)}

    monkeypatch.chdir(repo)
    for verb in ("log", "state"):
        assert main([verb, "--json"]) == 0
        assert capsys.readouterr().out.rstrip("\n") == expected[verb]


def test_diff_cli_json_matches_view_byte_for_byte(tmp_path, capsys, monkeypatch):
    """R21: `sgt diff --json <a> <b>` output is byte-identical to `sgt.api.ideal_diff_view`."""
    from sgt.cli import main

    repo = corpus.CORPUS["diverged_chain"].build(tmp_path / "repo")
    corpus.checkout(repo, "release")
    get(repo)
    corpus.checkout(repo, "main")
    get(repo)
    expected = json.dumps(ideal_diff_view(repo, "main", "release"), indent=2)

    monkeypatch.chdir(repo)
    assert main(["diff", "--json", "main", "release"]) == 0
    assert capsys.readouterr().out.rstrip("\n") == expected
