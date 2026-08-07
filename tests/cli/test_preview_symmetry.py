"""Preview symmetry for mutating verbs (Phase 3 item 4).

Every verb that changes state should show what it is about to do before it does it, and the shape of
that showing should not depend on which optional dependencies happen to be installed. Three families
degrade three ways, and all three must end in the user being *asked*:

* ideal edits (`revert`/`restore`) -- consequence pane, else `[y/N]`
* collaboration (`land`/`sync`/`propose land`/`resolve`) -- pane, else the printed feedforward graph
  plus `[y/N]` (`confirm_collab`)
* metadata feature verbs (`merge`/`rename`/`move`) -- pane, else the printed summary plus `[y/N]`
  (`confirm_summary`)

The third had no degrade: `maybe_confirm` returns `None` when `textual` is absent, and the caller
read that as "proceed", so on a machine without that optional package a feature re-cut applied with
nothing shown and nothing asked.

Off a tty every family still applies immediately -- that machine/CI contract is deliberate, and
these tests pin it too, since "always prompt" would hang scripts.
"""

from __future__ import annotations

import pytest

from sgt.cli import _common, feature
from sgt.core.lens import get
from sgt.lens import map as lensmap
from sgt.lens import tree
from sgt.lens import verbs as lens_verbs
from tests.laws import corpus


@pytest.fixture()
def repo(tmp_path):
    r = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(r)
    lensmap.build_map(r)
    return r


def _leaves(repo):
    """Leaf ids from the *persisted* tree. Deliberately not `build_map`, which re-clusters and can
    hand back ids that are no longer leaves -- the reorg verbs only accept leaves."""
    return sorted(nid for nid, nd in (tree.load(repo) or {"nodes": {}})["nodes"].items()
                  if not nd["children"])


def _two_features(repo):
    """Force the fixture's single leaf into two so merge/move have something to operate on."""
    preview = lens_verbs.plan_split(repo, _leaves(repo)[0])
    assert preview.ok, preview.message
    result = lens_verbs.apply_split(repo, preview, confirm=True)
    return sorted(nid for nid, nd in result["nodes"].items() if not nd["children"])


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
    survivor, absorbed = _two_features(repo)[:2]
    _tty(monkeypatch, textual_available=False)
    asked = {}

    def fake_input(prompt):
        asked["prompt"] = prompt
        return "n"  # decline

    monkeypatch.setattr("builtins.input", fake_input)
    before = set(_leaves(repo))

    rc = feature._feature_merge(str(repo), survivor, absorbed)

    assert rc == 1, "a declined confirm must not report success"
    assert "prompt" in asked, "the user was never asked"
    out = capsys.readouterr().out
    assert "merge" in out
    assert "metadata only" in out, "the summary must say what kind of change this is"
    assert set(_leaves(repo)) == before, "nothing may have changed"


def test_feature_verb_applies_when_the_prompt_is_accepted(repo, monkeypatch):
    survivor, absorbed = _two_features(repo)[:2]
    _tty(monkeypatch, textual_available=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    rc = feature._feature_merge(str(repo), survivor, absorbed)
    assert rc == 0
    assert absorbed not in _leaves(repo)


def test_off_a_tty_a_feature_verb_still_applies_immediately(repo, monkeypatch):
    """The machine/CI contract: a script or an editor shelling out has nobody to answer a prompt, so
    it must not be asked. Pinned because "always confirm" would hang every non-interactive caller."""
    survivor, absorbed = _two_features(repo)[:2]
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)

    def boom(prompt):
        raise AssertionError("a non-interactive caller must never be prompted")

    monkeypatch.setattr("builtins.input", boom)
    assert feature._feature_merge(str(repo), survivor, absorbed) == 0


def test_rename_and_move_share_the_same_gate(repo, monkeypatch, capsys):
    """All three metadata verbs route through one `_confirm`, so none can drift back to applying
    silently."""
    features = _two_features(repo)
    _tty(monkeypatch, textual_available=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    assert feature._feature_rename(str(repo), features[0], "a new name") == 1
    assert "a new name" in capsys.readouterr().out

    ops = sorted((tree.load(repo) or {"op_leaf": {}})["op_leaf"])[:1]
    assert feature._feature_move(str(repo), ops, features[1]) == 1
    assert "op(s)" in capsys.readouterr().out


def test_split_previews_by_default_without_needing_a_prompt(repo, capsys):
    """`split` was already symmetric a different way -- it prints the groups and does nothing unless
    `--apply` is passed. Pinned so the new gate didn't turn a safe default into a prompt."""
    fid = _leaves(repo)[0]
    rc = feature._feature_split(str(repo), fid, do_apply=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "group 0" in out and "--apply" in out


def test_json_mode_never_prompts(repo, monkeypatch):
    """`--json` is a machine surface; a prompt there would corrupt the payload."""
    survivor, absorbed = _two_features(repo)[:2]
    _tty(monkeypatch, textual_available=False)

    def boom(prompt):
        raise AssertionError("--json must never prompt")

    monkeypatch.setattr("builtins.input", boom)
    assert feature._feature_merge(str(repo), survivor, absorbed, as_json=True) == 0
