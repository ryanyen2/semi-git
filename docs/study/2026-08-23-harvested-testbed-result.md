# The harvested testbed, and what it settled

Written 2026-08-23, after building a study repository by letting agents do real
work in it rather than by scripting an episode spec. Everything below is measured
output, and the commands that produce it are in `scripts/study/harvest/`.

## What was built

`bikecount` is a dashboard over the Fremont Bridge bicycle counter in Seattle,
62,030 hourly readings from 2012 to 2019, published by the city. Python standard
library only, server-rendered HTML, hand-drawn SVG. Six pages.

The seed was one commit: load the file, show the busiest day and the last two
weeks. Everything after that was harvested. Twelve agents, each given one job off
an analyst's to-do list, each working in its own `sgt session` worktree, none told
what the repository was for or that the others existed.

    bootstrap.sh   seed the repo and mine it
    harvest.sh     run roles.json, one session each
    select_target.py   measure every landed session against fixed criteria
    snap.py        render every page to diffable text
    render_git_arm.sh  strip sgt's trailers and plumbing for the git half

## The task the gate selected

Not chosen in advance. `select_target.py` tried all twelve reverts for real and
three passed; `weekday-headline` was the only one that moves a headline number
while leaving the rest of the app alone.

    2 ops · 2 symbols · 2 files · 1 commit
    symbols: counts.py::yearly_summary, pages.py::render_years
    later work in the same files: date-range, rounding
    git revert 1f50681: conflicts in bikecount/pages.py
    sgt revert: applied · smoke ok · renders ok
    pages that moved: by-year.txt (16% max number shift)

What a participant sees. The published report quotes the 2018 average day as
2,882. The dashboard says 3,432, under a column headed "Average weekday". Total
crossings and busiest day are identical, so exactly one number is wrong, and the
label is the clue. No domain knowledge is needed and no code has to be read.

Somebody had changed what "average day" meant, for a good reason, and the report
was written against the old meaning.

## What the two halves actually have to do

sgt, on the piece of work the analyst described:

    $ sgt revert --session weekday-headline --yes
      subtracted from shared code (later work kept):
        bikecount/counts.py::yearly_summary, bikecount/pages.py::render_years
      ✓ revert applied
    $ python3 check.py
      ok: ... by-year ... render          2,882 is back

git, on the commit that holds it:

    $ git revert 1f50681
    CONFLICT (content): Merge conflict in bikecount/pages.py

    <<<<<<< HEAD
    def render_years(summary, start, end):
    =======
    def render_years(summary):
    >>>>>>> parent of 1f50681

The two sides differ in two independent ways at once. `start, end` came from
`date-range`, a later piece of work that has nothing to do with the question being
asked. Resolving the conflict the obvious way, by taking the side the revert
wants, deletes that later work:

    TypeError: render_years() takes 1 positional argument but 3 were given

So the git half is not "type a command and wait". It is: notice that two unrelated
changes are tangled in one hunk, keep one and drop the other, by hand, under a
clock, with the app broken until you get it right. That is the difference the
study is trying to measure, and it arrived on its own.

## What the history refused to give

Three role prompts were written hoping for a particular outcome. None delivered it,
and that is the strongest evidence the method works.

`sidewalk-doubt` asked an agent to decide whether the two sidewalk counters could
still be trusted. The east sidewalk peaks at 8am and the west at 5pm, so dropping
either one flips the headline busiest hour from 5pm to 8am, which would have been
the most visible symptom in the dataset. The agent checked, found `total` equals
east plus west across all 62,030 hours and that the split drifts smoothly with no
sensor-fault signature, and kept both. Both claims verify against the raw file.

`quiet-days` produced a defensible threshold that moves one year by 1.3 percent,
too small to see. `denominator` then reused that threshold two sessions later, so
one intent came to live in two sessions and reverting either alone breaks the app.

Harvesting means the target is whatever survives the gate. Writing prompts until
the wanted answer appears is authoring the history slowly, and the rule has to be
the one written down first.

## What it found in sgt

Nine defects, findings 39 to 47 in `sgt-findings.md`. Four would have broken the
study; three are one-line fixes; all nine are things a person hits on day one and
none are visible if fixtures are built by scripting commits.

The two that matter most:

**Finding 43.** `sgt session start` records the base with `ideal_for_ref` and does
not mine first, while `new_op_ids`, the other side of the same subtraction, does.
On a fresh repo the base reads as empty, so the first session is credited with
every symbol in the codebase. `sgt revert --session hour-of-day` then offered to
remove 48 edits across 21 symbols instead of 11 across 8, and the preview was
honest about it, which means a participant who read it correctly would conclude the
tool is dangerous.

**Finding 47.** `sgt revert <feature>@<n>` is the sharpest thing in the tool. It
subtracts one checkpoint's contribution from symbols that later work also edited
and keeps the later work, which is precisely what git cannot do. Its preview
reports `67→69 edits` and marks the checkpoint being reverted as `kept`.

## What it found about the claim

The harvested git log needs no defending:

    1f50681 average day on the by-year page now means weekday
    d31f708 drop snowstorm-quiet days from the hour-of-day average
    facfabe add a by-year summary table

When an agent writes the code and the message, the message says what it was asked
to do, and that is the intent. Git history built this way already carries intent at
the commit level. At the same moment sgt's feature tree had collapsed nine of the
twelve sessions into one node called "Time-Based Count Summaries", and had filed
the weekday-split work under "Monthly Trend Charts".

On finding the work, git was ahead. That is C1 in the protocol, and the protocol
already says equal locate performance falsifies it.

What survives is everything above about the revert. The claim the evidence supports
is about operating on intent, not reading it. Reading is largely solved once agents
write the messages. Undoing one intent whose lines three later pieces of work have
edited is not, and that is where the two representations genuinely differ.

One consolation for the feature layer: the checkpoint names underneath it are good
("Weekday Average Day", "Drop Snowstorm-Quiet Days", "Date Range Picker"). They are
drawn from the saves' own words. The feature labels are LLM summaries of a cluster,
and summarising is the operation that throws the specificity away. The better labels
already exist; the tree is not using them.

## Still open

The twin, `footfall`, has its data prepared and nothing else. Spencer St-Collins St
in Melbourne was picked because it is the only pair that behaves like the Fremont
sidewalks, both peaking at 5pm together and at 8am on one side alone. It still
needs its own harvest and its own gate run, and its target has to pass on its own
merits rather than by analogy.

The surface pass against `legibility-rubric.md` is done for criteria 2, 3 and 4 and
not for criterion 1, which asks whether a plain-English description of the symptom
finds the right work through `sgt find`. That needs a key and a person to judge the
ranking.
