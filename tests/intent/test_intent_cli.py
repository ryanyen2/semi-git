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
    assert _in(tmp_path, ["intent", "build"]) == 0
    capsys.readouterr()

    assert _in(tmp_path, ["intent", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    from sgt.api import intent_view

    assert payload == intent_view(tmp_path)


def test_intent_show_commit_resolves_atom_and_lists_ops(tmp_path, capsys):
    gb = _seed(tmp_path)
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["intent", "show", sha, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["kind"] == "atom"
    assert payload["commit_sha"] == sha
    assert len(payload["op_ids"]) >= 1


def test_intent_show_unknown_target_fails(tmp_path, capsys):
    _seed(tmp_path)

    assert _in(tmp_path, ["intent", "show", "no-such-target", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_intent_build_writes_themes_json_second_build_is_a_no_op_cache_hit(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    _seed(tmp_path)

    assert _in(tmp_path, ["intent", "build"]) == 0
    themes_path = tmp_path / ".sgt" / "intent" / "themes.json"
    assert themes_path.is_file()
    before_mtime = themes_path.stat().st_mtime_ns

    assert _in(tmp_path, ["intent", "build"]) == 0
    after_mtime = themes_path.stat().st_mtime_ns
    assert before_mtime == after_mtime  # no-op cache hit -- save_json_if_changed skips the write


def test_intent_usage_on_missing_or_unknown_sub(tmp_path, capsys):
    _seed(tmp_path)
    assert _in(tmp_path, ["intent"]) == 2
    assert "usage: sgt intent" in capsys.readouterr().out


# -- alignment review queue: sgt intent review -------------------------------------------------


def _queue_one(tmp_path, reason="make search better"):
    from sgt.intent import review
    return review.record_review(
        tmp_path, subject=[{"op": "o1", "sha": "s", "fp": "f"}], reason=reason, evidence=["t1"],
        posterior=0.62, signals=[{"name": "topic", "value": 1.0}], aligner_version="1")


def test_intent_review_list_then_confirm_promotes_to_ledger(tmp_path, capsys):
    from sgt.intent import rationale
    _seed(tmp_path)
    rid = _queue_one(tmp_path)

    assert _in(tmp_path, ["intent", "review", "--json"]) == 0
    pending = json.loads(capsys.readouterr().out)["pending"]
    assert len(pending) == 1 and pending[0]["reason"] == "make search better"

    assert _in(tmp_path, ["intent", "review", "confirm", rid[:12], "--json"]) == 0
    capsys.readouterr()
    recs = rationale.for_op(tmp_path, "o1")
    assert len(recs) == 1 and recs[0]["confirmed"] is True and recs[0]["recorded_by"] == "user"

    assert _in(tmp_path, ["intent", "review", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pending"] == []  # decided -> off the queue


def test_intent_review_reject_drops_without_promoting(tmp_path, capsys):
    from sgt.intent import rationale
    _seed(tmp_path)
    rid = _queue_one(tmp_path, reason="fix the thing")

    assert _in(tmp_path, ["intent", "review", "reject", rid[:12], "--json"]) == 0
    capsys.readouterr()
    assert rationale.load_rationale(tmp_path) == {}  # nothing promoted

    assert _in(tmp_path, ["intent", "review", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pending"] == []


def test_intent_review_confirm_unknown_id_fails(tmp_path, capsys):
    _seed(tmp_path)
    assert _in(tmp_path, ["intent", "review", "confirm", "rv-nope", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


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

    assert _in(tmp_path, ["intent", "revert", sha, "--emit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert sorted(payload["removed"]) == sorted(expected.removed)
    assert sorted(payload["added"]) == sorted(expected.added)
    assert payload["forked"] == expected.forked


def test_intent_revert_emit_shows_diff_without_flipping_the_ideal(tmp_path, capsys):
    gb = _seed(tmp_path)
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["intent", "revert", sha, "--emit", "--json"]) == 0
    capsys.readouterr()

    from sgt.core.lens import current_ideal

    before = current_ideal(tmp_path).op_ids
    assert _in(tmp_path, ["intent", "revert", sha, "--emit", "--json"]) == 0
    capsys.readouterr()
    after = current_ideal(tmp_path).op_ids
    assert before == after  # --emit never applies


def test_intent_revert_unknown_target_fails(tmp_path, capsys):
    _seed(tmp_path)
    assert _in(tmp_path, ["intent", "revert", "no-such-target", "--json"]) == 1
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
    assert _in(tmp_path, ["intent", "build"]) == 0
    capsys.readouterr()

    from sgt.api import intent_view

    (theme_entry,) = intent_view(tmp_path)["themes"]

    # select only the earlier commit (base) while excluding the later, dependent one (caller) --
    # reverting base would cascade into removing caller too, so this must be refused by name
    # rather than silently sweeping caller away as an unselected side effect.
    assert _in(
        tmp_path, ["intent", "revert", theme_entry["theme_id"], "--subset", sha_a[:12], "--json"],
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
    assert _in(tmp_path, ["intent", "build"]) == 0
    capsys.readouterr()

    from sgt.api import intent_view

    (theme_entry,) = intent_view(tmp_path)["themes"]
    a_op_ids = frozenset(op.id for op in Store(tmp_path).all_ops() if sha_a in op.provenance)

    # selecting only the later (dependent) commit is valid on its own -- nothing else requires it,
    # so it must not cascade into removing anything from the earlier commit it depends on.
    assert _in(
        tmp_path,
        ["intent", "revert", theme_entry["theme_id"], "--subset", sha_b[:12], "--emit", "--json"],
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

    assert _in(tmp_path, ["intent", "build"]) == 0
    capsys.readouterr()

    from sgt.api import intent_view

    (theme_entry,) = intent_view(tmp_path)["themes"]
    assert theme_entry["tier"] == "thematic"  # sanity: intent_view agrees before we assert the CLI does

    assert _in(tmp_path, ["intent", "revert", theme_entry["theme_id"], "--emit"]) == 0
    out = capsys.readouterr().out
    assert "tier: thematic" in out


def test_intent_revert_json_preview_includes_tier_field(tmp_path, capsys):
    gb = _seed(tmp_path)
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["intent", "revert", sha, "--emit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tier"] in ("coupled", "co-changed", "thematic")


def test_intent_revert_single_atom_degrades_without_a_tree(tmp_path, capsys):
    """No tree has been built at all (`op_leaf` unavailable) -- `tier()` must still degrade to a
    valid tier rather than crashing, and the revert must still succeed."""
    gb = _seed(tmp_path, subject="add foo")  # no conventional-commit scope -> scope-less atom
    sha = gb.rev_parse("HEAD")

    assert _in(tmp_path, ["intent", "revert", sha, "--emit", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["tier"] == "co-changed"  # single commit, no feature span available


# -- U5: staleness signal + revert refusal ------------------------------------------------------


def _build_one_theme(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    _seed(tmp_path)
    assert _in(tmp_path, ["intent", "build"]) == 0
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

    assert _in(tmp_path, ["intent", "list"]) == 0
    out = capsys.readouterr().out
    assert "stale" in out
    assert vanished_sha[:8] in out


def test_intent_show_renders_stale_marker_for_a_theme_with_a_missing_member(tmp_path, capsys, monkeypatch):
    theme_entry = _build_one_theme(tmp_path, monkeypatch)
    capsys.readouterr()
    vanished_sha = _mark_theme_stale(tmp_path, theme_entry["theme_id"])

    assert _in(tmp_path, ["intent", "show", theme_entry["theme_id"]]) == 0
    out = capsys.readouterr().out
    assert "stale" in out
    assert vanished_sha[:8] in out


def test_intent_revert_refuses_a_theme_with_one_missing_member(tmp_path, capsys, monkeypatch):
    theme_entry = _build_one_theme(tmp_path, monkeypatch)
    capsys.readouterr()
    vanished_sha = _mark_theme_stale(tmp_path, theme_entry["theme_id"])

    assert _in(tmp_path, ["intent", "revert", theme_entry["theme_id"], "--json"]) == 1
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

    assert _in(tmp_path, ["intent", "revert", theme_entry["theme_id"], "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "sgt intent build" in payload["error"]


def test_intent_relabel_overrides_checkpoint_and_marks_user_source(tmp_path, monkeypatch):
    """`sgt intent relabel <feature@n> "<intent>"` writes a committed pin that overrides the
    checkpoint's label (source=user) and, being a separate layer from segments.json, survives a
    later `sgt intent build`."""
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    from sgt.intent import theme_segment
    monkeypatch.setattr(theme_segment, "get_client", _no_client)

    _seed(tmp_path, "feat(x): add foo")
    # U14: `sgt map` folded onto `sgt log --tree`; `--refresh` builds the tree so a checkpoint exists.
    assert _in(tmp_path, ["log", "--tree", "--refresh"]) == 0

    from sgt.api import intent_view
    seg = intent_view(tmp_path)["segments"][0]
    ckpt = f"{seg['feature_id']}@{seg['seg_index']}"

    assert _in(tmp_path, ["intent", "relabel", ckpt, "My", "Custom", "Intent"]) == 0
    after = next(s for s in intent_view(tmp_path)["segments"] if s["checkpoint"] == seg["checkpoint"])
    assert after["intent"] == "My Custom Intent"
    assert after["source"] == "user"

    # survives a build (which only rewrites the boundary/label layer, not the pins layer)
    assert _in(tmp_path, ["intent", "build"]) == 0
    after_build = next(s for s in intent_view(tmp_path)["segments"] if s["checkpoint"] == seg["checkpoint"])
    assert after_build["intent"] == "My Custom Intent"
    assert after_build["source"] == "user"


def test_intent_relabel_rejects_a_non_checkpoint_target(tmp_path):
    _seed(tmp_path)
    assert _in(tmp_path, ["intent", "relabel", "not-a-checkpoint", "label"]) == 1


def test_intent_open_lists_unfulfilled_and_done_retires_it(tmp_path, capsys):
    """The intent-ledger unfulfilled surface (M1): `sgt intent open` lists a stated-but-unlanded
    intent; `sgt intent done <id-prefix>` retires it and it leaves the surface."""
    from sgt.intent import rationale

    _seed(tmp_path)
    rid = rationale.record_rationale(tmp_path, subject=[], reason="add rate limiting",
                                     actor="human", evidence=[], open=True, ts=1.0)

    assert _in(tmp_path, ["intent", "open", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["open"]) == 1 and payload["open"][0]["reason"] == "add rate limiting"

    assert _in(tmp_path, ["intent", "done", rid[:12]]) == 0
    capsys.readouterr()

    assert _in(tmp_path, ["intent", "open", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["open"] == []


def test_intent_done_rejects_an_unknown_id(tmp_path):
    _seed(tmp_path)
    assert _in(tmp_path, ["intent", "done", "r-nope"]) == 1


def test_intent_record_captures_a_chat_turn_from_hook_stdin(tmp_path, monkeypatch):
    """`sgt intent record` is the `UserPromptSubmit` hook sink: the payload's prompt lands
    verbatim as a chat-keyed turn, and the command stays silent (exit 0)."""
    import io

    from sgt.intent.turns import turns_for

    _seed(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "cs-9", "prompt": "make auth stateless"}'))
    assert _in(tmp_path, ["intent", "record"]) == 0
    hits = turns_for(tmp_path, "cs-9", key_kind="chat")
    assert len(hits) == 1 and hits[0]["text"] == "make auth stateless"


def test_intent_record_is_a_no_op_outside_an_sgt_repo(tmp_path, monkeypatch):
    """The hook fires wherever Claude Code runs; without a prior `sgt init` (no `.sgt/`) the sink
    must exit 0 AND write nothing -- materializing `.sgt/` into an arbitrary cwd is pollution
    (testbed 2026-07-31: a stray fire minted `/tmp/.sgt`)."""
    import io
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"session_id": "cs-9", "prompt": "hello"}'))
    assert _in(tmp_path, ["intent", "record"]) == 0
    assert not (tmp_path / ".sgt").exists()


def test_intent_activity_appends_a_tool_event_from_hook_stdin(tmp_path, monkeypatch):
    """`sgt intent activity` is the `PostToolUse` hook sink: the payload's tool + edited file land
    as one row in the local activity feed, and the command stays silent (exit 0)."""
    import io

    from sgt.intent.activity import load_activity

    _seed(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        '{"session_id": "cs-9", "tool_name": "Edit", "tool_input": {"file_path": "a.py"}}'))
    assert _in(tmp_path, ["intent", "activity"]) == 0
    feed = load_activity(tmp_path)
    assert len(feed) == 1
    assert feed[0]["tool"] == "Edit" and feed[0]["file"] == "a.py" and feed[0]["session_id"] == "cs-9"


def test_intent_activity_is_a_no_op_outside_an_sgt_repo(tmp_path, monkeypatch):
    """Like `record`, the PostToolUse hook fires wherever Claude Code runs; without a prior
    `sgt init` (no `.sgt/`) the sink exits 0 AND writes nothing."""
    import io
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setattr("sys.stdin", io.StringIO(
        '{"tool_name": "Write", "tool_input": {"file_path": "a.py"}}'))
    assert _in(tmp_path, ["intent", "activity"]) == 0
    assert not (tmp_path / ".sgt").exists()


def test_prompt_hook_installs_with_an_absolute_sgt_path(tmp_path, monkeypatch):
    """The hook command must carry the running `sgt`'s absolute path: hooks execute in whatever
    shell Claude Code has, and a bare `sgt` silently no-ops when the venv is not on that PATH
    (testbed 2026-07-31). Under pytest argv[0] is not `sgt`, so the resolver falls back to
    `shutil.which` -- pinned here to a fake binary for hermeticity."""
    import shutil

    from sgt.cli.init import _install_prompt_hook

    fake = tmp_path / "bin" / "sgt"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(fake))

    assert _install_prompt_hook(str(tmp_path)) is True
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    (entry,) = settings["hooks"]["UserPromptSubmit"]
    (hook,) = entry["hooks"]
    assert hook["command"] == f'"{fake.resolve()}" intent record'

    assert _install_prompt_hook(str(tmp_path)) is True  # idempotent: no duplicate entry
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1


def test_activity_hook_installs_a_posttooluse_matcher_and_preserves_the_prompt_hook(tmp_path, monkeypatch):
    """The PostToolUse edit hook installs alongside the prompt hook (both live in the same
    settings): it carries the Edit|Write|MultiEdit matcher and the absolute-path `intent activity`
    command, is idempotent, and never clobbers an existing UserPromptSubmit entry."""
    import shutil

    from sgt.cli.init import _install_activity_hook, _install_prompt_hook

    fake = tmp_path / "bin" / "sgt"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(fake))

    assert _install_prompt_hook(str(tmp_path)) is True
    assert _install_activity_hook(str(tmp_path)) is True
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    (entry,) = settings["hooks"]["PostToolUse"]
    assert entry["matcher"] == "Edit|Write|MultiEdit"
    (hook,) = entry["hooks"]
    assert hook["command"] == f'"{fake.resolve()}" intent activity'
    assert len(settings["hooks"]["UserPromptSubmit"]) == 1  # prompt hook preserved

    assert _install_activity_hook(str(tmp_path)) is True  # idempotent: no duplicate entry
    settings = json.loads((tmp_path / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert len(settings["hooks"]["PostToolUse"]) == 1


def test_intent_record_rejects_harness_injected_wrappers(tmp_path, monkeypatch):
    """`UserPromptSubmit` fires for more than typed prompts: the harness routes task
    notifications, system reminders, and slash-command markup through the same hook, wrapped in a
    leading tag. Recording those as `actor="human"` poisons every surface that trusts the turn
    store (dogfood 2026-09-01: 137 of 294 captured "prompts" were `<task-notification>` blobs, and
    `sgt now` would happily report one as what the developer is working on). A leading known
    wrapper tag means "not the user's voice" -- skip it, silently, exit 0."""
    import io

    from sgt.intent.turns import turns_for

    _seed(tmp_path)
    for tag in ("task-notification", "system-reminder", "command-name", "local-command-stdout"):
        payload = json.dumps({"session_id": "cs-9", "prompt": f"<{tag}>whatever</{tag}>"})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        assert _in(tmp_path, ["intent", "record"]) == 0
    assert turns_for(tmp_path, "cs-9", key_kind="chat") == []


def test_intent_record_keeps_a_real_prompt_that_merely_mentions_a_tag(tmp_path, monkeypatch):
    """Only a LEADING wrapper tag marks an injection. A user asking about the machinery --
    pasting a `<task-notification>` mid-sentence, or starting with unrelated markup -- is still
    a human utterance and must be kept verbatim."""
    import io

    from sgt.intent.turns import turns_for

    _seed(tmp_path)
    text = "why does <task-notification> show up in my turn store?"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "cs-9", "prompt": text})))
    assert _in(tmp_path, ["intent", "record"]) == 0
    hits = turns_for(tmp_path, "cs-9", key_kind="chat")
    assert len(hits) == 1 and hits[0]["text"] == text


def test_intent_activity_skips_an_edit_outside_this_repo(tmp_path, monkeypatch):
    """The PostToolUse hook fires with cwd = the session's repo, but the edited file can live
    anywhere (dogfood 2026-09-01: a sibling checkout's edits landed in this repo's feed). An
    event whose file is outside the repo root is another repo's motion, not this one's."""
    import io

    from sgt.intent.activity import load_activity

    _seed(tmp_path)
    payload = json.dumps({"session_id": "cs-9", "tool_name": "Edit",
                          "tool_input": {"file_path": "/somewhere/else/b.py"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    assert _in(tmp_path, ["intent", "activity"]) == 0
    assert load_activity(tmp_path) == []


def test_a_grounded_ask_names_the_checkpoint_and_resolves_back(tmp_path):
    """The weave end to end (P3): a hook-captured prompt, its session's activity, and the save meet
    at the save beat -- and the checkpoint list names the chapter with the user's ask (source
    `words`), surfaces the ask in `words`, and the SAME label resolves back through the bare-name
    resolver, proving the list and the resolvers share one cut (`segments_for`)."""
    from sgt.api import segments_view
    from sgt.intent.activity import record_activity
    from sgt.intent.segment import resolve_checkpoint_label
    from sgt.intent.turns import record_turn
    from sgt.lens import tree

    _seed(tmp_path)
    record_turn(tmp_path, key="cs-9", key_kind="chat", actor="human", channel="hook",
                text="teach foo to count higher")
    record_activity(tmp_path, tool="Edit", file="a.py", session_id="cs-9")
    (tmp_path / "a.py").write_text(
        "def foo():\n    return 2\n\n\ndef ceiling():\n    return 99\n", encoding="utf-8")
    assert _in(tmp_path, ["save"]) == 0  # 2 new ops of the feature's 3: past WORDS_DOMINANCE

    # A hand-authored one-leaf feature tree (same idiom as tests/intent/test_segment.py): the
    # checkpoint projection reads features from the persisted tree, not from the cut itself.
    nodes = {"F-A": {"parent": None, "children": [], "members": ["a.py::foo", "a.py::ceiling"],
                     "size": 2, "dir": "", "label": "F-A"}}
    ops = Store(tmp_path).all_ops()
    tree.save(tmp_path, {"nodes": nodes, "roots": ["F-A"],
                         "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
                         "max_depth": 0, "cannot_link_moves": [], "identity_events": []})

    seg = next(s for s in segments_view(tmp_path) if s["source"] == "words")
    assert seg["intent"] == "teach foo to count higher"
    assert "teach foo to count higher" in seg["words"]

    resolved = resolve_checkpoint_label(tmp_path, "teach foo to count higher")
    assert resolved is not None
    op_ids, display = resolved
    assert set(seg["op_ids"]) <= set(op_ids) or set(op_ids) <= set(seg["op_ids"])
    assert "teach foo to count higher" in display

    # ...and the context pack (P4): the ask verbatim, the recorded why, the symbols, and the way
    # back into the conversation -- resolved through the same cut the list and the revert use.
    from sgt.api import checkpoint_context

    pack = checkpoint_context(tmp_path, seg["checkpoint"])
    assert pack["ok"] is True and pack["checkpoint"] == seg["checkpoint"]
    assert [a["text"] for a in pack["asked"]] == ["teach foo to count higher"]
    assert pack["asked"][0]["channel"] == "hook"
    assert "teach foo to count higher" in [r["reason"] for r in pack["why"]]
    assert "a.py::ceiling" in pack["touches"]
    assert pack["dependent_op_ids"] == []  # nothing built on this chapter yet
    assert pack["resume"] == [{"claude_session_id": "cs-9", "command": "claude --resume cs-9"}]

    # The CLI show surface carries the pack too, under the same checkpoint target.
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert _in(tmp_path, ["intent", "show", seg["checkpoint"], "--json"]) == 0
    shown = json.loads(buf.getvalue())
    assert shown["kind"] == "checkpoint"
    assert [a["text"] for a in shown["context"]["asked"]] == ["teach foo to count higher"]


def test_checkpoint_context_refuses_a_non_checkpoint(tmp_path):
    from sgt.api import checkpoint_context

    _seed(tmp_path)
    pack = checkpoint_context(tmp_path, "no-such-thing@7")
    assert pack["ok"] is False and "names no checkpoint" in pack["message"]
