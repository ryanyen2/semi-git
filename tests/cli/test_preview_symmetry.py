"""Preview symmetry for mutating verbs (Phase 3 item 4).

Every verb that changes state should show what it is about to do before it does it, and the shape of
that showing should not depend on which optional dependencies happen to be installed. Three families
degrade three ways, and all three must end in the user being *asked*:

* ideal edits (`revert`/`restore`) -- consequence pane, else `[y/N]`
* collaboration (`sync`/`resolve`) -- pane, else the printed feedforward graph
  plus `[y/N]` (`confirm_collab`)
* `feature rename` -- pane, else the printed summary plus `[y/N]` (`confirm_summary`)

The third had no degrade: `maybe_confirm` returns `None` when `textual` is absent, and the caller
read that as "proceed", so on a machine without that optional package a relabel applied with nothing
shown and nothing asked.

Off a tty every family still applies immediately -- that machine/CI contract is deliberate, and
these tests pin it too, since "always prompt" would hang scripts.
"""

from __future__ import annotations

import pytest

from ugh.cli import _common, feature
from ugh.core.lens import get
from ugh.lens import map as lensmap
from ugh.lens import tree
from ugh.lens import verbs as lens_verbs
from tests.laws import corpus


@pytest.fixture()
def repo(tmp_path):
    r = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(r)
    lensmap.build_map(r)
    return r


def _leaves(repo):
    """Leaf ids from the *persisted* tree. Deliberately not `build_map`, which re-clusters and can
    hand back ids that are no longer leaves."""
    return sorted(nid for nid, nd in (tree.load(repo) or {"nodes": {}})["nodes"].items()
                  if not nd["children"])


def _label(repo, fid):
    return lens_verbs.plan_rename(repo, fid, "probe").old_label


def _tty(monkeypatch, *, textual_available: bool):
    """An interactive tty, with `textual` present or absent. `maybe_confirm` returning None is
    exactly how the no-textual case presents itself."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    if not textual_available:
        monkeypatch.setattr(_common, "maybe_confirm", lambda *a, **k: None)


def test_feature_verb_on_a_tty_without_textual_asks_before_applying(repo, monkeypatch, capsys):
    """The regression. Without `textual` the verb must print its consequence and prompt -- and a
    declined prompt must leave the tree untouched."""
    fid = _leaves(repo)[0]
    before = _label(repo, fid)
    _tty(monkeypatch, textual_available=False)
    asked = {}

    def fake_input(prompt):
        asked["prompt"] = prompt
        return "n"  # decline

    monkeypatch.setattr("builtins.input", fake_input)

    rc = feature._feature_rename(str(repo), fid, "a new name")

    assert rc == 1, "a declined confirm must not report success"
    assert "prompt" in asked, "the user was never asked"
    out = capsys.readouterr().out
    assert "a new name" in out
    assert "metadata only" in out, "the summary must say what kind of change this is"
    assert _label(repo, fid) == before, "nothing may have changed"


def test_feature_verb_applies_when_the_prompt_is_accepted(repo, monkeypatch):
    fid = _leaves(repo)[0]
    _tty(monkeypatch, textual_available=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    assert feature._feature_rename(str(repo), fid, "a new name") == 0
    assert _label(repo, fid) == "a new name"


def test_off_a_tty_a_feature_verb_still_applies_immediately(repo, monkeypatch):
    """The machine/CI contract: a script or an editor shelling out has nobody to answer a prompt, so
    it must not be asked. Pinned because "always confirm" would hang every non-interactive caller."""
    fid = _leaves(repo)[0]
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)

    def boom(prompt):
        raise AssertionError("a non-interactive caller must never be prompted")

    monkeypatch.setattr("builtins.input", boom)
    assert feature._feature_rename(str(repo), fid, "a new name") == 0


def test_json_mode_never_prompts(repo, monkeypatch):
    """`--json` is a machine surface; a prompt there would corrupt the payload."""
    fid = _leaves(repo)[0]
    _tty(monkeypatch, textual_available=False)

    def boom(prompt):
        raise AssertionError("--json must never prompt")

    monkeypatch.setattr("builtins.input", boom)
    assert feature._feature_rename(str(repo), fid, "a new name", as_json=True) == 0
