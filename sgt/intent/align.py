"""The alignment pipeline (design doc `docs/plans/2026-08-01-001-the-alignment-pipeline.md` §3.2):
staged inference from verbatim conversation turns (`sgt.intent.turns`) to weighted edges onto the
fine-grained ops that landed. Where `rationale.reflect_planned_match` *transcribes* an alignment the
plan loop already computed, this module *infers* one for the messy majority of turns -- corrections,
backchannels, deixis, compound asks, half-finished sentences -- that the plan loop never touches.

Seven stages, A--G (§3.2). This module builds the deterministic, model-free core A--F; the bounded
LLM adjudicator G is P4. Each stage is a pure function of its inputs so it is testable in isolation
against the messy-turn fixture bank (`tests/intent/test_align.py`), which is also the §6 evaluation
seed. The stages, in order:

- **A. Type the turn** (`type_turn`): four dialogue-act classes -- intent-bearing, backchannel/ack,
  question, correction/repair. Backchannels carry no alignment signal and are withheld, never
  force-aligned; a correction *inverts* the prior alignment rather than minting fresh intent, so
  misreading one as intent poisons everything downstream -- hence it is the highest-leverage cheap
  stage. Compound turns (a correction that also carries new content) are flagged, not mis-typed.
- **B--F**: reference resolution, episode segmentation, candidate generation, Fellegi--Sunter
  scoring, and the calibrated three-region decision -- added incrementally, each behind its own
  fixtures.

Design note on why typing is rules, not a model: the dialogue-act tail is unlearnable (SWBD-DAMSL's
200+ labels collapse to ~42 because a handful of classes carry the mass), and the cheap cue-lexicon
features here hit 92% on the analogous query-reformulation task (Huang, CIKM'09). The model tier
(G) exists for the genuinely ambiguous turns this stage *flags* (`compound`), not to replace it.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field

# Bumps when the A--F stage logic changes shape enough that older records' scores are no longer
# comparable -- stamped onto every alignment record (`aligner_version`) so a re-score can tell which
# records it supersedes. (Sibling of `rationale.REFLECTOR_VERSION`.)
ALIGNER_VERSION = "1"

# --- Stage A: turn typing --------------------------------------------------------------------

TURN_INTENT = "intent"  # states something to do -- the only class that mints fresh alignment
TURN_BACKCHANNEL = "backchannel"  # ack/filler/low-info -- withheld from alignment entirely
TURN_QUESTION = "question"  # asks, does not assert intent -- no op to align to
TURN_CORRECTION = "correction"  # repairs a prior turn -- inverts/relocates an alignment (stage B/E)

# Repair typology (Dingemanse & Enfield), decided only for TURN_CORRECTION:
REPAIR_OPEN = "open"  # "no, not like that" -- no located target; invalidates the most recent alignment
REPAIR_RESTRICTED = "restricted"  # "no, the parser one" -- names/points at the target to re-attach
REPAIR_CANDIDATE_OFFER = "candidate_offer"  # "you mean the JSON parser?" -- offers a target to confirm

# Whole-utterance acknowledgement / filler tokens. A turn that is *only* these (modulo punctuation
# and a trailing emoji) is a backchannel -- it carries no alignment signal. "ok now add X" is NOT a
# backchannel: the ack is a prefix, the intent is the rest (handled by the is-only-ack test below).
_ACK_PHRASES = frozenset({
    "ok", "okay", "k", "kk", "kay", "yes", "yep", "yeah", "yup", "ya", "sure", "right",
    "thanks", "thank you", "thx", "ty", "cheers", "great", "cool", "nice", "perfect", "awesome",
    "good", "sounds good", "sg", "lgtm", "looks good", "works", "that works", "nvm", "never mind",
    "go", "go on", "go ahead", "continue", "keep going", "proceed", "next", "carry on",
    "done", "wip", "hm", "hmm", "yeah ok", "ok thanks", "ok cool", "ok great",
})

# Leading cues that mark a repair/correction turn. Matched at the start of the (normalized) text so
# "not that file" corrects but "add a not-null check" does not.
_CORRECTION_LEADS = (
    "no wait", "no,", "no.", "no ", "nope", "nah", "not like that", "not that", "not the",
    "not what", "don't", "dont", "stop", "undo", "revert that", "revert it", "wait,", "wait no",
    "actually", "instead", "that's not", "thats not", "that's wrong", "thats wrong", "wrong",
    "scratch that", "ignore that", "never mind", "nvm", "on second thought", "hold on",
)

# Action / imperative content -- an action verb or request frame. Presence means the turn asserts
# work to do, which (a) rescues a "?"-terminated request from being typed a bare question and (b)
# marks a correction as *compound* (it also carries new intent, so route it to G rather than
# collapsing it to a pure repair).
_ACTION_VERBS = frozenset({
    "add", "fix", "make", "implement", "build", "write", "create", "change", "update", "remove",
    "delete", "rename", "refactor", "handle", "support", "use", "move", "split", "merge", "wrap",
    "extract", "rewrite", "replace", "introduce", "drop", "convert", "cache", "batch", "guard",
    "validate", "check", "return", "raise", "throw", "log", "expose", "hide", "enable", "disable",
    "set", "store", "load", "parse", "render", "emit", "skip", "keep", "let", "allow", "avoid",
    "ensure", "prevent", "revert", "undo", "test", "assert",
})
_REQUEST_FRAMES = ("can you", "could you", "would you", "please", "let's", "lets", "we should",
                   "we need", "i want", "i'd like", "id like", "how about", "what if", "maybe")
# Verbs that ARE the correction, not new work atop it: "undo that"/"revert that" is a pure open
# repair, so its verb must not read as the "also does new work" signal that flags a compound.
_CORRECTION_VERBS = frozenset({"undo", "revert"})

# A locating reference for a *restricted* repair: a definite noun phrase ("the parser", "that file",
# "the other one") or a symbol-shaped token. Cheap and deliberately permissive -- stage B does the
# real resolution; this only splits restricted ("no, the parser one") from open ("no, not like
# that") repair. Two regexes because case matters for symbol shapes but not for the phrases, and
# they cannot share an IGNORECASE flag: under IGNORECASE a CamelCase pattern like `[A-Z][a-z]+[A-Z]`
# matches *any* word, so every open correction ("nope", "wait") mis-reads as restricted.
_LOCATOR_PHRASE_RE = re.compile(
    r"\b(?:the|this)\s+\w+|\bthe\s+other\b|\b\w+\s+one\b"
    r"|\bthat\s+(?:file|one|function|class|method|thing|line|part|bit|module|feature|test)\b",
    re.IGNORECASE,
)
# Symbol-shaped tokens, matched against the ORIGINAL (un-lowercased) text so CamelCase survives:
# a dotted path (`Document.insert`), a `.py` file, a `::` qualname, snake_case, or a CamelCase name.
_SYMBOL_SHAPE_RE = re.compile(r"\b\w+\.py\b|::|\b\w+\.\w+\b|\b[a-z]+_[a-z_]+\b|\b[A-Z][a-z]+[A-Z]\w*\b")
_CANDIDATE_OFFER_RE = re.compile(r"^(?:do you mean|you mean|did you mean|is it|the)\b.*\?\s*$",
                                 re.IGNORECASE)


def _locates_target(text: str, norm: str) -> bool:
    """Does a correction point at a specific target (restricted repair), vs. reject wholesale (open
    repair)? Phrases are matched on the normalized text, symbol shapes on the verbatim `text`."""
    return bool(_LOCATOR_PHRASE_RE.search(norm) or _SYMBOL_SHAPE_RE.search(text))


@dataclass(frozen=True)
class TypedTurn:
    """The output of stage A for one turn. `kind` is the dialogue act; `repair` names the repair
    subtype for a correction (else None); `compound` flags a turn carrying more than one concern (a
    correction that also states new work, or an ask bundling several intents) -- the design routes
    compounds to the LLM adjudicator (G) rather than letting stage E mis-score them as one pair."""

    kind: str
    repair: str | None = None
    compound: bool = False

    @property
    def aligns(self) -> bool:
        """Does this turn carry alignment signal at all? Intent and correction turns do (a correction
        carries *negative*/relocating signal); backchannels and pure questions do not, and are
        withheld from candidate generation entirely (§3.2-A: 'recorded, never force-aligned')."""
        return self.kind in (TURN_INTENT, TURN_CORRECTION)


def _normalize(text: str) -> str:
    """Lowercase, strip a trailing emoji/symbol run, collapse whitespace. Used for cue matching --
    NOT for storage (turns keep verbatim text); this is a throwaway view for classification."""
    t = text.strip().lower()
    # Drop trailing non-word, non-terminal-punctuation runs (emoji, "!!!", "🎉") so "lgtm 👍" acks.
    t = re.sub(r"[^\w?.!,'\s]+\s*$", "", t).strip()
    return re.sub(r"\s+", " ", t)


def _is_only_ack(norm: str) -> bool:
    """True iff the whole utterance is one or more acknowledgement phrases and nothing else -- so
    "ok", "ok thanks", and "great, that works" ack, but "ok now add retries" does not (its remainder
    is real intent). Split on commas into *clauses* (not whitespace, else the two-word ack "that
    works" shatters into non-ack tokens), and require every clause to be an ack -- either as a whole
    phrase ("that works") or word-by-word ("ok thanks")."""
    stripped = norm.strip(" .!?,")
    if not stripped:
        return True  # empty / punctuation-only carries no signal
    if stripped in _ACK_PHRASES:
        return True
    clauses = [c.strip() for c in stripped.split(",") if c.strip()]
    if len(clauses) > 4:
        return False
    return bool(clauses) and all(
        c in _ACK_PHRASES or all(w in _ACK_PHRASES for w in c.split()) for c in clauses
    )


def _tokens(norm: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", norm)


def _has_action(norm: str) -> bool:
    """Does the turn assert work to do -- an imperative action verb or a request frame? Correction
    verbs (undo/revert) don't count: they are the repair itself, not new work atop it."""
    if any(frame in norm for frame in _REQUEST_FRAMES):
        return True
    toks = set(_tokens(norm))
    return bool(toks & (_ACTION_VERBS - _CORRECTION_VERBS))


def _leading_correction(norm: str) -> bool:
    return any(norm == lead.strip() or norm.startswith(lead) for lead in _CORRECTION_LEADS)


def type_turn(text: str, prev_user_text: str | None = None) -> TypedTurn:
    """Stage A: classify one turn's dialogue act from cheap cue-lexicon + shape features. `text` is
    the verbatim turn; `prev_user_text` (the immediately-preceding *user* turn, if any) lets a bare
    "no" be read as a repair of it -- reserved for future overlap features, accepted now for the
    stable signature. Pure and deterministic.

    Order matters: a backchannel is decided first (shortest-circuit, no signal), then correction (a
    leading negation cue -- highest downstream cost if missed), then a bare question, else intent
    (the default: when a turn plainly states or requests work, or is an unclassifiable fragment, we
    keep it as intent and let stages E/F decide whether it actually aligns to anything -- abstention
    lives downstream, not here)."""
    norm = _normalize(text)
    if _is_only_ack(norm):
        return TypedTurn(TURN_BACKCHANNEL)

    if _leading_correction(norm):
        compound = _has_action(norm)  # correction that also states new work -> route to G
        if _CANDIDATE_OFFER_RE.match(norm):
            repair = REPAIR_CANDIDATE_OFFER
        elif _locates_target(text, norm):
            repair = REPAIR_RESTRICTED
        else:
            repair = REPAIR_OPEN
        return TypedTurn(TURN_CORRECTION, repair=repair, compound=compound)

    # A candidate-offer ("you mean X?") outside a leading-correction context is still a repair move.
    if _CANDIDATE_OFFER_RE.match(norm):
        return TypedTurn(TURN_CORRECTION, repair=REPAIR_CANDIDATE_OFFER)

    if norm.endswith("?") and not _has_action(norm):
        return TypedTurn(TURN_QUESTION)

    # Intent by default. A compound intent bundles multiple asks ("add X and also handle Y"); flag
    # it so segmentation/E know not to treat it as a single concern.
    compound = bool(re.search(r"\b(?:also|and then|plus|as well|too|besides)\b", norm)) and _has_action(norm)
    return TypedTurn(TURN_INTENT, compound=compound)


# --- Stage B: reference resolution -----------------------------------------------------------
#
# Design §3.2-B: before any lexical matching, resolve pronouns/deixis/ellipsis ("make it faster" ->
# make *the parser from turn 12* faster) so stages D/E score against a concrete symbol set rather
# than a dangling "it". Two candidate pools, resolved against their union: the *conversation chain*
# (entities mentioned earlier in the episode, recency-weighted -- later references compress and get
# harder, PhotoBook ACL'19) and the *workspace focus* (open file / recently-touched symbols / diff
# -- the Bolt "put-that-there" principle: deixis resolves against what is co-present). Short
# low-content references ("it", "this", "that file") prefer the workspace pool; a described
# reference ("the parser one") matches its descriptor against the pools. Discourse deixis ("revert
# that") points at the previous *action*, not an entity, so it resolves to no symbol.
#
# This is the cheap tier: a "needs resolution?" gate (most turns are no-ops -- Utterance ReWriter
# ACL'19: EM 98% on no-op cases) plus a most-recent-compatible heuristic. The model tier (G) is an
# LLM rewrite that must cite its source turn(s); it is not built here.

PRIOR_ACTION = "@prev-action"  # the resolution target of discourse deixis ("revert that")

# Symbol-shaped tokens named explicitly in the text -- these need no pool lookup, they ARE the
# target. Dotted paths / qualnames / .py files / snake_case / CamelCase. Matched on the verbatim
# text (case matters), like Stage A's _SYMBOL_SHAPE_RE, but here we EXTRACT the tokens, not just
# test for presence.
_EXPLICIT_SYMBOL_RE = re.compile(
    r"\b[\w/]+\.py(?:::\w+)?\b|\b\w+(?:\.\w+)+\b|\b\w+::\w+\b|\b[a-z]+_[a-z_]+\b|\b[A-Z][a-z]+[A-Z]\w*\b"
)
# A *described* deictic reference: "the parser one", "the json file", "parser one" -- captures the
# descriptor word ("parser") to match against the pools. "other" is special (see _resolve_described).
_DESCRIBED_RE = re.compile(
    r"\bthe\s+(\w+)\s+(?:one|file|function|class|method|module|test)\b|\b(\w+)\s+one\b", re.IGNORECASE)
# A bare, low-content deictic reference -- a pronoun or a demonstrative + generic noun. Prefers the
# workspace pool (co-present). "the other" is described-but-descriptorless (the alternative target).
_BARE_DEIXIS_RE = re.compile(
    r"\b(?:it|its|this|that|these|those|them|they)\b|\bthat\s+file\b|\bthe\s+other\b", re.IGNORECASE)
# Discourse-action verbs: govern a demonstrative that points at the previous action, not a symbol.
_DISCOURSE_VERB_RE = re.compile(r"\b(?:revert|undo|redo|re-?run|rerun|repeat)\b", re.IGNORECASE)


@dataclass(frozen=True, eq=False)
class ResolvedRef:
    """The output of stage B for one turn. `symbols` is the resolved reference set fed to stages D/E
    (explicit mentions plus any pool-resolved deixis). `needs_resolution` is the cheap gate: False
    means the turn named its targets outright (or referenced nothing) and stage B was a no-op.
    `discourse_deictic` marks a turn pointing at the previous *action* (`PRIOR_ACTION`), not an
    entity. `resolved` is the audit trail -- one `{mention, target, source}` per resolution, so an
    inferred reference stays traceable to the pool entry it came from (the design's provenance
    requirement: a rewrite without a cited source is inadmissible as alignment evidence)."""

    symbols: tuple[str, ...]
    needs_resolution: bool = False
    discourse_deictic: bool = False
    resolved: tuple[dict, ...] = ()


def _dedupe(items) -> list[str]:
    return list(dict.fromkeys(items))


def _compose_qualified(explicit: list[str]) -> list[str]:
    """Compose a loose file + bare-name co-mention into the qualified `file::name` symbol the miner
    actually stores (design §3.2-B: a prose mention counts as a symbol only when qualified). "add a
    retry to fetch_page in fetcher.py" names both a file (`fetcher.py`) and a name (`fetch_page`),
    but as two separate tokens -- and neither bare token joins the stored `pkg/fetcher.py::fetch_page`
    (`_symbol_matches` needs the `::name` to compare). When a turn names exactly one `.py` file and
    one-or-more dot-free names, pair them; the qualified forms subsume the bare tokens they were built
    from. Ambiguity (more than one file) is left to the model tier, not guessed."""
    files = [t for t in explicit if t.endswith(".py") and "::" not in t]
    names = [t for t in explicit if "." not in t and "::" not in t]
    if len(files) != 1 or not names:
        return explicit
    f = files[0]
    subsumed = {f, *names}
    return _dedupe([f"{f}::{n}" for n in names] + [t for t in explicit if t not in subsumed])


def _resolve_described(descriptor: str, chain: list[str], focus: list[str]) -> str | None:
    """Match a described reference's descriptor ("parser" from "the parser one") against the pools by
    substring, most-recent / most-salient first. "other" has no descriptor -- it names the
    *alternative* to the current focus, approximated as the second-most-recent chain entity."""
    if descriptor == "other":
        return chain[-2] if len(chain) >= 2 else (focus[0] if focus else None)
    d = descriptor.lower()
    for cand in list(reversed(chain)) + focus:  # chain recency first, then workspace salience
        if d in cand.lower():
            return cand
    return None


def resolve_references(text: str, *, chain=None, focus=None) -> ResolvedRef:
    """Stage B: resolve a turn's references to a concrete symbol set. `chain` is the conversation
    chain -- prior entity mentions in this episode, oldest-first (recency = last). `focus` is the
    workspace focus -- co-present symbols (open file, recently-touched, diff), most-salient-first.
    Both are plain symbol/entity strings; either may be empty. Pure and deterministic.

    Resolution order: explicit symbols named in the text resolve to themselves; a described deixis
    ("the parser one") matches its descriptor against the pools; a bare low-content deixis ("it",
    "this") takes the most-salient co-present entity (workspace pool preferred), falling back to the
    most recent chain entity; a discourse deixis ("revert that") resolves to `PRIOR_ACTION`. A turn
    with explicit symbols and no dangling deixis needs no resolution -- the common no-op case."""
    chain = _dedupe(chain or [])
    focus = _dedupe(focus or [])
    norm = _normalize(text)
    explicit = _compose_qualified(_dedupe(m for m in _EXPLICIT_SYMBOL_RE.findall(text) if m))
    resolved: list[dict] = []
    symbols: list[str] = list(explicit)

    # Discourse deixis first: a discourse verb + a bare demonstrative, with no explicit target, points
    # at the previous action, not a symbol. "revert Document.insert" names its target -> not this.
    if _DISCOURSE_VERB_RE.search(norm) and _BARE_DEIXIS_RE.search(norm) and not explicit:
        resolved.append({"mention": norm, "target": PRIOR_ACTION, "source": "action"})
        return ResolvedRef(symbols=(), needs_resolution=True, discourse_deictic=True,
                           resolved=tuple(resolved))

    # Described deixis: resolve each descriptor against the pools.
    for m in _DESCRIBED_RE.finditer(norm):
        descriptor = m.group(1) or m.group(2)
        target = _resolve_described(descriptor, chain, focus)
        if target and target not in symbols:
            src = "chain" if target in chain else "focus"
            resolved.append({"mention": m.group(0).strip(), "target": target, "source": src})
            symbols.append(target)

    # Bare deixis: only when nothing more specific resolved it (explicit or described), take the
    # most-salient co-present entity -- workspace pool first (co-presence), then chain recency.
    if not symbols and _BARE_DEIXIS_RE.search(norm):
        target = focus[0] if focus else (chain[-1] if chain else None)
        if target:
            src = "focus" if focus else "chain"
            resolved.append({"mention": _BARE_DEIXIS_RE.search(norm).group(0),
                             "target": target, "source": src})
            symbols.append(target)

    needs = bool(resolved)  # a resolution happened iff a deixis was pool-resolved
    return ResolvedRef(symbols=tuple(_dedupe(symbols)), needs_resolution=needs,
                       resolved=tuple(resolved))


# --- Stage C: episode segmentation -----------------------------------------------------------
#
# Design §3.2-C: an episode is a contiguous-ish stretch of one concern -- the unit alignment
# attaches to (a turn rarely explains one op; it explains an episode whose ops share a concern).
# Sessions *braid* concerns, so episodes are not strictly contiguous: each new turn either attaches
# to an open episode or starts a new one, an online reply-to *pointer* formulation (conversation
# disentanglement -- Kummerfeld et al. ACL'19; Yu & Joty EMNLP'20, ~73 F1 with features that are
# exactly ours: time gap, symbol overlap with each open episode, and a mention-memory of the
# symbols each episode has touched). Repair turns never open an episode -- they re-attach.
#
# Scope note: the design names two composed mechanisms -- this online pointer AND a TextTiling
# depth-score boundary detector with a self-supervised learned coherence scorer as its upgrade
# path. The pointer is the one grounded with concrete numbers on exactly our features, and it
# subsumes the linear (consecutive-only) case through symbol overlap. Building both overlapping
# mechanisms now would be premature; the depth-score / learned-coherence scorer plugs into the same
# attach decision later (swap the coherence signal, keep the pointer), so it is deliberately
# deferred, matching how the rest of sgt ships a cheap tier first.

_ATTACH_OVERLAP_FLOOR = 0.3  # symbol-overlap coefficient to attach to an existing episode (match.py:40)
_CONTINUITY_HORIZON_S = 1800.0  # a symbol-less turn continues the most recent episode within this gap


@dataclass
class Episode:
    """One concern's stretch, produced by stage C. `turns` are indices into the segmented input, in
    order; `symbols` is the union of the turns' resolved references (the mention-memory stage D
    generates candidates against); `start_ts`/`end_ts` bound its active span."""

    turns: list[int]
    symbols: set[str]
    start_ts: float
    end_ts: float
    words: set[str] = field(default_factory=set)  # content words (topic anchor), union of the turns'


@dataclass
class SegTurn:
    """One turn as stage C sees it: its resolved reference symbols (from stage B), its timestamp,
    and whether stage A typed it a repair (a repair re-attaches, it never opens a new episode).
    `words` are the turn's content words (stage D's `topic` generator matches them against ops)."""

    symbols: tuple[str, ...]
    ts: float
    is_repair: bool = False
    words: tuple[str, ...] = ()


def _overlap_coefficient(a: set[str], b: set[str]) -> float:
    """Intersection over the smaller set -- the same shape as `match.py:_overlap`. Zero if either is
    empty (a turn that references nothing shares nothing)."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def segment_episodes(turns: list[SegTurn]) -> list[Episode]:
    """Stage C: assign each turn to an episode by the online pointer. Symbol overlap drives the
    decision -- a turn whose references overlap an open episode's mention-memory (>= floor) attaches
    to it (interleaving: it need not be the most recent episode); a turn whose references diverge
    from every open episode opens a new one, even if temporally close (divergence is the concern
    signal). A repair re-attaches to the most recently active episode. A symbol-less turn (a bare
    fragment) can't signal divergence, so it rides time continuity -- the most recent episode within
    `_CONTINUITY_HORIZON_S`, else a new one. Pure and deterministic; input order is preserved."""
    episodes: list[Episode] = []

    def _open(i: int, t: SegTurn) -> None:
        episodes.append(Episode(turns=[i], symbols=set(t.symbols), start_ts=t.ts, end_ts=t.ts,
                                words=set(t.words)))

    def _attach(e: Episode, i: int, t: SegTurn) -> None:
        e.turns.append(i)
        e.symbols |= set(t.symbols)
        e.words |= set(t.words)
        e.end_ts = t.ts

    def _best_overlap(syms: set[str]):
        best = max(episodes, key=lambda e: (_overlap_coefficient(syms, e.symbols), e.end_ts))
        return best if _overlap_coefficient(syms, best.symbols) >= _ATTACH_OVERLAP_FLOOR else None

    for i, t in enumerate(turns):
        if not episodes:
            _open(i, t)
            continue
        most_recent = max(episodes, key=lambda e: e.end_ts)
        if t.is_repair:
            # A repair never opens an episode -- it re-attaches. A *restricted*/candidate repair
            # named its target (stage B resolved it to symbols), so it re-attaches to the episode it
            # names, NOT blindly the most recent one ("no wait, the parser one" corrects the parser
            # concern even while a later fetch concern is active). An *open* repair ("revert that")
            # resolved no target, so it falls back to the most recently active episode.
            target = _best_overlap(set(t.symbols)) if t.symbols else None
            _attach(target or most_recent, i, t)
            continue
        if not t.symbols:
            if t.ts - most_recent.end_ts <= _CONTINUITY_HORIZON_S:
                _attach(most_recent, i, t)
            else:
                _open(i, t)
            continue
        # Best symbol overlap (ties toward the more recently active episode); a turn whose
        # references diverge from every open episode opens a new one.
        target = _best_overlap(set(t.symbols))
        _attach(target, i, t) if target else _open(i, t)
    return episodes


# --- Stage D: candidate generation (blocking) ------------------------------------------------
#
# Design §3.2-D: for each episode, the candidate op set -- recall-first, precision comes later (E/F).
# An *ensemble* of cheap generators, unioned: a single "best" retriever starves the scorer of the
# true link (the RRF lesson; FRLink's filter-before-score). This is the entity-resolution blocking
# stage (Magellan/Ditto): its only job is to not drop true pairs.
#
# Three generators, unioned:
#  - *symbol*: ops whose footprint overlaps the episode's resolved references, joined by the lenient
#    `rationale._symbol_matches` (the same structured `path::name` join the ledger uses). This join
#    only fires on *qualified* mentions -- a bare prose token ("parser") cannot match a structured
#    `pkg/parser.py::parse`, exactly the design's rule that bare common tokens give temporal-grade
#    evidence only, never a symbol mention.
#  - *temporal*: ops minted within the episode's span (padded), for the bare-reference and
#    no-reference turns the symbol generator can't reach.
#  - *requires*: ops one `requires`-hop from an already-surfaced op, via a caller-supplied adjacency
#    (the store's requires graph) -- structural recall for an op the episode never named.
#
# Pure over a minimal op view (`CandidateOp`): the store adapter that maps real `core.op.Op`s (with
# provenance-derived timestamps and the requires graph) into this view is wiring, deferred with the
# rest of the pipeline hook-up so this stage stays unit-testable against synthetic ops.

from sgt.intent.rationale import _symbol_matches

_TEMPORAL_PAD_S = _CONTINUITY_HORIZON_S  # slack around an episode's span for temporally-adjacent ops

# --- topic generator: aboutness for vague/typo prose -----------------------------------------
#
# `symbol` fires only on an exact qualname mention, which real prompts never contain ("make the
# search better", not "add find_by_keyword to search.py"). `topic` is the softer aboutness signal:
# a prompt's content words matched against a change's identifier subwords (what it is *named* --
# NOT the file basename, which a whole file shares), tolerant of typos via `difflib` close-match.
# It joins `symbol` in `_ABOUTNESS` so a
# topic-anchored pair is *eligible* to ALIGN -- the scorer still decides whether it actually does,
# so a coincidental common word earns low FS weight and stays in REVIEW, not a false ALIGN.

_TOPIC_MIN_LEN = 4  # tokens shorter than this carry no topic signal ("bug", "the", "fix")
_TOPIC_CLOSE_RATIO = 0.85  # difflib ratio for a typo to count as a match ("keword" ~ "keyword")
# Dev-filler words that survive the length floor but denote no code entity -- dropped from both
# sides so they never anchor. Deliberately conservative: only clearly non-topical prose/verbs.
_TOPIC_STOP = frozenset({
    "make", "fix", "add", "update", "change", "changed", "better", "thing", "things", "stuff",
    "code", "work", "works", "working", "need", "want", "please", "should", "could", "would",
    "this", "that", "these", "those", "with", "your", "just", "some", "then", "here", "there",
    "when", "what", "have", "been", "also", "like", "really", "actually", "kinda", "yeah",
    "okay", "great", "thanks", "good", "into", "from", "about", "more", "less", "very", "still",
    "feels", "feel", "seems", "look", "looks", "does", "doing", "done", "them", "they",
})


def _topic_tokens(raw: str) -> frozenset[str]:
    """Split `raw` into lowercased alpha tokens, breaking camelCase and any non-alpha (so `_`, `.`,
    `/`, `::` all separate), then keep the ones long enough and not dev-filler. Shared by both
    sides -- the prompt's prose and an op's footprint symbols -- so they tokenize identically."""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", raw)  # camelCase boundary -> space
    toks = re.findall(r"[A-Za-z]+", spaced)
    return frozenset(t.lower() for t in toks
                     if len(t) >= _TOPIC_MIN_LEN and t.lower() not in _TOPIC_STOP)


def _content_words(text: str) -> frozenset[str]:
    """The content words of a prompt turn (its `topic` firing set): normalize (lowercase/strip) then
    tokenize. Contentless asks ("fix the bug") reduce to the empty set -- no topic anchor."""
    return _topic_tokens(_normalize(text))


def _op_tokens(symbols: frozenset[str]) -> frozenset[str]:
    """An op's topic vocabulary, from the *identifier* (qualname) subwords of its footprint symbol
    ids (`bm/search.py::find_by_keyword` -> find/keyword). The file-path segment is deliberately
    dropped: a basename ("search") is shared by every symbol in the file, so anchoring on it would
    blast a whole file's ops from one vague mention -- the identifier is what a change is *named*,
    and that is the discriminating topical evidence. The miner's pseudo-symbols (`__residue__`,
    `__anchor__`, the `\\x00HEAD\\x00` sentinel) carry no human topic, so they're stripped first."""
    quals: list[str] = []
    for s in symbols:
        for marker in ("__residue__", "__anchor__"):
            s = s.replace(marker, "")
        s = s.replace("\x00HEAD\x00", "").replace("\x00", "")
        quals.extend(s.split("::")[1:])  # drop the file-path segment; keep the qualname(s) only
    return _topic_tokens(" ".join(quals))


def _topic_matches(words: set[str], op_tokens: frozenset[str]) -> bool:
    """Does any prompt content word match an op token, exactly or by typo-close difflib ratio?"""
    if not words or not op_tokens:
        return False
    for w in words:
        if w in op_tokens:
            return True
        if any(difflib.SequenceMatcher(None, w, t).ratio() >= _TOPIC_CLOSE_RATIO for t in op_tokens):
            return True
    return False


@dataclass(frozen=True)
class CandidateOp:
    """The minimal view of an op stage D blocks over: its id, its footprint symbol ids, and its mint
    time (the min of its provenance commits' timestamps in the real adapter; `None` when unknown,
    which simply withholds it from the temporal generator)."""

    id: str
    symbols: frozenset[str]
    ts: float | None = None


@dataclass(frozen=True)
class Candidate:
    """One (episode, op) candidate pair surfaced by stage D. `generators` records which cheap
    generators fired -- carried forward so stage E reads them as labeling-function firings rather
    than recomputing the blocking work."""

    op_id: str
    generators: frozenset[str]


def generate_candidates(episode: Episode, ops: list[CandidateOp], *,
                        requires_adj: dict[str, set[str]] | None = None,
                        temporal_pad_s: float = _TEMPORAL_PAD_S) -> list[Candidate]:
    """Stage D: the recall-first candidate op set for one episode, from the unioned generators
    above. Returns one `Candidate` per surfaced op, sorted by op id for determinism, tagged with the
    generators that surfaced it. Recall-first by construction: an op is kept if *any* generator
    fires -- precision is E/F's job, not this stage's."""
    gens: dict[str, set[str]] = {}
    ep_syms = set(episode.symbols)
    ep_words = set(episode.words)
    lo, hi = episode.start_ts - temporal_pad_s, episode.end_ts + temporal_pad_s
    for op in ops:
        if ep_syms and any(_symbol_matches(q, m) for q in ep_syms for m in op.symbols):
            gens.setdefault(op.id, set()).add("symbol")
        if ep_words and _topic_matches(ep_words, _op_tokens(op.symbols)):
            gens.setdefault(op.id, set()).add("topic")
        if op.ts is not None and lo <= op.ts <= hi:
            gens.setdefault(op.id, set()).add("temporal")
    # requires-hop: expand from the directly-surfaced seed set (one hop, both directions handled by
    # the caller's adjacency), so a structurally-linked op the episode never named still competes.
    if requires_adj:
        for oid in list(gens):
            for nb in requires_adj.get(oid, ()):
                gens.setdefault(nb, set()).add("requires")
    return [Candidate(op_id=oid, generators=frozenset(g)) for oid, g in sorted(gens.items())]


# --- Stage E: Fellegi--Sunter scoring with learned signal reliabilities ----------------------
#
# Design §3.2-E: each signal is a *labeling function* voting per (episode/turn, op) candidate pair,
# not a rung to fall through. Combination is Fellegi--Sunter record linkage (JASA 1969): each signal
# i has m_i = P(fires | true match) and u_i = P(fires | non-match); a pair's matching weight is the
# sum of log-likelihood ratios over the signals -- log(m_i/u_i) where the signal fired, log((1-m_i)/
# (1-u_i)) where it did not (the full FS weight, both agreement and disagreement).
#
# The reliabilities are learned WITHOUT any labels, from the agreement/disagreement structure of the
# signals over the unlabeled candidate pool -- FS's own EM (Winkler): a latent match/non-match class
# per pair, a naive-Bayes emission model per signal, iterated to a fixed point. Zero ground truth
# required -- which is exactly what fits sgt's cold start (a corpus recalibrates it, §6, but is not
# needed to fit). Identifiability (EM's label-switching failure) is pinned by the FS convention that
# a firing signal is evidence *for* a match: initialize every m_i > u_i.
#
# Two required disciplines. (i) *Complexity conditioning*: the session's concern count (from stage
# C's episode structure) discounts every score -- a tangled session's evidence is weaker per the
# Herzig--Zeller decay curve; applied here as `score_pair(..., concern_count=)`. (ii) *Correlation
# correction*: signals that read the same evidence (symbol-mention and the LLM judgment) must not be
# summed as independent -- deferred, because that correlated pair does not co-occur until the LLM
# stage (G) ships; the naive-Bayes fit is exact while every present signal reads distinct evidence
# (key/temporal/symbol/requires). Calibration of the score->probability map (temperature scaling) is
# stage F's concern.

_EM_ITERS = 100  # EM is cheap (a handful of signals over the pool) and converges well within this
_EM_TOL = 1e-6  # stop early once the class prior stops moving
_PROB_EPS = 1e-3  # clamp m/u off {0,1} so a never/always-firing signal yields a finite weight
_COMPLEXITY_LAMBDA = 0.15  # per-extra-concern score discount (complexity conditioning)


@dataclass
class Reliabilities:
    """The fitted label model: `prior` = P(a candidate pair is a true match), and per-signal `m`
    (P(fires | match)) and `u` (P(fires | non-match)). `signals` is the fixed vocabulary the fit
    ran over -- a signal absent from a pair's firing set is scored as *not fired*, not abstained, so
    its disagreement weight still applies (a true match usually leaves a symbol mention; its absence
    is mild evidence against)."""

    prior: float
    m: dict[str, float]
    u: dict[str, float]
    signals: tuple[str, ...] = ()


def _clamp(p: float) -> float:
    return min(1.0 - _PROB_EPS, max(_PROB_EPS, p))


def fit_reliabilities(pool: list[frozenset[str]], *, signals=None) -> Reliabilities:
    """Learn m/u and the class prior from the firing patterns of the unlabeled candidate `pool`
    (each pair's set of fired signal names) by Fellegi--Sunter EM. Deterministic: fixed init
    (m=0.9 > u=0.2, prior=0.2 -- the m>u convention pins the match class), fixed iteration cap. An
    empty pool returns the init priors unchanged (nothing to learn from)."""
    names = tuple(signals) if signals is not None else tuple(sorted({s for p in pool for s in p}))
    m = {s: 0.9 for s in names}
    u = {s: 0.2 for s in names}
    prior = 0.2
    if not pool or not names:
        return Reliabilities(prior=prior, m=m, u=u, signals=names)

    for _ in range(_EM_ITERS):
        # E-step: posterior P(match | pattern) per pair, naive-Bayes over the signals.
        posts = []
        for pat in pool:
            ll_m = math.log(_clamp(prior))
            ll_u = math.log(_clamp(1 - prior))
            for s in names:
                fired = s in pat
                ll_m += math.log(m[s] if fired else 1 - m[s])
                ll_u += math.log(u[s] if fired else 1 - u[s])
            posts.append(1.0 / (1.0 + math.exp(ll_u - ll_m)))  # sigmoid(ll_m - ll_u)
        # M-step: re-estimate prior and each signal's m/u as posterior-weighted firing rates.
        wsum = sum(posts)
        new_prior = wsum / len(posts)
        nm, nu = {}, {}
        neg = len(posts) - wsum
        for s in names:
            fm = sum(p for p, pat in zip(posts, pool) if s in pat)
            fu = sum((1 - p) for p, pat in zip(posts, pool) if s in pat)
            nm[s] = _clamp(fm / wsum) if wsum > 0 else m[s]
            nu[s] = _clamp(fu / neg) if neg > 0 else u[s]
        moved = abs(new_prior - prior)
        prior, m, u = _clamp(new_prior), nm, nu
        if moved < _EM_TOL:
            break
    return Reliabilities(prior=prior, m=m, u=u, signals=names)


def score_pair(fired: frozenset[str], rel: Reliabilities, *, concern_count: int = 1) -> float:
    """The Fellegi--Sunter matching weight for one candidate pair: the summed per-signal
    log-likelihood ratio (agreement weight log(m/u) where the signal fired, disagreement weight
    log((1-m)/(1-u)) where it did not), discounted by session complexity. The prior is *not* folded
    in -- it is the threshold's concern (stage F); this is the evidence weight alone. Higher = more
    match-like; a non-discriminating signal (m~=u) contributes ~0 by construction."""
    weight = 0.0
    for s in rel.signals:
        m_s, u_s = rel.m[s], rel.u[s]
        if s in fired:
            weight += math.log(m_s / u_s)
        else:
            weight += math.log((1 - m_s) / (1 - u_s))
    discount = 1.0 / (1.0 + _COMPLEXITY_LAMBDA * max(0, concern_count - 1))
    return weight * discount


def posterior(fired: frozenset[str], rel: Reliabilities, *, concern_count: int = 1) -> float:
    """The calibrated-ish match probability for a pair: the fitted prior combined with the FS
    matching weight through a logistic link. Used by stage F to place the decision bars from the
    fitted posteriors at cold start (before conformal/selective-prediction calibration)."""
    logit_prior = math.log(_clamp(rel.prior) / _clamp(1 - rel.prior))
    return 1.0 / (1.0 + math.exp(-(logit_prior + score_pair(fired, rel, concern_count=concern_count))))


# --- Stage F: three-region decision with abstention ------------------------------------------
#
# Design §3.2-F: Fellegi--Sunter's decision rule is three-region, and that structure is the point:
#  - ALIGN (posterior above the upper bar): the edge is written.
#  - REVIEW (between the bars): no edge -- the pair feeds G's adjudication queue and §6's evaluation
#    sampling. This is the *abstention* band: the model declines rather than guess.
#  - NO_ALIGN (below the lower bar): nothing written; the turn contributes to the residual (§3.4).
#
# The bars are not hand-picked constants in the mature system: once corrections accumulate they are
# set by selective prediction with a risk target (Geifman & El-Yaniv) / split-conformal quantiles --
# "at most X% of accepted alignments wrong" is a parameter we *set*. That path needs a calibration
# set we do not have at cold start, so it is deferred; here the bars default to the fitted-posterior
# scale (a clear majority to review, high confidence to align) and are overridable, exactly the
# cold-start behavior the design specifies. Temperature scaling of the score->probability map is
# likewise deferred (most data-efficient, but still needs held-out data).

ALIGN = "align"
REVIEW = "review"
NO_ALIGN = "no_align"

_COLD_ALIGN_BAR = 0.75  # cold-start upper bar on the posterior; replaced by a conformal quantile
_COLD_NO_ALIGN_BAR = 0.5  # cold-start lower bar; below this the pair is residual, not reviewed


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate pair after stages E+F: its FS matching weight, its posterior, and the region it
    fell in. Only `region == ALIGN` writes an edge; `REVIEW` abstains into G's queue; `NO_ALIGN`
    is residual."""

    op_id: str
    score: float
    posterior: float
    region: str


def decide(p: float, *, align_bar: float = _COLD_ALIGN_BAR,
           no_align_bar: float = _COLD_NO_ALIGN_BAR) -> str:
    """The three-region rule over a pair's posterior `p`: ALIGN at or above the upper bar, NO_ALIGN
    at or below the lower bar, REVIEW (abstain) in between. Boundaries resolve toward a decision
    (>= upper -> ALIGN, <= lower -> NO_ALIGN) so the review band is the open interval between."""
    if p >= align_bar:
        return ALIGN
    if p <= no_align_bar:
        return NO_ALIGN
    return REVIEW


# The aboutness signals: generators that constitute evidence the user's words *referred to* the op
# (not merely co-occurred with it). At least one must fire for a pair to ALIGN (see the gate below).
_ABOUTNESS = frozenset({"symbol", "topic"})


def align_candidates(candidates: list[Candidate], *, concern_count: int = 1,
                     rel: Reliabilities | None = None, align_bar: float = _COLD_ALIGN_BAR,
                     no_align_bar: float = _COLD_NO_ALIGN_BAR) -> list[ScoredCandidate]:
    """The end-to-end E+F pass over one session's candidate pool: fit the label model from the
    candidates' own firing patterns (unless a `rel` fitted elsewhere is supplied), score each pair,
    and place it in a region. `concern_count` is the session's episode count (stage C) for
    complexity conditioning. Order follows the input."""
    rel = rel or fit_reliabilities([c.generators for c in candidates])
    out = []
    for c in candidates:
        s = score_pair(c.generators, rel, concern_count=concern_count)
        p = posterior(c.generators, rel, concern_count=concern_count)
        region = decide(p, align_bar=align_bar, no_align_bar=no_align_bar)
        # An aboutness anchor is NECESSARY for ALIGN: the user's words must have referred to the op,
        # by an exact symbol mention (`symbol`) or a topical content-word match (`topic`).
        # Temporal/requires corroboration alone can score arbitrarily high once EM labels `requires`
        # discriminating (a requires-hop off a temporal-only seed in a dense fresh repo), but an
        # episode that referred to nothing pointed at nothing -- writing an edge would fabricate
        # intent. Such a pair caps at REVIEW (feeds the review pile / G's queue) rather than ALIGN.
        if region == ALIGN and not (c.generators & _ABOUTNESS):
            region = REVIEW
        out.append(ScoredCandidate(op_id=c.op_id, score=s, posterior=p, region=region))
    return out
