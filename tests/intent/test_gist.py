"""The ask inside a real prompt.

Every surface that shows captured words -- a checkpoint's name, a recorded reason, `sgt now`'s
current task -- used to show the prompt's first *line*, which is only the ask when the prompt was
typed like a commit message. Real prompts are not: they open with "so i think we should probably",
they run three requests together with commas, they carry the reasoning after the request, and a
long one has no line break at all, so "the first line" is the whole paragraph.

This suite is the specimen collection. Every prompt here is written the way the dogfood turn store
has them -- typos left in, no capitals, run-on clauses -- because a gist that only works on tidy
input is a gist that works on the input nobody types.
"""

from __future__ import annotations

from sgt.intent.gist import ask_gist

# The prompt that prompted this module, near enough verbatim: two asks, a clarification, three
# qualifications, no full stop anywhere, 900 characters of it.
_LONG = (
    "so I would like to include that in the study bundle, though the repository was built when "
    "the hooks not active, but I guess we can just add is post hoc, we can simulate the prompt "
    "that was written, and making sure they correctly being parsed (not the raw) and then "
    "allocated and correctly refelcted that by how the checkpoint named and organized? also one "
    "thing I would like to clarify is that I dont think user will really use the sgt intent edit, "
    "intent show ... they should be more like an attribute showing when users sgt show some "
    "commit or feature@checkpoint"
)


def test_the_request_survives_and_the_lead_in_does_not():
    """"so I would like to" is throat-clearing. The ask is what follows it, and a name built from
    the first 60 characters of the raw prompt is 40 characters of throat-clearing."""
    assert ask_gist(_LONG) == "include that in the study bundle"


def test_a_request_marker_beats_position():
    """The first clause is a complaint; the second is the actual instruction. A gist that always
    takes the first clause names the chapter after the symptom instead of the work."""
    text = ("hey\n\nthe event days are messing up the averages, we should keep them out of the "
            "average but still in the totals\n\nalso the chart colors are ugly")

    assert ask_gist(text) == "keep them out of the average but still in the totals"


def test_typos_are_kept_verbatim():
    """These are the user's own words or they are worth nothing: the whole claim a recorded reason
    makes is that nobody rewrote it. An excerpt may be shorter than the prompt; it may not be
    tidier."""
    text = ("so i think we shoudl proably add teh csv download thing for teh daily totals, like a "
            "link on the page? becuase the committee keeps emailing me spreadsheets and i dont "
            "wanna do it by hand anymore")

    assert ask_gist(text) == "add teh csv download thing for teh daily totals"


def test_a_question_shaped_ask_keeps_only_the_ask():
    text = "can you make the hourly page split weekday vs weekend charts pls"

    assert ask_gist(text) == "make the hourly page split weekday vs weekend charts"


def test_a_short_prompt_is_left_alone():
    """Nothing to trim is the common case for a one-line ask, and an ellipsis on a complete
    sentence is a lie about there being more."""
    assert ask_gist("mark the event days on the daily chart") == "mark the event days on the daily chart"


def test_a_prompt_that_is_only_lead_in_keeps_its_words():
    """Stripping every word would leave a chapter called "" -- worse than a clumsy name. The floor
    is: an excerpt always has words in it."""
    assert ask_gist("ok so i think") == "ok so i think"
    assert ask_gist("please") == "please"


def test_nothing_captured_is_nothing_shown():
    assert ask_gist("") == ""
    assert ask_gist("   \n  ") == ""


def test_one_long_clause_is_clipped_on_a_word_boundary_with_an_ellipsis():
    """A single 200-character clause has no seam to cut at, so the excerpt is a prefix -- and it
    has to say so, because this is the one case where the reader is seeing a fragment of a
    sentence rather than a whole one."""
    text = ("rewrite the whole daily page so it pulls from the new counter table instead of the "
            "csv and also handles the sensor gaps by interpolating across them the way the "
            "committee asked for in the march meeting")

    out = ask_gist(text, width=72)

    assert out.endswith("…")
    assert len(out) <= 72
    assert out.startswith("rewrite the whole daily page so it pulls from the new counter table")
    assert " instea…" not in out  # never mid-word


def test_trailing_filler_is_dropped():
    assert ask_gist("add a by-year summary table, etc...") == "add a by-year summary table"
    assert ask_gist("split the hourly page in two, thanks!") == "split the hourly page in two"


def test_a_code_fence_is_not_the_ask():
    """An agent-relayed prompt often carries a stack trace or a snippet. The words around it are
    the ask; the block is evidence."""
    text = ("this is failing:\n```\nTraceback (most recent call last):\n  File \"pages.py\"\n"
            "```\nfix the date window so it stops throwing on an empty range")

    assert ask_gist(text) == "fix the date window so it stops throwing on an empty range"


def test_a_slash_command_body_is_not_the_ask():
    """`/goal <text>` and friends arrive with the command as the first token. The user's words
    start after it."""
    assert ask_gist("/goal add a north v south page") == "add a north v south page"


def test_the_width_is_honoured_by_every_caller_size():
    """Three sizes exist in the UI -- a 60-char lane label, a 72-char status row, a 120-char card
    -- and the same excerpt has to degrade gracefully across all of them rather than each caller
    inventing its own truncation."""
    for width in (60, 72, 120):
        out = ask_gist(_LONG, width=width)
        assert len(out) <= width
        assert not out.endswith(" ")


# The three specimens below are real prompts out of this repo's own turn store. They are here
# because each one broke a rule that looked sound until it met them.

def test_a_list_of_asks_is_not_a_chapter_called_commit():
    """Cutting on every comma turned "commit, push, rebundle, …" into "commit". A comma only
    separates clauses once what precedes it is long enough to describe work."""
    out = ask_gist("commit, push, rebundle, update vscode extension if touched, and then deploy",
                   width=120)

    assert out == "commit, push, rebundle, update vscode extension if touched, and then deploy"


def test_a_preamble_clause_is_skipped_even_behind_a_lead_in():
    """"ok before we move on to X; I would like to Y" asks for Y. The subordinate test has to run
    after the lead-in is stripped, or the "ok" hides the "before"."""
    text = ("ok before we move on to run end to end test; I think I would like to redesign the "
            "granularity of the study task")

    assert ask_gist(text) == "redesign the granularity of the study task"


def test_a_misspelled_lead_in_still_yields_the_ask():
    """"we shoudl proably" is not in any list of openings and never will be. The pronoun in front
    of the verb is what marks it as throat-clearing."""
    text = "we got the new key, try to use the cheaper model like gpt-5.6-luna"

    assert ask_gist(text) == "use the cheaper model like gpt-5.6-luna"


def test_a_line_break_is_a_seam_a_comma_never_crosses():
    """"add a quux helper\\nand keep it tiny" is two lines, and the excerpt is the first. The
    short-tail merge that keeps "commit, push, rebundle" together used to run across the newline
    too, which came back as "add a quux helper, and keep it tiny" -- the tool putting its own
    punctuation into somebody's words."""
    assert ask_gist("add a quux helper\nand keep it tiny") == "add a quux helper"
