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


def _a_logged_save_id(repo) -> str:
    """One id string exactly as `sgt log` prints it in its id column -- taken from the projection
    the renderer reads, so what is under test is the token the user was actually shown."""
    from sgt.api import grid_view, map_view
    from sgt.tui.graph import episode_rail_layout, episodes

    rows = episode_rail_layout(episodes(map_view(repo), grid_view(repo)))["rows"]
    return next(row["sha"][:7] for row in rows if row.get("sha"))


# -- safety ------------------------------------------------------------------------------------


# Pure derived caches: a read is allowed to refresh them, because every read mines on contact and
# the whole point of these files is to make the next read cheap. `ops_dirstat` in particular is
# written on a wall-clock condition (`opindex._ops_dir_stat` only persists a scan it can prove is
# not racy, more than two seconds after the last write to `.sgt/ops/`), so hashing it made this
# test pass or fail on how long the fixture happened to take.
_DERIVED_CACHES = frozenset({
    "local/ops_dirstat.json", "local/derive_cache.json", "local/extract_cache.json",
    "local/label_cache.json", "local/refs_cache.json", "local/intent_cache.json",
    "local/sync_cache.json", "local/op_index.json", "local/structural_edges.json",
    "local/fused_snapshot.json",
})


def test_show_writes_nothing(tmp_path):
    """`show` is a read. Pinned by hashing every file under `.sgt/` before and after: the consequence
    number comes from `plan_revert_op_set`, and a plan that leaked a write would make the safe
    pre-flight check itself destructive. Derived caches are excluded -- what must not move is the
    op store, the ideal, and the journal."""
    repo, result = _repo(tmp_path)
    sgt_dir = repo / ".sgt"

    def snapshot():
        return {rel: p.read_bytes()
                for p in sorted(sgt_dir.rglob("*")) if p.is_file()
                for rel in [p.relative_to(sgt_dir).as_posix()]
                if rel not in _DERIVED_CACHES}

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
    """The P0-A rule, enforced: a suggestion that fails is worse than no suggestion.

    Checked with `scripts.check_docs_commands.unrunnable`, the same function that validates the
    commands in the skills and docs, rather than a parser walk of its own. This test previously had
    one, and it shared the blind spot the docs checker had: an unknown *subcommand* was treated as a
    positional argument, so when `why` was promoted out of the `feature` grouping this test kept
    passing while `show_view` went on suggesting `sgt feature why`."""
    from scripts.check_docs_commands import unrunnable

    repo, result = _repo(tmp_path)
    targets = [_feature(result), "a.py::foo", "README.md", "nonsense-token"]
    seen = 0
    for target in targets:
        for step in api.show_view(repo, target)["next"]:
            reason = unrunnable(step["cmd"])
            assert reason is None, f"suggested {step['cmd']!r}: {reason}"
            seen += 1
    assert seen >= 6, "expected suggestions across the selection kinds"


# Named explicitly rather than derived: sgt has no runtime "is this verb a write" bit, and inferring
# one from the parser would be inventing a classification this test then trusts. Short list, one line
# to extend when a verb is added, and wrong only in the safe direction (a missing entry weakens the
# test; it can never fail a read).
_MUTATING_VERBS = frozenset({"revert", "save", "commit", "switch", "fulfill", "split", "merge",
                             "edit", "plan", "reconcile", "identity", "checkpoint", "drift"})


def test_a_miss_never_offers_a_verb_that_would_change_the_repo(tmp_path):
    """The user typed a token `show` does not recognize -- so `show` does not know what the token
    means. Suggesting `sgt revert <that same token>` answered "what is this?" with a demolition
    charge: `revert` resolves a phrase *by meaning*, so on the one input where sgt has admitted it
    cannot identify the target, the offered next step was to let a different resolver guess and then
    act on the guess. `revert` has no `--dry-run`, so there was no safe way to take the suggestion.

    A miss is a read that failed; every way out of it must also be a read."""
    repo, _ = _repo(tmp_path)
    view = api.show_view(repo, "the waitlist promotion logic")
    assert view["ok"] is False
    offered = [s["cmd"] for s in view["next"]]
    assert offered, "a miss must still say where to look"
    for cmd in offered:
        verb = cmd.split()[1] if cmd.startswith("sgt ") else cmd
        assert verb not in _MUTATING_VERBS, f"a miss offered the mutating verb {verb!r}: {cmd!r}"
    # And the reason the phrase failed is stated, since "not a known feature" reads as "you have no
    # such feature" when the real fact is that this verb does not resolve phrases at all.
    assert "phrase" in view["message"] or "label" in view["message"], view["message"]


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


def _rewritten_symbol(tmp_path):
    """A symbol introduced in one commit and rewritten in a later one -- the shape that made `show`
    name the wrong commit."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "rewritten"
    repo.mkdir()
    gb, _ = init_store(repo)
    (repo / "cli.py").write_text("def cmd_search(q):\n    return [q]\n", encoding="utf-8")
    gb.commit_all("add course search")
    (repo / "cli.py").write_text(
        "def cmd_search(q, store):\n    return store.find(q)\n", encoding="utf-8")
    gb.commit_all("extract Repository class for persistence")
    get(repo)
    lensmap.build_map(repo)
    return repo


def test_show_symbol_provenance_covers_its_whole_history_not_just_its_current_op(tmp_path):
    """"When did this land, and what else happened to it" is the question `show <symbol>` exists to
    answer, and it was answering with only the symbol's *current defining op*. A symbol selection is
    deliberately the frontier tip -- that is the correct thing to *revert* -- but as a read it meant
    `sgt show coursecraft/cli.py::cmd_search` reported `saves: extract Repository class for
    persistence` while `git log -S"def cmd_search"` said the symbol was introduced by `add course
    search`. On the study fixture that is Request 1's exact question, answered with the wrong commit.

    The revert target must not change, so only the provenance and the edit count widen."""
    repo = _rewritten_symbol(tmp_path)
    view = api.show_view(repo, "cli.py::cmd_search")

    assert view["kind"] == "symbol"
    subjects = [s["subject"] for s in view["saves"]]
    assert "add course search" in subjects, subjects          # the introduction
    assert "extract Repository class for persistence" in subjects, subjects   # the rewrite
    assert view["op_count"] >= 2                              # not "1 edit"

    # The revert target is untouched: consequences still describe the frontier tip alone.
    assert view["consequences"]["affected_symbol_count"] >= 1


def test_the_elided_save_count_comes_with_a_way_to_see_them(tmp_path, capsys, monkeypatch):
    """"(+2 older save(s))" named a quantity of hidden information and offered no verb to reveal it.

    The `save_limit` cap existed only as an API keyword, so from the CLI those saves were unreachable:
    nothing in `next:` lists a *symbol's* saves (`log --focus` lists a feature's checkpoints,
    `advanced blame` lists a file's symbols). On the study fixture that is not cosmetic -- the two
    saves the default cap hides on `enrollment.py::enroll` are the early links of the waitlist chain,
    which is the whole of Request 2. Saying what isn't shown is half the rule; the other half is
    letting the reader look."""
    from sgt.store.gitbind import init_store

    repo = tmp_path / "many"
    gb, _ = init_store(repo)
    for i in range(8):
        (repo / "fetch.py").write_text(
            f"def fetch(u):\n    return get(u, retries={i})\n", encoding="utf-8")
        gb.commit_all(f"retry pass {i}")
    get(repo)
    lensmap.build_map(repo)
    monkeypatch.chdir(repo)

    assert main(["show", "fetch.py::fetch"]) == 0
    capped = capsys.readouterr().out
    assert "older save(s)" in capped, capped
    assert "--saves" in capped, "the elision must name the way to widen it"

    assert main(["show", "--saves", "50", "fetch.py::fetch"]) == 0
    widened = capsys.readouterr().out
    assert "older save(s)" not in widened, widened
    assert widened.count("retry pass") > capped.count("retry pass")


def test_a_save_id_from_the_log_is_shown_as_a_save(tmp_path):
    """The id column of `sgt log` is the 7-char commit sha, so it is the token most likely to be
    typed back into `show` -- and the one that used to resolve to nothing. In the pilot that
    dead-ended the primary read verb six times out of ten and sent the participant back to plain
    git, in the sgt condition.

    Also runs the suggested `git show` for real: it is the one non-sgt command `show` offers, so
    `unrunnable` cannot vouch for it and nothing else would notice it rotting."""
    import subprocess

    repo, _result = _repo(tmp_path)
    token = _a_logged_save_id(repo)

    view = api.show_view(repo, token)
    assert view["ok"], view.get("message")
    assert view["kind"] == "save"
    # The handle is the string the log printed, so the header reads as being about the thing the
    # user typed; the id is the full sha, which is what a machine or a copy-out needs.
    assert view["handle"] == token
    assert view["id"].startswith(token) and len(view["id"]) == 40
    assert view["label"], "a save's label is its commit subject"
    assert view["op_count"] >= 1

    git_show = next(s["cmd"] for s in view["next"] if s["cmd"].startswith("git show "))
    assert subprocess.run(git_show.split(), cwd=repo, capture_output=True).returncode == 0


def test_a_save_is_not_offered_a_revert_that_revert_cannot_take(tmp_path):
    """`sgt revert`'s ladder is checkpoint/op/symbol/feature -- it does not take a commit sha, and
    answers `no feature matches handle` for one. So the revert offer, which every other live
    selection gets, must be withheld here.

    The second half is what keeps this honest: it asserts revert really does still reject the sha.
    If revert ever learns saves, this test fails and says to put the offer back, rather than
    quietly leaving `show` less useful than the tool it describes."""
    from sgt.lens.verbs import resolve_feature

    repo, _result = _repo(tmp_path)
    token = _a_logged_save_id(repo)

    view = api.show_view(repo, token)
    assert view["consequences"]["live_op_count"], "precondition: this save is live"
    assert not [s for s in view["next"] if s["cmd"].startswith("sgt revert")]
    assert resolve_feature(repo, token) is None, "revert now takes a sha -- restore the offer"


def test_a_commit_shaped_miss_says_which_of_the_three_things_went_wrong(tmp_path):
    """A commit-shaped token that reaches the refusal has already been past the save rung, so the
    only useful thing left to say is why it wasn't claimed. `not a known feature, checkpoint, op,
    or symbol` named none of the three and was the message the pilot participant hit."""
    repo, _result = _repo(tmp_path)

    view = api.show_view(repo, "deadbee")
    assert not view["ok"]
    assert "not a commit in this repo" in view["message"]
    assert "more than one" in view["message"]
    assert "recorded no edits" in view["message"]


def test_show_answers_a_row_of_work_across_features(tmp_path, monkeypatch):
    """F133. A ◆ row was the one noun `show` could not answer for.

    `sgt log` draws it, `sgt log --focus` opens it, and `sgt revert`/`sgt restore` both act on it by
    name -- but asked about the piece of work a task actually names, the verb whose whole job is
    "what is this, and what would come with it" replied that it was not a known feature, checkpoint,
    op, or symbol. It is felt exactly where it costs most: a ◆ carries no id in the log the way a
    lane does, so its label is the only handle a reader has.

    The answer has to carry the consequence too, from the same `plan_revert_op_set` every other kind
    uses -- "what comes out with it" is the question the stage before the removal asks."""
    from sgt import state
    from sgt.intent import theme
    from sgt.store.gitbind import init_store

    monkeypatch.setattr(
        theme, "get_client",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no LLM in tests")))

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("track event days")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("keep event days out of the averages")
    get(tmp_path)

    themes = theme.build_themes(tmp_path)
    tid, entry = next(iter(themes.items()))
    entry["label"] = "Event Day Handling"
    state.save_json(tmp_path, "intent_themes", {tid: entry})

    view = api.show_view(tmp_path, "Event Day Handling")
    assert view["ok"] is True, view.get("message")
    assert view["kind"] == "work across features"
    assert view["label"] == "Event Day Handling"
    assert view["id"] == tid
    assert view["op_count"] > 0
    # The sentence saying what the work WAS. Everything else on the card is shape, and shape does
    # not tell a reader coming to unfamiliar history what they are looking at.
    assert view["rationale"] == entry["rationale"]
    # Pinned against the source it is read from rather than a literal: this fixture is two commits
    # in one feature, and the number that matters (7, on the study repo) is a property of the repo.
    theme_row = next(t for t in api.intent_view(tmp_path)["themes"] if t["theme_id"] == tid)
    assert view["across_features"] == len(theme_row["feature_span"])
    assert view["consequences"]["live_op_count"] >= 0  # a real preview ran, not a stub
    # The `next:` steps are the two verbs that take this name, spelled the way they must be typed.
    cmds = [s["cmd"] for s in view["next"]]
    assert any('sgt log --focus "Event Day Handling"' == c for c in cmds), cmds
    assert any('sgt revert "Event Day Handling"' == c for c in cmds), cmds

    # Which acting verb the card offers depends on where the work stands. With it in, `revert`;
    # with it already out, `restore` -- the stage that asks for it back must not be handed the verb
    # that took it away, under a consequence line it has to reason backwards from.
    monkeypatch.chdir(tmp_path)
    assert main(["revert", "Event Day Handling", "--yes"]) == 0
    out_view = api.show_view(tmp_path, "Event Day Handling")
    assert out_view["consequences"]["removes"] == 0, "the revert did not take it out"
    assert any(c["cmd"] == 'sgt restore "Event Day Handling"' for c in out_view["next"]), \
        [c["cmd"] for c in out_view["next"]]

    # The two projects spell the same work differently, and a name copied off a stage card has to
    # land either way -- the acting verbs already match it blind to case and punctuation.
    assert api.show_view(tmp_path, "event-day handling")["id"] == tid

    # Still not a phrase resolver: a name that merely mentions the work misses, as `show` promises.
    assert api.show_view(tmp_path, "the bit that handles event days")["ok"] is False


# ── the `asked` attribute ───────────────────────────────────────────────────────────────────────
#
# `show` deliberately does not re-derive *why* -- that is `sgt why`'s job, and two views deriving
# one answer drift apart. This is a different thing: one attribute of the thing on screen, in the
# same class as `symbols` and `saves`, saying what somebody asked for in the words they typed. It
# is here rather than behind `sgt intent show <cp>` because a reader holding a commit or a
# checkpoint reaches for `show`, and a verb nobody runs is provenance nobody sees.

def _asked_repo(tmp_path):
    """A one-save repo whose save closed a capture window with a real, messy prompt in it."""
    from sgt.core.store import Store
    from sgt.intent.activity import record_activity
    from sgt.intent.manifest import record_manifest
    from sgt.intent.turns import record_turn
    from sgt.store.gitbind import init_store

    repo = tmp_path / "repo"
    gb, _ = init_store(repo)
    (repo / "pages.py").write_text("def render():\n    return 1\n", encoding="utf-8")
    sha = gb.commit_all("add the daily page")
    get(repo)
    prompt = ("so i think we shoudl proably add teh daily page for teh committee, becuase they "
              "keep emailing me spreadsheets and i do it by hand every single time")
    record_turn(repo, key="cs-1", key_kind="chat", actor="human", channel="hook", text=prompt,
                ts=10.0)
    record_activity(repo, tool="Edit", file="pages.py", session_id="cs-1", ts=20.0)
    ops = [op for op in Store(repo).all_ops()]
    record_manifest(repo, sha=sha, ops=ops, end=30.0, prev_save_ts=0.0)
    return repo, sha, prompt


def test_show_says_what_a_save_was_asked_for(tmp_path):
    repo, sha, prompt = _asked_repo(tmp_path)

    view = api.show_view(repo, sha[:7])

    top = view["asked"]["top"]
    assert view["asked"]["count"] == 1
    # The request, not the prompt: an excerpt starting at the ask, verbatim, with the size of what
    # it came out of -- so a renderer can offer the rest instead of implying this is all of it.
    assert top["gist"] == "add teh daily page for teh committee"
    assert top["trimmed"] is True
    assert top["chars"] == len(prompt)
    assert top["source"] == "you, in a Claude Code chat"
    # No `asks` list unless asked for: `show` is a read a user repeats, and every prompt in a
    # selection's history is a payload nobody has opened yet.
    assert "asks" not in view["asked"]


def test_show_asked_reads_the_conversation_back_in_full(tmp_path):
    repo, sha, prompt = _asked_repo(tmp_path)

    view = api.show_view(repo, sha[:7], include_asked=True)

    assert [a["text"] for a in view["asked"]["asks"]] == [prompt]


def test_show_says_nothing_about_asks_for_history_that_predates_capture(tmp_path):
    """Most commits in most repositories. "No ask recorded" on every card would be a line about
    sgt, in the reader's way, about something they cannot change."""
    repo, _result = _repo(tmp_path)

    view = api.show_view(repo, _a_logged_save_id(repo))

    assert view["asked"] == {"top": None, "count": 0}


def test_the_asked_line_is_an_excerpt_when_the_card_is_rendered(tmp_path, capsys, monkeypatch):
    repo, sha, prompt = _asked_repo(tmp_path)
    monkeypatch.chdir(repo)

    assert main(["show", sha[:7]]) == 0
    out = capsys.readouterr().out

    assert '“add teh daily page for teh committee”' in out
    assert "you, in a Claude Code chat" in out
    assert "--asked" in out  # the way to the rest of it
    assert prompt not in out  # ...and never the paragraph itself on the card
