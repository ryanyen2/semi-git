"""The prompt tokenizer `revert`'s ledger rung borrows: content words of a phrase, and a typo-tolerant
overlap count between two token sets."""

from __future__ import annotations

import difflib
import re


def _normalize(text: str) -> str:
    """Lowercase, strip a trailing emoji/symbol run, collapse whitespace."""
    t = text.strip().lower()
    t = re.sub(r"[^\w?.!,'\s]+\s*$", "", t).strip()
    return re.sub(r"\s+", " ", t)


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


def topic_overlap(words: set[str], other: frozenset[str]) -> int:
    """How many of `words` match some token in `other`, exactly or by typo-close difflib ratio. Used
    to rank a revert phrase against the intent ledger's captured reasons."""
    n = 0
    for w in words:
        if w in other or any(
            difflib.SequenceMatcher(None, w, t).ratio() >= _TOPIC_CLOSE_RATIO for t in other
        ):
            n += 1
    return n
