"""Smoke test for the Textual TUI: it boots against a real project, lists the graph, and a
dry-run preview action does not mutate anything. Skipped when Textual is not installed."""

import asyncio

import pytest

pytest.importorskip("textual")

from sgt.effects.model import Effect  # noqa: E402
from sgt.project import Project  # noqa: E402
from sgt.store.graph import Node, NodeKind  # noqa: E402
from sgt.tui.app import SgtTui  # noqa: E402
from textual.widgets import DataTable  # noqa: E402


def _seed(tmp_path):
    proj = Project.init(tmp_path)
    proj.add_feature(Node(id="base", kind=NodeKind.CAPABILITY, intent="base"),
                     [Effect.add_def("m.py", "base", "def base():\n    return 1")])
    proj.add_feature(Node(id="user", kind=NodeKind.CAPABILITY, intent="uses base"),
                     [Effect.add_def("m.py", "user", "def user():\n    return base()")])
    proj.write_working_tree()
    proj.commit("seed")
    return proj


def test_tui_boots_and_lists_graph(tmp_path):
    _seed(tmp_path)

    async def drive():
        app = SgtTui(str(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#nodes", DataTable)
            assert table.row_count == 2
            # a dry-run preview must not mutate the graph
            await pilot.press("r")
            await pilot.pause()
        assert len(Project.open(tmp_path).graph.nodes()) == 2

    asyncio.run(drive())


def test_tui_filter_narrows_rows(tmp_path):
    _seed(tmp_path)

    async def drive():
        app = SgtTui(str(tmp_path))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            table = app.query_one("#nodes", DataTable)
            assert table.row_count == 2
            app._filter = "uses"  # matches only "uses base"
            app._populate()
            await pilot.pause()
            assert table.row_count == 1

    asyncio.run(drive())


def test_tui_narrow_mode_folds_side_pane(tmp_path):
    _seed(tmp_path)

    async def drive():
        app = SgtTui(str(tmp_path))
        # below the narrow threshold: the detail pane folds away
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app._narrow is True
            assert app.query_one("#side").display is False

    asyncio.run(drive())
