"""The color contract: the OKLCH identity generator must be byte-identical across the
Python (`sgt/tui/color.py`) and JS (`editor/vscode/media/graph.js`) implementations.

CLAUDE.md asserts this test exists; research found it did not. It does now. The JS side is
driven straight from `graph.js` (the math functions are sliced out and run under node), so the
test fails if either implementation drifts. The webview's `themeLC()` is defined but not
called — we drive `oklchToHex` with the fixed dark L/C that `color.py` uses unconditionally.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from sgt.tui.color import color_for

_GRAPH_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/graph.js"
_IDS = ["abc123", "feature-x", "kg-population", "", "a", "node-7f3a", "integrate-kg-rag-build"]


def test_js_python_color_parity():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")

    text = _GRAPH_JS.read_text(encoding="utf-8")
    # Slice the color math: GOLDEN const through (not including) colorFor — gives GOLDEN,
    # themeLC, hashId, oklchToHex as definitions without invoking any DOM access.
    start = text.index("const GOLDEN")
    end = text.index("function colorFor")
    snippet = text[start:end]

    harness = snippet + (
        "const L = 0.72, C = 0.13;\n"
        f"const ids = {json.dumps(_IDS)};\n"
        "const out = {};\n"
        "for (const id of ids) out[id] = oklchToHex(L, C, ((hashId(id) * GOLDEN) % 1) * 360);\n"
        "console.log(JSON.stringify(out));\n"
    )
    res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    js_colors = json.loads(res.stdout)

    for id_ in _IDS:
        assert js_colors[id_] == color_for(id_), f"color drift for {id_!r}"
