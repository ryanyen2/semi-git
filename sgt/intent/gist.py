"""The ask inside a captured prompt.

Everything that shows captured words -- a checkpoint's name, a recorded reason, `sgt now`'s current
task, the `asked` attribute on `sgt show` -- needs one short line to put on screen. Until now that
line was the prompt's first *line* (`sgt.intent.working._first_line`), which is the ask only when
the prompt was typed like a commit message. The dogfood turn store says they are not: prompts open
with "so i think we should probably", carry the reasoning after the request, run three asks together
with commas, and a long one has no line break at all -- so "the first line" is the whole paragraph,
and a 60-character name built from it is 40 characters of throat-clearing.

So the excerpt starts at the ask. One rule: strip the conversational lead-in from each clause, and
take the first clause that then begins with a verb someone would actually type at a coding agent.
Nothing else about the words changes -- typos, casing and grammar are kept exactly as typed, because
the entire claim a recorded reason makes is that nobody rewrote it. An excerpt may be shorter than
the prompt; it may never be tidier than the prompt.

Deliberately deterministic and offline, for the same reason `sgt show` is: this runs inside read
paths a cautious user repeats, and a name that came back different on the second read would be
worse than a clumsy one. Nothing here is persisted either -- `apply_words_labels` applies it at
read time, so a verbatim prompt never leaks into shared state through a label.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# What a status row, a lane label and a card can hold. One place, because the same excerpt has to
# degrade across all three rather than each surface inventing its own truncation.
ROW_WIDTH = 72
LABEL_WIDTH = 60
CARD_WIDTH = 120

# Throat-clearing: matched at a clause's start, stripped repeatedly. Longest first so "i would like
# to" wins over "i", and "let's" over "let". These only ever remove a PREFIX -- no phrase in this
# list can eat a word from the middle of an ask.
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

# The imperative vocabulary people type at a coding agent. An excerpt starts here. A prompt whose
# ask uses a verb outside this list falls back to its first substantial clause -- a duller name,
# never a wrong one, which is the right way round for a list that can never be complete.
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

# The lead-in list cannot cover how people actually spell it -- "we shoudl proably add …" is one
# typo away from "we should probably" and a list of misspellings has no end. What survives a typo is
# the pronoun: words before an ask verb that include an "i"/"we"/"you" are somebody talking about
# themselves on the way to the request, whatever the words between are. A clause with no pronoun in
# front of the verb ("the csv link should show daily totals") is a statement about the code and
# keeps its subject.
_PERSON = frozenset(("i", "im", "i'm", "ive", "i've", "i'd", "id", "we", "we'd", "we've", "weve",
                     "us", "you"))
# ...and what cannot sit directly in front of it. Half the ask vocabulary is also an ordinary noun
# -- report, record, log, order, name, check, review, plot -- so "the quiet-day figure we quoted in
# the report is basically wrong" contains "report" behind a pronoun and read as a request to report
# something. Nobody writes "the" or "of" in front of an imperative, so a determiner or preposition
# immediately before the verb rules the clause out and the real ask later in the prompt wins.
_BLOCKS_JUMP = frozenset(("the", "a", "an", "this", "that", "these", "those", "my", "our", "your",
                          "its", "their", "his", "her", "of", "in", "on", "at", "for", "from",
                          "with", "by", "into", "about", "over", "under", "no", "not"))
# How many words of throat-clearing can precede the verb before the clause is no longer a request.
_MAX_PREFIX = 8

# Tokens that trail an ask without being part of it.
_TRAILING = frozenset(("etc", "etc.", "thanks", "thanx", "thx", "ty", "pls", "plz", "please",
                       "ok", "okay", "right", "yeah", "yep", "cheers", "sorry", "then"))

# "before we move on to X, I would like to Y" asks for Y. A clause opening with one of these is
# the frame around the request, so it is passed over as a candidate -- still available to the
# fallback, which is what keeps a prompt made only of such clauses from coming back empty.
_SUBORDINATE = ("before", "after", "once", "when", "while", "whenever", "if", "unless", "since",
                "because", "although", "though", "instead of", "rather than", "as long as",
                # Explanations of a request already made. "the reason is that i get asked for these
                # numbers twice a week" is why the csv download was wanted, not the ask for it.
                "the reason is", "the reason being", "the problem is", "the thing is that")

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_SENTENCE = re.compile(r"[\n\r]+|(?<=[.?!;:])\s+")
_WORD = re.compile(r"[a-z']+")

# How far into a prompt an ask can hide before it is no longer the headline.
_MAX_CLAUSES = 8
# Below this a clause is a fragment ("hey", "ok wait"), not a description of work.
_MIN_CLAUSE = 12
# ...and below this a comma is not a seam at all, so "commit, push, rebundle" stays one clause
# rather than becoming a chapter called "commit".
_MIN_COMMA_CLAUSE = 24


class Ask(NamedTuple):
    """`gist` is what goes on screen; `trimmed` says whether the prompt held more than this, which
    is what tells a surface to offer the rest rather than implying the excerpt is the whole ask."""

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
    """`text` bounded to `width`, cut on a word boundary and marked. Mid-word truncation is what
    makes a trimmed line read as corrupted rather than as an excerpt."""
    if len(text) <= width:
        return text, False
    cut = text[: width - 1]
    space = cut.rfind(" ")
    if space > width // 3:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-") + "…", True


def _clauses(text: str) -> list[str]:
    """`text` cut into candidate clauses.

    A newline or a sentence end is a real seam. A comma is not, quite: it separates clauses in a
    run-on ("add the csv link, the committee keeps asking") and it separates items in a list of
    asks ("commit, push, rebundle, then deploy"), and cutting on every comma turns the second into
    a chapter called "commit". So a comma only cuts once what precedes it is long enough to be a
    description of work; below that the pieces are joined back up."""
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
            # A short tail ("…, thanks", "…, etc") belongs to the clause it trailed, where
            # `_drop_trailing` will take it off, rather than standing as a clause of its own --
            # but only within its own sentence. Merged across the sentence break, "add a quux
            # helper\nand keep it tiny" came back as one comma-joined clause, which is the tool
            # putting punctuation into somebody's words.
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


def ask_parts(text: str, width: int = ROW_WIDTH) -> Ask:
    """The excerpt and whether the prompt held more than it. See `ask_gist` for the rule."""
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
        # No clause opens with an ask -- a question, a complaint, a paste. Fall back to the first
        # one with enough in it to describe work, still without its conversational opening, and
        # only then to the prompt as typed: an excerpt always has words in it.
        chosen = next((c for c in clauses if len(c) >= _MIN_CLAUSE), clauses[0] if clauses else "")
        opened = _strip_lead_in(chosen)
        chosen = _drop_trailing(opened if len(opened) >= _MIN_CLAUSE else chosen)

    gist, clipped = _clip(chosen, width)
    return Ask(gist, clipped or gist.casefold() != _drop_trailing(cleaned).casefold())


def ask_gist(text: str, width: int = ROW_WIDTH) -> str:
    """One line of `text` to put on screen: the first clause that reads as a request, verbatim,
    with its conversational opening and trailing filler removed and clipped to `width`.

    `""` for nothing captured. Never a paraphrase, never re-cased, never spell-corrected."""
    return ask_parts(text, width).gist
