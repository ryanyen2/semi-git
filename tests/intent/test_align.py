"""Tests for sgt.intent.align -- the staged alignment inference pipeline (design doc
`docs/plans/2026-08-01-001-the-alignment-pipeline.md` §3.2). The fixture banks here are the
robustness spec: real turns are messy (backchannels, corrections, deixis, compound asks,
half-finished sentences, speculative/unimplemented requests), and each stage must handle that input
without minting phantom intent or force-aligning noise. This file is also the §6 evaluation seed.

Stage A (turn typing) is covered first; later stages append their own banks."""

from __future__ import annotations

import pytest

from sgt.intent import align
from sgt.intent.align import (
    PRIOR_ACTION,
    REPAIR_CANDIDATE_OFFER,
    REPAIR_OPEN,
    REPAIR_RESTRICTED,
    TURN_BACKCHANNEL,
    TURN_CORRECTION,
    TURN_INTENT,
    TURN_QUESTION,
    ALIGN,
    NO_ALIGN,
    REVIEW,
    Candidate,
    CandidateOp,
    Episode,
    Reliabilities,
    SegTurn,
    align_candidates,
    decide,
    fit_reliabilities,
    generate_candidates,
    posterior,
    resolve_references,
    score_pair,
    segment_episodes,
    type_turn,
)

# --- Stage A: turn typing ----------------------------------------------------------------------

# Clean intent-bearing turns: state or request work. The default class.
INTENT_TURNS = [
    "Add conflict.py to represent a code-dependency problem as first-class converged state",
    "make the parser faster",
    "can you add a retry to the fetch loop?",
    "please rename Document.insert to Document.type",
    "we should batch the writes so it stops hammering the disk",
    "implement resolve() and reopen() in resolve.py",
    "the RGA needs to order concurrent inserts deterministically",
]

# Backchannels / acks / low-info: carry NO alignment signal, must be withheld.
BACKCHANNEL_TURNS = [
    "ok", "okay", "k", "yes", "yep", "sure", "thanks", "thank you", "lgtm", "great",
    "cool", "nice", "perfect", "sounds good", "continue", "go on", "go ahead", "proceed",
    "next", "done", "wip", "ok thanks", "ok, cool", "great, that works", ".", "  ", "👍",
    "lgtm 👍", "hmm",
]

# Pure questions: ask, do not assert intent -- no op to align to.
QUESTION_TURNS = [
    "why is this failing?",
    "what's the diff here?",
    "how does the RGA order concurrent inserts?",
    "is the bus channel-scoped?",
    "which file owns the tombstone logic?",
]

# Open repair: negation with no located target -- invalidates the most recent alignment.
OPEN_CORRECTION_TURNS = [
    "no", "nope", "no wait", "not like that", "that's wrong", "that's not right",
    "undo that", "revert that", "scratch that", "wait no", "hold on",
]

# Restricted repair: names/points at the target to re-attach.
RESTRICTED_CORRECTION_TURNS = [
    "no, the parser one",
    "not that file, the other one",
    "no, I meant Document.insert",
    "not the bus, the room",
]

# Candidate-offer repair: offers a target for confirmation.
CANDIDATE_OFFER_TURNS = [
    "you mean the JSON parser?",
    "do you mean Document.insert?",
    "did you mean the conflict resolver?",
]

# Compound: a correction (or ask) that ALSO carries new work -> flagged for G, not mis-typed.
COMPOUND_TURNS = [
    "no wait, also handle the null case",
    "actually, add a timeout too",
    "not like that -- use a set instead of a list",
    "add the retry and also log the failure",
]


@pytest.mark.parametrize("text", INTENT_TURNS)
def test_intent_turns_type_as_intent(text):
    assert type_turn(text).kind == TURN_INTENT


@pytest.mark.parametrize("text", BACKCHANNEL_TURNS)
def test_backchannels_type_as_backchannel_and_do_not_align(text):
    t = type_turn(text)
    assert t.kind == TURN_BACKCHANNEL, f"{text!r} should be a backchannel"
    assert not t.aligns  # withheld from alignment entirely


@pytest.mark.parametrize("text", QUESTION_TURNS)
def test_pure_questions_type_as_question_and_do_not_align(text):
    t = type_turn(text)
    assert t.kind == TURN_QUESTION, f"{text!r} should be a question"
    assert not t.aligns


def test_request_shaped_question_is_intent_not_question():
    # "can you add X?" ends with '?' but requests work -- it must not collapse to a bare question.
    for text in ("can you add a retry?", "could you make the parser faster?", "please add X?"):
        assert type_turn(text).kind == TURN_INTENT, text


@pytest.mark.parametrize("text", OPEN_CORRECTION_TURNS)
def test_open_corrections(text):
    t = type_turn(text)
    assert t.kind == TURN_CORRECTION, f"{text!r} should be a correction"
    assert t.repair == REPAIR_OPEN
    assert not t.compound
    assert t.aligns  # a correction carries (negative/relocating) alignment signal


@pytest.mark.parametrize("text", RESTRICTED_CORRECTION_TURNS)
def test_restricted_corrections(text):
    t = type_turn(text)
    assert t.kind == TURN_CORRECTION, f"{text!r} should be a correction"
    assert t.repair == REPAIR_RESTRICTED


@pytest.mark.parametrize("text", CANDIDATE_OFFER_TURNS)
def test_candidate_offer_corrections(text):
    t = type_turn(text)
    assert t.kind == TURN_CORRECTION, f"{text!r} should be a correction"
    assert t.repair == REPAIR_CANDIDATE_OFFER


@pytest.mark.parametrize("text", COMPOUND_TURNS)
def test_compound_turns_are_flagged(text):
    # Compound turns are flagged (routed to G), never silently collapsed to one concern.
    assert type_turn(text).compound, f"{text!r} should be flagged compound"


def test_ack_prefix_before_real_intent_is_still_intent():
    # "ok now add X" is intent, not a backchannel -- the ack is a prefix, the rest is the ask.
    assert type_turn("ok now add retries to the fetch loop").kind == TURN_INTENT
    assert type_turn("sure, implement resolve() next").kind == TURN_INTENT


def test_incomplete_fragment_is_not_a_backchannel():
    # Half-finished sentences carry a (weak) reference, not an ack -- they must reach later stages
    # as intent so B can try to resolve them, rather than being withheld as noise.
    for text in ("the parser", "and then the...", "also the conflict one"):
        assert type_turn(text).kind != TURN_BACKCHANNEL, text


def test_empty_text_is_backchannel_no_signal():
    assert type_turn("").kind == TURN_BACKCHANNEL
    assert not type_turn("").aligns


# --- Stage B: reference resolution -------------------------------------------------------------


def test_explicit_symbols_need_no_resolution():
    # A turn that names its targets outright is a no-op for stage B (the common base-rate case).
    r = resolve_references("please rename Document.insert to Document.type")
    assert not r.needs_resolution
    assert not r.discourse_deictic
    assert "Document.insert" in r.symbols
    assert "Document.type" in r.symbols


def test_prose_reference_is_not_a_symbol():
    # "the parser" is prose, not a symbol-shaped token, and there is no deixis to resolve -- so the
    # turn references nothing concrete and stage B stays a no-op (E scores it as temporal-only).
    r = resolve_references("make the parser faster")
    assert not r.needs_resolution
    assert r.symbols == ()


def test_file_and_name_co_mention_composes_a_qualified_symbol():
    # "add a retry to fetch_page in fetcher.py" names a file and a name as two loose tokens; a bare
    # name alone is not a symbol mention (design §3.2-B: qualified = file + name). Stage B composes
    # them into the `fetcher.py::fetch_page` the miner-stored symbol actually joins on.
    r = resolve_references("add a retry to fetch_page in fetcher.py")
    assert "fetcher.py::fetch_page" in r.symbols
    assert not r.needs_resolution  # naming a target, even loosely, is a resolution no-op


def test_composed_symbol_joins_the_miner_footprint():
    # The whole point of composition: the qualified form lenient-matches the path-prefixed symbol the
    # miner stores, which neither bare token (`fetch_page`, `fetcher.py`) does on its own.
    r = resolve_references("retry fetch_page in fetcher.py")
    assert any(align._symbol_matches(s, "pkg/fetcher.py::fetch_page") for s in r.symbols)


def test_composition_needs_an_unambiguous_single_file():
    # Two files in one turn is ambiguous -- Stage B does not guess which name binds to which file
    # (the model tier's job); it leaves the tokens bare rather than minting a wrong qualified symbol.
    r = resolve_references("move fetch_page from fetcher.py to net.py")
    assert not any("::" in s for s in r.symbols)


def test_bare_pronoun_resolves_to_workspace_focus():
    # "make it faster" -> the co-present symbol. Workspace pool is preferred for low-content deixis.
    r = resolve_references("make it faster", focus=["parser.py::parse"])
    assert r.needs_resolution
    assert r.symbols == ("parser.py::parse",)
    assert r.resolved[0]["source"] == "focus"
    assert r.resolved[0]["target"] == "parser.py::parse"


def test_bare_pronoun_falls_back_to_chain_recency():
    # No workspace focus -> the most recent conversation-chain entity (recency = last).
    r = resolve_references("make it faster", chain=["old_thing", "parser"])
    assert r.symbols == ("parser",)
    assert r.resolved[0]["source"] == "chain"


def test_workspace_focus_preferred_over_chain_for_bare_deixis():
    r = resolve_references("fix it", chain=["old"], focus=["new.py::f"])
    assert r.symbols == ("new.py::f",)
    assert r.resolved[0]["source"] == "focus"


def test_described_deixis_matches_descriptor_by_recency():
    # "the parser one" -> the most recent chain entity whose name contains the descriptor.
    r = resolve_references("no, the parser one", chain=["json_parser", "xml_parser", "room"])
    assert r.needs_resolution
    assert r.symbols == ("xml_parser",)


def test_the_other_one_resolves_to_the_alternative():
    # "the other one" names the alternative to the current focus -- the second-most-recent entity.
    r = resolve_references("not that file, the other one", chain=["first", "second"])
    assert "first" in r.symbols


def test_discourse_deixis_points_at_prior_action_not_a_symbol():
    # "revert that" points at the previous action, so it resolves to no entity.
    r = resolve_references("revert that")
    assert r.discourse_deictic
    assert r.needs_resolution
    assert r.symbols == ()
    assert r.resolved[0]["target"] == PRIOR_ACTION


def test_explicit_target_beats_discourse_deixis():
    # "revert Document.insert" names its target -- it is not bare discourse deixis.
    r = resolve_references("revert Document.insert")
    assert not r.discourse_deictic
    assert "Document.insert" in r.symbols


def test_no_pools_means_unresolvable_deixis_is_a_noop():
    # A dangling "it" with nothing to resolve against yields no symbol -- stage B does not guess.
    r = resolve_references("make it faster")
    assert r.symbols == ()
    assert not r.needs_resolution


# --- Stage C: episode segmentation -------------------------------------------------------------


def _assignment(episodes):
    """Per-turn episode index, so tests can assert on which concern each turn joined."""
    label = {}
    for idx, ep in enumerate(episodes):
        for turn in ep.turns:
            label[turn] = idx
    return [label[i] for i in range(len(label))]


def test_single_concern_is_one_episode():
    turns = [SegTurn(("A",), 0.0), SegTurn(("A",), 10.0), SegTurn(("A",), 20.0)]
    episodes = segment_episodes(turns)
    assert len(episodes) == 1
    assert episodes[0].turns == [0, 1, 2]
    assert episodes[0].symbols == {"A"}


def test_interleaved_concerns_split_and_reattach():
    # A/B/A/B braided -> two episodes; the later A rejoins the A episode, not the most recent one.
    turns = [SegTurn(("A",), 0.0), SegTurn(("B",), 1.0), SegTurn(("A",), 2.0), SegTurn(("B",), 3.0)]
    episodes = segment_episodes(turns)
    assert len(episodes) == 2
    assert _assignment(episodes) == [0, 1, 0, 1]


def test_repair_reattaches_to_most_recent_and_never_opens():
    turns = [SegTurn(("A",), 0.0), SegTurn(("B",), 1.0), SegTurn((), 2.0, is_repair=True)]
    episodes = segment_episodes(turns)
    assert len(episodes) == 2  # the repair opened nothing
    assert episodes[1].turns == [1, 2]  # it joined the most recently active episode


def test_restricted_repair_reattaches_to_the_episode_it_names():
    # "no wait, the parser one" corrects the PARSER concern even though a later fetch concern is the
    # most recently active episode -- a repair that resolved a target routes by overlap, not recency.
    turns = [
        SegTurn(("parser.py::parse",), 0.0),          # episode 0: parser
        SegTurn(("fetcher.py::fetch",), 30.0),         # episode 1: fetch (most recent)
        SegTurn(("parser.py::parse",), 70.0, is_repair=True),  # names the parser -> episode 0
    ]
    episodes = segment_episodes(turns)
    assert len(episodes) == 2
    assert episodes[0].turns == [0, 2]  # re-attached to the parser episode, not the fetch episode


def test_open_repair_without_a_target_reattaches_to_most_recent():
    turns = [
        SegTurn(("A",), 0.0),
        SegTurn(("B",), 30.0),
        SegTurn((), 70.0, is_repair=True),  # "revert that" -- no target -> most recent episode
    ]
    episodes = segment_episodes(turns)
    assert episodes[1].turns == [1, 2]


def test_symbol_less_fragment_continues_within_time_horizon():
    turns = [SegTurn(("A",), 0.0), SegTurn((), 100.0)]  # bare fragment, close in time
    episodes = segment_episodes(turns)
    assert len(episodes) == 1
    assert episodes[0].turns == [0, 1]


def test_symbol_less_fragment_after_long_gap_opens_new():
    turns = [SegTurn(("A",), 0.0), SegTurn((), 100000.0)]
    episodes = segment_episodes(turns)
    assert len(episodes) == 2


def test_symbol_divergence_opens_new_even_when_time_close():
    # Disjoint references one second apart still start a new concern -- divergence is the signal.
    turns = [SegTurn(("A",), 0.0), SegTurn(("C",), 1.0)]
    episodes = segment_episodes(turns)
    assert len(episodes) == 2


def test_empty_input_is_no_episodes():
    assert segment_episodes([]) == []


# --- Stage D: candidate generation -------------------------------------------------------------


def _episode(symbols, start=0.0, end=10.0):
    return Episode(turns=[0], symbols=set(symbols), start_ts=start, end_ts=end)


def _gens(candidates, op_id):
    for c in candidates:
        if c.op_id == op_id:
            return c.generators
    return None


def test_symbol_generator_surfaces_lenient_footprint_match():
    # A qualified reference matches an op whose stored symbol carries a repo-relative path prefix.
    ep = _episode({"parser.py::parse"})
    ops = [CandidateOp("op1", frozenset({"pkg/parser.py::parse"}))]
    cands = generate_candidates(ep, ops)
    assert _gens(cands, "op1") == frozenset({"symbol"})


def test_temporal_generator_surfaces_ops_in_span_and_excludes_far_ops():
    ep = _episode(set(), start=100.0, end=200.0)
    ops = [CandidateOp("near", frozenset({"x"}), ts=150.0),
           CandidateOp("far", frozenset({"y"}), ts=999999.0)]
    cands = generate_candidates(ep, ops)
    assert _gens(cands, "near") == frozenset({"temporal"})
    assert _gens(cands, "far") is None


def test_bare_prose_reference_does_not_symbol_match():
    # A bare prose token ("parser") is not a qualified mention -- it must not join a structured
    # footprint symbol. With no timestamp, the op is not surfaced at all.
    ep = _episode({"parser"})
    ops = [CandidateOp("op1", frozenset({"pkg/parser.py::parse"}))]
    assert generate_candidates(ep, ops) == []


def test_requires_hop_pulls_structurally_linked_op():
    ep = _episode({"pkg/a.py::f"})
    ops = [CandidateOp("op1", frozenset({"pkg/a.py::f"})),
           CandidateOp("op2", frozenset({"pkg/b.py::g"}))]
    cands = generate_candidates(ep, ops, requires_adj={"op1": {"op2"}})
    assert _gens(cands, "op1") == frozenset({"symbol"})
    assert _gens(cands, "op2") == frozenset({"requires"})  # never named, reached via one hop


def test_union_records_every_generator_that_fired():
    ep = _episode({"pkg/a.py::f"}, start=0.0, end=10.0)
    ops = [CandidateOp("op1", frozenset({"pkg/a.py::f"}), ts=5.0)]
    cands = generate_candidates(ep, ops)
    assert _gens(cands, "op1") == frozenset({"symbol", "temporal"})


def test_no_ops_no_candidates():
    assert generate_candidates(_episode({"x"}), []) == []


# --- Stage E: Fellegi--Sunter scoring ----------------------------------------------------------

# An unlabeled pool where a minority of pairs fire the discriminating signals (symbol, key) atop a
# signal that fires on everything (temporal). EM must, with no labels, learn that symbol/key are
# match-evidence and temporal is not.
_MATCH_PATTERN = frozenset({"symbol", "temporal", "key"})
_NOISE_PATTERN = frozenset({"temporal"})
_POOL = [_MATCH_PATTERN] * 8 + [_NOISE_PATTERN] * 32


def _weight(rel, signal):
    import math
    return math.log(rel.m[signal] / rel.u[signal])


def test_discriminating_signal_is_learned_as_match_evidence():
    rel = fit_reliabilities(_POOL)
    assert rel.m["symbol"] > rel.u["symbol"]  # firing symbol is evidence FOR a match
    assert rel.m["key"] > rel.u["key"]
    assert _weight(rel, "symbol") > 1.0


def test_signal_that_fires_everywhere_earns_near_zero_weight():
    # "temporal" fires on every pair -> no discriminating power -> weight ~= 0 by construction.
    rel = fit_reliabilities(_POOL)
    assert abs(_weight(rel, "temporal")) < 0.2


def test_score_orders_match_like_above_non_match():
    rel = fit_reliabilities(_POOL)
    assert score_pair(_MATCH_PATTERN, rel) > 0 > score_pair(_NOISE_PATTERN, rel)


def test_posterior_separates_the_two_classes():
    rel = fit_reliabilities(_POOL)
    assert posterior(_MATCH_PATTERN, rel) > 0.5 > posterior(_NOISE_PATTERN, rel)


def test_complexity_conditioning_discounts_a_tangled_session():
    rel = fit_reliabilities(_POOL)
    clean = score_pair(_MATCH_PATTERN, rel, concern_count=1)
    tangled = score_pair(_MATCH_PATTERN, rel, concern_count=5)
    assert 0 < tangled < clean  # a positive score shrinks toward zero as concerns multiply


def test_fit_is_deterministic():
    assert fit_reliabilities(_POOL).m == fit_reliabilities(_POOL).m


def test_empty_pool_returns_init_priors():
    rel = fit_reliabilities([])
    assert rel.signals == ()
    assert 0.0 < rel.prior < 1.0


# --- Stage F: three-region decision ------------------------------------------------------------


def test_decide_three_regions():
    assert decide(0.95) == ALIGN
    assert decide(0.6) == REVIEW  # abstain: no edge, feeds G's queue
    assert decide(0.3) == NO_ALIGN  # residual


def test_decide_boundaries_resolve_toward_a_decision():
    assert decide(0.5, align_bar=0.75, no_align_bar=0.5) == NO_ALIGN  # <= lower bar
    assert decide(0.75, align_bar=0.75, no_align_bar=0.5) == ALIGN  # >= upper bar


def test_align_candidates_writes_edges_only_for_match_like_pairs():
    # The full E+F pass over a mixed pool: the match-pattern op aligns, the noise op does not.
    candidates = ([Candidate(f"m{i}", _MATCH_PATTERN) for i in range(8)]
                  + [Candidate(f"n{i}", _NOISE_PATTERN) for i in range(32)])
    scored = {s.op_id: s for s in align_candidates(candidates)}
    assert scored["m0"].region == ALIGN
    assert scored["n0"].region == NO_ALIGN


def test_align_candidates_abstains_under_complexity_pressure():
    # A tangled session (many concerns) discounts scores, pulling borderline aligns into review --
    # the abstention band widens rather than the model guessing. The match pattern still aligns; we
    # assert the discount lowered its posterior (evidence weakened, not ignored).
    candidates = ([Candidate(f"m{i}", _MATCH_PATTERN) for i in range(8)]
                  + [Candidate(f"n{i}", _NOISE_PATTERN) for i in range(32)])
    clean = {s.op_id: s for s in align_candidates(candidates, concern_count=1)}
    tangled = {s.op_id: s for s in align_candidates(candidates, concern_count=8)}
    assert tangled["m0"].posterior < clean["m0"].posterior
