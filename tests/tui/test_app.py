"""Smoke tests for the Textual TUI (`sgt.tui.app.SgtTui`): it boots against a real mined fixture,
lists the feature tree, and the status line reflects `status_view`'s `sync_status` (U8). Skipped
when Textual is not installed."""

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import DataTable, Static  # noqa: E402

import sgt.core.lens as lens_mod  # noqa: E402
from sgt.core.lens import get  # noqa: E402
from sgt.lens.map import build_map  # noqa: E402
from sgt.tui.app import SgtTui  # noqa: E402
from tests.laws import corpus  # noqa: E402


def _seed(tmp_path):
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)
    build_map(repo)
    return repo


def test_tui_boots_and_lists_features(tmp_path):
    repo = _seed(tmp_path)

    async def drive():
        app = SgtTui(str(repo))
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#nodes", DataTable)
            assert table.row_count > 0

    asyncio.run(drive())


def test_status_line_shows_indexing_segment_when_sync_is_incomplete(tmp_path, monkeypatch):
    """U8: a ref whose first-contact `_sync()` chunk is cut short by a deadline (same fixture
    technique as U6/U7's tests) makes the status line show an indexing segment; a fully-synced
    fixture shows no such segment."""
    monkeypatch.setattr(lens_mod, "_CHUNK_BUDGET_SECONDS", -1.0)
    repo = corpus.CORPUS["mixed_coverage"].build(tmp_path / "repo")
    get(repo)  # first-contact chunk, deadline already past -- zero progress, incomplete sync

    async def drive():
        app = SgtTui(str(repo))
        async with app.run_test() as pilot:
            await pilot.pause()
            line = app.query_one("#status-line", Static)
            assert "indexing" in line.content.plain

    asyncio.run(drive())


def test_status_line_hides_indexing_segment_when_sync_is_complete(tmp_path):
    repo = _seed(tmp_path)

    async def drive():
        app = SgtTui(str(repo))
        async with app.run_test() as pilot:
            await pilot.pause()
            line = app.query_one("#status-line", Static)
            assert "indexing" not in line.content.plain

    asyncio.run(drive())
