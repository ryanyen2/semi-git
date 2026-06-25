"""Decision-layer dataclasses: JSON round-trip and the default (tip) frontier."""

from sgt.decisions.model import Alternative, Decision, Frontier, Intent, LifecycleKind


def _dec(did, feature, landing, kind=LifecycleKind.INTRODUCE, of=None):
    return Decision(
        id=did,
        node_id=did.split("@")[0],
        feature=feature,
        landing=landing,
        intent=Intent(decision=f"do {did}", context="because", consequence="now works"),
        footprint=[f"m.py::{did.split('@')[0]}"],
        commits=["abc1234"],
        alternatives=[Alternative("other way", "too slow", source="plan", confidence="high")],
        lifecycle_kind=kind,
        lifecycle_of=of,
    )


def test_intent_slug_round_trips():
    it = Intent(decision="Switch ranking to BM25", slug="BM25 index",
                context="scan got slow", consequence="latency drops")
    back = Intent.from_dict(it.to_dict())
    assert back == it
    assert back.slug == "BM25 index"
    # a bare (log-recovered) intent has no slug
    assert Intent.from_dict({"decision": "x"}).slug is None


def test_decision_round_trips():
    d = _dec("retr@2", "retr", 2, LifecycleKind.REVISE, "retr@1")
    back = Decision.from_dict(d.to_dict())
    assert back == d
    assert back.intent.context == "because"
    assert back.alternatives[0].source == "plan"
    assert back.lifecycle_kind is LifecycleKind.REVISE
    assert back.lifecycle_of == "retr@1"


def test_frontier_round_trips():
    f = Frontier({"retr": "retr@2", "embed": "embed@1"})
    assert Frontier.from_dict(f.to_dict()) == f
    assert f.in_force() == {"retr@2", "embed@1"}


def test_tip_frontier_picks_latest_landing_per_lane():
    decisions = [
        _dec("embed@1", "embed", 1),
        _dec("retr@2", "retr", 2),
        _dec("retr@5", "retr", 5, LifecycleKind.REVISE, "retr@2"),
    ]
    f = Frontier.tip_of(decisions)
    # retr lane resolves to its tip (landing 5), not the earlier landing 2
    assert f.selection == {"embed": "embed@1", "retr": "retr@5"}
