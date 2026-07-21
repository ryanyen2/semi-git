"""Smoke tests for the TUI's U9 interactions, driven through a real pilot (the same
`app.run_test()` pattern as `test_app`): the app boots, a feature expands into its member symbols,
a fuzzy fragment narrows the table (and clears back), spacebar multi-selects a row, and the frontier
panel opens. The pure ranking/counting logic is unit-tested in `test_app_logic`; here we only
verify the widgets are wired to it."""

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable, Input  # noqa: E402

from sgt.core.lens import get  # noqa: E402
from sgt.lens.map import build_map  # noqa: E402
from sgt.tui.app import FrontierScreen, SgtTui  # noqa: E402
from tests.laws import corpus  # noqa: E402


def _seed(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    build_map(repo)
    return repo


def test_expand_and_fuzzy_filter_narrows_then_clears(tmp_path):
    repo = _seed(tmp_path)

    async def drive():
        app = SgtTui(str(repo))
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#nodes", DataTable)
            assert table.row_count >= 1

            await pilot.press("e")  # expand the highlighted feature into its member symbols
            await pilot.pause()
            expanded = table.row_count
            assert expanded > 1  # feature row + >=1 member symbol row

            inp = app.query_one("#filter", Input)
            inp.value = "compute"  # a fragment that matches the `compute` symbol, not every row
            await pilot.pause()
            assert 0 < table.row_count < expanded

            inp.value = "zzzznomatchxyz"  # no match -> empty state, not a crash
            await pilot.pause()
            assert table.row_count == 0

            inp.value = ""  # clearing restores all rows
            await pilot.pause()
            assert table.row_count == expanded

    asyncio.run(drive())


def test_spacebar_multi_selects_and_frontier_panel_opens(tmp_path):
    repo = _seed(tmp_path)

    async def drive():
        app = SgtTui(str(repo))
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("space")  # mark the highlighted row into the selection set
            await pilot.pause()
            assert len(app._selected) == 1

            await pilot.press("f")  # open the frontier panel over the selection
            await pilot.pause()
            assert isinstance(app.screen, FrontierScreen)

    asyncio.run(drive())
