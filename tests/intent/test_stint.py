"""Tests for `sgt.intent.stint` (capture weave P2, design doc 2026-09-01 §4c): the deterministic
prompt→op join over capture manifests. Each derivation test carries one of the design's numbered
cases; `reflect_save`'s emission is covered here on constructed stores, and end-to-end through a
real save in tests/test_porcelain.py / tests/mcp/test_server.py."""

from __future__ import annotations

from sgt.intent.stint import derive_stints, reflect_save


def _turn(tid, session, ts, text, *, seq=0, kind="chat", channel="hook"):
    return {"id": tid, "key": session, "key_kind": kind, "seq": seq,
            "actor": "human", "channel": channel, "text": text, "ts": ts}


def _event(session, ts, file):
    return {"seq": 0, "tool": "Edit", "file": file, "session_id": session, "ts": ts}


def _manifest(sha, start, end, turns=(), events=(), ops=()):
    return {"sha": sha, "start": start, "end": end,
            "turns": list(turns), "events": list(events), "ops": list(ops)}


def test_each_prompt_claims_only_the_ops_its_events_ground(tmp_path):
    """Case 3 (N:1, ten prompts one save): a single commit's ops split by which turn's events
    touched their files -- the sub-commit answer the atom alone can never give."""
    m = _manifest("c1", 0.0, 100.0,
                  turns=[_turn("t1", "cs-1", 10.0, "add auth"),
                         _turn("t2", "cs-1", 50.0, "now add logging")],
                  events=[_event("cs-1", 20.0, "auth.py"), _event("cs-1", 60.0, "log.py")],
                  ops=[{"id": "op-a", "symbols": ["auth.py::login"]},
                       {"id": "op-b", "symbols": ["log.py::emit"]}])

    out = derive_stints({"c1": m}, "c1", root=tmp_path)

    assert [(s["turn"]["id"], s["op_ids"]) for s in out["stints"]] == \
        [("t1", ["op-a"]), ("t2", ["op-b"])]
    assert out["residual_op_ids"] == []


def test_a_question_that_produced_no_code_is_not_a_stint(tmp_path):
    """Case 5: a turn owning no events grounds nothing and labels nothing -- without this, every
    time-window join would poison on conversational turns."""
    m = _manifest("c1", 0.0, 100.0,
                  turns=[_turn("t1", "cs-1", 10.0, "how does the fold work?")],
                  events=[], ops=[{"id": "op-a", "symbols": ["a.py::foo"]}])

    out = derive_stints({"c1": m}, "c1", root=tmp_path)

    assert out["stints"] == [] and out["residual_op_ids"] == ["op-a"]


def test_ownership_is_per_session_never_global_time(tmp_path):
    """Case 6 (two agent windows, one repo): session B's later turn must not steal session A's
    events just because it is nearer in wall-clock."""
    m = _manifest("c1", 0.0, 100.0,
                  turns=[_turn("ta", "cs-a", 10.0, "work on auth"),
                         _turn("tb", "cs-b", 29.0, "work on logging")],
                  events=[_event("cs-a", 30.0, "auth.py"), _event("cs-b", 31.0, "log.py")],
                  ops=[{"id": "op-a", "symbols": ["auth.py::login"]},
                       {"id": "op-b", "symbols": ["log.py::emit"]}])

    out = derive_stints({"c1": m}, "c1", root=tmp_path)

    assert [(s["turn"]["id"], s["op_ids"]) for s in out["stints"]] == \
        [("ta", ["op-a"]), ("tb", ["op-b"])]


def test_a_hand_typed_edit_falls_to_the_residual(tmp_path):
    """Case 7: an op whose file no captured event touched is never attributed to the nearest
    prompt -- file-grounding is what licenses the time window."""
    m = _manifest("c1", 0.0, 100.0,
                  turns=[_turn("t1", "cs-1", 10.0, "add auth")],
                  events=[_event("cs-1", 20.0, "auth.py")],
                  ops=[{"id": "op-a", "symbols": ["auth.py::login"]},
                       {"id": "op-hand", "symbols": ["notes.py::todo"]}])

    out = derive_stints({"c1": m}, "c1", root=tmp_path)

    assert out["stints"][0]["op_ids"] == ["op-a"]
    assert out["residual_op_ids"] == ["op-hand"]


def test_an_open_stint_spans_saves_until_its_session_speaks(tmp_path):
    """Case 4 (1:M, one prompt many saves): the turn lives in the first manifest; the session works
    silently into the second window, so the second save's events -- and the ops they ground --
    still belong to that turn."""
    m1 = _manifest("c1", 0.0, 30.0,
                   turns=[_turn("t1", "cs-1", 10.0, "implement the plan")],
                   events=[_event("cs-1", 20.0, "step1.py")],
                   ops=[{"id": "op-1", "symbols": ["step1.py::a"]}])
    m2 = _manifest("c2", 30.0, 60.0,
                   turns=[], events=[_event("cs-1", 40.0, "step2.py")],
                   ops=[{"id": "op-2", "symbols": ["step2.py::b"]}])

    out = derive_stints({"c1": m1, "c2": m2}, "c2", root=tmp_path)

    assert [(s["turn"]["id"], s["op_ids"]) for s in out["stints"]] == [("t1", ["op-2"])]


def test_an_abandoned_prompt_never_reopens_onto_later_work(tmp_path):
    """Case 9: the ask's events all fell in the first window; with no fresh events from that
    session in the second, the old turn claims nothing there -- even though the second save's op
    touches the very same file."""
    m1 = _manifest("c1", 0.0, 30.0,
                   turns=[_turn("t1", "cs-1", 10.0, "try a rewrite")],
                   events=[_event("cs-1", 20.0, "core.py")], ops=[])
    m2 = _manifest("c2", 30.0, 60.0, turns=[], events=[],
                   ops=[{"id": "op-later", "symbols": ["core.py::main"]}])

    out = derive_stints({"c1": m1, "c2": m2}, "c2", root=tmp_path)

    assert out["stints"] == [] and out["residual_op_ids"] == ["op-later"]


def test_a_correction_chain_keeps_both_claims(tmp_path):
    """Case 8: "add auth" and "no, sessions not JWTs" are BOTH the why of the code that survived;
    neither derivation nor emission silently drops the earlier ask."""
    m = _manifest("c1", 0.0, 100.0,
                  turns=[_turn("t1", "cs-1", 10.0, "add auth"),
                         _turn("t2", "cs-1", 50.0, "no, sessions not JWTs")],
                  events=[_event("cs-1", 20.0, "auth.py"), _event("cs-1", 60.0, "auth.py")],
                  ops=[{"id": "op-a", "symbols": ["auth.py::login"]}])

    out = derive_stints({"c1": m}, "c1", root=tmp_path)

    assert [(s["turn"]["id"], s["op_ids"]) for s in out["stints"]] == \
        [("t1", ["op-a"]), ("t2", ["op-a"])]


def test_a_pre_weave_save_derives_to_honest_nothing(tmp_path):
    assert derive_stints({}, "c0", root=tmp_path) == {"stints": [], "residual_op_ids": []}
    assert reflect_save(tmp_path, "c0") == []


def test_absolute_event_paths_ground_repo_relative_footprints(tmp_path):
    """The hook records absolute paths; footprints are repo-relative. The join must not miss on
    that mismatch (nor match a same-named file outside the repo)."""
    m = _manifest("c1", 0.0, 100.0,
                  turns=[_turn("t1", "cs-1", 10.0, "fix foo")],
                  events=[_event("cs-1", 20.0, str(tmp_path / "a.py")),
                          _event("cs-1", 21.0, "/somewhere/else/b.py")],
                  ops=[{"id": "op-a", "symbols": ["a.py::foo"]},
                       {"id": "op-b", "symbols": ["b.py::bar"]}])

    out = derive_stints({"c1": m}, "c1", root=tmp_path)

    assert out["stints"][0]["op_ids"] == ["op-a"]
    assert out["residual_op_ids"] == ["op-b"]
