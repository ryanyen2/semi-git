"""The revert-closure classification contract: `classifyAffected` in
editor/vscode/media/workbench.js splits a verb preview's `affected` rows into the three roles the
graph overlay paints -- target / blast (features losing ops) / foundation (features gaining
re-drafted ops) -- matching the CLI's blast/foundation buckets (sgt.api._affected_rows). Pure, so
we slice it out and run it under node."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_JS = pathlib.Path(__file__).resolve().parents[1] / "editor/vscode/media/workbench.js"


def _run(result: dict, target: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    text = _JS.read_text(encoding="utf-8")
    start = text.index("function classifyAffected")
    end = text.index("// ---- end-closure")
    snippet = text[start:end]
    harness = snippet + (
        f"console.log(JSON.stringify(classifyAffected({json.dumps(result)}, {json.dumps(target)})));\n"
    )
    res = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _affected(*rows):
    return {"affected": [{"feature_id": f, "direction": d, "op_count": c} for f, d, c in rows]}


def test_target_is_separated_from_collateral_blast_and_redraft_foundation():
    # Reverting F1: F1 and F2 lose ops (blast); F9 gains re-drafted hollows (foundation).
    result = _affected(("F1", "blast", 10), ("F2", "blast", 3), ("F9", "foundation", 2))
    out = _run(result, "F1")
    assert out == {"target": "F1", "blast": ["F2"], "foundation": ["F9"]}


def test_target_never_appears_as_its_own_collateral():
    result = _affected(("F1", "blast", 10))  # only the target loses ops
    out = _run(result, "F1")
    assert out == {"target": "F1", "blast": [], "foundation": []}


def test_missing_affected_is_just_the_bare_target():
    out = _run({"ok": True}, "F1")
    assert out == {"target": "F1", "blast": [], "foundation": []}
