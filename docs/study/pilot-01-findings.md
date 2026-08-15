# Pilot 01: a moderated run of the sgt condition, and what it broke

Date: 2026-08-13. Condition: sgt + agent, task set A (coursecraft). One participant (P0), one
moderator. Tool: `semi-git 0.1.0` installed from PyPI the way the README tells a new user to.

Companion docs: `participant-materials.md` (the protocol), `sgt-findings.md` (the standing bug
ledger), `testbed-spec.md` (ground truth). Full transcript and the moderator's own runs:
`~/study/pilot-01/notes/think-aloud.md` and `~/study/pilot-01/moderator/moderator-log.md`.

## 1. What this was

A dry run of a real session, end to end: install the tool from scratch, build a warm-up repo,
work the 10-minute tutorial, then S1 through S6 on a fresh copy of the study repo. P0 knew only
what the handouts said — no access to sgt's source, docs, or agent skills — and kept a live
think-aloud log. The moderator ran every load-bearing claim independently on scratch copies before
recording it, which caught one case where P0's reading was wrong and several where it was
understated.

This was worth doing before participant 1 rather than after: **the session found six defects that
would each have cost a real participant their data point**, and six more came out of following
those threads afterwards. All twelve are fixed, including the data-loss path. What remains open is
led by a plan matcher that reports 0/3 on work implementing its own plan. The tool's own primary view was routing participants at the wrong
target for S2.

## 2. Outcome

| Subtask | Result | Notes |
|---|---|---|
| S1 provenance | **Correct, high confidence, inside budget** | 4 commands from an English sentence to the answer. The strongest showing of the session. |
| S2 removal | Completed, ~25 commands against a ~22 budget | Most of the overrun was repairing damage the tool caused, not doing the task. |
| S3 selective restore | Completed by hand | `sgt restore` crashed; P0 recovered the fixture with `git show`. |
| S4 regression repair | **Correct fix, plus a better test than the repo had** | Found the cause in one command. Could not use sgt to apply it; the op is atomic across six symbols. |
| S5 plan and fork | Completed, on budget, **entirely in git** | Tried `sgt session`, understood it, declined it. See O8. |
| S6 history edit | **Half done — and the done half was already done by the tool** | The best result of the session and a hard limit, in one task. See below. |

S4's fix deserves a note for the rubric: P0 changed one word (`ranges_clash` → `overlaps` at the
call site), deliberately left `ranges_clash` and the room audit alone because the sheet says to
keep the rest of that change, and then wrote the test the repo was missing. The existing
`test_back_to_back_is_fine` asserts on `slots.overlaps` directly, so it **stayed green through the
entire outage** — it guards a function the product no longer calls. P0: *"A guard that doesn't
touch the code path the product uses is not a guard."* That is a defect in our testbed, not in
sgt, and S4's answer key should say so.

### S6 is where the tool's central claim was tested, and it half held

S6 asks the participant to split the tangled `add course search` commit so each half has a clear
name, without touching code. P0 looked before acting and found **sgt had already done the split at
mining time**: the one commit appears in two features under two separately-named checkpoints
(`Search Commands @1 :add-course-search` and `Time Slots @2 :parser-polish`), with no history
rewrite and no code change. git cannot do this at all — you would need an interactive rebase, which
rewrites every downstream sha.

> "This is the single most convincing thing the tool did all day, and I only found it because S6
> made me look."

(Moderator check: the split survives the epoch collapse of O2 — post-refresh the same save still
spans five features including `Time Slots` — though the feature *names* differ across the boundary,
so a participant's notes still do not carry over.)

The other half is not achievable. `Parser Polish` is a poor name for "accept lowercase day names",
and **there is no verb that renames a checkpoint** — the level the tangle actually lives at.
`sgt feature rename` renames features; `regroup move --to` requires an already-existing leaf, so a
well-named home cannot be created either. P0 stopped deliberately rather than guess with `--apply`.

P0 finished the substantive work. They finished it **outside the tool**: three of the eight
"daily spine" verbs failed on them (`revert` corrupted the code, `restore` crashed, `save`
refused), and they fell back to plain git for the repairs. Their verdict:

> "From here I treat `sgt revert` as 'a fast, accurate first pass that leaves me a punch list',
> not as an operation that completes."

And, unprompted, the other side of it:

> "The reason I got S1 and the S2 plan right at all was sgt's attribution and its revert preview,
> which git genuinely cannot do."

Both halves matter. The comprehension story (RQ1) held up under a naive user. The operation story
(RQ2) did not survive contact.

## 3. Defects fixed in this pass

Twelve fixes in total: six found by the pilot session (F1–F6) and six more from following its
threads afterwards (F7–F12). All are on `main` with tests.

**Suite status, and a test-suite defect worth knowing about.** Three tests — the CLI-surface golden
and two label tests in `tests/lens/test_tree.py` — fail *only when an `OPENAI_API_KEY` is present in
the environment*. The goldens record the deterministic offline fallback label (`baz qux`); with a
key, the LLM labeler runs and produces something else each time (`Cross-File Symbol Moves`,
`Rename Detection`, `Multi-File Diffs`). Verified: with the key unset the whole of
`tests/lens/test_tree.py` passes, and with it set the same tests fail with different values run to
run.

So these are not "pre-existing drift" — they are green in CI (no key) and red for any developer who
has a key configured, which is every developer working on the labeler. Either the labeler should be
stubbed under test, or those assertions should not depend on generated text. The golden snapshots
in this change were regenerated with the key unset, so their diff is exactly the two intentional
projection/help changes and nothing else.

### F1 — `sgt revert` wrote syntactically invalid Python (the headline bug)

Reverting the waitlist feature left `coursecraft/enrollment.py` like this:

```python
class EnrollError(Exception):
    passdef find_section(data: dict, section_id: int) -> dict:
```

Three glued lines in one file, `find_student` silently relocated, `pytest` down to 13 collection
errors with nothing run — under a green `✓ revert applied`. P0 hit it independently and, being a
careful engineer, repaired it by hand and kept going; a less stubborn participant loses the block.

**Cause.** The fold reconstructs a file as a verbatim byte partition — each entity's image, then
that entity's residue gap, ordered by anchor facts — and synthesizes zero separator bytes, by
design. A removal whose op-set owns the save that first recorded a file's partition takes the
*layout* chains of entities it otherwise keeps: those chains are upward-closed inside the removal,
so they get excluded while the entities survive on later ops. `EnrollError`, `find_student`, and
`find_section` each ended up live with no residue and no anchor. No residue means no separator;
no anchor means the fold's sorted end-of-file fallback, which is the relocation.

`subtract.py` already bottoms the layout artifacts of entities being *removed*, deliberately and
with a comment. The missing half was the entities being *kept*.

**Fix.** `sgt/core/subtract.py::_repair_layout` re-grounds the residue and anchor of every kept
entity after a subtraction, re-pointing anchors whose predecessor the removal took. It replays the
*recorded* images, so no file bytes are invented — only the anchor's predecessor marker, which is
metadata. Tests: two in `tests/core/test_verbs.py`, verified to fail without the fix.

**This also closes standing finding 14.** That finding recorded that a hand edit to a spliced file
could not be saved afterwards ("the mined rework fails to ground"). It was a symptom: the file did
not parse, so the extractor had nothing to ground against. `sgt save` after a hand edit now
succeeds. The debugging step finding 14 proposed — comparing two version constructors — was
chasing the wrong thing.

### F2 — `sgt restore` crashed with a raw traceback

```
$ sgt restore "tests/test_drop.py::store"
KeyError: None      # order.py:506, _ordered_chains
```

`_ordered_chains` assumed every op set handed to it is downward-closed and so carries each
symbol's birth. `plan_restore` breaks that on purpose: when the reduced source has parked a
symbol's chain it widens resolution to the whole store, and a store slice can start mid-chain, on
a ghost whose birth an earlier revert excluded. Fixed by finding the chain's actual root — the op
no other op in the slice feeds — instead of indexing `steps[None]`. Behavior on a proper ideal is
unchanged. Tests in `tests/core/test_order.py`.

This is what turned P0 against the tool ("two of the spine verbs have now failed — one silently,
one loudly"), so it is worth more than its size.

### F3 — the map and the tree presented husk features as real ones, and S2 routes through one

The worst representation defect, because it is a **silent wrong answer** on the study's central
operation task.

`sgt log --map` showed a feature called **Section Waitlist**. It is not the waitlist. It is a
cluster whose single op touches only bookkeeping sentinels: `sgt show` reports it as `0 symbols in
0 files`, and reverting it removes one test file and nothing else — while printing the same
"removed" vocabulary a correct revert prints. The feature that actually holds the waitlist is
called **Priority Waitlist**, named after the senior-priority experiment that was *deleted* from
this history, and the map did not show it at all.

The two surfaces were counting different things: the tree printed the clustering's `members` (every
symbol ever assigned), `sgt show` counts the feature's own ops' footprints minus sentinels. Same
id, two numbers — `Section Waitlist · 5 symbol(s)` beside `0 symbols in 0 files`. P0 hit the same
class of mismatch in the warm-up repo within four minutes of meeting the tool (`--tree` said 19
symbols, `show` said 8, they had written 8 functions) and wrote: *"two different numbers for the
same word on two adjacent screens is the kind of thing that makes me stop trusting the numbers."*

**Fix.** `map_view` now emits `own_symbols` per feature, computed exactly the way `sgt show`
computes it, and both the tree and the map lanes count and filter on that. On the study repo the
tree went from 21 features to 11 — the 10 that vanished held no symbols of their own — and
`Priority Waitlist · 11 symbol(s)` now matches `sgt show`'s `11 symbols in 5 files` exactly. The
husk rows are gone from both views, so the wrong-target path is closed.

### F4 — `--json` was not parseable on first contact

`sgt log --tree --json | jq` failed on any repo with unmined edits, which is every freshly cloned
or copied one: a human status line printed to stdout above the `{`. The README promises agents
"Every read command takes `--json`", and the study's premise is that participants work through an
agent. Both status lines now go to stderr.

### F5 — a flag could turn a preview into a mutation

The tutorial teaches, and every revert shape honors, "previews first, applies only with `--yes`".
`sgt revert <sel> --keep-dependents` did not: it wrote continuation hollows into the store and
registered a draft immediately, with no preview and no `--yes`. P0 added the flag while still
deciding whether to use it, and got a mutation plus a draft id.

> "The tutorial's contract was 'revert always shows you a preview first'. Adding a flag silently
> voided that contract. I did not consent to a mutation there — I was still shopping. If a flag
> can turn a preview into an action, I can't reason about which of these commands are safe."

Now gated on `--yes`, and the refusal says plainly what the flag would do and how to see a plain
removal instead.

### F6 — `feature regroup` accepted only the full 64-hex id

P0 tried `sgt feature regroup split "Time Slots"`, then `split 044954f3` — the label and the short
handle that `log --tree`, `show`, `--focus` and every `next:` footer print — and both failed with
"is not a leaf feature". Only the full 64-character hash worked, which they found by hand. This was
their third selector surprise of the session (`show` takes no commit sha, `show` takes no phrase,
`regroup` takes no handle): *"Selector support is per-verb and undocumented."*

`plan_merge` already resolved refs properly; `plan_move` and `plan_split` did not. They now do.

### F7 — `sgt advanced fulfill --from-tree` no longer overwrites uncommitted work (was O1)

The data-loss blocker. `stage` writes the candidate over the working tree via
`lens._write_working_tree`, which bypasses `put()` — so it never consulted the
"would overwrite uncommitted changes" guard that (correctly) blocks `sgt save`. It now makes the
same refusal:

```
$ sgt advanced fulfill 19315ef6a6c4 --from-tree
✗ fulfill would overwrite uncommitted changes: ['coursecraft/slots.py'] -- record them with
  `sgt save`, or commit / `git restore` those files, then re-run (the draft is untouched;
  nothing has been staged)
```

Two things this had to get right, and my first attempt got both wrong:

- **Scope.** A blanket guard breaks the normal flow: `--from-tree` reads the hollow's image out of
  uncommitted bytes, so the authored file is *supposed* to be dirty. Four existing tests caught
  this. The guard now excludes the paths the draft itself authors and fires only on everything
  else the fold would rewrite — which is exactly where the loss happened, since the participant's
  work was in files the draft never touched. Scoped the same way `put()`'s own delta guard is.
- **Order.** Checked *before* the ops are stored and the hollows unlinked. My first version checked
  after, which made the refusal itself destructive: the draft survived but its hollows were gone,
  so the retry the message asks for died with "hollow not found". A refusal must not break the
  thing it refused to touch.

The CLI now renders it as a clean `✗` line rather than a raw traceback. Test:
`test_fulfill_refuses_to_overwrite_uncommitted_work_and_stays_retryable`, verified to fail without
the guard, and it asserts the retry works after the remedy.

### F8 — `sgt save --resolve-plan` prints the command that settles it

It used to re-print the ambiguity and tell the user to run `sgt save --resolve-plan` — the command
they had just run — while the `--confirm-hollow`/`--confirm-op` flags that actually resolve it
appeared nowhere. It now prints the executable line with the ids filled in:

```
      settle it:  sgt save --resolve-plan --confirm-hollow 0aa03bad2c19 ... --confirm-op 09cf8a3b7fe7 ...
```

Running that line: `✓ confirmed 3 hollow(s) matched to 9 op(s)`, and `sgt plan status` goes to
`(no active plan sessions)`. The loop closes.

### F9 — `plan`, `intent` and `session` document their subcommands

All three rendered their positional arguments as `sub` / `target` / `rest` / `name` with no
descriptions and no subcommand list, so `--help` taught nothing. P0 abandoned `intent` and `plan`
on exactly this, which put S4's and S5's intended sgt mechanisms out of reach. Each now names its
subcommands and what they take — including the one that bit P0 hardest: `intent show <commit-sha>`
now says in the help that it takes **a COMMIT, not a feature id**.

### F10 — `sgt show`'s `next:` footer no longer suggests a command that always fails

It offered `sgt intent show <feature-id>`, which exits 1 with "no theme or commit found in the
intent overlay" every time, because that verb resolves a commit. `_show_next`'s own docstring
forbids exactly this ("a suggestion that silently no-ops is worse than no suggestion"). Replaced
with `sgt log --focus <handle>`, which answers the same question and works. Every command the
footer now prints was run and checked.

### F11 — the top-level help advertised a verb that does not exist

`sgt feature <cmd> … regroup, rename, select, why` — but `sgt feature why` is
`invalid choice: 'why'`. The verb is the top-level `sgt why <sel>`. The help line now says so.

### F12 — `sgt session gc` reaps the branch too

`gc` removed the worktree and the session record but left `sgt-session/<name>` in `git branch`
forever, so a tidy-up verb quietly accumulated refs. It now deletes the branch as well (`-D`: `gc`
already refuses to reap a session with unlanded work unless `--force`, and force-removes the
worktree either way).

## 4. Open, not fixed — ranked by what they cost a participant

### O1 — `sgt advanced fulfill --from-tree` overwrites uncommitted work (data loss)

The chain that F5 starts ends here. sgt hands the user a literal next command
(`edit the working tree, then: sgt fulfill <draft> --from-tree`). Running it rewrote **five files,
174 insertions, 220 deletions** on a clean tree in the moderator's repro — from a draft created to
remove one symbol's edit — and printed `✓ staged 348 op(s)`. In P0's repo it also reverted their
uncommitted S4 edits and resurrected waitlist code they had deleted an hour earlier.

> "Twice now a ✓ has meant 'your codebase no longer parses', and this time it also silently
> reverted deliberate, committed work. The whole chain that got me here was the tool leading me
> by the hand into destroying my own tree."

They recovered with `git stash`, deliberately keeping the wreckage for us, and noted they were no
longer willing to trust `sgt undo`.

The specific gap: `put()` already refuses to overwrite uncommitted changes — that guard is exactly
what blocked `sgt save` in section O5 — but `fulfill --from-tree` does not consult it. **Proposed:
`fulfill` should preview like every other mutating verb, and must refuse (not warn) when it would
overwrite uncommitted changes.** Left unfixed here deliberately: it is in the repair loop, which I
have not read closely enough to change safely, and it deserves its own pass.

**This blocks piloting S4/S5 in the sgt condition.**

### O2 — the feature set a participant is handed is thrown away on first refresh

Every participant gets a `cp -R` of the study repo. Measured on a fresh copy:

| | before refresh | after `sgt log --refresh` (27s) |
|---|---|---|
| features | **34** | **21** |

Thirteen disappear, including eleven whose labels are bare file paths with duplicate names (three
different features all called `coursecraft/storage.py`) and ids in a different shape (`af-m0829`).
Two keep their id and are renamed underneath the user — `15d99310` goes from **`Promote Next`** to
**`Course Scheduling`**.

That last one is the dangerous one, and it is not hypothetical: P0 worked the whole of S2 in the
pre-refresh epoch, reading `sgt show 15d99310` as a 10-symbol promotion feature. In the moderator's
post-refresh repo the same id is a 23-symbol grab-bag spanning search, rooms, and storage;
reverting it removes 66 edits. **The same id means two different things depending on when you
looked, and nothing announces the switch.**

Proposed: refresh at hand-over (add it to the facilitator setup script) so every participant starts
in the settled epoch; and, in the tool, either build the map at `init` or say plainly that the
current view is provisional rather than in dim grey at the top of one command.

### O3 — the map and the rail still name different things

With F3 fixed, the husk names are gone from both views. What remains is structural: the map
collapses features into subsystem meta-lanes (`Course Planning (2)`), while `sgt log` lists the
leaves (`Priority Waitlist`, `Data Storage`, `Time Slots`). Grepping both outputs for the same ten
feature names still gives near-disjoint results — not because they disagree, but because one shows
aggregates and the other shows members, with no visible relationship between them.

This is the "users have to rebuild the map" complaint in its final form. Proposed: print the
collapsed leaf labels under each meta-lane, the way `@n` checkpoint chips already appear, so the
map and the rail share one vocabulary and a reader can move between them without re-deriving
anything.

### O4 — the revert preview is honest and is not the map

The subtraction preview is, in P0's words, "the best output this tool has produced" and "a work
plan, not a warning" — it names what goes automatically, the one symbol it refuses to touch, and
the exact callers that will break. Every warning it gave came true. But it is a flat list in a
different visual language from the lane view the participant just learned, and `· 20 other
feature(s) unchanged` is its only reference back. Proposed: render the preview *as* the map with
the affected lanes marked, so "what will this do" is answered in the representation the user
already holds.

Also in that preview: `1→0 edits` and `[0███]` are undefined notation, and `░` appears with no key.

### O5 — words that mean something else than they say

- **"removed going forward"** does not mean removed. The preview listed
  `coursecraft/promotion.py::promote_next` as removed; `git diff` on that file afterwards is
  **empty**. It is the R4 backstop (standing finding 4) keeping a file the fold cannot regenerate.
  The truth is recorded only in `sgt status` ("kept 4 unreproducible file(s)"), which the revert
  never mentions. P0: *"That phrase cost me real time."*
- **Four counting units** — edits, ops, symbols, saves — none defined in the tutorial, and they
  disagree on the same operation: `sgt show` says the feature has 24 edits, the preview header says
  `23→8`, its footer says `removes 22`, and the applied line says `22 removed, 9 added`. P0 stopped
  reading the numbers: *"the edit counts have been meaningless all session."*
- **`sgt show`'s own `next:` footer** prints `sgt intent show <feature-id>`, which always fails
  (`✗ no theme or commit ... found in the intent overlay`, exit 1) because that verb takes a commit
  sha. The footers are the most-praised part of the interface, which is why one that lies is
  expensive.
- **`sgt show <commit-sha>`** fails, though the tutorial says "any id sgt printed can be handed
  back to it". Two id namespaces, no cross-lookup. **`sgt show "<phrase>"`** also refuses, and
  helpfully suggests the user type `sgt revert <phrase>` instead — pointing a read-only question
  at the most destructive verb (standing finding 11).
- **`sgt intent --help` and `sgt plan --help`** document their positional arguments as `sub`,
  `target`, `rest`, with no subcommand list. P0 abandoned `intent` on that basis — which makes S4's
  intended sgt mechanism unreachable.

### O6 — sgt degrades the git repo it sits on

`git show` on a study commit prints **344 `Sgt-Op:` trailer lines**; they accumulate with the ideal
(94 at the first commit, 344 by the last). sgt's own commits use a 64-hex subject, so
`git log --oneline` is unreadable too:

```
f46d95c sgt revert f-10462e17eb7dd53bfa275af8a33aae05a2a77ac74507043473d9ca790166bf1a
```

> "sgt has made the underlying git repo materially worse to use with git. Nobody warned me. If I
> hand this repo to a colleague who doesn't use sgt, they inherit that."

The README's promise is "runs on top of an ordinary git repo". Proposed: a readable subject line
for sgt's own commits, and trailers folded to one reference rather than the full op set.

### O7 — symbol-level revert cannot split an atomic op, which is S4's whole task

S4's cause is one op touching six symbols. Reverting the one symbol P0 wanted drags the room audit
out with it. The tool's headline pitch is symbol-level surgery; on the one study task that needs
it, the granularity is not there. This is a real limitation to state in the paper rather than a
bug — but S4's rubric currently assumes an sgt mechanism (`intent` rewind / `revert f@n`) that this
participant could not reach.

### O8 — the participant read the session feature, understood it, and declined it

S5 is the task sessions exist for. P0 ran `sgt session start`, saw it is a git worktree on a branch
("a sane design and I'd have been happy to use it"), and backed out for two stated reasons:

1. `sgt session status` reported **"86 new op(s)"** on a session where they had made zero edits.
   *"I can't reconcile that number with reality, and unexplained numbers are how I got burned twice
   today."*
2. The value a session adds over `git checkout -b` is `sgt session land` — *"an unfamiliar mutation
   verb from a tool whose mutation verbs have, today, corrupted a file, crashed, refused, and wiped
   my working tree. I am not putting two afternoons of work through it to find out."*

They were explicit that this was not reflex: *"If `revert`/`fulfill` had behaved this session I'd
have used it."* This is the clearest evidence in the pilot that the F1/F2/O1 defects do not just
cost their own tasks — they spend the credibility the rest of the tool needs. A session-condition
S5 data point is not obtainable until the mutation verbs are trustworthy.

Also: `sgt session gc` reported "reaped session" but left the branch `sgt-session/swap-transactional`
in `git branch`.

### O9 — `feature regroup split` proposes a vacuous split

With F6 fixed, the split preview is now reachable by handle — and it is wrong. On `Time Slots` it
offers group 0 = the real symbols and group 1 = **only `__residue__` sentinels**:

```
group 0: slots.py::SlotError, slots.py::_parse_hhmm, slots.py::format_slot, slots.py::parse_slot, ...
group 1: slots.py::__residue__::HEAD, slots.py::__residue__::SlotError, slots.py::__residue__::overlaps, ...
```

Applying it would separate code from bookkeeping artifacts, not one concern from another. Both
groups contain `parse_slot`. P0 declined to run `--apply` on a preview they could see was wrong,
which is the right call and also the reason S6 cannot be completed. The internal `__residue__`
marker should never reach user output, and the clustering should not treat sentinels as splittable
members. Left unfixed: this is the split heuristic's input, and changing it late without
understanding the clustering is how you get a worse bug than the one you fixed.

### O10 — a cleanup save mints unnamed "new feature" rows for the code you deleted

Recording the finished S2/S3 work on the fixed build succeeds (this is finding 14's path, now
closed), and the save output attributes the deletions like this:

```
✓ save 8dfb5f4 "remove the waitlist (join, promotion, seat notices); keep drop working ..."
  ├─ ● Enrollment Drop (46494e2a)  cli.py::cmd_drop, tests/test_drop.py::...
  ├─ ● init repo (af-m1c654ff)  enrollment.py::enroll, pytest.ini, tests/test_drop.py::store
  ├─ ○ new feature (af-m19244a4) — unnamed; name it: sgt feature rename ...  cli.py::cmd_waitlist_promote
  ├─ ○ new feature (af-m43fdede) — unnamed; name it: sgt feature rename ...  promotion.py::promote_next
  ├─ ○ new feature (af-m4e8d589) — unnamed; name it: sgt feature rename ...  test_promotion.py::test_no_promotion_while_full
```

Three **new** features, born from symbols the participant *deleted*, each asking to be named. A
developer who has just spent twenty minutes removing a feature is told they have created three, and
the tool's own suggested next action is to name the corpses. `init repo` also acts as a catch-all,
absorbing `enrollment.py::enroll` and `pytest.ini`. Proposed: a save whose footprint for a symbol
is a deletion should not found a feature; deletions belong to the feature the symbol was leaving.

### O11 (FIXED) — the plan matcher misses, and its recovery route was a dead end

P0 never exercised `sgt plan`: `sgt plan --help` documents its positional arguments as `rest` and
lists no subcommands, so they could not learn it existed in any usable form and said so. Since the
plan loop is the flagship for AI-authored history — and the paper's thesis is that developers now
express changes as intents to agents and sgt records history at that level — I ran it myself
afterwards, with knowledge P0 did not have (the subcommands appear only in the top-level help).

The loop does not close. Stating a plan, doing exactly that work, and saving it:

```
$ sgt plan intake "Add a swap command: enrollment.swap drops the old section and enrolls the
   new one, rolling back if the new section is refused. Add cmd_swap in cli.py and a swap
   subparser. Add tests/test_swap.py covering the happy path and the rollback."
✓ intake: session ce6a261f — 3 step(s)
    Implement swap logic  [f-6f11b0c5ef2777...]
    Wire swap command     [af-m19244a4005a8...]
    Add swap tests

   ... implement exactly that: enrollment.swap with rollback, cmd_swap, a swap subparser,
   tests/test_swap.py with the happy path and the rollback. `pytest -q` -> 33 passed ...

$ sgt save -m "add a swap command with rollback"
✓ save 6d93c58
  ├─ ○ new feature (af-m04337e1) — unnamed  tests/test_swap.py::store
  ├─ ○ new feature (af-m1250cd5) — unnamed  cli.py::cmd_swap, enrollment.py::swap, ... +1 more
  ├─ ○ new feature (af-m44425c4) — unnamed
  ├─ ○ new feature (af-md252657) — unnamed
  ├─ ○ new feature (af-mea38d83) — unnamed
  ⚠ one save touched 8 features — deliberate?
  ⚠ ambiguous: 3 step(s) <-> 9 op(s) -- run `sgt save --resolve-plan`

$ sgt plan status
  ce6a261f  0/3 step(s) matched
```

**0 of 3.** The work implements the plan sentence for sentence and nothing matched. The save also
minted five unnamed features — three of them with no symbols listed at all — for what is one
coherent piece of work, and then asked whether touching eight features was deliberate.

The recovery route the tool names is a closed loop (this is standing finding 6, now with a measured
cost):

```
$ sgt save --resolve-plan
  ⚠ ambiguous: 3 step(s) <-> 9 op(s) -- run `sgt save --resolve-plan`
      hollow: 0aa03bad2c19, 28a81c78cfae, 469820468bd6
      op:     09cf8a3b7fe7, 28091bbc6c70, ... (9 ids)
```

It re-printed the ambiguity and instructed the user to run the command they had just run. The
`--confirm-hollow`/`--confirm-op` form that actually settles it was named nowhere in the output.

**Correction, after fixing it (F8):** the loop *does* close — I had written that it "does not
close", and that was too strong. With the settling command printed with its ids filled in, running
the line the tool now prints resolves all three steps against the nine ops and closes the session
(`✓ confirmed 3 hollow(s) matched to 9 op(s)`, then `(no active plan sessions)`). What was broken
was discoverability, not the mechanism.

**Second correction (2026-08-14): the matcher is fixed.** The remaining half of O11 — auto-matching
returning nothing for work that implements its own plan — was not a scoring problem in the
step<->op join. The join was finding the work. The grouping was throwing it away.

Candidate step<->op edges were union-found into transitive clusters, and a cluster holding more than
one step never auto-confirms (`sgt/cli/porcelain.py::_fold_plan_matches` takes only single-step
groups; the rest wait for `save --resolve-plan`). One save routinely emits a coarse op carrying two
steps' *disjoint* work — here, one op held `enrollment.py::swap` and both functions of
`tests/test_swap.py` — and that single shared op chained the steps into one blob. Nothing about
which step was built was in doubt; the tool asked anyway, and every step stayed pending.

Reproduced end to end on a repo built to the shape above: three steps, implemented exactly, gave
one 2-step group plus one 1-step group — `1/3 matched`, the same defect as the pilot's `0/3` at a
different arity. Two changes in `sgt/loop/match.py`:

- Each step now carries the ops that matched **it**, not its cluster's. Steps are grouped as one
  ambiguous n:m match only when they *compete* — their predictions share a match key, so no
  evidence tells them apart. Two steps predicting the same symbol still group (and still route to
  `--resolve-plan`); two steps sharing only an op do not. The same repro now reports three
  single-step groups, the save auto-confirms all three, and `sgt plan status` reads `3/3`.
- A bare-file prediction is resolved against the files really touched (standing finding 7): the
  decomposer writes `cli.py`, the repo path is `coursecraft/cli.py`, and joined as strings those
  never met — the step was unmatchable *and* its work read as drift, permanently. An ambiguous
  basename (two `cli.py` under different packages) is left unresolved rather than guessed.

Also fixed alongside: confirming one op against two steps used to overwrite its own row in
`plan_matches.json`, so the op's recorded intent named only whichever step was confirmed last.

Tests: four in `tests/loop/test_match.py` (including the competition guard and the ambiguous
basename), one in `tests/test_cli.py` at the save surface. All verified to fail without the change.

One consequence for the study stands. S5's sgt mechanism ("agent plan loop plus `sgt session`") was
not reachable for P0 for a different reason — they could not find the loop at all (F9 fixes the
`--help` half of that). **RQ4 asks how intent-aligned history changes the way developers direct and
check an agent**, and the plan loop is its instrument; an agent that states its intent and does what
it said now has that recorded as fulfilment rather than as drift.

## 5. What S2 actually costs when the tool works

Section 6 of the first draft of this document said we did not know, because P0's S2 budget went on
repairing damage. Measured afterwards on the fixed build, running S2+S3 exactly as the tool's own
report directs:

1. `sgt log --refresh` (facilitator, at hand-over)
2. `sgt revert 10462e17 --yes` — removes the waitlist, reports one overlap (`enrollment.py::enroll`)
   and nine still-referencing symbols
3. hand edits following that report: `enroll`'s full-section message; `cli.py`'s waitlist/notices
   subparsers, imports and the `promote_next` call; `storage.py`'s persisted `waitlist` key; delete
   `promotion.py`, `notify.py` and the three husk test files; drop the dead pytest markers
4. S3: recover the `store` fixture (`git show <sha>:tests/test_drop.py`), minus its
   `join_waitlist` line, and rewrite the promotion assertion as "the seat frees, nobody moves"
5. `pytest -q` → **31 passed** (38 baseline − 7 waitlist/promotion/notify tests), and
   `build_parser()` constructs cleanly with no `waitlist` subcommand
6. `sgt save -m "..."` → records

So the tool does the mechanical removal and produces an accurate punch list; **the punch list is
most of the work**, and it is hand editing. Every item on it was named in the preview before the
revert applied. Two traps worth noting for the rubric, both of which I walked into while doing this
and which a participant will too:

- an *empty* `waitlist` subparser survives the cleanup and keeps `waitlist` in the CLI's subcommand
  list, while every test passes — the CLI smoke check catches it, the suite does not;
- `storage.EMPTY` keeps its `"waitlist": []` key, which nothing tests at all.

Budget implication: 15 minutes for S2+S3 is tight but not unreasonable **for the fixed build**. It
is not achievable on 0.1.0.

## 6. What has to happen before participant 1

Done since this document was first written:

1. **The build participants get.** `setup-session.sh` installs sgt from a pinned source checkout
   instead of PyPI, and records the build sha in the workspace. Version 0.1.0 corrupts
   `enrollment.py` during request 2, so a participant on it loses the task. Verified: the pinned
   build reverts the waitlist with no syntax errors.
2. **The `fulfill` data-loss path (O1).** Fixed as F7, with a test. S4 and S5 are no longer blocked
   in the sgt condition on that account.
3. **Refresh at hand-over (O2).** The setup script does it, so every participant starts in the
   settled feature set rather than the provisional one.
4. **The scoring script.** `scripts/score_study_repo.py` compares a participant's copy against a
   pristine one, reports per feature whether its tests were kept, removed or broken, and starts the
   program. Checked against three known states: a correct removal passes, an untouched copy is
   caught, and the state where tests pass but the program cannot start is caught and named.
5. **The missing safety net.** The suite never builds the command line parser, so a participant can
   finish with green tests and a dead program. The scorer now starts the program, and the protocol
   says to record that outcome on its own rather than as collateral damage.
6. **The participant materials.** Rewritten in plain language in `materials/`, and the facilitator
   protocol rewritten with them. They were internal notes before, written in the project's own
   vocabulary.
7. **The request 4 answer key.** Records that `test_back_to_back_is_fine` checks a function the app
   no longer calls, so it stays green through the regression it is supposed to guard.

Still open before participant 1:

8. ~~**The plan matcher.**~~ Fixed 2026-08-14, see O11 below. A plan built as stated now
   auto-confirms every step, so RQ4 keeps its instrument and does not need rescoping.
9. **Pilot the git condition.** In progress. Until it runs we don't know whether the requests are
   doable in 45 minutes without sgt, which is half the comparison.
10. **Pilot task set B.** confplan has never been run by anyone. Its being equivalent to
    coursecraft is an assumption.
11. **Pilot with people.** Both pilots so far were agents. They are good at finding defects and
    cannot calibrate time pressure, confusion or the point at which someone gives up. The design
    asks for three people.
12. **The instruments.** Consent form, background questionnaire, the workload and usability
    questionnaires, and the block quiz are all described and none are built.
13. **Ethics approval and pre-registration.** Neither has been started, and the design says to
    pre-register before participant 1.

## 7. Method notes for the next pilot

- The think-aloud protocol worked. Requiring `want/guess/ran/got/read/feel/next` per step produced
  usable data and, more importantly, caught the moments where the participant's *model* of the
  tool broke, which is where the findings are.
- Moderator verification is not optional. P0 reported that `sgt now` and `sgt log` print different
  ids for the same commits; they print 8-char and 7-char prefixes of the same sha. The belief was
  wrong, the cause (inconsistent truncation width) is real, and only re-running it revealed which.
  Two other claims were understated rather than wrong.
- One pilot in the sgt condition is not enough to size the tasks. S2's budget was blown entirely on
  damage repair, so we still do not know what S2 costs when the tool works.


## 8. The participant's exit debrief, verbatim

Collected at the end of the session, unprompted by any of the findings above — P0 never saw this
document and had no access to the moderator's runs. Reproduced in full because the three changes
they ask for are, independently, three of the six defects fixed in section 3, which is the
strongest evidence available that this pilot found the things that actually matter to a user rather
than the things that were easiest to find.

Note their framing of the split verdict — **"For reading history, yes, starting today. For anything
that writes, no."** That is RQ1 and RQ2 separating cleanly in a single participant, and it is the
shape the paper should expect to measure at n=12.

---

### 1. Would I use this on a real project?

For reading history, yes, starting today. For anything that writes, no.

The read side earned its place three separate times in one afternoon. The write side failed four
times in that same afternoon on a 26-commit toy repo: `revert --yes` emitted Python that no
longer parsed, `restore` died on a bare KeyError traceback, `save` refused my changes outright,
and `fulfill` rewrote six files and resurrected code I had deleted an hour earlier. Two of those
printed a green checkmark while doing it. That is not a rough edge on a good tool, it is an
unfinished half. By the end I was committing after every verified step because I could not
predict which command would eat my tree, and that tax cancels out everything the read side
buys me.

### 2. What I would tell a colleague, in two sentences

It indexes your history by function instead of by file, so you can ask "what changed this symbol,
and what else came along for the ride" and get a real answer in one command — that part is
excellent and git genuinely cannot do it. Use it to find things, then make the change yourself
with git, and do not let it write.

### 3. What would have to change to flip my answer

1. **Never print a success marker over output that does not parse.** It ships tree-sitter.
   Parse what it just wrote, and refuse. Twice today a checkmark meant "your code no longer
   imports."
2. **One safety contract, no exceptions.** Preview by default, `--yes` to apply, across every
   verb and every flag combination. `--keep-dependents` silently voided the contract the tutorial
   had just taught me, and that is the chain that led me into `fulfill`.
3. **Uniform selectors.** `show` rejects commit shas; `regroup` rejects the short ids that every
   other screen prints at me. Whatever form an id is printed in should be accepted everywhere.

### 4. Where it genuinely beat git

- **Symbol-level co-change.** One command gave me the culprit commit *and* the five other symbols
  that moved atomically with it, including the one caller that had no business being in that
  change. That was the bug. `git log -S` gets you the commit; it does not get you the set.
- **The revert preview.** Before acting it named the symbol it would refuse to touch and listed
  every caller I would have to fix by hand. Every warning came true. Best pre-flight I have used.
- **Splitting a tangled commit without rewriting history.** One commit already sat in two features
  under two separately named checkpoints, zero code touched, no shas changed. Git needs an
  interactive rebase for that, which is a different and worse thing.

---

One methodological note: P0 produced this only after being asked to write it to a file rather than
reply. Three prior requests for the same debrief went unanswered while the agent reported itself
idle. If a future pilot runs the participant as an agent, collect terminal instruments as written
artifacts, not as replies.
