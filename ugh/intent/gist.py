"""The ask inside a captured prompt: one short line of the person's words for `ugh show` and
`ugh find`. Prompts open with lead-in ("so i think we should probably") and run several asks
together, so the excerpt starts at the ask: strip the lead-in from each clause and take the first
clause that then begins with a verb a person types at a coding agent. The words are kept exactly as
typed; an excerpt may be shorter than the prompt, never tidier. Deterministic, offline, not stored.
"""

from __future__ import annotations

import re
from typing import NamedTuple

CARD_WIDTH = 120

# Lead-in, stripped repeatedly from a clause's start only; longer phrases first, so they win.
_LEAD_IN = (
    "another thing is that", "one thing i would like to", "do you think you can",
    "i was wondering if you could", "i was wondering if", "i would like you to",
    "i would like to", "i'd like you to", "i'd like to", "i want you to", "i want to",
    "i need you to", "i need to", "we might want to", "we should probably", "we should",
    "we need to", "we could", "we want to", "we can", "you should", "you need to",
    "it would be good to", "it would be nice to", "if you can", "if possible",
    "one thing", "another thing", "the thing is", "i think", "i guess", "i feel like",
    "i wonder if", "can you please", "can you", "could you", "would you", "will you",
    "can we", "could we", "should we", "by the way", "let's", "lets", "let us", "let me",
    "try to", "try and", "go ahead and", "help me to", "help me",
    "so", "ok", "okay", "kk", "hey", "hi", "hello", "well", "now", "then", "also", "and", "but",
    "btw", "anyway", "actually", "basically", "just", "maybe", "perhaps", "please", "pls", "plz",
)

# Verbs an ask starts with. An ask outside this list falls back to the first substantial clause:
# duller, never wrong.
_ASK_VERBS = frozenset("""
add address allow annotate apply audit backfill build bump bundle cache call capture change chart
check clarify clean clear commit compare convert copy create cut debug delete deploy deprecate
describe design document draw drop enable evaluate expand explain expose extract find fix fold
format
decide give group handle hide hoist hook implement include inline install investigate keep label
limit load log make mark measure merge migrate mock move name note open order package pass
persist pick pin plot polish print profile propose publish pull push put rebuild record redesign
reduce refactor refine release remove rename render replace report restore return reuse revert
review rewrite ring round route run save scope seed send separate set ship shorten show simplify
sketch skip sort split start stop store stub suggest summarize support surface swap switch take
teach test tidy tighten track trim truncate turn update upgrade use validate verify wire wrap
write
""".split())

# A misspelled lead-in still has its pronoun: words with "i", "we" or "you" before an ask verb are
# the way to a request ("we shoudl proably add ..."); without one the clause is about the code.
_PERSON = frozenset(("i", "im", "i'm", "ive", "i've", "i'd", "id", "we", "we'd", "we've", "weve",
                     "us", "you"))
# Many ask verbs are also nouns ("in the report"); a determiner or preposition just before one
# means it is not an imperative.
_BLOCKS_JUMP = frozenset(("the", "a", "an", "this", "that", "these", "those", "my", "our", "your",
                          "its", "their", "his", "her", "of", "in", "on", "at", "for", "from",
                          "with", "by", "into", "about", "over", "under", "no", "not"))
# How many words of throat-clearing can precede the verb before the clause is no longer a request.
_MAX_PREFIX = 8

# Tokens that trail an ask without being part of it.
_TRAILING = frozenset(("etc", "etc.", "thanks", "thanx", "thx", "ty", "pls", "plz", "please",
                       "ok", "okay", "right", "yeah", "yep", "cheers", "sorry", "then"))

# A clause opening with one of these frames the request ("before we move on to X, do Y" asks Y),
# so it is skipped as a candidate but kept for the fallback.
_SUBORDINATE = ("before", "after", "once", "when", "while", "whenever", "if", "unless", "since",
                "because", "although", "though", "instead of", "rather than", "as long as",
                # explanations of a request already made
                "the reason is", "the reason being", "the problem is", "the thing is that")

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_SENTENCE = re.compile(r"[\n\r]+|(?<=[.?!;:])\s+")
_WORD = re.compile(r"[a-z']+")

# How far into a prompt an ask can hide before it is no longer the headline.
_MAX_CLAUSES = 8
# Below this a clause is a fragment ("hey", "ok wait"), not a description of work.
_MIN_CLAUSE = 12
# ...and below this a comma is not a seam, so "commit, push, rebundle" stays one clause.
_MIN_COMMA_CLAUSE = 24


class Ask(NamedTuple):
    """`gist` is what goes on screen; `trimmed` says whether the prompt held more, so a surface
    offers the rest."""

    gist: str
    trimmed: bool


def _clean(text: str) -> str:
    """The prompt with the parts that are evidence rather than words removed: fenced blocks (a
    stack trace an agent relayed), and a leading slash-command token (`/goal <text>` arrives with
    the command attached, and the user's words start after it)."""
    text = _FENCE.sub(" ", text or "")
    text = text.strip()
    if text.startswith("/"):
        head, _, rest = text.partition(" ")
        if rest.strip() and re.fullmatch(r"/[\w:-]+", head):
            text = rest.strip()
    return re.sub(r"[ \t]+", " ", text).strip()


def _strip_lead_in(clause: str) -> str:
    """`clause` with its conversational opening removed, repeatedly -- "so i would like to include
    x" is "so" + "i would like to" + the ask."""
    out = clause.strip()
    changed = True
    while changed and out:
        changed = False
        low = out.casefold()
        for phrase in _LEAD_IN:
            if low.startswith(phrase) and (len(low) == len(phrase)
                                           or not low[len(phrase)].isalnum()):
                out = out[len(phrase):].lstrip(" ,:;-").strip()
                changed = True
                break
    return out


def _drop_trailing(clause: str) -> str:
    """The tail an ask picks up on the way out: "…, thanks!", "…, etc...", "… pls"."""
    out = clause.strip()
    while out:
        stripped = out.rstrip(" .,;:!?…-")
        if stripped != out:
            out = stripped
            continue
        word = out.rsplit(" ", 1)[-1]
        if word.casefold() in _TRAILING and " " in out:
            out = out[: -len(word)].strip()
            continue
        return out
    return out


def _from_ask_verb(clause: str) -> str:
    """`clause` from its ask verb onward, or `""` if it does not read as a request.

    The verb is usually already first, because `_strip_lead_in` removed the opening. It is not when
    the opening was misspelled, so a verb a few words in is also accepted -- but only behind a
    pronoun (`_PERSON`), which is what separates "we shoudl proably add the csv link" from "the csv
    link should show daily totals"."""
    if not clause:
        return ""
    words = clause.split()
    for i, word in enumerate(words[:_MAX_PREFIX]):
        match = _WORD.match(word.casefold())
        if not match or match.group() not in _ASK_VERBS:
            continue
        if i == 0:
            return " ".join(words[i:])
        before = _WORD.match(words[i - 1].casefold())
        if before and before.group() in _BLOCKS_JUMP:
            return ""  # "…in the report is wrong": a noun, not an instruction
        prefix = [m.group() for w in words[:i] if (m := _WORD.match(w.casefold()))]
        if any(w in _PERSON for w in prefix):
            return " ".join(words[i:])
        return ""  # a verb this deep with no pronoun in front of it belongs to the sentence
    return ""


def _clip(text: str, width: int) -> tuple[str, bool]:
    """`text` bounded to `width`, cut on a word boundary and marked with an ellipsis."""
    if len(text) <= width:
        return text, False
    cut = text[: width - 1]
    space = cut.rfind(" ")
    if space > width // 3:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…", True


def _clauses(text: str) -> list[str]:
    """`text` cut into candidate clauses at newlines and sentence ends. A comma cuts only once what
    precedes it is long enough to describe work, so a list of asks ("commit, push, rebundle")
    stays one clause."""
    out: list[str] = []
    for sentence in _SENTENCE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        pieces: list[str] = []
        buf = ""
        for piece in sentence.split(", "):
            buf = f"{buf}, {piece}" if buf else piece
            if len(buf) >= _MIN_COMMA_CLAUSE:
                pieces.append(buf)
                buf = ""
        if buf:
            # a short tail joins the clause it trailed in its own sentence, for `_drop_trailing`
            if pieces:
                pieces[-1] = f"{pieces[-1]}, {buf}"
            else:
                pieces.append(buf)
        out.extend(pieces)
    return out


def _is_subordinate(clause: str) -> bool:
    low = clause.casefold()
    return any(low.startswith(w) and (len(low) == len(w) or not low[len(w)].isalnum())
               for w in _SUBORDINATE)


def ask_parts(text: str, width: int = CARD_WIDTH) -> Ask:
    """The excerpt of `text` and whether the prompt held more than it: the first clause that reads
    as a request, verbatim, with its conversational opening and trailing filler removed and clipped
    to `width`. An empty excerpt for nothing captured. Never a paraphrase, never re-cased, never
    spell-corrected."""
    cleaned = _clean(text)
    if not cleaned:
        return Ask("", False)

    clauses = _clauses(cleaned)
    chosen = ""
    for clause in clauses[:_MAX_CLAUSES]:
        opened = _strip_lead_in(clause)
        if _is_subordinate(opened):  # after the lead-in: "ok before we move on to X, do Y" asks Y
            continue
        stripped = _from_ask_verb(_drop_trailing(opened))
        if stripped:
            chosen = stripped
            break
    if not chosen:
        # no clause opens with an ask: the first substantial one without its lead-in, else as typed
        chosen = next((c for c in clauses if len(c) >= _MIN_CLAUSE), clauses[0] if clauses else "")
        opened = _strip_lead_in(chosen)
        chosen = _drop_trailing(opened if len(opened) >= _MIN_CLAUSE else chosen)

    gist, clipped = _clip(chosen, width)
    return Ask(gist, clipped or gist.casefold() != _drop_trailing(cleaned).casefold())


