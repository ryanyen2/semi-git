# Pilot 01 findings

**Date:** 2026-08-13
**Condition:** sgt + AI agent, task set A (coursecraft project)
**Participant:** One AI agent (called P0), one human moderator
**Tool version:** semi-git 0.1.0, installed from PyPI

Related documents: `participant-materials.md` (the study protocol), `sgt-findings.md` (the running bug list), `testbed-spec.md` (the ground-truth answer key). The full transcript and the moderator's own verification runs are at `~/study/pilot-01/notes/think-aloud.md` and `~/study/pilot-01/moderator/moderator-log.md`.


## What this pilot was

A dry run of the full study session, from scratch installation to task completion. The participant (P0) worked through the install, built a warm-up repository, completed the 10-minute tutorial, and then attempted all six study requests (S1 through S6) on a fresh copy of the study repository. P0 had access only to the study handouts — no access to sgt's source code, documentation, or agent skills — and kept a running think-aloud log.

The moderator independently re-ran every load-bearing claim on scratch copies to verify findings. This caught one case where P0's reading was incorrect and several where the problem was worse than P0 had described.

This pilot was worth running before participant 1: **the session found six defects that would each have cost a real participant their data point**, and six more came out of follow-up investigation. All twelve are now fixed, including the data-loss path.


## Task outcomes

| Request | Result | Notes |
|---|---|---|
| S1 (provenance) | **Correct, high confidence, within budget** | Went from an English question to the answer in 4 commands. The strongest showing of the session. |
| S2 (removal) | Completed, ~25 commands against a ~22 budget | Most of the overrun was repairing damage the tool had caused, not doing the actual task. |
| S3 (selective restore) | Completed by hand | `sgt restore` crashed with a traceback; P0 recovered the fixture using `git show`. |
| S4 (regression repair) | **Correct fix, plus a better test than the repo had** | Found the root cause in one command. Could not use sgt to apply the fix because the relevant change was recorded as one atomic operation across six symbols. |
| S5 (plan and fork) | Completed, on budget, **entirely in git** | Tried `sgt session`, understood it, declined to use it (see finding O8 below). |
| S6 (history edit) | **Half done — and the done half was already done by the tool** | The best result of the session and a hard limit, in one task. See below. |

**A note on S4's fix.** P0 changed one word (`ranges_clash` to `overlaps` at the call site), deliberately left the `ranges_clash` function and the room audit alone (because the task instructions say to keep those), and then wrote the test the repository was missing. The existing test `test_back_to_back_is_fine` asserts on `slots.overlaps` directly, so it **stayed green throughout the entire outage** — it was guarding a function the product no longer calls. P0's comment: *"A guard that doesn't touch the code path the product uses is not a guard."* That is a defect in our test repository, not in sgt, and the S4 answer key has been updated to acknowledge it.


## S6: where sgt's central claim was tested, and it half held

S6 asks the participant to split a tangled commit (one commit that did two unrelated things) so that each half has a clear name, without touching code.

P0 looked before acting and found that **sgt had already done the split at mining time**: the single commit appeared in two features under two separately-named checkpoints (`Search Commands @1 :add-course-search` and `Time Slots @2 :parser-polish`), with no history rewrite and no code change. Git cannot do this at all — in git you would need an interactive rebase, which rewrites every downstream commit hash.

> "This is the single most convincing thing the tool did all day, and I only found it because S6 made me look."

(The moderator verified that this split survives the feature-set refresh of finding O2. After a refresh the same checkpoint still spans five features including `Time Slots`, though the feature *names* differ across the boundary, so a participant's notes would not carry over.)

The other half is not achievable. "Parser Polish" is a poor name for "accept lowercase day names", and **there is no command to rename a checkpoint** — the level the tangle actually lives at. `sgt feature rename` renames features (the grouping above checkpoints), and `sgt feature regroup move --to` requires an already-existing destination feature, so a well-named home cannot be created either. P0 stopped deliberately rather than guess with `--apply`.

**The overall picture:** P0 finished the substantive work, but they finished it **outside the tool**. Three of the eight core verbs failed on them (`revert` produced unparseable code, `restore` crashed, `save` refused their changes), and they fell back to plain git for the repairs. Their verdict:

> "From here I treat `sgt revert` as 'a fast, accurate first pass that leaves me a punch list', not as an operation that completes."

And, unprompted, the other side of it:

> "The reason I got S1 and the S2 plan right at all was sgt's attribution and its revert preview, which git genuinely cannot do."

Both halves matter. The comprehension story (research question 1: can developers understand history better?) held up under a naive user. The operation story (research question 2: can developers act on that understanding?) did not survive contact.


## Defects fixed in this pass

Twelve fixes total: six found during the pilot session (F1–F6) and six more from follow-up investigation (F7–F12). All are on `main` with tests.

**A note about the test suite.** Three tests (the CLI-surface golden test and two label tests in `tests/lens/test_tree.py`) fail *only when an `OPENAI_API_KEY` environment variable is present*. The golden tests record the deterministic offline label (`baz qux`); with a key, the LLM labeller runs and produces different text each time. These tests are green in CI (no key) and red for any developer who has a key set. Either the labeller should be stubbed under test, or those assertions should not depend on generated text. The golden snapshots in this change were regenerated with the key unset.


### F1 — `sgt revert` produced syntactically invalid Python (the headline bug)

Reverting the waitlist feature left `coursecraft/enrollment.py` like this:

```python
class EnrollError(Exception):
    passdef find_section(data: dict, section_id: int) -> dict:
```

Three function definitions glued together on the same line, `find_student` silently relocated to a different position in the file, `pytest` down to 13 collection errors with nothing runnable — all under a green `✓ revert applied` message. P0 hit this independently and, being a careful engineer, repaired it by hand and continued. A less persistent participant would have lost the block entirely.

**Root cause.** sgt reconstructs each file by placing each symbol's code in order, separated by the whitespace between them. When a removal's set of operations includes the save that first recorded a file's layout, it can take the spacing information for symbols it is supposed to *keep*. Those symbols end up alive with no whitespace before them and no anchor telling the file rebuilder where to place them — which is why they ran together and why `find_student` moved to the wrong position.

**Fix.** `sgt/core/subtract.py::_repair_layout` now re-establishes the spacing and position data for every kept symbol after a subtraction. It replays the *recorded* file content, so no file bytes are invented — only the position marker is metadata. Tests: two in `tests/core/test_verbs.py`, verified to fail without the fix.

**This also closed standing finding 14.** That finding recorded that a hand-edit to a file that sgt had spliced could not be saved afterwards ("the mined rework fails to ground"). It was a symptom of the same problem: the file did not parse because it had been spliced, so the extractor had nothing to work with.


### F2 — `sgt restore` crashed with a raw traceback

```
$ sgt restore "tests/test_drop.py::store"
KeyError: None      # order.py:506, _ordered_chains
```

The ordering code assumed every set of operations it receives would be complete (containing each symbol's initial creation). `restore` breaks that assumption by design: when the thing being restored was only partially removed, it widens its search to the full store, and a slice from the full store can start mid-chain on an operation whose original creation was excluded by an earlier revert. Fixed by finding the chain's actual starting point instead of assuming there is one. Tests in `tests/core/test_order.py`.

This is what turned P0 against the tool's mutation verbs: *"Two of the spine verbs have now failed — one silently, one loudly."*


### F3 — the map and the tree showed empty features as real ones, and S2 routes through one

The worst representation defect, because it is a **silent wrong answer** on the study's central operation task.

`sgt log --map` showed a feature called **Section Waitlist**. It is not the waitlist. It is a cluster whose single operation touches only internal bookkeeping — `sgt show` reports it as "0 symbols in 0 files", and reverting it removes one test file and nothing else, while printing the same "removed" vocabulary a correct revert prints. The feature that actually holds the waitlist code is called **Priority Waitlist**, named after a senior-priority experiment that was deleted from this history, and the map did not show it at all.

The two display surfaces were counting different things: the tree counted every symbol ever assigned to a cluster (including internal bookkeeping), while `sgt show` counted only the operations' real code footprints. Same feature id, two different numbers — `Section Waitlist · 5 symbol(s)` next to `0 symbols in 0 files`. P0 hit the same class of mismatch in the warm-up repo within four minutes of meeting the tool and wrote: *"two different numbers for the same word on two adjacent screens is the kind of thing that makes me stop trusting the numbers."*

**Fix.** `map_view` now computes `own_symbols` per feature the same way `sgt show` does, and both the tree and the map filter on it. On the study repo the tree went from 21 features to 11 — the 10 that vanished held no symbols of their own — and the numbers now match exactly.


### F4 — `--json` output was not parseable on first contact

`sgt log --tree --json | jq` failed on any repository with unmined edits (which is every freshly cloned or copied one): a human-readable status line was printed to stdout above the JSON, breaking the parse. The README promises agents that "every read command takes `--json`", and the study's premise is that participants work through an agent. Both status lines now go to stderr.


### F5 — a flag could silently turn a preview into a mutation

The tutorial teaches that every revert shows a preview first and applies only with `--yes`. However, `sgt revert <selection> --keep-dependents` did not follow this rule: it immediately wrote continuation operations into the store and registered a draft, with no preview and no `--yes` required. P0 added the flag while still deciding whether to use it, and got a mutation plus a draft id.

> "The tutorial's contract was 'revert always shows you a preview first'. Adding a flag silently voided that contract. I did not consent to a mutation there — I was still shopping. If a flag can turn a preview into an action, I can't reason about which of these commands are safe."

Now gated on `--yes`, and the refusal explains what the flag would do.


### F6 — `feature regroup` only accepted the full 64-character hex id

P0 tried `sgt feature regroup split "Time Slots"` (the label), then `split 044954f3` (the short handle that `log --tree`, `show`, and other commands print) — and both failed with "is not a leaf feature". Only the full 64-character hash worked, which they found by hand. This was their third selector problem of the session: *"Selector support is per-verb and undocumented."*

The merge command already resolved references properly; the move and split commands did not. They now do.


### F7 — `sgt advanced fulfill --from-tree` no longer overwrites uncommitted work (data loss)

This was the data-loss blocker. The `fulfill` command writes its candidate over the working tree via an internal function that bypasses the "would overwrite uncommitted changes" guard that (correctly) blocks `sgt save`. It now makes the same refusal:

```
$ sgt advanced fulfill 19315ef6a6c4 --from-tree
✗ fulfill would overwrite uncommitted changes: ['coursecraft/slots.py'] -- record them with
  `sgt save`, or commit / `git restore` those files, then re-run (the draft is untouched;
  nothing has been staged)
```

Two things this had to get right:
- **Scope:** A blanket guard would break the normal flow, because `--from-tree` deliberately reads the working tree to obtain the code it needs. The guard now only fires on files the draft never touches — exactly the files where the loss happened.
- **Order:** Checked *before* operations are stored. The first attempt checked after, which made the refusal itself destructive: the draft survived but its operations were gone, so the retry the message asks for died with "hollow not found".


### F8 — `sgt save --resolve-plan` now prints the command that settles it

It used to re-print the ambiguity and tell the user to run `sgt save --resolve-plan` — the command they had just run — while the `--confirm-hollow`/`--confirm-op` flags that actually resolve it appeared nowhere. It now prints the executable command with the ids filled in:

```
      settle it:  sgt save --resolve-plan --confirm-hollow 0aa03bad2c19 ... --confirm-op 09cf8a3b7fe7 ...
```

Running that line: `✓ confirmed 3 hollow(s) matched to 9 op(s)`, and `sgt plan status` goes to `(no active plan sessions)`.


### F9 — `plan`, `intent` and `session` now document their subcommands

All three rendered their positional arguments as `sub` / `target` / `rest` with no descriptions and no subcommand list, so `--help` taught nothing. P0 abandoned `intent` and `plan` on this basis, which put S4's and S5's intended sgt mechanisms out of reach. Each now lists its subcommands and what they take.


### F10 — `sgt show`'s footer no longer suggests a command that always fails

It offered `sgt intent show <feature-id>`, which exits 1 every time because that verb expects a commit hash, not a feature id. The footer's own design rule says "a suggestion that silently no-ops is worse than no suggestion". Replaced with `sgt log --focus <handle>`, which answers the same question and works. Every command the footer now prints was run and checked.


### F11 — the top-level help no longer advertises a non-existent verb

`sgt feature <cmd> … regroup, rename, select, why` — but `sgt feature why` does not exist. The verb is the top-level `sgt why <selection>`. The help line now says so.


### F12 — `sgt session gc` now cleans up the branch too

`gc` removed the worktree and the session record but left `sgt-session/<name>` in `git branch` forever, so the tidy-up verb was quietly accumulating refs. It now deletes the branch as well.


## Open issues, ranked by impact on participants

### O1 — `sgt advanced fulfill --from-tree` overwrites uncommitted work (data loss)

**Status: fixed as F7 above.**

The chain that F5 starts ends here. sgt handed the user a next-command suggestion (`edit the working tree, then: sgt fulfill <draft> --from-tree`). Running it rewrote **five files, 174 insertions, 220 deletions** on a clean tree in the moderator's reproduction — from a draft created to remove one symbol's edit — and printed `✓ staged 348 op(s)`. In P0's repository it also reverted their uncommitted S4 edits and resurrected waitlist code they had deleted an hour earlier.

> "Twice now a ✓ has meant 'your codebase no longer parses', and this time it also silently reverted deliberate, committed work. The whole chain that got me here was the tool leading me by the hand into destroying my own tree."

They recovered with `git stash`, deliberately keeping the damage for us, and noted they were no longer willing to trust `sgt undo`.


### O2 — the feature set a participant is handed is thrown away on first refresh

Every participant gets a copy of the study repository. Measured on a fresh copy:

| | Before refresh | After `sgt log --refresh` (27 seconds) |
|---|---|---|
| Features | **34** | **21** |

Thirteen disappear. Two keep their id but are renamed underneath the user — for example, `15d99310` goes from **Promote Next** to **Course Scheduling**. P0 worked the whole of S2 in the pre-refresh epoch, reading that id as a 10-symbol promotion feature. After a refresh the same id is a 23-symbol grab-bag spanning search, rooms, and storage; reverting it removes 66 edits. **The same id means two different things depending on when you looked, and nothing announces the switch.**

**Fix:** refresh at hand-over (added to the facilitator setup script) so every participant starts in the settled epoch.


### O3 — the map and the summary still use different names

With F3 fixed, the empty features are gone from both views. What remains is structural: the map collapses features into subsystem summaries (`Course Planning (2)`), while `sgt log` lists the individual features (`Priority Waitlist`, `Data Storage`, `Time Slots`). Searching both outputs for the same feature name still gives near-disjoint results — not because they disagree, but because one shows aggregates and the other shows members, with no visible relationship between them.

**Proposed:** print the feature names under each subsystem summary in the map, the way checkpoint chips already appear, so both views share one vocabulary.


### O4 — the revert preview is honest and is not the map

The subtraction preview is, in P0's words, "the best output this tool has produced" and "a work plan, not a warning" — it names what goes automatically, the one symbol it refuses to touch, and the exact callers that will break. Every warning it gave came true.

But it is a flat list in a different visual language from the lane view the participant just learned, and `· 20 other feature(s) unchanged` is its only reference back. **Proposed:** render the preview *as* the map with the affected lanes marked, so "what will this do" is answered in the representation the user already holds.


### O5 — words that mean something different from what they say

Five misleading elements found during the session:

- **"removed going forward"** does not mean removed. The preview listed a function as removed; after the revert, `git diff` on that file was empty. The truth is recorded only in `sgt status` ("kept 4 unreproducible file(s)"), which the revert never mentions. P0: *"That phrase cost me real time."*
- **Four different counting units** — edits, ops, symbols, saves — none defined in the tutorial, and they disagree on the same operation. `sgt show` says a feature has 24 edits, the preview header says `23→8`, its footer says `removes 22`, and the applied line says `22 removed, 9 added`. P0 stopped reading the numbers: *"the edit counts have been meaningless all session."*
- **`sgt show`'s footer** printed a command that always fails (fixed as F10).
- **`sgt show <commit-sha>` fails**, though the tutorial says "any id sgt printed can be handed back to it". And `sgt show "<phrase>"` refuses and helpfully suggests the user type `sgt revert <phrase>` instead — pointing a read-only question at the most destructive verb.
- **`sgt intent --help` and `sgt plan --help`** document their arguments as `sub` and `rest` with no subcommand list (fixed as F9).


### O6 — sgt degrades the git repository it sits on

`git show` on a study commit prints **344 `Sgt-Op:` trailer lines**; they accumulate with the ideal (94 at the first commit, 344 by the last). sgt's own commits use a 64-character hex string as the subject line, so `git log --oneline` is also unreadable:

```
f46d95c sgt revert f-10462e17eb7dd53bfa275af8a33aae05a2a77ac74507043473d9ca790166bf1a
```

> "sgt has made the underlying git repo materially worse to use with git. Nobody warned me. If I hand this repo to a colleague who doesn't use sgt, they inherit that."

The README promises that sgt "runs on top of an ordinary git repo". **Proposed:** a readable subject line for sgt's own commits, and trailers folded to one reference rather than the full operation set.


### O7 — symbol-level revert cannot split an atomic operation, which is S4's whole task

S4's root cause is one operation that touches six symbols at once. Reverting the one symbol P0 wanted drags the room audit out with it. The tool's headline pitch is symbol-level surgery; on the one study task that needs it, the granularity is not there.

This is a real limitation to state in the paper rather than a bug, but S4's rubric currently assumes an sgt mechanism (`intent` rewind / `revert f@n`) that this participant could not reach.


### O8 — the participant read the session feature, understood it, and declined to use it

S5 is the task that sessions exist for. P0 ran `sgt session start`, saw it is a git worktree on a branch ("a sane design and I'd have been happy to use it"), and backed out for two stated reasons:

1. `sgt session status` reported **"86 new op(s)"** on a session where they had made zero edits. *"I can't reconcile that number with reality, and unexplained numbers are how I got burned twice today."*
2. The value a session adds over `git checkout -b` is `sgt session land` — *"an unfamiliar mutation verb from a tool whose mutation verbs have, today, corrupted a file, crashed, refused, and wiped my working tree. I am not putting two afternoons of work through it to find out."*

They were explicit that this was not a reflexive reaction: *"If revert/fulfill had behaved this session I'd have used it."* This is the clearest evidence in the pilot that the F1/F2/O1 defects do not just cost their own tasks — they spend the credibility the rest of the tool needs.


### O9 — `feature regroup split` proposes a vacuous split

With F6 fixed, the split preview is reachable by handle — but it proposes a meaningless split. On the `Time Slots` feature it offers: group 0 = the real symbols, group 1 = only internal bookkeeping sentinels (entries like `slots.py::__residue__::HEAD`). Applying it would separate code from internal metadata, not one concern from another. P0 declined to run `--apply` on a preview they could see was wrong, which was the right call and also the reason S6 cannot be completed.


### O10 — a cleanup save mints unnamed features for code you just deleted

Recording the finished S2/S3 work produces:

```
✓ save 8dfb5f4 "remove the waitlist ..."
  ├─ ○ new feature (af-m19244a4) — unnamed; name it: sgt feature rename ...
  ├─ ○ new feature (af-m43fdede) — unnamed ...
  ├─ ○ new feature (af-m4e8d589) — unnamed ...
```

Three new features, born from symbols the participant just *deleted*, each asking to be named. A developer who has just spent twenty minutes removing a feature is told they have created three, and the tool's suggested next action is to name the corpses. **Proposed:** a save whose effect on a symbol is a deletion should not create a new feature for it.


### O11 — the plan matcher misses, and its recovery route was a dead end

**Status: fixed 2026-08-14.**

P0 never exercised the plan loop because `sgt plan --help` was unhelpful (fixed as F9). The moderator ran it afterwards with more knowledge and found that stating a plan, implementing it exactly, and saving the result gave **0 of 3 steps matched**.

The root cause was that the matching grouped steps into transitive clusters, and a cluster holding more than one step never auto-confirmed. One save routinely produces a coarse operation that carries two steps' disjoint work — here, one operation held `enrollment.py::swap` and both functions of `tests/test_swap.py` — and that single shared operation chained the steps into one blob. Nothing about which step was built was in doubt, but the tool asked anyway.

Two changes fixed it:
- Each step now carries the operations that matched *it*, not its cluster's. Steps are grouped as one ambiguous match only when they genuinely compete (predicting the same symbol).
- A bare-file prediction (like `cli.py` for a file at `coursecraft/cli.py`) is now resolved against the actual file paths.

The same reproduction now auto-confirms all three steps and `sgt plan status` reads `3/3`.


## What S2 actually costs when the tool works

P0's S2 budget went entirely on repairing damage, so we did not know what S2 costs with a working tool. Measured afterwards on the fixed build:

1. `sgt log --refresh` (facilitator runs this at hand-over)
2. `sgt revert 10462e17 --yes` — removes the waitlist, reports one overlap and nine still-referencing symbols
3. Hand edits following that report: fix the full-section message in `enroll`, remove the waitlist/notices subparsers and imports in `cli.py`, remove the dead `promote_next` call, remove the persisted `waitlist` key in `storage.py`, delete the implementation files (`promotion.py`, `notify.py`) and their test files, and drop the dead pytest markers
4. S3: recover the `store` fixture using `git show`, minus the `join_waitlist` line, and rewrite the promotion assertion
5. `pytest -q` → **31 passed** (38 baseline minus 7 waitlist/promotion/notify tests), and the parser builds cleanly with no `waitlist` subcommand
6. `sgt save` records the work

The tool does the mechanical removal and produces an accurate punch list; **the punch list is most of the work**, and it is hand editing. Every item on it was named in the preview before the revert applied.

Two traps worth noting for the scoring rubric:
- An *empty* `waitlist` subparser survives the cleanup and keeps `waitlist` in the CLI's subcommand list, while every test passes
- `storage.EMPTY` keeps its `"waitlist": []` key, which nothing tests at all

Budget implication: 15 minutes for S2+S3 is tight but not unreasonable for the fixed build. It is not achievable on version 0.1.0.


## What had to happen before participant 1

Done since this document was first written:

1. **The build participants get.** The setup script now installs sgt from a pinned source checkout instead of PyPI, and records the build hash. Version 0.1.0 corrupts `enrollment.py` during request 2, so a participant on it loses the task.
2. **The `fulfill` data-loss path (O1).** Fixed as F7, with a test.
3. **Refresh at hand-over (O2).** The setup script does it, so every participant starts in the settled feature set.
4. **The scoring script.** `scripts/score_study_repo.py` compares a participant's copy against a pristine one, reports per feature whether tests were kept, removed, or broken, and starts the program.
5. **The missing safety net.** The test suite never builds the command-line parser, so a participant can finish with green tests and a dead program. The scorer now starts the program.
6. **The participant materials.** Rewritten in plain language in `materials/`.
7. **The request 4 answer key.** Records that `test_back_to_back_is_fine` checks a function the app no longer calls, so it stays green through the regression it is supposed to guard.
8. ~~**The plan matcher.**~~ Fixed 2026-08-14 (see O11).

Still open:

9. **Pilot the git condition.** Until it runs we do not know whether the requests are doable in 45 minutes without sgt, which is half the comparison.
10. **Pilot task set B.** The confplan project has never been run by anyone. Its equivalence to coursecraft is an assumption.
11. **Pilot with people.** Both pilots so far used AI agents. They are good at finding defects but cannot tell us about time pressure, confusion, or the point at which someone gives up.
12. **The instruments.** Consent form, background questionnaire, the workload and usability questionnaires, and the block quiz are all described but not built.
13. **Ethics approval and pre-registration.** Neither has been started, and the design says to pre-register before participant 1.


## Method notes for the next pilot

- The think-aloud protocol worked. Requiring `want/guess/ran/got/read/feel/next` per step produced usable data and, more importantly, caught the moments where the participant's *model* of the tool broke, which is where the findings live.
- Moderator verification is not optional. P0 reported that `sgt now` and `sgt log` print different ids for the same commits; they actually print 8-character and 7-character prefixes of the same hash. The belief was wrong, the cause (inconsistent truncation width) is real, and only re-running it revealed which.
- One pilot in the sgt condition is not enough to size the tasks. S2's budget was blown entirely on damage repair, so we still do not know what S2 costs when the tool works.


## The participant's exit debrief, verbatim

Collected at the end of the session. P0 never saw this document and had no access to the moderator's runs. Reproduced in full because the three changes they ask for are, independently, three of the six defects fixed in section 3 — the strongest evidence that this pilot found the things that actually matter to a user.

Note their framing of the split verdict: **"For reading history, yes, starting today. For anything that writes, no."** That is research question 1 and research question 2 separating cleanly in a single participant, and it is the shape the paper should expect to measure at n=12.

---

### 1. Would I use this on a real project?

For reading history, yes, starting today. For anything that writes, no.

The read side earned its place three separate times in one afternoon. The write side failed four times in that same afternoon on a 26-commit toy repo: `revert --yes` emitted Python that no longer parsed, `restore` died on a bare KeyError traceback, `save` refused my changes outright, and `fulfill` rewrote six files and resurrected code I had deleted an hour earlier. Two of those printed a green checkmark while doing it. That is not a rough edge on a good tool, it is an unfinished half. By the end I was committing after every verified step because I could not predict which command would eat my tree, and that tax cancels out everything the read side buys me.

### 2. What I would tell a colleague, in two sentences

It indexes your history by function instead of by file, so you can ask "what changed this symbol, and what else came along for the ride" and get a real answer in one command — that part is excellent and git genuinely cannot do it. Use it to find things, then make the change yourself with git, and do not let it write.

### 3. What would have to change to flip my answer

1. **Never print a success marker over output that does not parse.** It ships tree-sitter. Parse what it just wrote, and refuse. Twice today a checkmark meant "your code no longer imports."
2. **One safety contract, no exceptions.** Preview by default, `--yes` to apply, across every verb and every flag combination. `--keep-dependents` silently voided the contract the tutorial had just taught me, and that is the chain that led me into `fulfill`.
3. **Uniform selectors.** `show` rejects commit hashes; `regroup` rejects the short ids that every other screen prints. Whatever form an id is printed in should be accepted everywhere.

### 4. Where it genuinely beat git

- **Symbol-level co-change.** One command gave me the culprit commit *and* the five other symbols that moved atomically with it, including the one caller that had no business being in that change. That was the bug. `git log -S` gets you the commit; it does not get you the set.
- **The revert preview.** Before acting it named the symbol it would refuse to touch and listed every caller I would have to fix by hand. Every warning came true. Best pre-flight I have used.
- **Splitting a tangled commit without rewriting history.** One commit already sat in two features under two separately named checkpoints, zero code touched, no hashes changed. Git needs an interactive rebase for that, which is a different and worse thing.

---

One methodological note: P0 produced this debrief only after being asked to write it to a file rather than reply. Three prior requests for the same debrief went unanswered while the agent reported itself idle. If a future pilot runs the participant as an agent, collect instruments as written artifacts, not as replies.
