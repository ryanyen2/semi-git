"""Print what the confirm screen actually says before a session revert.

`sgt revert --session <name>` shows a Textual pane on a tty and a plain prompt
everywhere else, so piping it into a file shows you the wrong one. This drives
the real pane the way the test suite does and prints the four things a person
reads off it: the consequence line, the code rail, the fallout rows, and the key
hints.

    python3 scripts/study/harvest/inspect_consequence.py <repo> <session-name>
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sgt.api import _project_verb_preview, grid_view, map_view, segments_view
from sgt.core import verbs
from sgt.tui.consequence import ConsequenceApp
from textual.widgets import DataTable, Label, Static


def text_of(app, selector, kind=Static):
    try:
        widget = app.query_one(selector, kind)
    except Exception:
        return None
    render = widget.render() if hasattr(widget, "render") else widget
    return str(render)


async def drive(repo, name):
    preview = verbs.plan_revert_session(repo, name)
    pview = _project_verb_preview(repo, preview)

    app = ConsequenceApp(pview, map_view(repo), grid_view(repo), segments_view(repo), focus_fid=None)
    async with app.run_test() as pilot:
        await pilot.pause()

        print("── the one line that answers 'so what?' ──")
        print(text_of(app, "#so-what") or "(nothing)")

        print("\n── the code rail: where the edit lands ──")
        print(text_of(app, "#rail-body") or "(nothing)")

        print("\n── fallout you have to decide about ──")
        try:
            table = app.query_one("#fallout-table", DataTable)
            if table.row_count == 0:
                print("(no rows: nothing else is built on this work)")
            for key in table.rows:
                print("  " + " | ".join(str(c) for c in table.get_row(key)))
            print(text_of(app, "#fallout-counts") or "")
        except Exception:
            print("(no fallout table: nothing else is built on this work)")

        print("\n── what the keys do ──")
        print(text_of(app, "#hint", Label) or "(nothing)")

    print("\n── the raw numbers behind all of the above ──")
    for field in ("removes", "adds", "symbols", "files", "fallout", "frontier", "so_what"):
        if field in pview:
            value = pview[field]
            if isinstance(value, list):
                print(f"  {field}: {len(value)} -> {value[:6]}")
            else:
                print(f"  {field}: {value}")


if __name__ == "__main__":
    asyncio.run(drive(sys.argv[1], sys.argv[2]))
