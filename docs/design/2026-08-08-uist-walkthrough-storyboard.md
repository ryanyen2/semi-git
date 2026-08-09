# The spec you hold and the history git keeps

A system walkthrough for the paper. One maintainer, one release, one afternoon of reckoning.

Everything on screen here is a surface that exists today. Where a beat leans on something that is
only partly built, it says so in the fidelity notes at the end, rather than pretending.

---

## The claim this walkthrough has to prove

Version control records **what changed**. A maintainer holds **what was supposed to change**. Those
two things used to drift apart slowly, at the speed a person can type. An LLM writes more code in an
afternoon than its reviewer reads in a week, so now they drift apart faster than anyone can check.

The gap has a specific shape, and it is not "the agent wrote a bug." It is three separate questions,
and git can answer none of them because git never held the intent:

1. Did the work I asked for actually land?
2. Did work land that I did not ask for?
3. Why is this code the way it is, given that the commit message lies?

sgt can answer all three because the plan and the history are recorded in the **same unit**: a named
symbol. A question asked about `_keep_creator` gets an answer about `_keep_creator`.

The last beat is the one that should land hardest with reviewers, so it is worth stating plainly up
front: **when one agent writes both the code and its tests, a green test suite is circular.** The
suite agrees with the code because the same author wrote both. The only thing in the room that did
not come from the agent is the spec the human wrote down before any code existed. sgt makes that
spec a checkable object. That is the whole idea.

---

## The person

Dana maintains `sanitag`, a Python library that strips patient-identifying information out of
medical scan files before researchers are allowed to share them. Six years old, about 40
contributors, a few hundred research groups depend on it. Dana's background is medical imaging, not
tooling. They know the standard cold, which is the reason people trust the library.

The standard is a table. It lists every field in a scan file and says what has to happen to it:
delete it, or replace it with a harmless placeholder. When the standard is revised, Dana's job is to
walk the table and make the code match it.

Here is Dana, in their own words, on why they are trying a new tool:

> Last time I did a revision I used Claude for it and it went fine, mostly. Two afternoons instead
> of two weeks. Then four months later a group in Utrecht emailed me because a scan they had shared
> publicly still had the referring doctor's name in it. It was in a private field. Somewhere in
> those two afternoons the model had widened a condition so private fields got preserved instead of
> dropped. It was a two line change sitting in a commit called "tidy up anonymizer helpers" next to
> forty other edits I did want. I only found it because someone got hurt first.
>
> The thing that bothers me is not that the model made a mistake. It is that I had the correct spec
> open on my other monitor the entire time, and there was no way to ask whether the code matched it.
> Git will tell me every line that changed. It will not tell me that a line changed which I never
> asked to change. Those are different questions and only one of them has a command.

This is the revision cycle right after that email.

---

## Scene 0. Adopting it on a six year old repo

Dana has 3,100 commits of history they are not willing to rewrite for a tool they have not decided
to trust yet.

```
$ sgt init
✓ initialized sgt kernel in . (.sgt/ + git)
✓ installed Claude Code prompt hook (.claude/settings.local.json) — your prompts become local
  intent evidence; remove the UserPromptSubmit entry to opt out
✓ installed Claude Code edit hook — each Edit/Write becomes a live activity event `sgt now` surfaces
```

Nothing was rewritten, nothing was checked out, no branch moved. sgt read the existing history and
broke each commit into per-symbol edits behind it. The working tree is byte for byte what it was.

Dana opens VS Code. Every line of `profiles.py` now has a faint colored tint and a stripe in the
gutter. Hovering says which piece of functionality owns that line.

The first thing Dana does is disagree with it. Two of the auto-named groups are wrong: the date
handling and the UID remapping got lumped together, and one group is called `helpers.py` which is a
filename, not a feature.

This matters for the paper, so do not hide it. Dana right-clicks in the **Features** view and picks
**Split**, then **Rename** on the other. Three clicks. The tint updates.

```
$ sgt log --tree
▾ Field handling                        4 feature(s)
  ● date shifting              12 symbol(s)
  ● UID remapping               8 symbol(s)
  ...
```

The grouping is a label on top of the history, never the history itself. Getting it wrong costs a
rename. This is why Dana is willing to keep going: the tool guessed, was partly wrong, said so in a
way they could see, and the repair was free.

**Why this beat is here.** Adoption cost is where research tools die. Six years of history, zero
migration, and the one thing the tool is allowed to be wrong about is the one thing that is cheap to
fix.

---

## Scene 1. Writing the spec down where the tool can check it

Dana has the revised table in front of them. Thirty-one changes. Instead of just starting the agent,
they state it first:

```
$ sgt plan intake "The 2024 revision changes the basic profile. Thirty-one changes.
For each field in the table, either delete it or replace it with a placeholder.
Three fields move from delete to replace: referring physician name, patient phone,
operator name. Do not touch the private field handling, that is a separate discussion."

✓ intake: session 41c9e2b8 — 31 step(s)
    Replace referring physician name with a placeholder   [field handling]
    Replace patient phone with a placeholder              [field handling]
    Replace operator name with a placeholder              [field handling]
    Delete institution department name                    [field handling]
    ...
```

Each step now has a name and a **predicted footprint**: which symbols sgt expects the work to land
in. Nothing is enforced. Nothing is checked out. This is a written-down expectation, in the same
vocabulary the history is recorded in.

Two things about that last sentence in the plan. Dana wrote "do not touch the private field
handling" because of Utrecht. It is an ordinary sentence in an ordinary instruction. It is also, four
hours from now, the sentence that makes an entire class of bug findable.

**Why git/jj cannot be here yet.** Neither tool has a place to put this. A branch name is not a
spec, and a commit message is written after the fact, by whoever wrote the code, which in this case
is the thing being checked.

---

## Scene 2. Two afternoons

Dana runs Claude Code against the plan and goes back to their actual job. The agent works across
`profiles.py`, `fields.py`, `_dates.py`, `_private_blocks.py`, and the test suite. Roughly 340
symbol-level edits over two sessions.

Dana does not watch. Nobody watches. That is the honest version of how this is used, and any
walkthrough that shows a human reading an agent's diff in real time is describing something that
does not happen.

At the end, the agent reports all thirty-one items complete. `pytest` is green, 1,240 passed.

Dana has been here before and does not believe it.

---

## Scene 3. The reckoning

Dana opens the **Composition Workbench**.

The screen is one horizontal time axis. Each piece of functionality is a lane. Every save is a card
on its lane, at the moment it happened, colored by which feature it belongs to. Two afternoons of
agent work is a dense band of cards on the right.

Then there is a vertical rule labeled `now`, and past it, a faintly washed region with a few more
cards in it. Those cards are dashed and hollow. They sit to the right of `now` because they have not
happened.

```
  ▾ Field handling                                                  now
    ● field handling          ▃▅█▇▅▇█▇▆        ┊  ◇ Replace referring physician…
    ● date shifting           ▂▄▆█▃            ┊  ◇ Replace patient phone…
    ● private blocks          ·▁▃              ┊
```

That is the first answer, and Dana gets it without reading anything. **Two of the thirty-one things
they asked for have no code behind them.** The cards are still in the "not yet" region. The agent
said they were done. The test suite says everything passes.

Dana clicks the first hollow card. The inspector shows the step, its predicted footprint, and the
fact that no edit was ever recorded against those symbols.

Then Dana opens the test the agent wrote for it:

```python
def test_referring_physician_removed():
    out = sanitag.clean(scan)
    assert "ReferringPhysicianName" not in out
```

That assertion passes. It has always passed. It passed before the revision, because the old behavior
was to delete the field, and it will keep passing forever, because it encodes the **old** spec. The
new spec says the field must be present and hold a placeholder. The test cannot tell the two apart.

This is the beat to put on the slide. The suite is green and the suite is useless, because the agent
wrote the code and the test in the same breath, from the same understanding, and they agree with
each other. sgt is not smarter than the agent about medical imaging. It is holding the one artifact
the agent did not author: Dana's sentence from Scene 1.

The status bar reads `29/31 steps · 1 stalled`. Dana clicks it, and it offers to reopen the exact
conversation that was building the plan, by session id, rather than starting a fresh one that has
lost the context.

Now the second answer. Dana opens **Changes** in the sidebar:

```
▾ Unplanned changes (5)
    extend:  fields.py::_normalize_vr
    extend:  profiles.py::_load_table
    extend:  _private_blocks.py::_keep_creator
    add:     tests/test_private.py::test_creator_preserved
    extend:  docs/profiles.md
▾ Untracked files (0)
```

Five edits nobody asked for. This list is not a diff. A diff of two afternoons is 340 edits and Dana
would skim it. This is the five that fall outside anything Dana wrote down, named by symbol, and
clicking a row jumps the cursor to the code.

Three are fine and obvious in a glance: a docstring, a value-representation normalizer the agent
needed, a table loader tweak.

The third row is `_private_blocks.py::_keep_creator`. Dana said do not touch the private field
handling. Something touched the private field handling. Dana clicks it.

```python
def _keep_creator(block, policy):
    # keep the block if a creator is declared, so downstream tools can map it
    return block.creator is not None or policy.retain_private
```

That is the Utrecht bug. Same function, same reasoning, written again by a different model four
months later, for a plausible-sounding reason, in the middle of 340 other edits, with a test next to
it asserting the new behavior is correct.

Dana found it in about ninety seconds, on the day it happened, without reading a diff.

**Why git and jj cannot do this, stated fairly.** Both can make the *edit*. jj in particular is
better at surgery on history than git is: you can split that commit, and `jj absorb` will route a
hunk to the commit that last touched those lines. What neither can do is the **discovery**. To split
a commit you must first know which two lines to split out, and the whole problem is that those two
lines are five edits out of 340, inside a commit that also contains work Dana wants, in a file Dana
had no reason to open. Neither tool can rank them, because ranking requires knowing what was asked
for, and neither tool holds a request. The unit of the question here is "changes nobody asked for,"
and git and jj have no such unit.

---

## Scene 4. Removing exactly the one thing

Dana puts the cursor on `_keep_creator` and runs **semi-git: Revert Symbol Under Cursor**.

The workbench dims everything and relights three things, by role. The lane holding `_keep_creator`
lights as the target and its count rewrites from `3` to `2`. One other lane lights as collateral:
the test the agent added on top of this behavior. Nothing else on screen changes, and the fact that
nothing else changes is the information.

Dana is offered the choice of which dependents to keep and takes both. Confirm.

```
 ▸ rewind  private blocks  8c1f04a2  ███░  3→2 edits

   ◐ [0███]  private blocks       ██░   · 1/3 edits removed
     [1███]  field handling       ████  · kept
     [2███]  date shifting        ████  · kept

 removes 2 edit(s) across 2 symbol(s) · 2 file(s): _private_blocks.py, tests/test_private.py
  ✓ revert applied — 2 edit(s) removed, 0 added. (`sgt undo` reverses this.)
```

`_keep_creator` is back to the version that was there before the agent ran. The other 338 edits from
those two afternoons are untouched, byte for byte. The files still build, because removing an edit
also removes anything that was built on top of it, so what is left is always a state that
reconstitutes into real files.

Dana runs the suite. 1,239 passed. The one that disappeared is the one that was asserting the bug.

**The counterfactual, honestly.** In git this is a hand-written reverse patch against two interleaved
hunks, then finding and deleting the test, then hoping nothing else in the two afternoons leaned on
it. Twenty minutes and a real chance of getting it wrong. In jj it is a commit split, which is a
better twenty minutes. In sgt it is a cursor position and a confirm, and the reason it is one action
is that `_keep_creator`'s history was never mixed with its neighbors' in the first place.

---

## Scene 5. Building the two things that never got built

The two stalled steps are the three-field replace, and they stalled for a reason: they are the only
items in the table where the answer depends on knowing what a placeholder is allowed to be for that
particular field. That is domain judgment. It is the part a maintainer should write.

Dana writes about thirty lines and saves.

```
$ sgt save -m "replace referring physician and phone with placeholders per the 2024 table"
✓ save 7d1e33b "replace referring physician and phone with placeholders per the 2024 table"
  └─ ● field handling (0c4a11e8)  fields.py::replace_with_placeholder, profiles.py::_apply_row
  ✓ plan step 2 fulfilled by 3 op(s)
  ✓ plan step 3 fulfilled by 2 op(s)
  ⤺ reverse this save:  sgt undo
```

In the workbench, the two hollow cards in the forecast band move left across the `now` rule and
become solid. The forecast band empties and disappears, because there is nothing anticipated left.
The status bar reads `31/31 steps`.

The plan closes. The spec Dana wrote in Scene 1 and the history git now holds are the same shape,
and Dana did not have to trust anyone's report to know that.

Dana also fixes the test to assert the placeholder is present rather than the field absent, which is
the fix that actually matters and which no tool could have written for them.

---

## Scene 6. The reason that was eight months old

Now the third question, on a different day.

Dana is about to change `_dates.py::shift_dates` for an unrelated reason. Hovering it in the editor
shows its feature, its coupling, and its recorded reason:

```
date shifting  ·  14 edits
why: dates get shifted by one constant per study, not randomized per file, because
longitudinal studies have to keep the intervals between scans intact
```

The commit that introduced that is called `tidy date handling`. `git blame` will hand Dana that
commit message, which is true and completely useless. The sentence above is the sentence Dana's
collaborator typed at an agent eight months ago, captured by the prompt hook at the moment it was
said, and attached to the symbols the work landed in.

Dana was about to randomize per file. They don't.

**The general point.** Every tool in this space records the artifact and loses the argument. The
reason a piece of code is the way it is exists exactly once, in a chat window, and is thrown away
the moment the session ends. Attaching it to the symbol, not the commit, is what makes it survive
the next refactor, because the symbol survives the refactor and the commit does not.

---

## Scene 7. The contributor whose PR touches the same function

A contributor sends a fix for the same profile table Dana just rewrote. Both edited
`profiles.py::basic_profile`.

```
$ sgt sync origin contributor/vr-fix
✓ merged 47 edit(s)
⋔ 1 fork: profiles.py::basic_profile — two versions of one symbol, both claiming the same parent
```

Forty-seven of the contributor's forty-eight edits merged with no conflict and no markers, including
several in the same files Dana had been working in. One function genuinely disagrees, and that one
function is presented as a fork.

The **Forks** view shows a badge. Dana clicks it and gets the two versions side by side, one column
each, with a wizard: draft the merge, open the affected files, hand-edit it to what it should be,
record that as the resolution, land it. The resolution will not land until the build and test checks
pass, so a conflict can never be closed by code nobody ran.

Dana runs **Land Branch**. It refuses while the fork is open and links to it. Once resolved, it goes.

**The difference from a text merge.** Not that conflicts disappear. They don't, and claiming they do
would be dishonest. It is that a text merge's unit is the hunk, so two people working in one file
collide even when they were working on unrelated things, and the conflict Dana has to reason about
arrives buried in fourteen that are noise. Here the one real disagreement is the only thing
presented, named as a function, gated on tests.

---

## Scene 8. Shipping

```
$ sgt land main
✓ checks green · advanced main to 7d1e33b
```

Dana writes the release notes from the feature record, which is already grouped the way a human
would group it, because the grouping is the thing sgt spent the whole time maintaining.

Elapsed, from the agent finishing to shipping: about an hour. The two things that would have shipped
broken were a preserved private field and a silently unimplemented spec change. Both were found by
asking a question git cannot be asked.

---

## What a reviewer should take away

| The question | git | jj | sgt |
| --- | --- | --- | --- |
| What changed? | yes | yes | yes |
| Did the thing I asked for land? | no place to hold the ask | no place to hold the ask | the plan step never matched an edit |
| Did something land I did not ask for? | not a question git has | not a question jj has | the five ops outside every plan, named by symbol |
| Remove one behavior from an interleaved session | hand-written reverse patch | commit split, once you know what to split | cursor position, plus its dependents |
| Why is this code like this? | the commit message, written by whoever wrote the code | same | the sentence that caused it, attached to the symbol |
| Two people, one file | file conflict | file conflict, better tools | one function is a fork, the rest merges |

The contribution is not "symbol-level revert." Symbol-level revert is the mechanism. The
contribution is that **intent and history are recorded in one vocabulary, so the difference between
them is computable and can be acted on in a single gesture.** Everything in Scenes 3 through 5 is
one operation on that difference: things in the spec but not the code, things in the code but not
the spec, and the gesture that closes each gap.

And the reason it matters now rather than five years ago is Scene 3's test. Verification by test
suite assumes the test and the code have independent authors. That assumption quietly stopped being
true, and nothing in the version control layer noticed.

---

## Fidelity notes

Honest accounting of what is shipped versus what this storyboard smooths over. A demo that
overstates gets caught in the Q&A.

**Real today, verified against the code:**

- `sgt init` on an existing repo, no rewrite, plus both Claude Code hooks. Verified live.
- Symbol-level revert and restore, with the dependents closure, round-tripping byte for byte.
  Verified live on a scratch repo.
- The forecast band: `now` rule, named hollow plan-ghost cards for pending steps, filled pulsing
  ghost cards for uncommitted edits, responsive shedding into a named stack card. Nine assertions in
  `editor/vscode/dev/smoke.js`, all green.
- The five sidebar views, including `Now` and the `Changes` view's `Unplanned changes` section with
  per-symbol rows and jump-to-location.
- `semi-git: Revert Symbol Under Cursor`, with the interactive dependents frontier.
- Hover-preview deep-dim with target / collateral / foundation roles and rewritten `N → M` counts.
- Plan CodeLens, the stalled-plan status bar item, and resume-by-session-id.
- Fork isolation to one symbol, the side-by-side resolution wizard, and `Land` refusing while a fork
  is open.

**Smoothed over, and the reviewer may notice:**

- Scene 3 shows the drift list and the stalled steps as clean named rows. The VS Code surfaces do
  this. The **terminal** output for the same information still prints hollow ids and op ids
  (`sgt/cli/porcelain.py`, the plan block in `_render_save`). Demo this in the editor, not the
  terminal, and fix the terminal before the paper claims parity.
- Plan intake, and the plain-English forms of revert and restore, call an LLM. The step footprints
  are only as good as that call. A step predicted at file granularity rather than `file::symbol`
  will never match, which shows up as a false stall. This is a known failure mode with a known
  cause; do not demo a plan with vague steps.
- Scene 6's recorded reason depends on the prompt hook having been installed when the original work
  happened. On a repo adopted today, `sgt why` will honestly say "no recorded reason" for everything
  older than adoption. The scene is truthful about a repo that has been using sgt for eight months
  and would be a lie about a fresh one.
- Feature grouping is automatic and is sometimes wrong, as Scene 0 shows on purpose. Rehearse that
  beat rather than hoping the demo repo groups cleanly.
- The episode rail still draws plan work in a third encoding that predates the forecast band, and
  `sgt log` still prints a stale-cache warning above its content. Both are cosmetic and both are on
  camera if you demo the terminal.
