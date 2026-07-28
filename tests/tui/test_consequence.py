"""The consequence focus pane (`sgt.tui.consequence.ConsequenceApp`): the interactive confirm step
a mutating verb shows on a tty in place of `[y/N]`. Driven through a real pilot (the
`app.run_test()` pattern). We verify the pane is wired to the
pure so-what layer -- `space` toggles a blast dependent into the kept-set and the so-what line
recomputes; `enter` returns `Decision(True, kept)`; `escape` returns `Decision(False)`. The
sentence wording itself is pinned in `tests/test_so_what.py`."""

import asyncio

import pytest

pytest.importorskip("textual")

from sgt.api import _project_verb_preview, grid_view, map_view, segments_view  # noqa: E402
from sgt.core import verbs  # noqa: E402
from sgt.core.lens import get  # noqa: E402
from sgt.core.store import Store  # noqa: E402
from sgt.lens.map import build_map  # noqa: E402
from sgt.store.gitbind import init_store  # noqa: E402
from sgt.tui.consequence import ConsequenceApp, Decision, frontier_counts  # noqa: E402


def _chain_repo(tmp_path):
    """helper <- user <- caller: reverting `user` makes `caller` a toggleable blast dependent,
    so the pane shows a fallout row to toggle."""
    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    gb.commit_all("helper")
    (repo / "b.py").write_text("from a import helper\n\ndef user():\n    return helper()\n", encoding="utf-8")
    gb.commit_all("user")
    (repo / "c.py").write_text("from b import user\n\ndef caller():\n    return user()\n", encoding="utf-8")
    gb.commit_all("caller")
    get(repo)
    build_map(repo)
    return repo


def _revert_pview(repo, symbol_fragment):
    ops = Store(repo).all_ops()
    op = next(o for o in ops if any(symbol_fragment in f for f in o.footprint))
    preview = verbs.plan_revert(repo, op.id)
    return _project_verb_preview(repo, preview)


def test_space_toggles_a_dependent_and_the_so_what_recomputes(tmp_path):
    repo = _chain_repo(tmp_path)
    pview = _revert_pview(repo, "b.py::user")
    assert pview["fallout"], "fixture must have a toggleable blast dependent"

    async def drive():
        app = ConsequenceApp(pview, map_view(repo), grid_view(repo),
                             segments_view(repo), focus_fid=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._kept == set()
            before = str(app.query_one("#so-what").render())
            assert "to re-draft" in before  # a dependent will break

            await pilot.press("space")  # keep the highlighted blast dependent alive
            await pilot.pause()
            assert len(app._kept) == 1
            after = str(app.query_one("#so-what").render())
            assert "keeping 1" in after  # the kept-set flipped the sentence

            await pilot.press("space")  # toggle it back off
            await pilot.pause()
            assert app._kept == set()

    asyncio.run(drive())


def test_enter_returns_apply_with_the_kept_set(tmp_path):
    repo = _chain_repo(tmp_path)
    pview = _revert_pview(repo, "b.py::user")
    kept_ids = {r["op_id"] for r in pview["fallout"]}

    async def drive():
        app = ConsequenceApp(pview, map_view(repo), grid_view(repo),
                             segments_view(repo), focus_fid=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("space")  # keep the one blast dependent
            await pilot.pause()
            await pilot.press("enter")
        return app.return_value

    result = asyncio.run(drive())
    assert isinstance(result, Decision)
    assert result.apply is True
    assert result.kept == frozenset(kept_ids)


def test_escape_returns_abort_and_keeps_nothing(tmp_path):
    repo = _chain_repo(tmp_path)
    pview = _revert_pview(repo, "b.py::user")

    async def drive():
        app = ConsequenceApp(pview, map_view(repo), grid_view(repo),
                             segments_view(repo), focus_fid=None)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
        return app.return_value

    result = asyncio.run(drive())
    assert result == Decision(False, frozenset())


def _merge_pview():
    """A metadata-verb projection: no code rail, no fallout -- just the precomputed `summary`
    lines and a so-what sentence, exactly what `_project_feature_preview` hands the pane."""
    return {
        "ok": True, "verb": "merge", "target": "auth", "affected_symbols": ["auth"],
        "forked": False, "message": "", "files": {}, "fallout": [], "carry_count": 0,
        "reversible": True, "summary": ["absorb login → auth", "3 op(s) · 5 member(s)"],
        "so_what": "Merges into auth — metadata only, code untouched. Undo-able.",
    }


def test_metadata_verb_pane_renders_summary_and_has_no_fallout_table():
    pview = _merge_pview()

    async def drive():
        app = ConsequenceApp(pview)  # no views -- a metadata verb passes none
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._blast == []  # nothing to toggle
            assert not app.query("#fallout-table")  # table only mounts when there's blast
            rail = str(app.query_one("#rail-body").render())
            assert "absorb login → auth" in rail
            so_what = str(app.query_one("#so-what").render())
            assert "metadata only, code untouched" in so_what
            await pilot.press("enter")
        return app.return_value

    result = asyncio.run(drive())
    assert result == Decision(True, frozenset())


def test_metadata_verb_pane_escape_aborts():
    async def drive():
        app = ConsequenceApp(_merge_pview())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
        return app.return_value

    assert asyncio.run(drive()) == Decision(False, frozenset())


# -- frontier_counts (pure logic, no pilot) -----------------------------------


def _frontier():
    return [
        {"op_id": "a", "bucket": "blast", "toggleable": True},
        {"op_id": "b", "bucket": "carry", "toggleable": True},
        {"op_id": "c", "bucket": "foundation", "toggleable": False},
    ]


def test_frontier_counts_default_kept_removes_every_toggleable_dependent():
    c = frontier_counts(_frontier(), kept=set())
    assert c["removed"] == 2 and c["kept"] == 0
    assert c["toggleable"] == ["a", "b"]
    assert c["foundation"] == ["c"]


def test_frontier_counts_keeping_a_dependent_moves_it_out_of_removed():
    c = frontier_counts(_frontier(), kept={"a"})
    assert c["removed"] == 1 and c["kept"] == 1


def test_frontier_counts_ignores_a_kept_id_that_is_not_toggleable():
    # foundation ("c") is read-only: it can never be "kept" and never counts as removed.
    c = frontier_counts(_frontier(), kept={"c", "stale"})
    assert c["removed"] == 2 and c["kept"] == 0


def test_frontier_counts_empty_rows():
    c = frontier_counts([], kept=set())
    assert c == {"toggleable": [], "foundation": [], "kept": 0, "removed": 0}
