"""Tests for sgt.intent.align_session -- the orchestrator that runs the pure A--F stages of
`sgt.intent.align` over real captured turns + real mined ops and writes ALIGN-region rationale
records. No LLM/network: mining + git history + the deterministic scorer only, same fixture idiom
as tests/intent/test_segment.py.
"""

from __future__ import annotations

from sgt.core.op import make_op
from sgt.core.store import Store
from sgt.intent import align, align_session, rationale, turns
from sgt.store.gitbind import GitBinding, init_store


def _mine_fully(repo) -> None:
    """Loop `get()` until the backward walk reaches genesis, so a fixture's op set is never gated by
    a single deadline-bounded backfill chunk (test-machine-speed artifact; see test_segment)."""
    from sgt.core.lens import _load_backfill_state, _ref_key, get

    gb = GitBinding(repo)
    key = _ref_key(gb) or gb.head()
    for _ in range(10):
        get(repo)
        if _load_backfill_state(repo).get(key, {}).get("reached_genesis"):
            return
    raise AssertionError(f"{repo}: backfill did not reach genesis after 10 get() chunks")


def _fetcher_repo(tmp_path):
    """A one-commit repo with two snake_case functions, mined -- the canonical clean fixture."""
    gb, _ = init_store(tmp_path)
    (tmp_path / "fetcher.py").write_text(
        "def fetch_page():\n    return 1\n\n\ndef parse_html():\n    return 2\n")
    gb.commit_all("feat(net): add fetch_page and parse_html")
    _mine_fully(tmp_path)
    return gb


# -- the store->CandidateOp adapter ---------------------------------------------------------------


def test_candidate_ops_stamp_the_real_commit_time(tmp_path):
    gb = _fetcher_repo(tmp_path)
    cand_ops, _ = align_session._candidate_ops(tmp_path)
    commit_ts = next(iter(gb.commit_times().values()))
    assert cand_ops, "expected mined ops"
    # Every op is embodied by the single commit, so each carries its real committer wall-clock.
    assert all(c.ts == float(commit_ts) for c in cand_ops)
    fetch = next(c for c in cand_ops if "fetcher.py::fetch_page" in c.symbols)
    assert fetch.ts == float(commit_ts)


def test_candidate_ops_build_a_symmetric_requires_graph(tmp_path):
    init_store(tmp_path)
    st = Store(tmp_path)
    dep = st.add(make_op({"svc.py::helper": (None, "v1")}, {"svc.py::helper": b"x"}))
    con = st.add(make_op({"svc.py::caller": (None, "v1")}, {"svc.py::caller": b"y"},
                         requires=frozenset({("svc.py::helper", "v1")})))
    _, adj = align_session._candidate_ops(tmp_path)
    # The consumer is adjacent to the producer of what it requires, in both directions.
    assert dep.id in adj[con.id]
    assert con.id in adj[dep.id]


# -- turns -> episodes (stages A/B/C over real turns) ---------------------------------------------


def test_backchannel_and_question_only_session_yields_no_episodes(tmp_path):
    init_store(tmp_path)
    turns.record_turn(tmp_path, key="s1", key_kind="chat", actor="human", channel="hook",
                      text="ok thanks", ts=1.0)
    turns.record_turn(tmp_path, key="s1", key_kind="chat", actor="human", channel="hook",
                      text="what does this do?", ts=2.0)
    episodes, kept = align_session._episodes_for_session(tmp_path, "s1")
    assert episodes == []
    assert kept == []


def test_non_human_chat_turns_are_not_aligned(tmp_path):
    """The aligner writes each record as the user's own voice (`actor="human"`); an agent-authored
    turn captured under a chat key must never feed it, even if it reads like an intent."""
    init_store(tmp_path)
    turns.record_turn(tmp_path, key="s1", key_kind="chat", actor="agent", channel="note",
                      text="I will add fetch_page to fetcher.py", ts=1.0)
    episodes, kept = align_session._episodes_for_session(tmp_path, "s1")
    assert episodes == []
    assert kept == []


# -- the orchestrator end to end ------------------------------------------------------------------


def test_clean_single_concern_writes_one_align_record(tmp_path):
    _fetcher_repo(tmp_path)
    cand_ops, _ = align_session._candidate_ops(tmp_path)
    ct = int(cand_ops[0].ts)
    tid = turns.record_turn(tmp_path, key="s1", key_kind="chat", actor="human", channel="hook",
                            text="add fetch_page to fetcher.py", ts=float(ct))
    turns.record_turn(tmp_path, key="s1", key_kind="chat", actor="human", channel="hook",
                      text="ok great", ts=float(ct + 10))

    summary = align_session.align_session(tmp_path)
    assert summary["sessions"] == 1
    assert summary["episodes"] == 1
    assert summary["aligned"] == 1

    recs = [r for r in rationale.load_rationale(tmp_path).values()]
    assert len(recs) == 1
    r = recs[0]
    assert r["reason"] == "add fetch_page to fetcher.py"  # the user's own words, not fabricated
    assert r["evidence"] == [tid]
    assert r["subject"][0]["op"]  # anchored to the aligned op
    assert r["actor"] == "human" and r["confirmed"] is False
    assert r["aligner_version"] == "1"
    assert 0.75 <= r["confidence"] <= 1.0  # ALIGN region, by construction
    assert {s["name"] for s in r["signals"]} == {"symbol", "temporal"}
    assert r["recorded_by"] == "aligner"


def test_dry_run_scores_but_writes_nothing(tmp_path):
    _fetcher_repo(tmp_path)
    cand_ops, _ = align_session._candidate_ops(tmp_path)
    ct = int(cand_ops[0].ts)
    turns.record_turn(tmp_path, key="s1", key_kind="chat", actor="human", channel="hook",
                      text="add fetch_page to fetcher.py", ts=float(ct))
    summary = align_session.align_session(tmp_path, write=False)
    assert summary["aligned"] == 1
    assert rationale.load_rationale(tmp_path) == {}  # counted, but nothing persisted


def test_two_concerns_do_not_cross_align(tmp_path):
    gb, _ = init_store(tmp_path)
    (tmp_path / "fetcher.py").write_text("def fetch_page():\n    return 1\n")
    gb.commit_all("feat(net): add fetch_page")
    (tmp_path / "parser.py").write_text("def parse_config():\n    return 2\n")
    gb.commit_all("feat(cfg): add parse_config")
    _mine_fully(tmp_path)

    cand_ops, _ = align_session._candidate_ops(tmp_path)
    fetch_ts = next(c.ts for c in cand_ops if "fetcher.py::fetch_page" in c.symbols)
    parse_ts = next(c.ts for c in cand_ops if "parser.py::parse_config" in c.symbols)
    # Two turns, each near its own commit, spaced apart so stage C splits them into two episodes.
    turns.record_turn(tmp_path, key="s1", key_kind="chat", actor="human", channel="hook",
                      text="add fetch_page to fetcher.py", ts=float(fetch_ts))
    turns.record_turn(tmp_path, key="s1", key_kind="chat", actor="human", channel="hook",
                      text="add parse_config to parser.py", ts=float(parse_ts))

    episodes, _ = align_session._episodes_for_session(tmp_path, "s1")
    assert len(episodes) == 2

    align_session.align_session(tmp_path)
    store = Store(tmp_path)
    for r in rationale.load_rationale(tmp_path).values():
        op = store.get(r["subject"][0]["op"])
        touched = set(op.footprint)
        if "add fetch_page to fetcher.py" == r["reason"]:
            assert any("fetch_page" in s for s in touched)
            assert not any("parse_config" in s for s in touched)
        elif "add parse_config to parser.py" == r["reason"]:
            assert any("parse_config" in s for s in touched)
            assert not any("fetch_page" in s for s in touched)
