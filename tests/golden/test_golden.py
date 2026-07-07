"""Characterization golden master for the `sgt.api` projection (plan P0, INV-1).

For each deterministic corpus project, capture every public view and compare it byte-for-byte
against a committed snapshot. Later refactor phases (op-log fold, verbatim splice, Node-store
deletion) must keep these snapshots stable *modulo additive keys* — a drift here means an
observable behavior change a client would see.

Regenerate after an intentional, additive projection change:

    SGT_UPDATE_GOLDEN=1 uv run pytest tests/golden/ -q

then review the snapshot diff before committing.
"""

from __future__ import annotations

import difflib
import json
import os
import pathlib

import pytest

from tests.golden.corpus import CORPUS, KERNEL_CORPUS, capture_kernel_views, capture_views

_SNAPSHOTS = pathlib.Path(__file__).resolve().parent / "snapshots"


def _dump(views: dict) -> str:
    return json.dumps(views, indent=2, sort_keys=True) + "\n"


def _assert_matches_golden(snapshot_name: str, actual: str) -> None:
    snapshot = _SNAPSHOTS / snapshot_name

    if os.environ.get("SGT_UPDATE_GOLDEN"):
        snapshot.write_text(actual, encoding="utf-8")
        pytest.skip(f"updated golden snapshot {snapshot.name}")

    assert snapshot.exists(), f"missing golden {snapshot} — regenerate with SGT_UPDATE_GOLDEN=1"

    expected = snapshot.read_text(encoding="utf-8")
    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile=f"{snapshot_name} (golden)",
                tofile=f"{snapshot_name} (actual)",
                lineterm="",
            )
        )
        pytest.fail(f"sgt.api projection drifted for {snapshot_name!r}:\n{diff}")


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_api_projection_matches_golden(name, tmp_path):
    case = CORPUS[name]
    project = case.build(str(tmp_path))
    _assert_matches_golden(f"{name}.json", _dump(capture_views(project, case)))


@pytest.mark.parametrize("name", sorted(KERNEL_CORPUS))
def test_kernel_api_projection_matches_golden(name, tmp_path):
    """The operation-ideal kernel's read views (U7) captured on deterministic git fixtures."""
    _assert_matches_golden(f"kernel_{name}.json", _dump(capture_kernel_views(name, str(tmp_path))))
