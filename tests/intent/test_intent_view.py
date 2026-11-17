"""Tests for ugh.api.intent_view -- the intent overlay's canonical projection (plan U6): every
commit-keyed atom (recomputed on read) plus every persisted, LLM-named theme, each carrying its
dependency-graph-backed tier and cross-feature span. Additive to compose_view (R21)."""

from __future__ import annotations

from ugh.api import intent_view
from ugh.core.lens import get
from ugh.intent import prompts, theme
from ugh.store.gitbind import init_store


def test_shape_emits_themes_and_atoms_with_documented_keys_sorted(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("fix(auth): add foo")
    get(tmp_path)
    theme.build_themes(tmp_path)

    v = intent_view(tmp_path)
    assert set(v) == {"themes", "atoms", "segments"}
    (t,) = v["themes"]
    assert set(t) == {
        "theme_id", "label", "rationale", "source", "atom_shas", "stale_shas", "op_ids",
        "feature_span", "tier",
    }
    (a,) = v["atoms"]
    assert set(a) == {"commit_sha", "subject", "op_ids", "feature_span", "tier", "prompt",
                      "session_ids", "plan_ids", "claude_session_ids", "rationale"}
    assert v["atoms"] == sorted(v["atoms"], key=lambda x: x["commit_sha"])
    assert v["themes"] == sorted(v["themes"], key=lambda x: x["theme_id"])


def test_empty_repo_has_no_themes_but_still_reports_atoms(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    v = intent_view(tmp_path)
    assert v["themes"] == []
    assert len(v["atoms"]) == 1  # atoms derive straight from the store -- no build step needed


def test_no_ops_at_all_returns_empty_lists(tmp_path):
    init_store(tmp_path)
    v = intent_view(tmp_path)
    assert v == {"themes": [], "atoms": [], "segments": []}


def test_segments_carry_documented_keys_and_addressable_checkpoint(tmp_path):
    """The feature-scoped intent segments (checkpoints): deterministic on read, one per feature
    chapter, addressable as `<feature_id>@<seg_index>`, each carrying tier + novelty."""
    from ugh.core.store import Store
    from ugh.lens import tree

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feat(x): add foo")
    get(tmp_path)

    nodes = {"F-A": {"parent": None, "children": [], "members": ["a.py::foo"], "size": 1,
                     "dir": "", "label": "Foo Feature"}}
    ops = Store(tmp_path).all_ops()
    tree.save(tmp_path, {"nodes": nodes, "roots": ["F-A"],
                         "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
                         "max_depth": 0, "cannot_link_moves": [], "identity_events": []})

    segs = intent_view(tmp_path)["segments"]
    assert len(segs) == 1
    s = segs[0]
    assert set(s) == {
        "feature_id", "feature_label", "seg_index", "checkpoint", "intent", "rationale",
        # `present_op_count` is how many of `op_ids` are still in HEAD's ideal: a revert takes ops
        # out of the ideal and leaves them in the store, so a chapter the user reverted has to be
        # kept and *say* it is gone rather than silently shrink its `op_count`.
        "op_ids", "op_count", "present_op_count", "commit_shas", "asks", "first_index",
        "last_index", "novelty", "tier", "source",
        # The named symbols the chapter touched, with a per-symbol kind and the saves that touched
        # each, plus the two counts a surface prints and whether the work is still in the code --
        # so a card can say "1 change · 1 function · 1 save" without a dry run, and never an op
        # count (most ops are residue/anchor bookkeeping a reader does not recognise).
        "symbols", "symbol_count", "change_count", "symbols_truncated", "state", "taken_out_by",
        # What stands on the chapter before any preview: the chapters that hold its revert, and
        # whether that revert would go whole.
        "dependents", "clean",
    }
    assert s["checkpoint"] == "F-A@0"
    assert s["feature_label"] == "Foo Feature"
    assert s["op_count"] == len(s["op_ids"]) > 0
    assert s["symbols"] == [{"symbol": "a.py::foo", "file": "a.py", "kind": "added", "saves": [0],
                             "live": True}]
    assert (s["symbol_count"], s["change_count"], s["symbols_truncated"]) == (1, 1, 0)
    assert s["state"] == "live"
    assert (s["dependents"], s["clean"]) == ([], True)
    # Per-chapter captured asks: an excerpt plus whose words they were, never the raw prompt --
    # this list draws every chapter of every feature (`stint.ask_record`, `full=False`).
    assert isinstance(s["asks"], list)
    assert all({"gist", "source", "trimmed", "chars"} <= set(a) and "text" not in a
               for a in s["asks"])


def test_segments_say_what_stands_on_them_and_which_revert_is_clean(tmp_path):
    """Before any preview, each checkpoint says which other checkpoints hold its revert (the
    planner's holders) and whether its revert would go whole. `foo` is called by `bar`: taking out
    F-A is held by F-B; nothing stands on `bar` or `baz`. A chapter already taken out has nothing
    to say (`clean` None)."""
    from ugh.core import planner
    from ugh.core.store import Store
    from ugh.lens import tree

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("def baz():\n    return 3\n", encoding="utf-8")
    gb.commit_all("add foo and baz")
    get(tmp_path)
    (tmp_path / "b.py").write_text("from a import foo\n\n\ndef bar():\n    return foo()\n", encoding="utf-8")
    gb.commit_all("add bar calling foo")
    get(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("from a import foo\n\n\ndef bar():\n    return foo() + 1\n",
                                   encoding="utf-8")
    gb.commit_all("foo returns two, bar adds one")
    get(tmp_path)
    nodes = {f: {"parent": None, "children": [], "members": [m], "size": 1, "dir": "", "label": f}
             for f, m in (("F-A", "a.py::foo"), ("F-B", "b.py::bar"), ("F-C", "c.py::baz"))}
    tree.save(tmp_path, {"nodes": nodes, "roots": sorted(nodes),
                         "op_leaf": tree.assign_ops_to_leaves(nodes, Store(tmp_path).all_ops()),
                         "max_depth": 0, "cannot_link_moves": [], "identity_events": []})

    by_ref = {s["checkpoint"]: s for s in intent_view(tmp_path)["segments"]}
    assert (by_ref["F-A@0"]["dependents"], by_ref["F-A@0"]["clean"]) == (
        [{"ref": "F-B@0", "symbols": ["a.py::foo"]}], False)
    assert (by_ref["F-B@0"]["dependents"], by_ref["F-B@0"]["clean"]) == ([], True)
    assert (by_ref["F-C@0"]["dependents"], by_ref["F-C@0"]["clean"]) == ([], True)

    baz_ops = frozenset(by_ref["F-C@0"]["op_ids"])
    planner.apply(tmp_path, planner.plan(tmp_path, baz_ops, target="F-C@0"))
    taken_out = next(s for s in intent_view(tmp_path)["segments"] if s["checkpoint"] == "F-C@0")
    assert (taken_out["state"], taken_out["dependents"], taken_out["clean"]) == ("reverted", [], None)


def test_a_save_and_a_revert_after_the_last_map_build_are_on_their_lanes(tmp_path):
    """The persisted map files ops only when it is rebuilt, so a save after the build, and the ops
    a revert minted, sat on no lane until a refresh. Both are filed at read time by the lane that
    owns what they touch, and nothing is written back."""
    from ugh import state
    from ugh.api import _named_symbols, history_view
    from ugh.core import opindex, planner
    from ugh.core.store import Store
    from ugh.lens import tree

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n\n\ndef baz():\n    return 0\n",
                                   encoding="utf-8")
    gb.commit_all("add foo and baz")
    get(tmp_path)
    nodes = {f: {"parent": None, "children": [], "members": [m], "size": 1, "dir": "", "label": f}
             for f, m in (("F-A", "a.py::foo"), ("F-B", "a.py::baz"))}
    tree.save(tmp_path, {"nodes": nodes, "roots": sorted(nodes),
                         "op_leaf": tree.assign_ops_to_leaves(nodes, Store(tmp_path).all_ops()),
                         "max_depth": 0, "cannot_link_moves": [], "identity_events": []})
    persisted = state.load_json(tmp_path, "tree")
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n\n\ndef baz():\n    return foo()\n",
                                   encoding="utf-8")
    sha = gb.commit_all("foo returns two and baz calls it")
    get(tmp_path)
    foo_change = next(o.id for o in opindex.index_ops(tmp_path)
                      if sha in o.provenance and "a.py::foo" in o.footprint)
    done = planner.apply(tmp_path, planner.plan(tmp_path, {foo_change}, target="foo"))
    assert done.new_ops

    by_id = {o.id: o for o in opindex.index_ops(tmp_path)}
    late = [o for o in history_view(tmp_path, full=True)["ops"]
            if o["commit_index"] >= 1 and _named_symbols(by_id[o["id"]])]
    assert {o["id"] for o in late} >= {op.id for op in done.new_ops if _named_symbols(op)}
    for o in late:
        owner = "F-A" if "a.py::foo" in by_id[o["id"]].footprint else "F-B"
        assert o["feature_id"] == owner, o
    on_lane = {oid for s in intent_view(tmp_path)["segments"] if s["feature_id"] == "F-A"
               for oid in s["op_ids"]}
    assert {o["id"] for o in late if "a.py::foo" in by_id[o["id"]].footprint} <= on_lane
    assert state.load_json(tmp_path, "tree") == persisted  # read-time only


def test_cross_feature_theme_reports_both_features_with_correct_tier(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    from ugh.core.store import Store
    from ugh.lens import tree

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("fix(x): touch two unrelated files")
    get(tmp_path)

    nodes = {
        "F-A": {"parent": None, "children": [], "members": ["a.py::foo"], "size": 1, "dir": "", "label": "F-A"},
        "F-B": {"parent": None, "children": [], "members": ["b.py::bar"], "size": 1, "dir": "", "label": "F-B"},
    }
    ops = Store(tmp_path).all_ops()
    result = {
        "nodes": nodes, "roots": sorted(nodes), "op_leaf": tree.assign_ops_to_leaves(nodes, ops),
        "max_depth": 0, "cannot_link_moves": [], "identity_events": [],
    }
    tree.save(tmp_path, result)
    theme.build_themes(tmp_path)

    v = intent_view(tmp_path)
    (t,) = v["themes"]
    assert t["feature_span"] == ["F-A", "F-B"]
    assert t["tier"] == "co-changed"  # same commit, cross-feature, no dependency edge


def test_atom_prompt_surfaces_a_prompt_recorded_directly_by_commit_sha(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    sha = gb.commit_all("add foo")
    get(tmp_path)
    prompts.record_prompt(tmp_path, sha, "fix the login bug")

    v = intent_view(tmp_path)
    (atom,) = v["atoms"]
    assert atom["commit_sha"] == sha
    assert atom["prompt"] == "fix the login bug"


def test_atom_prompt_is_none_when_nothing_was_recorded(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("add foo")
    get(tmp_path)

    v = intent_view(tmp_path)
    (atom,) = v["atoms"]
    assert atom["prompt"] is None


# -- U5: stale_shas ------------------------------------------------------------------------------


def test_stale_shas_empty_when_every_member_sha_resolves(tmp_path, monkeypatch):
    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("fix(auth): add foo")
    get(tmp_path)
    theme.build_themes(tmp_path)

    (t,) = intent_view(tmp_path)["themes"]
    assert t["stale_shas"] == []


def test_stale_shas_reports_a_member_sha_that_no_longer_resolves(tmp_path, monkeypatch):
    """Simulates a rebase/amend invalidating a persisted theme member: `themes.json` still names
    a sha the current atom partition (`group.atoms`) no longer has -- `intent_view` must surface
    it rather than silently filtering it out of `atom_shas`/`op_ids` with no signal."""
    from ugh import state

    def _no_client(*args, **kwargs):
        raise RuntimeError("OPENAI_API_KEY not found in environment or .env")

    monkeypatch.setattr(theme, "get_client", _no_client)
    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("fix(auth): add foo")
    get(tmp_path)
    themes = theme.build_themes(tmp_path)
    (tid, entry) = next(iter(themes.items()))

    vanished_sha = "f" * 40
    entry["atom_shas"] = sorted({*entry["atom_shas"], vanished_sha})
    state.save_json(tmp_path, "intent_themes", {tid: entry})

    (t,) = intent_view(tmp_path)["themes"]
    assert t["stale_shas"] == [vanished_sha]
    assert vanished_sha in t["atom_shas"]  # atom_shas stays the persisted list, unfiltered
    real_atom_op_ids = frozenset(intent_view(tmp_path)["atoms"][0]["op_ids"])
    assert frozenset(t["op_ids"]) == real_atom_op_ids  # op_ids still excludes the vanished sha


def test_atom_joins_local_turns_rationale_and_chat_provenance(tmp_path):
    """Intent-ledger M1 integration: the words and provenance the ledger captures flow into the
    EXISTING atom projection -- a sha-keyed local turn reaches `prompt` via `_atom_prompt`'s
    fallback, a live rationale covering the atom's ops lands in `rationale`, and a plan session's
    `claude_session_id` rides along so a UI can offer `claude --resume` on the commit."""
    from ugh import state
    from ugh.core.op import Attribution
    from ugh.core.store import Store
    from ugh.intent import rationale as rationale_mod
    from ugh.intent import turns

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    gb.commit_all("fix(auth): add foo and bar")
    get(tmp_path)

    (atom,) = intent_view(tmp_path)["atoms"]
    sha = atom["commit_sha"]
    op_a, op_b = atom["op_ids"][0], atom["op_ids"][1]

    turns.record_turn(tmp_path, key=sha, key_kind="sha", actor="human", channel="cli",
                      text="lock down the auth entrypoint", ts=1.0)
    rationale_mod.record_rationale(
        tmp_path, subject=[{"op": op_a, "sha": sha, "fp": "f"}],
        reason="the old guard leaked sessions", actor="human", evidence=[], ts=2.0)
    # One commit fed by TWO plans (two chats), one op each: both must surface -- no 1:1 collapse
    # at the projection. (Within a single (op, sha) the kernel itself keeps one plan --
    # `merge_attribution`'s per-sha min-merge, a convergence law -- so per-op multiplicity is the
    # level the ledger can and must preserve.)
    Store(tmp_path).attribute(op_a, (Attribution(sha=sha, plan="p1"),))
    Store(tmp_path).attribute(op_b, (Attribution(sha=sha, plan="p2"),))
    state.save_json(tmp_path, "plan_sessions", {
        "p1": {"claude_session_id": "cs-42", "steps": []},
        "p2": {"claude_session_id": "cs-77", "steps": []},
    })

    (a,) = intent_view(tmp_path)["atoms"]
    assert a["prompt"] == "lock down the auth entrypoint"       # turns fallback in _atom_prompt
    assert a["rationale"] == ["the old guard leaked sessions"]  # live rationale joined by op
    assert a["claude_session_ids"] == ["cs-42", "cs-77"]        # every contributing chat, not one
    assert a["plan_ids"] == ["p1", "p2"]


def test_chat_provenance_joins_through_the_session_field_confirm_match_stamps(tmp_path):
    """`confirm_match` stamps the plan-session id into the attribution's *session* field (not
    `plan`), so the planned path's chat provenance arrives at the projection as a session id --
    the join must read both (testbed 2026-07-31: reading `plan_ids` alone meant no `chat:` line
    ever rendered for exactly the plan-matched commits the feature was built for)."""
    from ugh import state
    from ugh.core.op import Attribution
    from ugh.core.store import Store
    from ugh.intent import turns

    gb, _ = init_store(tmp_path)
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    gb.commit_all("feat: add foo")
    get(tmp_path)

    (atom,) = intent_view(tmp_path)["atoms"]
    sha, op_id = atom["commit_sha"], atom["op_ids"][0]
    Store(tmp_path).attribute(op_id, (Attribution(sha=sha, session="plan-s1"),))
    state.save_json(tmp_path, "plan_sessions", {"plan-s1": {"claude_session_id": "cs-9", "steps": []}})
    turns.record_turn(tmp_path, key="cs-9", key_kind="chat", actor="human", channel="hook",
                      text="add foo please", ts=1.0)

    (a,) = intent_view(tmp_path)["atoms"]
    assert a["claude_session_ids"] == ["cs-9"]   # session-field key resolved via plan_sessions
    assert a["prompt"] == "add foo please"       # chat-keyed hook turn reached the prompt join
