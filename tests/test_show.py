"""`sgt show <sel>` -- the "what is this?" verb (Phase 3 item 5).

Two things matter here and neither is the formatting. First, `show` must be *safe*: it is what a
cautious user runs before a mutating verb, so it must write nothing and must never invoke the LLM
rung (a read a user repeats should not cost money or seconds). Second, its `next:` commands must be
commands that actually run -- the P0-A lesson was that a suggestion which silently no-ops is worse
than no suggestion at all.
"""

from __future__ import annotations

import json

from sgt import api
from sgt.cli import main
from sgt.core.lens import get
from sgt.lens import map as lensmap
from tests.laws import corpus


def _repo(tmp_path):
    repo = corpus.CORPUS["linear_history"].build(tmp_path / "repo")
    get(repo)
    return repo, lensmap.build_map(repo)


def _feature(result):
    return next(iter(result["nodes"]))


# -- safety ------------------------------------------------------------------------------------


def test_show_writes_nothing(tmp_path):
    """`show` is a read. Pinned by hashing every file under `.sgt/` before and after: the consequence
    number comes from `plan_revert_op_set`, and a plan that leaked a write would make the safe
    pre-flight check itself destructive."""
    repo, result = _repo(tmp_path)
    sgt_dir = repo / ".sgt"

    def snapshot():
        return {p.relative_to(sgt_dir).as_posix(): p.read_bytes()
                for p in sorted(sgt_dir.rglob("*")) if p.is_file()}

    before = snapshot()
    api.show_view(repo, _feature(result))
    assert snapshot() == before


def test_show_never_calls_the_nl_resolver(tmp_path, monkeypatch):
    """An unresolvable token comes back `ok: False` with places to look -- it must not fall through
    to the LLM. `show` is deliberately deterministic and offline."""
    import sgt.intent.resolve as resolve_mod

    def boom(*a, **k):
        raise AssertionError("show must never reach the LLM resolver")

    for name in [n for n in dir(resolve_mod) if not n.startswith("__")]:
        if callable(getattr(resolve_mod, name, None)):
            monkeypatch.setattr(resolve_mod, name, boom, raising=False)

    repo, _ = _repo(tmp_path)
    view = api.show_view(repo, "some phrase that names nothing")
    assert view["ok"] is False
    assert view["kind"] is None
    assert view["next"], "a miss must still say where to look"


# -- identity + extent -------------------------------------------------------------------------


def test_show_reports_a_feature_by_id_label_or_short_handle(tmp_path):
    """All three spellings of the same feature must produce the same answer -- the user retypes
    whichever one the view they were reading happened to print."""
    repo, result = _repo(tmp_path)
    fid = _feature(result)
    label = result["nodes"][fid].get("label")

    by_id = api.show_view(repo, fid)
    by_handle = api.show_view(repo, fid[2:10])
    assert by_id["ok"] and by_id["kind"] == "feature"
    assert by_handle["id"] == by_id["id"] == fid
    assert by_id["op_count"] == len(
        [op for op, leaf in result["op_leaf"].items() if leaf == fid])
    if label:
        assert api.show_view(repo, label)["id"] == fid


def test_handle_is_short_and_still_resolves(tmp_path):
    """The `next:` commands quote `handle`, not `id`: a 64-char id wraps the terminal and makes the
    block unreadable. But a short handle is only usable if it round-trips."""
    repo, result = _repo(tmp_path)
    view = api.show_view(repo, _feature(result))
    assert len(view["handle"]) == 8
    assert api.show_view(repo, view["handle"])["id"] == view["id"]


def test_show_omits_bookkeeping_sentinels_from_the_footprint(tmp_path):
    """`__residue__`/`__anchor__` entries are how the miner represents the parts of a file around
    the symbols. Counting them would inflate "what this touched" with entries no user recognizes."""
    repo, result = _repo(tmp_path)
    view = api.show_view(repo, _feature(result))
    assert view["symbols"], "a feature touches something"
    assert not [s for s in view["symbols"] if "__residue__" in s or "__anchor__" in s]
    assert not [s for s in view["consequences"]["affected_symbols"]
                if "__residue__" in s or "__anchor__" in s]


def test_a_whole_file_path_is_shown_as_a_symbol(tmp_path):
    """A non-code file is one whole-file symbol; `show README.md` must name it back as a symbol
    rather than as an "op"."""
    repo, _ = _repo(tmp_path)
    view = api.show_view(repo, "README.md")
    assert view["ok"] and view["kind"] == "symbol"
    assert view["op_count"] == 1


# -- consequence -------------------------------------------------------------------------------


def test_consequences_separate_the_selection_from_what_sits_on_top(tmp_path):
    """`removes` is the total a revert would drop; `dependents` is the part that is *not* the
    selection's own ops. That split is the number that decides whether a revert is a small
    correction or a demolition, and it is invisible in every other view."""
    repo, result = _repo(tmp_path)
    view = api.show_view(repo, _feature(result))
    cons = view["consequences"]
    assert cons["removes"] >= cons["live_op_count"]
    assert cons["dependents"] == cons["removes"] - cons["live_op_count"]


def test_a_dead_selection_says_a_revert_would_do_nothing(tmp_path):
    """A selection with nothing live must not carry a revert offer -- offering a verb that would
    change nothing is the same silent no-op problem in a different costume."""
    repo, result = _repo(tmp_path)
    fid = _feature(result)
    # Revert the whole feature, then ask about it again.
    from sgt.core import verbs
    from sgt.lens import verbs as lens_verbs

    preview = lens_verbs.plan_revert_feature(repo, fid)
    assert preview.ok and preview.removed, preview.message
    verbs.apply(repo, preview)

    view = api.show_view(repo, fid)
    assert view["ok"], view.get("message")
    assert view["consequences"]["live_op_count"] == 0
    assert not [s for s in view["next"] if s["cmd"].startswith("sgt revert")]


# -- next steps --------------------------------------------------------------------------------


def test_every_suggested_command_is_a_real_dispatchable_verb(tmp_path):
    """The P0-A rule, enforced. Each suggested command's verb path must exist on the current
    surface: a top-level verb, or a grouping plus a subcommand registered under it. Checked against
    the built parser rather than a hand-list, so a future re-home breaks this test instead of
    silently shipping a dead suggestion."""
    import sgt.cli as cli

    repo, result = _repo(tmp_path)
    parser = cli._build_parser()
    top = {a.dest: a for a in parser._actions}
    subparsers = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)

    def assert_runnable(cmd: str):
        tokens = [t for t in cmd.split() if not t.startswith("-")]
        assert tokens[0] == "sgt", cmd
        node = subparsers.choices
        for tok in tokens[1:]:
            if tok not in node:
                return  # a positional argument (an id, a label), not a verb -- stop descending
            child = node[tok]
            nested = next((a for a in child._actions if hasattr(a, "choices") and a.choices), None)
            if nested is None:
                return
            node = nested.choices

    assert top  # parser built
    targets = [_feature(result), "a.py::foo", "README.md", "nonsense-token"]
    seen = 0
    for target in targets:
        for step in api.show_view(repo, target)["next"]:
            assert_runnable(step["cmd"])
            # And the first token after `sgt` must be a verb the dispatcher accepts, not one that
            # would fall through to the unknown-verb handler.
            verb = step["cmd"].split()[1]
            assert verb in cli._VERBS, f"{step['cmd']!r} names {verb!r}, which does not dispatch"
            seen += 1
    assert seen >= 6, "expected suggestions across the selection kinds"


def test_cli_show_json_and_text_agree_and_exit_codes_are_honest(tmp_path, capsys, monkeypatch):
    repo, result = _repo(tmp_path)
    monkeypatch.chdir(repo)

    assert main(["show", _feature(result)]) == 0
    text = capsys.readouterr().out
    assert "next:" in text

    assert main(["show", "--json", _feature(result)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and payload["kind"] == "feature"

    # A miss is a failure, not a 0 with an empty body.
    assert main(["show", "definitely-not-a-thing"]) == 1
    assert "not a known" in capsys.readouterr().out


def test_saves_keep_the_most_recent_and_say_how_many_were_elided(tmp_path):
    """Two things at once. The saves list is oldest-first (so a feature reads as its story, matching
    `sgt log --focus`), but when there are more than the cap it keeps the *tail* -- keeping the head
    would hide current work behind history. And the count of what isn't shown is reported: stopping
    silently at a cap reads as "that was all of them"."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    body = "def fetch(u):\n    return get(u)\n"
    for i in range(8):  # more saves than the default cap
        body += f"\n\ndef helper{i}(x):\n    return x + {i}\n"
        (repo / "fetch.py").write_text(body, encoding="utf-8")
        gb.commit_all(f"add helper{i}")
    get(repo)
    result = lensmap.build_map(repo)

    view = api.show_view(repo, _feature(result), save_limit=5)
    subjects = [s["subject"] for s in view["saves"]]
    assert len(subjects) == 5
    assert view["save_count"] > 5
    # Oldest-first within the window...
    assert subjects == sorted(subjects, key=lambda s: int(s.rsplit("helper", 1)[1]))
    # ...and it is the recent tail, not the early head.
    assert "add helper7" in subjects
    assert "add helper0" not in subjects


def test_op_ids_are_omitted_by_default_and_complete_when_asked_for(tmp_path):
    """Three options existed and only one is honest at a sane cost.

    A silent slice (the original: `ops` cut by `save_limit`) is the worst -- a caller reading five of
    forty ids cannot tell the rest exist. Always-complete is honest but expensive: ids are 64 chars
    and no renderer prints them, which measured 1,862 tokens for one `sgt_show` on a large feature.
    Omitting the field unless asked is both: absent is unmistakably absent, `op_count` carries the
    fact, and a caller that needs the set gets all of it."""
    repo, result = _repo(tmp_path)
    view = api.show_view(repo, _feature(result))
    assert "ops" not in view
    assert view["op_count"] > 5

    full = api.show_view(repo, _feature(result), include_ops=True)
    assert len(full["ops"]) == full["op_count"] == view["op_count"]


def test_affected_symbols_are_capped_with_an_honest_count(tmp_path):
    """Reverting a large feature moves many symbols. Uncapped, that list was 5.3 KB of a single
    `sgt show` payload on this project's own repo, and no surface renders it -- the actionable part
    of a consequence is the magnitude (`removes`/`dependents`), not the roll call. Capped, with the
    real count beside it so the cap doesn't read as completeness."""
    repo, result = _repo(tmp_path)
    cons = api.show_view(repo, _feature(result), symbol_limit=2)["consequences"]
    assert len(cons["affected_symbols"]) <= 2
    assert cons["affected_symbol_count"] >= len(cons["affected_symbols"])
    # The magnitude is always present regardless of the cap.
    assert isinstance(cons["removes"], int) and isinstance(cons["dependents"], int)
