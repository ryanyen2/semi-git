"""The color contract: the OKLCH identity generator must be byte-identical across all three
mirrors — Python (`sgt/tui/color.py`), the webview JS (`editor/vscode/media/decision.js`), and
the extension-host TypeScript (`editor/vscode/src/color.ts`).

Each JS/TS side is driven straight from its own source (the math functions are sliced out and
run under node), so the test fails if any implementation drifts. We drive them with the fixed
dark L/C that `color.py` uses unconditionally, comparing every mirror against the Python anchor —
so agreement is transitive across all three.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from sgt.tui.color import color_for

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_GRAPH_JS = _ROOT / "editor/vscode/media/decision.js"
_COLOR_TS = _ROOT / "editor/vscode/src/color.ts"
_IDS = ["abc123", "feature-x", "kg-population", "", "a", "node-7f3a", "integrate-kg-rag-build"]


def _node_strips_types(node: str) -> bool:
    """Whether this node can run TypeScript (type-stripping, node >= 22.6)."""
    with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as f:
        f.write("const x: number = 1;\nprocess.stdout.write(String(x));\n")
        probe = f.name
    res = subprocess.run(
        [node, "--experimental-strip-types", probe], capture_output=True, text=True
    )
    return res.returncode == 0 and res.stdout.strip() == "1"


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


def test_ts_python_color_parity():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if not _node_strips_types(node):
        pytest.skip("node too old to run TypeScript (needs type-stripping, >= 22.6)")

    text = _COLOR_TS.read_text(encoding="utf-8")
    # Slice just the pure math — GOLDEN plus hashId..oklchToRgb — skipping lc(), which reads
    # `vscode` and is the only theme-dependent piece. color.ts formats hex from an rgb tuple
    # (oklchToRgb) rather than returning it (oklchToHex), so we reproduce colorForNode's
    # formatting in the harness and compare the final #rrggbb.
    golden = text[text.index("const GOLDEN") : text.index("\n", text.index("const GOLDEN")) + 1]
    math = text[text.index("function hashId") : text.index("function hueForId")]

    harness = golden + math + (
        "const L = 0.72, C = 0.13;\n"
        f"const ids = {json.dumps(_IDS)};\n"
        "const out: Record<string, string> = {};\n"
        "for (const id of ids) {\n"
        "  const hue = ((hashId(id) * GOLDEN) % 1) * 360;\n"
        "  const [r, g, b] = oklchToRgb(L, C, hue);\n"
        '  out[id] = "#" + [r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("");\n'
        "}\n"
        "console.log(JSON.stringify(out));\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as f:
        f.write(harness)
        script = f.name
    res = subprocess.run(
        [node, "--experimental-strip-types", script], capture_output=True, text=True
    )
    assert res.returncode == 0, res.stderr
    ts_colors = json.loads(res.stdout)

    for id_ in _IDS:
        assert ts_colors[id_] == color_for(id_), f"color.ts drift for {id_!r}"
