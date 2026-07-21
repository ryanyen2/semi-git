"""Tests for `sgt intent` (plan U7/U8): the thin CLI layer over `sgt.api.intent_view`,
`sgt.intent.theme.build_themes`, and (U8) `sgt.intent.group.resolve_group` +
`sgt.core.verbs.plan_revert_op_set` for `intent revert`. Verb behavior is tested in
tests/intent/test_group.py; this is argument parsing, dispatch, and --json rendering, plus the
revert correctness contract (equivalence to a hand-issued revert over the same op-set, KTD6)."""

from __future__ import annotations

import json
import os

from sgt.cli import main
from sgt.core import verbs
from sgt.core.store import Store
from sgt.intent import theme
from sgt.store.gitbind import init_store


def _in(tmp_path, argv):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return main(argv)
    finally:
        os.chdir(cwd)


def _seed(tmp_path, subject: str = "fix(auth): add foo"):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all(subject)
    return gb


def test_intent_list_json_matches_intent_view(tmp_path, capsys, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    _seed(tmp_path)
    assert _in(tmp_path, ["advanced", "intent", "build"]) == 0
    capsys.readouterr()

    assert _in(tmp_path, ["advanced", "intent", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    from sgt.api import intent_view

    assert payload == intent_view(tmp_path)


def test_intent_show_commit_resolves_atom_and_lists_ops(tmp_path, capsys):
    gb = _seed(tmp_path)
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["advanced", "intent", "show", sha, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["kind"] == "atom"
    assert payload["commit_sha"] == sha
    assert len(payload["op_ids"]) >= 1


def test_intent_show_unknown_target_fails(tmp_path, capsys):
    _seed(tmp_path)

    assert _in(tmp_path, ["advanced", "intent", "show", "no-such-target", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_intent_build_writes_themes_json_second_build_is_a_no_op_cache_hit(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    _seed(tmp_path)

    assert _in(tmp_path, ["advanced", "intent", "build"]) == 0
    themes_path = tmp_path / ".sgt" / "intent" / "themes.json"
    assert themes_path.is_file()
    before_mtime = themes_path.stat().st_mtime_ns

    assert _in(tmp_path, ["advanced", "intent", "build"]) == 0
    after_mtime = themes_path.stat().st_mtime_ns
    assert before_mtime == after_mtime  # no-op cache hit -- save_json_if_changed skips the write


def test_intent_usage_on_missing_or_unknown_sub(tmp_path, capsys):
    _seed(tmp_path)
    assert _in(tmp_path, ["advanced", "intent"]) == 2
    assert "usage: sgt intent" in capsys.readouterr().out


# -- U8: sgt intent revert ---------------------------------------------------------------------


def test_intent_revert_commit_equals_hand_issued_revert_over_the_same_op_set(tmp_path, capsys):
    """The correctness contract for the whole feature (KTD6): resolving a commit sha to its
    deterministic op-set and reverting it must be byte-identical -- removed, added, and the
    resulting oracle-relevant ideal -- to calling `verbs.plan_revert_op_set` directly with that
    exact op-set. The LLM/theme layer is never in this path at all for a bare commit target."""
    from sgt.core.lens import get

    gb = _seed(tmp_path)
    sha = gb.rev_parse("HEAD")
    get(tmp_path)  # mine-on-contact -- the CLI path does this too, before computing its own set
    commit_op_ids = frozenset(op.id for op in Store(tmp_path).all_ops() if sha in op.provenance)

    expected = verbs.plan_revert_op_set(tmp_path, sha, commit_op_ids)

    assert _in(tmp_path, ["advanced", "intent", "revert", sha, "--emit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert sorted(payload["removed"]) == sorted(expected.removed)
    assert sorted(payload["added"]) == sorted(expected.added)
    assert payload["forked"] == expected.forked


def test_intent_revert_emit_shows_diff_without_flipping_the_ideal(tmp_path, capsys):
    gb = _seed(tmp_path)
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["advanced", "intent", "revert", sha, "--emit", "--json"]) == 0
    capsys.readouterr()

    from sgt.core.lens import current_ideal

    before = current_ideal(tmp_path).op_ids
    assert _in(tmp_path, ["advanced", "intent", "revert", sha, "--emit", "--json"]) == 0
    capsys.readouterr()
    after = current_ideal(tmp_path).op_ids
    assert before == after  # --emit never applies


def test_intent_revert_unknown_target_fails(tmp_path, capsys):
    _seed(tmp_path)
    assert _in(tmp_path, ["advanced", "intent", "revert", "no-such-target", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def _seed_two_commits_with_dependency(tmp_path):
    """`b.py::caller` (second commit) calls `a.py::base` (first commit) -- a real reference edge,
    so the two commits' atoms genuinely require each other in `group.group_requires`'s sense."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def base():\n    return 1\n", encoding="utf-8")
    sha_a = gb.commit_all("feat(x): add a.py")
    (tmp_path / "b.py").write_text(
        "from a import base\n\n\ndef caller():\n    return base() + 1\n", encoding="utf-8",
    )
    sha_b = gb.commit_all("feat(x): add b.py calling base")
    return gb, sha_a, sha_b


def test_intent_revert_subset_deselecting_a_required_atom_is_refused_by_name(tmp_path, capsys, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    gb, sha_a, sha_b = _seed_two_commits_with_dependency(tmp_path)
    from sgt.core.lens import get

    get(tmp_path)
    assert _in(tmp_path, ["advanced", "intent", "build"]) == 0
    capsys.readouterr()

    from sgt.api import intent_view

    (theme_entry,) = intent_view(tmp_path)["themes"]

    # select only the earlier commit (base) while excluding the later, dependent one (caller) --
    # reverting base would cascade into removing caller too, so this must be refused by name
    # rather than silently sweeping caller away as an unselected side effect.
    assert _in(
        tmp_path, ["advanced", "intent", "revert", theme_entry["theme_id"], "--subset", sha_a[:12], "--json"],
    ) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert sha_b[:8] in payload["error"]


def test_intent_revert_subset_reverts_only_chosen_atoms(tmp_path, capsys, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    gb, sha_a, sha_b = _seed_two_commits_with_dependency(tmp_path)
    from sgt.core.lens import get

    get(tmp_path)
    assert _in(tmp_path, ["advanced", "intent", "build"]) == 0
    capsys.readouterr()

    from sgt.api import intent_view

    (theme_entry,) = intent_view(tmp_path)["themes"]
    a_op_ids = frozenset(op.id for op in Store(tmp_path).all_ops() if sha_a in op.provenance)

    # selecting only the later (dependent) commit is valid on its own -- nothing else requires it,
    # so it must not cascade into removing anything from the earlier commit it depends on.
    assert _in(
        tmp_path,
        ["advanced", "intent", "revert", theme_entry["theme_id"], "--subset", sha_b[:12], "--emit", "--json"],
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["removed"]  # something was actually removed
    assert not (frozenset(payload["removed"]) & a_op_ids)  # but never any op from the earlier commit


# -- U4: revert surfaces tier ------------------------------------------------------------------


def test_intent_revert_thematic_tier_prints_badge_in_non_json_output(tmp_path, capsys, monkeypatch):
    """Two scope-less, structurally-disconnected commits the LLM coalesces into one theme (no
    dependency edge between them, no tree built) revert at `thematic` tier -- the weakest tier,
    since nothing in the dependency graph backs the cross-commit grouping. The tier line must
    print even though it's not part of the pre-existing "reverting N atom(s)" listing."""
    from types import SimpleNamespace

    class _FakeResponses:
        def __init__(self, output_parsed):
            self._output_parsed = output_parsed

        def parse(self, **kwargs):
            return SimpleNamespace(
                output_parsed=self._output_parsed,
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

    class _FakeClient:
        def __init__(self, output_parsed):
            self.responses = _FakeResponses(output_parsed)

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    sha_a = gb.commit_all("add foo")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    sha_b = gb.commit_all("add bar")

    coalesced = theme.ThemeGroup(label="Misc", rationale="grouped by LLM", atom_shas=[sha_a[:8], sha_b[:8]])
    fake = _FakeClient(theme.ThemeGroups(groups=[coalesced]))
    monkeypatch.setattr(theme, "get_client", lambda repo: fake)

    assert _in(tmp_path, ["advanced", "intent", "build"]) == 0
    capsys.readouterr()

    from sgt.api import intent_view

    (theme_entry,) = intent_view(tmp_path)["themes"]
    assert theme_entry["tier"] == "thematic"  # sanity: intent_view agrees before we assert the CLI does

    assert _in(tmp_path, ["advanced", "intent", "revert", theme_entry["theme_id"], "--emit"]) == 0
    out = capsys.readouterr().out
    assert "tier: thematic" in out


def test_intent_revert_json_preview_includes_tier_field(tmp_path, capsys):
    gb = _seed(tmp_path)
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["advanced", "intent", "revert", sha, "--emit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tier"] in ("coupled", "co-changed", "thematic")


def test_intent_revert_single_atom_degrades_without_a_tree(tmp_path, capsys):
    """No tree has been built at all (`op_leaf` unavailable) -- `tier()` must still degrade to a
    valid tier rather than crashing, and the revert must still succeed."""
    gb = _seed(tmp_path, subject="add foo")  # no conventional-commit scope -> scope-less atom
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["advanced", "intent", "revert", sha, "--emit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["tier"] == "co-changed"  # single commit, no feature span available


# -- U5: staleness signal + revert refusal ------------------------------------------------------


def _build_one_theme(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    _seed(tmp_path)
    assert _in(tmp_path, ["advanced", "intent", "build"]) == 0
    from sgt.api import intent_view

    (theme_entry,) = intent_view(tmp_path)["themes"]
    return theme_entry


def _mark_theme_stale(tmp_path, theme_id: str) -> str:
    from sgt import state

    themes = state.load_json(tmp_path, "intent_themes", default={})
    entry = themes[theme_id]
    vanished_sha = "f" * 40
    entry["atom_shas"] = sorted({*entry["atom_shas"], vanished_sha})
    state.save_json(tmp_path, "intent_themes", themes)
    return vanished_sha


def test_intent_list_renders_stale_marker_for_a_theme_with_a_missing_member(tmp_path, capsys, monkeypatch):
    theme_entry = _build_one_theme(tmp_path, monkeypatch)
    capsys.readouterr()
    vanished_sha = _mark_theme_stale(tmp_path, theme_entry["theme_id"])

    assert _in(tmp_path, ["advanced", "intent", "list"]) == 0
    out = capsys.readouterr().out
    assert "stale" in out
    assert vanished_sha[:8] in out


def test_intent_show_renders_stale_marker_for_a_theme_with_a_missing_member(tmp_path, capsys, monkeypatch):
    theme_entry = _build_one_theme(tmp_path, monkeypatch)
    capsys.readouterr()
    vanished_sha = _mark_theme_stale(tmp_path, theme_entry["theme_id"])

    assert _in(tmp_path, ["advanced", "intent", "show", theme_entry["theme_id"]]) == 0
    out = capsys.readouterr().out
    assert "stale" in out
    assert vanished_sha[:8] in out


def test_intent_revert_refuses_a_theme_with_one_missing_member(tmp_path, capsys, monkeypatch):
    theme_entry = _build_one_theme(tmp_path, monkeypatch)
    capsys.readouterr()
    vanished_sha = _mark_theme_stale(tmp_path, theme_entry["theme_id"])

    assert _in(tmp_path, ["advanced", "intent", "revert", theme_entry["theme_id"], "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "sgt intent build" in payload["error"]
    assert vanished_sha[:8] in payload["error"]


def test_intent_revert_refuses_a_theme_with_every_member_missing(tmp_path, capsys, monkeypatch):
    """A theme whose *every* member sha vanished must refuse with the reconcile message, not
    report a misleading "no change" the way `plan_revert_op_set` would on an empty op-set."""
    from sgt import state

    theme_entry = _build_one_theme(tmp_path, monkeypatch)
    capsys.readouterr()
    themes = state.load_json(tmp_path, "intent_themes", default={})
    entry = themes[theme_entry["theme_id"]]
    entry["atom_shas"] = ["f" * 40, "e" * 40]
    state.save_json(tmp_path, "intent_themes", themes)

    assert _in(tmp_path, ["advanced", "intent", "revert", theme_entry["theme_id"], "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "sgt intent build" in payload["error"]
