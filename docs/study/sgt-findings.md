# sgt bugs and limitations found during study preparation

Started: 2026-08-09. Every entry here came from building the coursecraft testbed by using sgt the
way a participant or their AI assistant would. Fixed entries name the fix. Open entries include
reproduction steps.

---

## Fixed during the testbed build

### Finding 1: inserting code at the top of a file wedged every later save

Anchor facts (the metadata sgt uses to position code within a file) were recorded only at creation
time and never updated. When someone inserted code at the top of a file, the fold's alphabetical
fallback placed the insertion incorrectly, `put()` refused because the bytes drifted, and the
repository got permanently stuck.

**Fix:** The miner now diffs anchor facts before and after each commit and emits revision ops with
canonical names across renames. This required a miner version bump from 6 to 7.
**Tests:** `tests/core/test_mine.py`, anchor-revision block.

### Finding 2: upgrading the miner did not invalidate the cache

After upgrading the miner, sgt kept serving the old (broken) mining results from `sync_cache.json`
because the no-op gate did not check the miner version.

**Fix:** `MINER_VERSION` is now part of the cache gate fingerprint, so a miner upgrade always
triggers a re-mine.
**Test:** `tests/core/test_perf_caches.py::test_sync_no_op_gate_misses_after_miner_upgrade`.

### Finding 3: redoing work after an undo wedged the repository permanently

When undoing a save and then re-authoring identical content, the system minted a "rebirth" op
chained onto the undo commit's salted bottom. That bottom's prune never existed (bookkeeping
commits are never mined), so reduction dropped the chain, and every subsequent save was refused.

**Fixes:** (a) The exclusion is now lifted on re-authoring, matched by symbol and after-version
content. (b) The rebirth lookback skips sgt bookkeeping commits. Miner version bumped from 7 to 8.
**Test:** `tests/core/test_verbs.py::test_redo_after_undo_saves_again`.

---

## Open bugs

### Finding 4: reverting a file's last content leaves the file on disk

When a revert removes all the live content ops from a file, the file should be deleted but stays
on the working tree. The R4 backstop keeps any path whose on-disk bytes the "maximal valid ideal"
cannot regenerate, and the maximal reduce itself drops the file's legitimate birth op (root cause
not yet isolated — no fork or prune is visibly involved).

**Reproduction:** In `~/repos/sgt-study/coursecraft` at commit `12d911a`, running
`sgt revert f-10462e17@2` claims to remove `tests/test_priority.py`, but the file stays and its
test keeps running. Additionally, that revert incorrectly minted an anchor-add op (`ffc9a925`,
`tests/test_prereqs.py::__anchor__::data`) for a file the revert never touched.

**Proposed direction:** Paths inside the edit's own delta are deliberate deletions and should skip
the backstop (the committed bytes are recoverable from git). Paths outside the delta that were
affected as collateral should keep the backstop protection.

---

## Interaction traps a participant or agent will hit

### Finding 5: no way to revert a multi-op save in one step

`sgt revert` accepts exactly one selection. A save that contains multiple ops has no "revert this
entire save" handle: git commit SHAs do not resolve, passing multiple IDs is interpreted as a
single natural-language phrase, and the natural-language resolver then offers only a single, overly
narrow op. The workaround (finding the ops via trailer-diff on the commit, or using an intent
segment handle like `f-xxxx@n`) is only discoverable by reading sgt internals.

### Finding 6: `save --resolve-plan` gave no next step (FIXED)

Running bare `sgt save --resolve-plan` re-printed the ambiguity but did not tell the user what to
do next. The `--confirm-hollow`/`--confirm-op` flags existed but were not mentioned in the output.
**Fixed:** The output now prints the full confirmation command with IDs filled in.

### Finding 7: plan intake predicted bare filenames instead of full paths (FIXED 2026-08-14)

Plan intake sometimes predicted bare filenames like `cli.py` instead of repository-relative paths
like `coursecraft/cli.py`. Those predictions could never match any mined ops and showed up as
permanent unresolved drift.

**Fix:** A predicted path that names no touched file now resolves to the single touched file whose
path ends in it. An ambiguous basename is left unresolved rather than guessed.

### Finding 8: exit code confusion on non-tty revert (corrected 2026-08-09)

Initially reported as "inconsistent preview gating" in the CLI revert command. The corrected
diagnosis: the CLI revert consistently previews and requires `--yes` in every form. The
Python-level `verbs.revert` applies directly — this is a library-vs-CLI difference, not a CLI bug.
(This same exit-code behavior later caused finding 21 in the VS Code extension.)

### Finding 9: plan confirmation accepted ops from unrelated old commits

`sgt save --resolve-plan --confirm-*` accepted groups containing ops from much older commits
without complaint. A sloppy caller could inflate a plan's record of what it accomplished. (During
the build, the resolver did this twice before being scoped to only the current commit's trailer
diff.)

### Finding 10: `sgt plan status` rejected `--no-color`

Other read verbs accept `--no-color`, but `plan status` did not.

### Finding 11: `sgt show` did not accept natural-language phrases

`sgt show <natural-language phrase>` refused with "not a known feature, checkpoint, op, or symbol,"
even though the help text's `<sel>` grammar advertises natural-language phrases. Only `revert`
resolved them. A participant's natural first question ("show me the waitlist feature") dead-ended.

---

## Finding 12: entangled revert demolished unrelated features (BLOCKER — FIXED 2026-08-09)

This was the most serious bug found during preparation. In the coursecraft testbed, running
`sgt revert "the waitlist feature and everything built on it"` resolved to op `d6123900`, previewed
"would remove 8 op(s)," and on `--yes` demolished 13 files. Every later edit of shared symbols
(`cli.py::build_parser`, residue segments, later reworks) was orphaned when a mid-chain op was
removed, and the reduction dropped those chains **silently**.

Result: `scheduling.py::room_clashes`, `cmd_stats`, `cmd_instructor`, `ranges_clash`, and the
E17+ features all vanished along with the waitlist. The preview count (8 ops) was misleading
relative to the actual sweep.

**Reproduction preserved:** `~/repos/sgt-study/_trial` at commit `e5cfb18`.

### Finding 13: restore could not recover the wrongly-swept survivors

After finding 12's demolition sweep, `sgt restore` could not bring back the incorrectly removed
code. Both the natural-language form and the exact symbol
(`coursecraft/scheduling.py::room_clashes`) answered "no live candidate survived re-planning."
The only recovery was `sgt undo` of the entire revert. "Remove feature F but keep what came after"
was not achievable on histories where features share symbols.

### Fix for findings 12 and 13

Implemented the same day (option (a) from the design analysis below). `sgt revert` now defaults to
**semantic removal plus forward subtraction** (`sgt/core/subtract.py` + `sgt/core/patch.py`):

- **Only the target's own work is removed.** Ops that are upward-closed are excluded as before. A
  target pinned mid-history has its per-symbol contribution subtracted at the live tip instead:
  a prune op for symbols the target introduced (plus their residue/anchor artifacts), and a
  `merge3` inverse-patch splice for shared symbols. Forward ops can never orphan a chain, so later
  work survives by construction.
- **Overlapping subtractions are preserved.** A subtraction that overlaps later edits is kept
  byte-identical and reported as "needs your edit." Surviving code that still references removed
  symbols is also reported ("still references removed code"), found by both `requires` edges and a
  byte scan over touched files.
- **The old blanket demolition is still available** as an explicit opt-in:
  `sgt revert --take-dependents`.
- **Verified end to end** on the study repo (`_trial3` through `_trial5`): reverting the waitlist
  now removes exactly the waitlist, reports `enrollment.enroll` (overlap) and the promotion chain
  (references), and after following the report the full test suite passes with zero collateral.

**Tests:** `tests/core/test_patch.py`, three revert tests in `tests/core/test_verbs.py`.

---

## Finding 14: subtraction revert broke Python syntax (FIXED 2026-08-13)

Originally misdiagnosed as a version-constructor disagreement. The actual cause: the subtraction
was writing syntactically invalid Python, so the miner had nothing to parse for a subsequent save.

A removal whose op-set owns the save that first recorded a file's residue/anchor partition also
takes the *layout* chains of entities it otherwise keeps (they are upward-closed inside the
removal, so they are excluded, even though the entities themselves survive on later ops). The fold
synthesizes no separator bytes by design, so a kept entity with no residue gets concatenated onto
its neighbor:

```python
class EnrollError(Exception):
    passdef find_section(data: dict, section_id: int) -> dict:
```

And a kept entity with no anchor lands in the sorted end-of-file fallback (during testing,
`find_student` moved three positions). On the study repo this caused pytest to report 13 collection
errors under a green "revert applied" message.

**Fix:** `sgt/core/subtract.py::_repair_layout` re-grounds the residue and anchor of every kept
entity after a subtraction, re-pointing anchors whose predecessor the removal took, and replaying
the recorded images (no invented bytes).
**Tests:** Two in `tests/core/test_verbs.py`, both verified to fail without the fix.
After this fix, `sgt save` after a hand edit to a spliced file succeeds (verified end to end).

Full pilot write-up, including four other fixed defects and the remaining open ones:
`pilot-01-findings.md`.

### Superseded analysis (kept for the record)

After a subtraction revert, a hand edit to a spliced file could refuse to save: the mined rework
failed to ground and `put()` reported "would overwrite uncommitted changes." `sgt advanced resync`
and cache clearing did not fix it.

Reproduction: `~/repos/sgt-study/_trial5` (cli.py and enrollment.py edited after the S2 reverts;
suite green, save refused). First debugging step: compare, for `cli.py::build_parser`, the splice
op's after-version against the miner's derived before-version — if they differ, the two version
constructors disagree on span boundaries and the fix is to make the splice reuse the extractor's
hash of its own merged image. Interim workaround: commit the hand cleanup with plain
`git add -A && git commit` so the foreign commit is absorbed on the next sgt contact.

### Superseded design options for findings 12/13 (kept for the record)

- **(a) Kernel fix** (chosen): Bridge chain gaps at revert time by re-grounding surviving later ops
  over the removed link, with segment-level inverse-patch application for residue text. The honest
  fix and the paper's strongest story, but required real kernel work (image splicing, 3-way merges).
- **(b) Testbed restructure:** Give each feature its own module and a per-feature CLI registration
  function so chains rarely interleave inside one symbol. Cheaper, but softens the strongest version
  of the entanglement claim.
- **(c) Redesign the rubric around current semantics:** Treat revert as a demolition with full
  disclosure, and ask participants to inspect the sweep and restore. Blocked by finding 13 (restore
  could not reach orphaned chains), so this option was not viable.

Recommendation at the time: (a) is the right long-term fix; (b) is a pragmatic unblock for pilots.
Option (a) was ultimately implemented.

---

## Build-quality notes for the answer keys

- E7 and E9 plan-session records over-claim ops (finding 9). The feature graph is unaffected.
- E16's revert needed a follow-up cleanup save to remove `tests/test_priority.py` (finding 4), so
  the history shows revert + cleanup. This is realistic but must be reflected in the S1/S4 ground
  truth.

---

## Open bugs found during pilot 01 (2026-08-13)

Full evidence and proposed fixes in `pilot-01-findings.md`. Listed here so this ledger stays the
single place to look.

### Finding 15: `fulfill --from-tree` overwrites uncommitted work (FIXED)

**Blocked piloting S4/S5 in the sgt condition.** On a clean tree, it rewrote 5 files
(174 additions, 220 deletions) from a draft created to remove one symbol's edit, printing
"staged 348 op(s)." In a participant's repo it also reverted their uncommitted edits and
resurrected code they had deleted.

The `put()` function already had a "would overwrite uncommitted changes" guard that correctly blocks
`sgt save`, but `fulfill` did not check it.

**Fix:** `stage` now makes the same refusal `put()` makes, scoped to exclude the paths the draft
itself authors (those are meant to be dirty for `--from-tree`), and checked before the ops are
stored and the hollows unlinked, so a refusal leaves the draft re-runnable.
**Test:** `tests/core/test_rewrite.py`.

### Finding 16: the feature set on a fresh copy was provisional and collapsed silently

A fresh copy showed 34 features. After the first refresh, 13 disappeared (eleven were duplicates
with bare file-path labels and `af-m…` IDs), and 2 kept their ID but were renamed — for example,
`15d99310` went from "Promote Next" to "Course Scheduling." The same ID therefore meant two
different things depending on when it was read: before the collapse it was a 10-symbol promotion
feature, and after it was a 23-symbol grab-bag whose revert removes 66 edits.

### Finding 17: `feature regroup split` proposed a vacuous split

On the "Time Slots" feature, it offered group 0 = the real symbols and group 1 = only
`__residue__` sentinels (internal bookkeeping markers). Applying it would separate code from
bookkeeping artifacts, not one concern from another, and the internal `__residue__` marker was
leaking into user-facing output.

The selector part of this (the `regroup` command only accepting full 64-hex IDs, not the short
handles or labels every other command prints) has been fixed: `plan_move`/`plan_split` now resolve
the same way `plan_merge` already did.

### Finding 18: no way to rename a checkpoint

Checkpoints are the level where the S6 task's tangle lives, and they have the worst generated
names (for example, "Parser Polish" for "accept lowercase day names"). `sgt feature rename` only
renames features (not checkpoints), and `regroup move --to` requires an already-existing leaf. So
there is no way to give a split checkpoint a meaningful name. As a result, S6 is only
half-achievable in the sgt condition.

### Finding 19: `session gc` left the branch behind (FIXED)

`gc` reported "reaped session" but the branch `sgt-session/swap-transactional` remained in
`git branch`.
**Fix:** `gc` now deletes the session's branch along with its worktree and record.

### Finding 20: sgt degrades the git repository it sits on

`Sgt-Op:` trailers accumulate with the ideal — 94 lines on the first commit, 344 by the 26th — so
`git show` becomes unreadable, and sgt's own commits use 64-hex subjects, so `git log --oneline`
is too. The README promises "an ordinary git repo," but a colleague who does not use sgt inherits
an unusable git history.

---

## Open bugs found during the first editor rehearsal (2026-08-16)

### Finding 21: every revert and restore from the editor failed (FIXED)

The VS Code extension ran `sgt revert <sel>` without `--yes`. When stdin is not a terminal (which
is every call from an extension), `ideal_edit.py:186` prints the preview and returns exit code 2.
The extension's `execFile` treated any non-zero exit as a failure. So the participant would see the
confirmation modal, click Apply, and the editor would show "Command failed" having changed nothing.

Finding 8 had recorded the same exit-2 behavior and concluded it was "a library-vs-CLI difference,
not a CLI bug" — true, but nobody then checked the third caller (the extension).

**Fix:** `Sgt.confirmedMutate`, used by the five call sites that mutate after their own modal
(revert and restore in `commands.ts`, revertKeep in `sgt.ts`, and two in `workbench.ts`).
This had blocked R2, R3, R4, and R5 in the sgt condition's editor half.

### Finding 22a: revert then restore is not a round trip

Reverting a feature and then restoring it does not return the codebase to its original state,
and the tool said nothing about this gap.

**Live reproduction:** Revert "Enrollment Drop" — the upset pulls in `enrollment.drop`, which
belongs to a different feature ("Drop Enrollment"). Then restore "Enrollment Drop" — it reports
success with 11 ops added, but `enrollment.drop` is still missing. Tests show 1 failed, 35 passed
(versus 38 at baseline).

**Structural cause:** Revert takes `I \ ↑X` (includes dependents from other features), but restore
takes `I ∪ ↓X` (includes only the target's own prerequisites). Since `↑X ⊄ ↓X`, they are not
inverses of each other.

**Mitigation:** Revert and restore now print (and return as `restore_gap` in JSON/MCP) a list of
symbols that the earlier revert removed but the restore did not bring back, pointing at `sgt undo`
as the true inverse. Detection walks the undo journal and handles both removal shapes: dropped op
IDs (absent from the ideal) and subtraction splices (still at the symbol's frontier tip).
**Tests:** `tests/cli/test_restore_gap.py`.

The kernel-level question — whether feature restore should optionally take the matching revert's
op-set instead — remains open.

### Finding 22b: two small UI-surface fixes from the same sweep

1. When a handle resolved to no matching feature, the JSON response had `candidates` but no
   `message`, so the extension showed "Cannot revert X." with no explanation. The JSON now carries
   the same message the terminal path shows.
2. `sgt find` was indexing subsystem nodes, so searching for "waitlist" ranked the whole-repo root
   first (its description mentions everything) — an unactionable result for every verb. The index
   now includes only leaf features.

### Finding 22: a revert can leave a file that will not import

Reverting `f-5a0c1336` ("Waitlist Promotion · test_promotion.py", 1 edit) left
`tests/test_promotion.py` beginning with `@pytest.fixture` but with the `import pytest` line
gone. Pytest aborted during collection (1 error, no tests run at all), so the participant's test
suite — their main safety net — reported nothing about the 38 tests that were still fine.

Related to finding 4 but a different symptom: not a file that outlives its ops, but a file left
depending on an op that was removed.

`sgt undo` recovers completely (verified: "undo 452f902 — 1 op(s) restored," then 38 passed),
and the tutorial teaches `undo`, so a participant is not stranded.

---

## Fixed during pilot 01, second pass (2026-08-13): findings 15, 19, and the plan loop

- **Finding 15** (`fulfill --from-tree` data loss): fixed as described above.
- **Finding 19** (`session gc` branch leak): fixed as described above.
- **Finding 6** (`save --resolve-plan` dead end): fixed as described above. The remaining problem
  was the matcher, which reported 0/3 on work that implemented its own plan — fixed separately
  (see the matcher fix below).
- **Also fixed:** `plan`, `intent`, and `session` now document their subcommands (participants had
  abandoned two verbs because `--help` taught nothing). `sgt show`'s `next:` footer no longer
  suggests `sgt intent show <feature-id>`, which always failed. The top-level help no longer
  advertises `sgt feature why`, which does not exist.

---

## Fixed 2026-08-14: the plan matcher (pilot 01, observation 11) and finding 7

A plan that was built exactly as stated reported "0/3 step(s) matched." The step-to-op join was
correct — it found the work. The problem was in the **grouping**: candidate edges were union-found
into transitive clusters, and a cluster holding more than one step never auto-confirmed. One save
op that carried two steps' disjoint work (`enrollment.py::swap` plus both functions of
`tests/test_swap.py`) chained those steps into a single blob, so a build with nothing ambiguous
about it was reported as ambiguous.

Reproduced end to end at 1/3 matched before the fix. Changes in `sgt/loop/match.py`:

- A step now carries only the ops that matched **it**, not its entire cluster's ops. Steps become
  one n:m group only when they **compete** — their predictions share a match key. Two steps
  predicting the same symbol still group and route to `save --resolve-plan`; two steps that merely
  share an op do not. This also fixes finding 9's over-claim by construction.
- Bare-file predictions now resolve against the files actually touched (finding 7).
- `confirm_match` merges `plan_matches.json` rather than overwriting, so an op fulfilling two steps
  records both instead of only the last one confirmed.

**Tests:** Four in `tests/loop/test_match.py`, one in `tests/test_cli.py`; all fail without the
change.

**Still open:** A step whose `predicted_footprint` is empty can never match, and its work reads as
drift. This is the offline path — the fallback decomposer cannot guess symbols — so it happens
whenever the language model decomposer is unavailable mid-session (for example, if the OpenAI key
expires). Nothing guesses a match there deliberately, but reporting the work as "unplanned drift"
is still incorrect.

---

## Test-suite defect found while verifying the above

Three tests — `tests/golden/test_cli_golden.py::test_cli_surface_matches_golden` and two label
tests in `tests/lens/test_tree.py` — fail **only when `OPENAI_API_KEY` is set**. The golden
snapshots record the deterministic offline fallback label (`baz qux`); with a key, the language
model labeler runs and returns something different each time. The tests are green in CI (no key)
and red for anyone with a key configured.

**Workaround:** Regenerate golden snapshots with the key unset:
```bash
env -u OPENAI_API_KEY SGT_UPDATE_GOLDEN=1 pytest tests/golden/
```
The proper fix is to stub the labeler under test.

---

## Finding 21: nine of twenty-four features in each study project owned no code

Found 2026-08-18, while rebuilding the warm-up repository.

`sgt show "Section Waitlist"` in `coursecraft` read:

```
feature cca80773  "Section Waitlist"
  1 edit · 0 symbols in 0 files · last touched 33d ago
  reverting this removes 1 edit
```

A feature with a name, a handle, an op count and a lane on the map, owning nothing. `sgt log --tree`
omitted it, so the tree and the map disagreed about how many features existed. `sgt find` ranked it
normally, because search matches saves and the generated description, so search hid the hole rather
than exposing it. Reverting it did nothing a person would recognise.

Nine such features in `coursecraft` out of twenty-four leaves, nine in `confplan` out of twenty-one.
They included `Section Waitlist`, `Waitlist Promotion` and `Drop Enrollment` — the features two of
the study's three requests ask a participant to remove. Pilot 03 already recorded a participant
picking one of these off the map to "remove the waitlist" and getting nothing; that was read at the
time as a display bug and patched so the two surfaces at least agreed on the number.

**Cause.** The clustering graph's nodes are every *content-bearing* symbol
(`cluster.alive_nodes` → `is_content_bearing`), which includes residue. That is correct for its
purpose: residue carries the within-file cohesion that keeps unrelated entities out of one god-lane.
But residue is positional gap-bytes, not behaviour, and it outnumbers entities roughly 27 to 20 in a
small repo — so Leiden readily forms a community made only of residue. `sgt/core/op.py` already
draws exactly this distinction and says so: `is_content_bearing` is the fold predicate,
`is_behavioral` is the segmentation predicate, "they differ only on `residue`, and that difference
is the point." Nothing was applying the second one when deciding what counts as a feature.

**Rejected fix.** Switching `alive_nodes` to `is_behavioral` removes the husks and also removes most
of the graph's edges: the warm-up repository collapsed from four features to one. Residue is
load-bearing for clustering quality even though it must not confer membership.

**Fix.** `tree._absorb_husk_leaves`, run beside `_prune_empty_leaves` and before any feature id is
minted. A leaf with no behavioral member is folded into the leaf that already owns the entity its
members hang off — which is where `_member_leaf_for` already routes that residue's *ops*, so
membership and op assignment now agree instead of naming two different lanes. Absorbed rather than
deleted, so leaves still partition the alive set exactly (`tests/lens/test_tree.py` asserts this).

**Effect.** `coursecraft` goes from 24 leaves with 9 husks to 14 leaves with none, and the waitlist
becomes a feature a participant can actually act on:

```
feature 10462e17  "Waitlist Enrollment"
  15 edits · 10 symbols in 4 files
  symbols  cli.py::cmd_waitlist_join, cli.py::cmd_waitlist_show, enrollment.py::join_waitlist,
           enrollment.py::waitlist_for, tests/test_waitlist.py::(3 tests), …
```

Its revert preview now names the seven symbols and three files it removes, which checkpoints go,
that `Waitlist Promotion Notices` and `Enrollment Drop` are affected, that eleven other features are
untouched, and that `enrollment.py::enroll` overlaps later edits and needs the participant's own
edit. That is the whole claim of request 2, made legible.

**Guard.** `scripts/check_graph_integrity.py`, wired into `make-study-bundle.sh` and
`make-practice-repo.sh`. It fails the build on a husk, or on a symbol that is in the working tree
but absent from the frontier. It notes, without blocking, symbols that are in the tree but in no
leaf — see below.

**Still open.** Both study projects carry two symbols that are members of a subsystem node but of no
leaf (`confplan/cli.py::cmd_speaker` and its test; the `coursecraft` equivalents resolved on
rebuild). No feature-scoped verb reaches them. This predates the fix above and is unaffected by it:
100 of 102 placed both before and after. It is a coverage gap rather than a false statement, so the
integrity check reports it and does not block.

---

## Finding 22: a tree merge that deleted the node it was rewriting

Found 2026-08-18, while dry-running the bundle build after finding 21.

`sgt log --refresh` on `confplan` printed:

```
✗ KeyError: 'af-m0530ca1c3d2d03aaaaf80de1ae3b8e4f99327844b804093883657ef54919770f'
```

and then rendered a map anyway. That is the whole trap: the command fails, says
so, and still shows you something — the *previous* tree, because `build_map`
died before writing the new one. So every fix to clustering appeared to have no
effect on this project, including finding 21's. The nine husks were being
removed correctly on every run and the result was being thrown away.

**Cause.** `tree._dedup` merges sibling leaves that share a label:

```python
dupes = leaves_by_label[nodes[c]["label"]]
...
for k in dupes[1:]:
    remap[k] = c
    del nodes[k]
nodes[c] = { ..., "depth": nodes[c]["depth"], ... }   # KeyError when k was c
```

When a parent's children list names the same leaf twice, that leaf appears in
its own `dupes[1:]`, so the loop deletes it and the rewrite three lines later
indexes a node that is gone.

**Fix.** Skip `k == c` in the delete loop. One line.

**Effect.** `confplan` builds again: 20 leaves with 9 husks becomes 14 leaves
with none.

**Also changed.** Bundles are built with `sgt log --rebuild` rather than
`--refresh`. A refresh splices unchanged subtrees from the tree it inherits, and
on `confplan` that leaves nine symbols owned by no feature — including all of
`slots.py`, which is exactly what request one asks about — where a cold
recluster of the same repo leaves two. A bundle is built once, from a pristine
copy, for one participant, so a minute buys the graph the current code actually
produces.

**Root cause, found on review.** Not splice. `_apply_assign_pins` builds
`{leaf_id: pinned_fid}` and refuses a self-rename, but never checks whether
`pinned_fid` is already held by a *different* live node. It is, whenever a pin's
plurality leaf moves between builds: the id still sits on the previous build's
leaf, carried across by Greene matching, while the pin now wants it elsewhere.
`_apply_id_map` rewrites every children list through that map, aliasing the two
nodes onto one id — so a parent names the same child twice, and `renamed[rid] =
nd` keeps whichever came last while the other node's members vanish. One bug,
both symptoms: the duplicate child that `_dedup` tripped over, and nine symbols
missing from `confplan` — every top-level symbol of `slots.py` plus its tests,
which is the module request one asks about.

`_apply_id_map`'s docstring claimed the collision could not happen because
"`id_map` only ever covers leaf ids, so internal `N*` ids and the fresh `F*` ids
never collide". The reasoning skips `af-` ids, which is exactly what collided.

Fixed by dropping any rename onto an id a live node already holds and is not
itself giving up in the same map. Both study projects now place 102 of 102.

**A correction to what this file said an hour ago.** The `--refresh` to
`--rebuild` switch was recorded here as the answer of record. It was not a fix:
a cold recluster happens to land the two aliased nodes under different parents,
so the duplicate-child symptom disappears while the members are still lost. It
is kept for a different and smaller reason — a bundle's graph should be a
function of the code, not of whatever tree the source repository happens to
carry.

---

## Found while harvesting the bikecount testbed (2026-08-23)

These came out of building a study repo by letting agents do real work sessions
against it, rather than replaying an authored episode spec. Both are things a
participant would hit in the first ten minutes.

### Finding 39 (open): `sgt session land` leaves the working tree behind the commit

`sgt session land <name>` advances the branch and writes the ops, but the main
repo's working tree and index stay at the pre-land state. `git status` shows the
landed work as staged deletions, the files on disk are the old ones, and
`sgt log --summary` reports "4 file(s) on disk differ from the recorded state
— `sgt save` absorbs them".

That last line is the dangerous part. The suggested next action is exactly wrong:
`sgt save` would absorb the stale tree and record the landed work as deleted. The
recorded ideal is already correct (49 ops, including all 36 the session stamped);
only the checkout is missing.

Reproduce:

    sgt session start work --base main
    # edit and `sgt save` inside .sgt/local/sessions/work
    sgt session land work
    git status --short          # staged modifications reverting the landed work
    grep <new symbol> <file>    # not there

`sgt switch main` does not fix it. It reports the right op count and writes
nothing, presumably because it declines to write over a dirty index. `git reset
--hard HEAD` does fix it, after which `sgt log --summary` says "in sync". The
harvest runner does that reset after every land as a workaround.

### Finding 40 (open): landing writes a plumbing commit and hundreds of trailers

Each land produces a merge commit subjected `sgt land: main`, and both it and the
work commit carry one `Sgt-Op:` trailer per op. A single session put 36 trailer
lines on two commits. sgt's own views hide these (`gitbind.is_bookkeeping`), so
this costs the sgt arm nothing.

It is the git arm that pays, which is the wrong way round for a study that is
trying to be fair to git. `git log` opens on plumbing subjects and `git show`
buries the message under trailers. Pilot 03 recorded the same thing and it was
closed for the old testbed; it comes back the moment history is built by landing
sessions rather than by committing directly. The fix belongs in the bundle build:
render the git arm by stripping the trailers and dropping the plumbing commits,
which changes the shas, so the answer key has to be regenerated per arm.

### Finding 41 (open, testbed design): the pedestrian twin is not isomorphic where it matters

The two study projects have to be the same shape, because a participant does one
in each half and the halves have to be equally hard. Fremont Bridge and the
Melbourne pedestrian counters are the same shape as data, one location with two
sensors and one row per hour, but not the same shape as a task.

Fremont's two sidewalks are directional. On a 2019 weekday the east sidewalk
peaks at 8am and the west at 5pm, so a decision to stop trusting one counter
moves the headline "busiest hour" from 5pm to 8am and visibly inverts the chart.
That is the clearest symptom in the dataset and it needs no domain knowledge to
read.

Melbourne has no equivalent. Scanning every sensor's 2019 weekday profile, all of
them are evening-heavy and none peaks in the morning; the two Bourke Street Mall
sides both peak at 1pm and differ only in level. Dropping one side changes the
numbers and changes nothing anyone would notice.

So one of three things has to happen, and it is a decision rather than a bug:

  - find a twin that is directional, which means another city's bike counter with
    a per-direction split rather than a pedestrian counter
  - pick a target story that works in both, for instance the headline moving from
    "average day" to "average weekday", which is a real change of about 19 percent
    in Fremont and exists in Melbourne too
  - keep Melbourne and accept that its 2020 lockdown collapse is its own strongest
    story, then require only that the two targets have comparable effect sizes
    rather than the same mechanism

The third is the most honest and the least tidy. Whichever is chosen, the
selection gate has to score both projects, not one.

### Finding 42 (open): `sgt now` and the extension's Now panel show sgt's own plumbing

`sgt now` is the orient-me surface, the thing a person reads when they sit back
down, and the thing the study will put in front of someone who has never seen the
repo. On a history built by landing sessions, most of it is sgt talking about
itself. Three of the five entries here are plumbing:

    recently done
        f2a6b202  sgt land: main
        6a8c38a1  add a monthly totals page with a bar chart
        11043d79  sgt land: main
        7c1f0b74  split the hour of day chart into weekday and weekend
        43f29733  sgt land: main

`recently_done` is `history_view(repo, limit=5)["latest_commits"]`
(`sgt/api.py:2707`), an unfiltered window over the newest commits.
`is_bookkeeping_message` already exists in `sgt/store/gitbind.py:110` and already
knows a `sgt land:` subject is not a person's work, but it is only called from the
miner (`sgt/core/mine.py:345`) and from `gitbind.py:789`, never here.

This is not only a terminal problem. `sgt/api.py:1398` says this view feeds the
extension's Now tree, and `editor/vscode/src/types.ts:816` consumes
`recently_done` directly, so the panel in the sidebar shows the same three lines.

It reads worse the more sessions there are, which is exactly the shape of history
this testbed is built to have. A five-line list that spends three lines on
`sgt land: main` is a list that answers nothing.

Filtering `is_bookkeeping_message` out of `recently_done` would fix both surfaces
at once. Worth raising the limit at the same time, since dropping the plumbing
from a window of five leaves two.

### Finding 43 (open): `sgt session start` does not mine before recording the base, so the first session claims the whole repo

`start` records what already existed with `lens.ideal_for_ref(repo, base_sha)`
(`sgt/core/session.py:133`) and does not mine on contact first. `new_op_ids`, the
function that later diffs against that record, does exactly the opposite: it calls
`lens.get(session.scratch)` first, with the comment "mine-on-contact first to
absorb any committed-but-not-yet-mined work". One side of the same subtraction
mines and the other does not.

When the base commit has not been read yet, `base_op_ids` comes back empty, every
op in the scratch tree looks new, and `land` stamps the session name onto all of
them. The session then owns code it never touched.

Measured on the first harvest. `weekday-split` and `monthly-trend` were attributed
correctly, 6 and 16 symbols, the work they actually did. `hour-of-day`, the first
session to start, claimed 43 symbols including `load_readings`, `Reading`,
`daily_totals`, `render_overview`, `page`, `serve`, `README.md` and the csv data
file, none of which it wrote. What that costs is the whole point of the verb:

    $ sgt revert --session hour-of-day
     ▸ rewind  hour-of-day
     also affected
       ● a dashboard over the…      loses 28 edits, re-draft
       ● add a monthly totals page… loses 6 edits, re-draft
       ● Hour-of-Day Charts         loses 14 edits, re-draft
     removes 48 edits across 21 symbols · 7 files

Reverting one afternoon's work offers to demolish the repo, and the preview is
honest about it, which means a participant who reads the preview correctly
concludes the tool is dangerous.

It only bites the first session of a fresh repo, which is exactly the case a study
fixture is built from, and exactly the case a new user is in on day one. Any read
that mines first avoids it; the harvest bootstrap now runs `sgt log --summary`
after the seed commit and asserts the op count is not zero. The real fix is one
line in `start`: mine before reading the base ideal, the way `new_op_ids` already
does.

**Finding 41 resolved, same day.** The problem was the sensor pair, not the city.
Bourke Street Mall is a shopping strip, so both of its sides peak at 1pm and no
commute-shaped story exists there. Scanning every Melbourne sensor's 2019 weekday
profile for a morning peak turns up several, and one of them is a matched pair at
a single location that behaves exactly like the Fremont sidewalks:

    2019 weekday, Spencer St-Collins St
      peak hour   both sides: 5pm    south only: 8am    north only: 5pm

It sits on the walk between Southern Cross Station and the Collins St offices, so
the two sides carry opposite halves of the commute. Dropping either sensor moves
the headline busiest hour, which is the same change the Seattle data supports:

                        both sensors    one sensor dropped
      bikecount             5pm                8am
      footfall              5pm                8am

Two cities, two different things being counted, one mechanism. The twin is
isomorphic where the task needs it to be, and `prep_counts.py` now defaults to
that pair with the reasoning written down next to it.

A second story was tested and rejected on the way. Moving the headline from
"average day" to "average weekday" is worth +16.6 percent in Fremont and +1.4
percent at Bourke Street Mall, so it would have been a real change in one arm and
noise in the other.

### Finding 44 (open): a landed session is addressable but not discoverable

`sgt revert --session <name>` is the most reliable removal verb sgt has. The
workflows guide says so: session attribution "does not depend on the grouping at
all" and "is exact from the very first run". Measured here, it is exactly that,
8 symbols and 11 edits for a session that wrote 8 symbols worth of code.

There is no way to find out that the session exists.

    sgt session status      lists only sessions that have not landed yet
    sgt log                 no session names
    sgt log --tree          no session names
    sgt log --map           no session names
    sgt now                 no session names
    sgt show hour-of-day    ✗ 'hour-of-day' is not a known feature, checkpoint,
                              op, or symbol

So the name has to be known before it can be used, and nothing in the tool will
tell you it. Worse, the only way to see what a session contains is to type
`sgt revert --session <name>` and read the preview, which means the single
affordance for looking at a piece of work is spelled with the word that destroys
it. A cautious person will not type it.

For the study this decides which verb the task can be built on. Either sessions
get a read surface, or the task is built on feature labels instead, which
`sgt log` already suggests by name and which the participant can actually see:

    next:  sgt show "add an east vs west sidewalk comparison page"  (what is it)
           sgt revert "add an east vs west sidewalk comparison page"  (remove it)

Two smaller things worth fixing alongside. `sgt show` refusing a session name is a
one-line resolver addition, since `ops_by_session` already exists and already does
the lookup. And `sgt session status` reporting "no active sessions" after a land
is true but reads as "there were none", when what a person wants is the list of
work that did land.

### Finding 45: agent-written commit messages are legible, which narrows what the study can claim

Not a bug. A result, found by building the testbed the way point 5 asks for, and
one that should change the paper's claim before the study runs rather than after.

The harvested git history reads like this, with no editing:

    ee68435 add a by-year summary table
    3d7ce27 show the east/west split by year, not just as one whole-file average
    5ff9a1d add an east vs west sidewalk comparison page
    d44d00a add a monthly trend page with a chart back to the start of the file
    1052a4d split the hour-of-day chart into weekday and weekend views
    de5d5f9 add hour of day chart for the quarterly report

Every subject says what the work was in the words a person would use. That is not
luck and it is not generosity toward git: when an agent writes the code and the
message, the message describes what it was asked to do, and what it was asked to
do is the intent. Git history built by agents already carries intent at the commit
level.

At the same moment sgt's own tree filed the weekday-split work under a feature
called "Monthly Trend Charts", which is wrong. On locating alone, git was ahead.

So "intent-aligned history makes it cheaper to locate the work behind a defect"
(C1 in the protocol) is in trouble, and the protocol already says that equal
locate performance falsifies it. Better to narrow the claim now than to run twelve
participants into it.

What survives, measured on the same history and the same piece of work:

    $ git revert de5d5f9
    CONFLICT (content): Merge conflict in bikecount/counts.py
    CONFLICT (content): Merge conflict in bikecount/pages.py
    CONFLICT (content): Merge conflict in bikecount/server.py
    CONFLICT (content): Merge conflict in check.py
    error: could not revert de5d5f9... add hour of day chart

    $ sgt revert --session hour-of-day --yes
      ✓ revert applied — 11 edits removed, 0 added.
    $ python3 check.py
      ok: 62,030 readings, 2,585 days, overview renders

Four conflicted files against a clean removal that leaves the app running. The
cause is structural rather than lucky: every session in this history edits
`pages.py::page` to add its nav link and `server.py::Handler.do_GET` to add its
route, so any commit's changes to those two symbols have been edited again by
every commit after it.

The claim the evidence supports is about operating on intent, not reading it.
Reading is a solved problem when agents write the messages. Undoing one intent
whose lines have been overwritten five times is not, and that is where the two
representations actually differ.

`select_target.py` now runs the git revert as a gate. A piece of work that plain
git can undo cleanly cannot carry a task, however good it looks otherwise.

### Finding 46 (open): the overlap warning names a bookkeeping symbol instead of the consequence

Two harvested sessions ended up sharing one rule. `quiet-days` added a
`QUIET_DAY_CROSSINGS` threshold and applied it to the by-year average;
`denominator`, two sessions later, reused the same constant on the hour-of-day
page. So one intent came to live in two sessions, which is the ordinary way an
idea spreads through a codebase.

Reverting the first one is therefore not clean, and sgt knows it:

    $ sgt revert --session quiet-days
     removes 3 edits across 2 symbols · 2 files
      ⚠ kept unchanged (the removal overlaps later edits — needs your edit):
        bikecount/counts.py::__residue__::hourly_averages

The detection is right and the refusal to silently rewrite is right. The sentence
is the problem. It names `__residue__::hourly_averages`, which is sgt's own
positioning record and not a thing anyone wrote, and the strongest word in it is
"needs your edit".

What it means is this:

    $ sgt revert --session quiet-days --yes
      ✓ revert applied — 3 edits removed, 0 added.
    $ python3 check.py
      NameError: name 'counts' is not defined

The app stops running. A person who read the warning had no way to get from
"a residue symbol was kept unchanged" to "the dashboard will not start". Under a
clock they will read the tick, see "revert applied", and move on.

`sgt undo` recovers it completely, which is the saving grace and worth the
participant knowing before they start.

Two fixes, in order of value. Say the consequence: an overlap that leaves a
dangling reference is a broken build, and the oracle is already configured and
could be run against the candidate before applying rather than after. And do not
show `__anchor__`/`__residue__` names in a message meant for a person; the
containing symbol or just the file would carry the same information.

Related to finding 43, where the same class of internal name inflated a footprint
from 8 symbols to 43. These entries are load-bearing inside sgt and meaningless
outside it, and they are currently leaking into the surfaces a person reads.

### Finding 47 (open): the checkpoint-revert preview does not describe what a checkpoint revert does

This is the best operation in the tool and the worst preview of one.

`sgt revert <feature>@<n>` subtracts one checkpoint's contribution from symbols
that later work also edited, and keeps the later work. That is the thing plain git
cannot do at all, and applying it says so clearly:

    $ sgt revert "Time-Based Count Summaries"@6 --yes
      subtracted from shared code (later work kept):
        bikecount/counts.py::yearly_summary, bikecount/pages.py::render_years
      ✓ revert applied — 2 symbol(s) changed, no whole edit removed.

The by-year column goes back from "Average weekday" to "Average day", every other
page is untouched, and the app still runs. Exactly right.

The preview shown before that, on the same command without `--yes`, says:

    ▸ rewind  Time-Based Count Summaries  0083b63a  ████████████  67→69 edits
         [6███]  @6 Weekday Average Day    ██  · kept
         ... every other checkpoint also · kept
     · 3 other features unchanged

Three things in that are wrong for a reader. The checkpoint being reverted is
labelled `kept`. The edit count goes up, 67 to 69, with nothing saying why. And
nothing anywhere says the two sentences that turn out to matter, which are that
two symbols will be rewritten and that later work on them survives.

A whole-feature revert previews beautifully by comparison, naming every checkpoint
and marking it `removed`. So the machinery for a good preview exists; the
checkpoint path is not using it.

For the study this is the difference between a participant reaching for the
sharpest verb sgt has and backing away from it. Under a clock, "everything kept,
edits went up" reads as "this will not do what I want".

The fix is to say in the preview what the apply path already says: name the
symbols that will be rewritten, say later work on them is kept, and either explain
the edit-count rise or drop the count from this path.

## Found while designing the name-addressed task set (2026-08-23)

The task is meant to have participants type the labels they can see, not ids:
`sgt revert "<Feature Name>:<Checkpoint Name>"`, and the same for `restore`.
Testing that exact pair of commands turned up three problems.

### Finding 48: revert takes a checkpoint by name, restore does not give it back

`sgt revert "Time-Based Count Summaries:Weekday Average Day"` works, resolving the
label through `checkpoint_slug`, and does the right thing: it subtracts that
checkpoint's contribution from two symbols later work also edited, keeps the later
work, and the app still runs. Note the separator is `:` and not `@`; `@` is
index-only (`sgt/intent/segment.py:381`), and a spec like `<feature>@<name>` falls
through to the natural-language resolver, which offered to remove 99 edits.

Running restore with the identical selector does nothing:

    $ sgt restore "Time-Based Count Summaries:Weekday Average Day" --yes
     restores 0 edits · no file changes
      ⚠ the earlier revert also removed 2 op(s) this restore does not bring back:
        bikecount/counts.py::yearly_summary, bikecount/pages.py::render_years

Honest, and still the wrong outcome. The warning names exactly the two symbols the
revert had just reported changing, so both halves know what happened and only one
acts on it. `sgt undo` restores it correctly.

### Finding 49: reverting a whole feature by name can leave a file that will not parse

**Diagnosis corrected after a wrong first attempt, kept here because the wrong one
is the tempting one.** My first fix made `_fold_file` emit a newline whenever two
entities ended up adjacent with no gap between them. It made the symptom go away
and it was the wrong layer. `tests/core/test_fold.py` asserts, deliberately and in
a comment, that the fold is "pure verbatim concatenation with zero synthesized
bytes between entities", and the test next to it records why that holds in
practice: *a real mined ideal always carries a trailing residue segment, even if
empty*. The fold synthesizing bytes is a stated invariant, not an oversight, and
the one failing test was the invariant doing its job. Backed out.

The real defect is upstream. A residue op carries the gap after its anchor entity,
and the revert removed one while leaving the entities on both sides of it live. The
gap did not go missing at materialization; it was deleted. So the fix belongs in
whatever decides a revert's op-set: a residue whose neighbouring entities both
survive should fall back to its earlier version rather than being pruned to
nothing. That is real kernel work with its own edge cases (what should happen when
the gap itself was introduced by the reverted work, and both neighbours were too),
and it should be designed rather than patched in the middle of designing a study.

The symptom, for the record:

    $ sgt revert "Monthly Trend Charts" --yes
      ✓ revert applied — 13 edits removed, 0 added.
    $ python3 check.py
      File "bikecount/counts.py", line 76
        return sorted(months.items())def hourly_averages(readings):
      SyntaxError: invalid syntax

Two functions concatenated with no newline between them. `workflows.md` §7 records
this hazard for `propose land --subset`, where two features sit adjacent in a file
and share the whitespace between them. It is not confined to `--subset`: a plain
feature revert hits it too, and the result is not a subtly wrong file but one
Python cannot read.

The oracle is configured in this repo and would have caught it. Nothing ran it.

### Finding 50: restore refuses to undo the revert that broke the file

    $ sgt restore "Monthly Trend Charts" --yes
    ✗ [restore] would leave two live versions of bikecount/pages.py::render_monthly:
      641858df and 6c6a6ee0 both claim the same next version, refused (+2 more)

So the state after finding 49 is a repository that does not compile, and the verb
whose whole job is putting work back declines. `sgt undo` recovers it fully, which
means the recovery path exists and is simply not the one the participant will
reach for after typing `revert`.

Taken together these three decide how the next task set can be written. Only two
commands round-trip today: `sgt revert "<Feature>:<Checkpoint>"` forward, and
`sgt undo` back. Any task that asks a participant to restore by name is asking
them to do something that does not currently work.


## Fixes landed for 48, 49 and 50 (2026-08-23)

All three are in, and the round trip the task set needs works by name in both
directions:

    before:   Average weekday
    sgt revert  "Time-Based Count Summaries@Weekday Average Day"  -> Average day
    sgt restore "Time-Based Count Summaries@Weekday Average Day"  -> Average weekday
    python3 check.py                                              -> ok

**Finding 48 and the `@` half.** `sgt/intent/segment.py` and `sgt/select/resolve.py`:
`@` took an index and nothing else, so `<feature>@<chapter name>` was not read as a
checkpoint at all, fell through to the natural-language rung, matched the feature
alone, and offered to remove 99 edits. Naming a chapter can no longer resolve to
something bigger than the chapter.

**Finding 49.** `sgt/core/subtract.py`. `plan_subtraction` returns early when the
removal is upward-closed, which is what an ordinary feature revert is, and that
branch ran `_prune_emptied_paths` but never `_repair_layout`. Its own comment said
"it needs the same pass" while running half of it. Both passes now run, so the
entities a revert keeps hold on to their separators and to whatever module-level
code lived in the gap.

Recorded above is a wrong first attempt at this, which patched `fold` to synthesize
a newline. One test failed and it was right to: "the fold synthesizes nothing" is a
stated invariant, and the defect was a residue op being deleted, one layer up.

**Finding 50 and the restore half of 48.** `sgt/core/verbs.py`, two changes to
`plan_restore_op_set`. A revert does not only remove, it synthesizes stand-in ops to
hold the layout; restore only ever added, so re-admitting the originals forked
against those stand-ins and restore refused the very rewind revert had just made.
Separately, a *checkpoint* revert removes no ops at all -- it layers a rework op
carrying the subtracted bytes -- so restore found nothing to re-admit and answered
"already in the current ideal; no change" against a page that plainly still showed
the revert. Both paths now peel the scaffolding off.

Two things worth a second opinion rather than burying:

  - Revert-authored ops are identified by the `intent` string a revert stamps on
    them, and intent is advisory metadata everywhere else in the kernel. The
    alternative rule, drop whichever side of a fork is not being restored, would
    discard a teammate's competing later edit, so this is deliberately the narrow
    and conservative choice. A structural marker would be better and is a bigger
    change.
  - Restore's output now reads backwards on the checkpoint path: `restores 0 edits
    ... ✓ restore applied — 2 edits removed, 0 added`. True, since what it removes
    is scaffolding, and confusing. It needs a wording pass before a participant
    reads it.

Finding 47 is untouched: the checkpoint preview still reports `67→69 edits` and
marks the checkpoint being reverted as `kept`. The operation underneath it is now
correct in both directions, and what it says about itself beforehand is not.

### Finding 51 (open): `--as` renames whatever feature the save lands in, which can make the label worse

The plan for the next testbed was to have each harvest agent name its own work with
`sgt save -m "..." --as "<name>"`, so the feature layer would carry labels as
specific as the checkpoint layer already does. It does the opposite.

`--as` names the feature the save *lands in*, and on a repo whose clustering has
already collapsed nine sessions into one node, that means renaming the node:

    $ sgt save -m "add busiest weekday" --as "Busiest Weekday"
      ✓ named "Busiest Weekday"
    $ sgt log --tree
        Busiest Weekday (0083b63a) · 30 symbols

Thirty symbols spanning most of the project, under the name of the six-line
function that happened to be saved last. The generated name it replaced,
"Time-Based Count Summaries", was vague but at least honest about its scope. Every
later agent doing the same thing would overwrite it again, so the final label is
whichever job happened to go last.

`--as` labels; it does not split. The actual problem is granularity, and the verb
for that is `sgt feature regroup split`.

Two ways forward for the study, and they are not equivalent:

  - Curate the tree at build time with `regroup split`/`rename`, then freeze it in
    the bundle. Defensible, since a real team would correct a bad auto-grouping and
    sgt ships the verbs for it, but the study would then be measuring sgt with a
    hand-corrected feature tree and the paper has to say so.
  - Build the task on checkpoints, which were already specific without any
    curation ("Weekday Average Day", "Drop Snowstorm-Quiet Days"). The feature part
    of the selector is then just the container the participant reads off the same
    line of `sgt log --map`, and these all resolve:

        sgt revert "Time-Based Count Summaries@Weekday Average Day"
        sgt revert "f-0083b63a@Weekday Average Day"
        sgt revert "0083b63a@Weekday Average Day"

The second needs nothing fixed and nothing curated, so it is what the next task set
should use.

One display bug spotted alongside: given `f-0083b63a@Weekday Average Day` the
preview echoes back `sgt revert 0083b63a`, dropping the checkpoint from the command
it suggests re-running. The bare-handle form echoes correctly.

### Finding 52 (open): checkpoint boundaries follow time, not intent, so an interleaved intent gets tangled back together

The footfall history was harvested with three jobs that build one idea, spaced out
so other work lands between them, which is how a real backlog behaves:

    4830f91  track the days that behave nothing like a normal day   (session 3)
    8345af6  mark event days on the daily and monthly charts        (session 5)
    af1e290  leave event days out of the averages                   (session 8)

sgt cut that into two features and, inside one of them, into a checkpoint that
merges the event-day work with a job that has nothing to do with it:

    Calendar Context (f-0129e017@2)   14 edits · 9 symbols in 6 files
      saves  4830f91  track the days that behave nothing like a normal day
             376992e  add a by-month page to see the office summer dip
             8345af6  mark event days on the daily and monthly charts

    Exclude Event Days (f-0a413ceb@3)  1 edit · 1 symbol
      saves  af1e290  leave event days out of the averages

So "take the event-day handling out" cannot be said in this vocabulary. Reverting
`Calendar Context` also deletes the by-month page. Reverting `Exclude Event Days`
gets a third of the way. The intent the participant is asked about exists in the
history, in three commits with three clear messages, and does not exist as anything
addressable in the feature tree.

This is worth stating plainly because it cuts against the thesis. The pitch is that
line-level history tangles unrelated work and intent-level history does not. Here
the intent-level grouping did the tangling: `376992e` fell between two event-day
jobs in time, and the segmenter cut on time.

Two things follow. For the study, a task cannot assume an intent is addressable
just because it is legible in the log; the gate has to check that a candidate is a
single selector, and the bikecount gate did not test for that. For sgt, the
segmenter has the save messages available and they say plainly which of those three
belong together. Whatever it is cutting on, it is not that.

The counterpart finding is that `sgt show <checkpoint>` is the best read surface in
the tool. It lists the symbols, names the saves that built the checkpoint, and says
what reverting costs, all in one screen. That is exactly how the tangle above became
visible in about ten seconds.

### Finding 53: whether a feature can be removed at all is decided by the app's architecture, not by the tool

Measured by reverting all sixteen checkpoints of the footfall history one at a
time, in a copy, and running the app afterwards. Seven leave a repository that will
not start. The cause is the same every time and it is not sgt's.

Every job that adds a page has to edit two shared symbols: `pages.py::page`, to put
a link in the nav, and `server.py::Handler.do_GET`, to add a route. So the fifth
page's work is welded to the first page's work through two functions neither job was
really about. Undo any early checkpoint and the routes that came later lose the
function that dispatches them.

This matters well beyond the testbed. The whole proposition is that history recorded
at the level of intent lets you remove one intent. That holds only if the intents
are separable in the code. A router and a nav bar that every feature must edit are a
funnel: they turn twelve independent jobs into one chain, and no representation of
history can unpick what the source has welded together. Line-level history is not
what defeats you there.

Two consequences. For the testbed, the seed is wrong and gets fixed: pages become
self-registering modules discovered at import, so adding one is a new file and
touches nothing shared. For the paper, this is a limit worth stating rather than
hiding, because it predicts where sgt helps and where it cannot. The measurement is
cheap and repeatable: revert each unit, run the app, count what survives.

The related trap, worth its own line. The default view had been showing the last
complete year, and that year is quiet enough that removing a feature moved no number
on any page. The gate reported "no change" for work that plainly changes the app.
Both gates now snapshot the full range. A visibility check that silently measures a
window where nothing happens is worse than having no visibility check, because it
answers confidently.

### Finding 53 confirmed: fixing the architecture fixed the feature tree

Finding 53 said that whether a piece of work can be removed is decided by the app's
shape, not by the tool, and that the seed's shared router and nav bar welded twelve
independent jobs into one chain. The seed was rebuilt so pages are self-registering
modules found at import, and the same twelve jobs were harvested again against it.

The grouping changed on its own, with no curation:

    before, shared router          after, self-registering pages
    5 features from 12 sessions    8 features from 12 sessions
    one node holding 27 symbols    largest node holds 21
    event-day work buried in a     "Event Day Tracking" is its own feature,
    checkpoint next to an          with its own chapters
    unrelated by-month page

Nothing about sgt changed between those two runs. The clustering could finally
separate the event-day work because the code no longer forced every job through
`pages.py::page` and `server.py::Handler.do_GET`.

That is the finding worth carrying into the paper. Intent-level history can only
offer you units the source actually has. Where a codebase funnels every feature
through a shared function, no representation recovers the separation, and where it
does not, the grouping falls out. It also gives a cheap diagnostic anyone can run
on their own repository: revert each unit in a copy, start the app, and count what
survives.

### Finding 54 (fixed): a revert can break the program while the preview reports one symbol

Reverting the target chapter in the bikecount testbed previewed as small and
truthful:

    removes 1 edit across 1 symbol · 1 file: bikecount/metrics.py
      subtracted from shared code (later work kept): metrics.py::hourly_averages

Applying it left the app dead:

    NameError: name 'events' is not defined

The chapter had added `import events` along with the code that used it. Imports are
ordinary text inside a residue, not symbols of their own (`workflows.md` §7), so the
import went back with the residue. A different function, in the same file, added by
a different job, still wanted it. Nothing in the preview was wrong. It was answering
a narrower question than the one the user was asking, which is "is my program still
going to work".

The counts are about the op set. Whether the program runs is a different question,
and the project already has something that knows the answer: the oracle in
`.sgt/oracle.json`, which every study repo configures and which nothing was running.

Fixed by running it once after a destructive verb applies and saying so when it
goes red:

    ✓ revert applied — 1 edit removed, 1 added. (`sgt undo` reverses this.)
    ⚠ smoke now fails after this revert. The edit did what it said; something it
      did not name depends on what went.
       `sgt undo` puts it back, or fix the break and `sgt save`.

Silent when the checks pass, silent when no oracle is configured, and never fatal:
the edit has already landed and this only reports. Verified on both testbeds, loud
on the bikecount target and quiet on the footfall one, which reverts cleanly.

This is the single change most likely to matter to a participant under a clock. The
old behaviour ended on a green tick.

### Finding 52 was wrong. sgt does group an interleaved intent, and I had not found the surface

Finding 52 said the intent spanning three sessions "does not exist as anything
addressable in the feature tree", and used that to argue the segmenter cuts on time
rather than on intent. The first half is true of the feature tree. The conclusion
was wrong, because the feature tree is not the only grouping sgt builds.

`sgt intent list` also prints themes, and the harvested history produced this one
without any help:

    ● Event Day Handling  [theme-df22484c1cd9]
      across f-02528149, f-03f61b86, f-05b64a22, f-08915a9f, f-1cda3c85  (coupled, llm)

    $ sgt intent revert theme-df22484c1cd9
    reverting 3 atom(s) as one group:
        138f7d96  keep event days out of the averages
        7e81c4cc  start tracking event days that break the normal commute pattern
        9fa083e6  mark event days on the daily and monthly charts
      tier: coupled ●

Exactly the three jobs, spread over three afternoons with unrelated work between
them, grouped as one thing and removable as one thing. That is the claim the
project makes, working, on a history nobody authored to make it work.

What was actually wrong was reachability, and it is the same shape as finding 44.
The grouping was computed, and printed, and then only a second verb most people
will never find could act on it:

    sgt show   theme-df22484c1cd9    ✗ not a known feature, checkpoint, op, or symbol
    sgt revert theme-df22484c1cd9    ✗ nothing in this codebase plausibly matches
    sgt intent revert theme-df22484c1cd9   works

Fixed by resolving a `theme-` id on the plain `revert`/`restore` path, through the
same `resolve_group` lookup `sgt intent revert` uses, so the two spellings cannot
disagree about what a theme contains. Verified round trip on the footfall testbed:
the removal leaves the app running and `restore` with the same id puts it back.

Two things worth carrying out of this. The tool was better than my finding said,
and I would not have known without trying the verb rather than reading the tree.
And the pattern is now three for three: sessions (44), themes (52), and chapters
(48) were each computed correctly and each unreachable from the verb a person would
actually type. The grouping work is done. The addressing is where this leaks.

### Finding 55 (open): a theme reverts but does not fully restore

Following finding 52's correction, `sgt revert <theme-id>` and `sgt restore
<theme-id>` were wired onto the plain verbs. Revert works:

    $ sgt revert theme-df22484c1cd9 --yes
      ✓ revert applied — 14 edits removed, 10 added.
    references to the removed module: 6 -> 0

Restore does not come all the way back:

    $ sgt restore theme-df22484c1cd9 --yes
      ✓ restore applied — 1 edit removed, 13 added.
    references to the removed module: 0 -> 1

The app runs either way, so nothing is loudly broken, and the counts are not
symmetric: 14 removed against 13 added.

**Diagnosed further, and it is not a one-liner.** Reverting the theme removed 13 of
its 18 ops and left seven stand-ins in their place. Restore adds the 13 back, and
drops exactly one stand-in, the only one that collides with what it re-admits. The
other six go on masking the restored content.

The obvious fix is to drop all seven, and it does not work: `Ideal.from_ops` refuses
the result, because other live ops are grounded on those stand-ins. They are
load-bearing, not leftovers. So the inverse of a forward subtraction is not "put the
originals back and take the patches away" -- the patches have been built on.

I tried that fix, watched it get rejected by validation on the case that motivated
it, and backed it out rather than leave a guarded branch that never fires. What this
needs is an inverse for forward subtraction that re-grounds the dependents as it
goes, which is the same shape of work as finding 49 and wants designing rather than
patching.

This is why the study's target is a chapter rather than the theme. The chapter
round-trips exactly, verified on both testbeds by the gate, and the theme does not.

Recorded rather than worked around, because the theme is the better story and
should become the target once this is fixed: it is the only grouping that holds all
three event-day jobs, both testbeds produced one unaided, and it is what a
participant would mean by "the event day handling".

A second thing the gate caught, worth keeping separate: reverting the whole theme
moved no rendered page at all over the full date range. Removing the tracking and
the exclusion together cancels out on every page the dashboard draws. A target
nobody can see the effect of cannot carry a task whatever else is true of it.

**One bug of my own, found by testing the apply rather than the preview.**
`_emit_verb_result` takes `yes` keyword-only, and the new branch passed it
positionally, where it landed in `extra`. The preview was right, the confirm line
said "re-run with --yes to apply" when `--yes` had been given, and nothing was
applied. A verb that previews correctly and silently does nothing is worse than one
that refuses, and only running the apply would have caught it.

### Finding 56: `restore` is not the inverse of `revert`. `undo` is.

Finding 55 read this as a theme-specific problem. It is general, and it is the most
important thing in this file.

Measured on the shipped footfall bundle, every chapter reverted in a copy and then
put back, with "put back" meaning every page renders identically to before:

    sgt restore <chapter>    2 of 21 chapters come back exactly
    sgt undo                 comes back exactly

Nineteen of twenty-one restores exit zero, leave the app running, and do not bring
the work back. Nothing says so. That is the same shape as finding 54, and worse,
because here the verb whose entire job is putting things back is the one that
quietly does not.

The cause, from finding 55's investigation: a revert does not only remove ops, it
synthesizes stand-ins to hold the layout and the shared symbols together, and later
work gets grounded on those stand-ins. Restore adds the originals back and drops
only the stand-in that collides. The rest keep masking the restored content, and
they cannot simply all be dropped because `Ideal.from_ops` refuses the result.
`undo` sidesteps all of it by inverting the recorded operation rather than
recomputing an inverse from the op set.

What this changes:

  - The study's card 4 does not name a verb, and the practice sheets teach `undo`
    alongside `restore`, so the task is completable. The sgt sheet's line "Both take
    the same words" is now a promise the tool does not keep, and is corrected.
  - The answer-key gate accepts either route and records which one worked. It
    required `restore` for one iteration, which failed every candidate on the
    bundle and would have been read as "the harvest produced no usable target"
    rather than as "the verb is broken".
  - A user reaching for `restore` after a `revert` today should be told to use
    `undo`. That is a one-line change to what revert prints, and worth making
    before this reaches anyone.

**And a process note.** The gate had this check in one of two code paths. The theme
path got it; the chapter path was a near-copy that never grew it, so every chapter
was certified on "restore exited zero and the app still runs" while the criterion
card 4 actually states went unmeasured. Two paths doing the same job, one of them
correct. They are one function now.

### Finding 57 (open): the selected target reaches almost every option the reach trial offers

With the theme as the target, the measured reach is 9 of the 10 things the trial
offers on footfall and 8 of 10 on bikecount. Ticking every box would score close to
perfect, so `blind`, `checked`, and the `gain` between them all compress toward the
ceiling and RQ1b stops discriminating.

This is the cost of the target the design asks for. A theme spanning three sessions
touches most of a small dashboard, and a narrower chapter has a much better reach
key but is a smaller piece of work. The trial's option list, not the target, is what
should change: ten options over six pages is too coarse when one intent legitimately
touches five of those pages.

Recorded rather than fixed, because fixing it means re-cutting what the trial offers
into finer observations, and that is a design change to make deliberately rather
than at the end of a long session.

### Finding 58 (fixed): a layout repair resurrected the very op the user asked to remove, and CI hung

**Corrected. The first diagnosis below was wrong, and the wrong one is the tidy one.**

CI timed out at thirty minutes on all three Python versions, stalled at 25 percent
with no progress for twenty-nine of them. I reasoned that the post-apply oracle run
from finding 54 was firing on every revert in the suite, gated it on a terminal, and
said that fixed it. It did not. CI timed out again in exactly the same place.

What found it was mechanical rather than clever. `pytest -o faulthandler_timeout`
dumped a stack from the hung test; `git checkout <pre-change> -- sgt/` confirmed the
hang was mine at all; restoring one changed file at a time landed on
`sgt/core/subtract.py`, which was neither file I had suspected.

The actual bug. `tests/core/test_tiers.py` loops "revert whatever ops still cover
`a.py`, until none do". Finding 49's fix re-grounds the residue of entities a
removal keeps, and it was re-emitting a residue op for `a.py` on every pass, so the
loop never terminated.

The test's pattern is fine. The repair was putting back the exact thing the user had
just named. Reverting that op reported success and changed nothing, which is the
same silent-no-op shape as findings 54 and 56. `_repair_layout` now skips any symbol
in the removal's *direct* targets, as opposed to everything its closure swept up.

Verified both directions: the hanging file passes in seconds, and finding 49's
original symptom stays fixed -- reverting the event-day feature still leaves
`metrics.py` parsing and every page rendering.

The gating of the oracle call is kept anyway. It is right on its own terms, since a
real build per revert on an automated path is cost nobody reads.

### The claim that was wrong: the post-apply check was not why CI timed out

Finding 54 added a run of the project's first oracle tier after a destructive verb,
so a revert that quietly breaks the program says so instead of ending on a green
tick. It ran on every revert and restore, on every path.

CI before that change: about four and a half minutes, failing one pre-existing
assertion. CI after: timed out at thirty minutes on all three Python versions,
stalled at 25 percent with no progress for twenty-nine of them. Hundreds of reverts
in the suite, each paying for a real build that nothing read.

Gated on an interactive terminal. The sentence exists for a person who is about to
walk away from a green tick; on the automated paths -- the test suite, an agent
driving the MCP server, a script -- there is nobody to read it and the cost is pure.

**What this says about how it was tested.** The change was verified by running the
verb by hand on two testbeds, and by running the test files that cover the code it
touches. Both passed. What neither could show is the cost of a per-call build
multiplied by a whole suite, because a targeted run is exactly the thing that hides
it. The full suite is around twenty-eight minutes locally against four in CI, so
"run the affected tests" had become the habit, and this is the class of regression
that habit cannot catch.

It was also pushed and tagged before CI finished, on the strength of the Release
workflow going green. Release only checks that the version numbers agree and builds
the artifacts. CI was the job that would have caught this.

### Finding 59 (fixes 56): `restore` resolves against the revert it reverses

Finding 56's diagnosis was right and its conclusion was too pessimistic. It read the
problem as needing "an inverse for forward subtraction that re-grounds the dependents
as it goes", which is real but is not what the common case needs. The common case
needs a different *source of truth*.

`I ∪ ↓X` cannot be `I \ ↑X`'s inverse, for two reasons no amount of care inside the
downset fixes. Revert removes an **up**-set, which reaches other features' work, while
restore unions a **down**-set that by construction reaches only prerequisites, so a
swept dependent stays swept. And revert does not only subtract. It mints stand-ins,
and a union can never take one back off.

But every applied edit already writes a journal entry holding its own before/after
op-sets. The reversal of a revert is therefore a *recorded fact* rather than something
to re-derive, which is why finding 56 measured `undo` as the verb that works. The only
thing keeping `restore` from that record was that the entry did not say which verb had
written it, or what the user had named. It does now
(`lens.record_ideal(..., meta=...)`), and `restore` looks for the revert it reverses
before falling back to the downset (`core.verbs._plan_restore_via_journal`).

The difference from `undo` is the point. Undo re-materializes the prior ideal as an
absolute snapshot, so it reverses only the tail event and refuses once anything landed
on top. Applying the same delta to the *current* ideal makes it random-access. Address
any recorded revert, keep the work committed after it.

Measured on the shipped footfall bundle by finding 56's criterion, every chapter
reverted in a copy and put back, "put back" meaning every page renders identically to
before:

    before   6 of 18 chapters come back exactly   (10 of 18 pass the weak gate)
    after   18 of 18 chapters come back exactly   (18 of 18 pass the weak gate)

The denominator is 18 where finding 56's was 21. The bundle segments into 18 chapters
as it stands today, segmentation having moved since that measurement, so the two
absolute counts are not comparable to each other. The before and after rows are, which
is the comparison this finding rests on. Same bundle, same chapters, same run, and the
only difference is which `sgt` is on the path.

The gap between 6 exact and 10 weak-passing is finding 56's other point, reproduced:
four chapters exit zero with the app running and the work still missing. Twelve of the
eighteen left the dashboard unable to render at all.

That weak gate was `gate_checkpoints.py`'s own restore check, which asked only for exit
0 and a running app. It now compares against the baseline render, the way card 4 states
the task. The two builds then separate cleanly on footfall. Before the fix the gate
returns no candidates at all, 5 restores failing outright and 6 leaving pages wrong,
the study's own target among them. After it, none fail and the target is the one
candidate the gate returns.

The task itself was run end to end, both builds, card 3 then card 4 on
`f-1cda3c85@Exclude Event Days`. Both pass the weak gate. Only the fixed build puts the
dashboard back.

**What is deliberately not fixed.** Where later work sits on a stand-in, that stand-in
is load-bearing and peeling it would orphan the later op. Finding 55's observation,
still true. Those cases decline the event inverse and fall back to the downset, which
behaves exactly as it did before, refusals included.

The decline is all or nothing. A revert can mint several stand-ins for one target, and
peeling only the ones still at a tip puts a definition back while leaving its call site
spliced out. Validation accepts that, because groundedness says nothing about whether a
reversal is complete. Better the old answer than a new way to be quietly wrong.

Reversing a revert *through* work layered on top of it still needs re-addition as a
forward merge at the tip, the dual of how `subtract` removes. That mechanism already
exists. It is `core.rewrite.merge_op`, wired to the CLI as `sgt resolve`, and it is
already the remedy this codebase points at for sibling forks. Extending it to a splice
one hop down, where peeling breaks the later op's `before_version`, is the real work.
That is what finding 55 asked for, and it is now the only case that needs it rather
than the common one.

The structural marker this fix writes (`verb`, `target`, `target_ops` on the journal
entry) is the one finding 50 asked for when it settled for matching an advisory
`intent` string prefix instead. That heuristic, `_revert_scaffolding_over`, is still
present and still correct. It answers for journal entries written before these keys
existed.

**The warning had the same bug, one level up.** `cli.ideal_edit._restore_gap` picked
the revert a restore reverses by taking the newest journal event carrying any delta,
checking neither `kind` nor `verb` nor what was named. Revert `bar`, revert `baz`,
restore `bar`, and it warned that `baz` stayed removed and pointed at `sgt undo`, which
would have thrown away the restore that had just worked. The CLI printed it and MCP
carried it in `restore_gap`, so an agent read the same false claim.

Two verbs answering "which revert is this" with two different rules is the defect
itself, so the warning now asks `plan_restore` and gets the event the edit used. That
needed the preview to carry `target_ops` for a restore, which only the removal planner
had been setting, though the field already meant "the ops the user actually named" for
every verb. Entries written before those keys match nothing and fall back to the old
walk. It also printed `\x00HEAD\x00` as though it were a symbol, a raw null byte on
the terminal and in the MCP payload, because `mine._RESIDUE_HEAD` carries no `__` and
survived the layout-infix collapse.

**What this leaves open for the study.** Card 4 does not name a verb, and the practice
sheets teach `undo` alongside `restore`, both because of finding 56. The tool no longer
requires that workaround, but the protocol is pre-registered and the target was chosen
under it, so changing either is a study-design decision and not a consequence of this
fix. Recorded here so it is made deliberately.

## Walking the sgt arm end to end before the pilot (2026-08-25)

Every stage run as a participant would run it, in both projects, reading the output
rather than the exit code. Four defects were fixed; two are recorded and left alone
because fixing them is not a mid-pilot change.

### Finding 60: `sgt find` printed a command that cannot run

The `next:` line under every search result was `sgt show {id[:12]}`. A symbol's id is
its path-qualified name, so the top hit `bikecount/metrics.py::hourly_averages` was
suggested as `sgt show bikecount/me`, which exits non-zero. The listing under each hit
had the same cut at sixteen characters, printing the file path as `bikecount/metric`
and a feature's handle in a width nothing else in the tool uses.

An id is either whole or it is not an id. `sgt/cli/select.py` now prints a feature
handle at the fourteen characters `sgt intent list` uses, leaves every other id alone,
and omits the id line entirely for a symbol, whose id and label are the same string.
Descriptions clip on a word boundary with an ellipsis; one had been reading
`separated int`.

### Finding 61: a cross-feature theme named `(unwitne`

`sgt intent list` — the screen stage 3 sends an sgt-arm participant to — listed a
theme spanning every feature in the repo whose name was eight characters of a
parenthesis. `intent.group.UNWITNESSED` is the synthetic atom key `"(unwitnessed)"`
for an op whose provenance commits are not in `history()`, and the theme labeller
sliced it with `commit_sha[:8]` as though it were a sha. A second label was cut
mid-word at exactly sixty characters, the hard bound every label site applied without
an ellipsis.

`short_sha` now passes any non-hex key through whole and `clip_label` ellipsizes on a
word boundary; `theme.py`, `theme_segment.py` and `segment.py` mint labels through
both. The labels are also *stored*, so the four shipped bundles carry the old ones:
`scripts/` has no repair verb, and the two sgt-arm study repos were repaired in place
and their `.study/sgt-pristine.tar` rebuilt, which is what `./stage N` restores.

### Finding 62: the consequence report printed twice under `--yes`

`sgt revert <x> --yes` printed the three subtraction lines, then `✓ revert applied`,
then the same three lines again. `_restore_gap_report` already carried a `if not yes`
guard with a comment giving exactly this reasoning; `_subtraction_report` did not. A
warning about work that stays gone reads as two separate problems when it repeats.

### Finding 63: `gains 1 edits`

The verb preview's per-feature badge interpolated a bare count. It now goes through
`plural`, like the two lines under it always did.

### Finding 64 (open): the broken-reference warning fires on the correct answer

Stage 3's removal, done correctly in one command, ends with

    ⚠ still references removed code (fix or revert separately):
      bikecount/charts.py::bar_chart, bikecount/pages/monthly.py::render,
      bikecount/pages/overview.py::render

and then `./check 3` reports that every page renders and the number is right. All
three are false. `bar_chart` takes a parameter *named* `label`; the two `render`
functions contain `class="label"` in their html and pass `label=` as a keyword. The
removed symbol is `bikecount/events.py::label` — which is still in the file. The
analysis is matching a bare name against a symbol name.

Left open deliberately. It is in `core/subtract.py`, it is correctness-critical, and
the fix is not a mid-pilot change. What it costs the study is worth stating plainly:
in a four-minute stage it fires at the moment a participant has just succeeded, and
stage 3's reverse-keyed item is *"I was worried that I had broken something else."*
Any effect it has runs against sgt.

### Finding 65 (open): sgt has less to say about uncommitted work than git

Stage 1 replays an eleven-file change (ten modified, one new module) into the working
copy. `sgt now` reports `unsaved 1 edit(s) in 1 feature`; the dirty miner folds the
whole replay into a single `rework` op. `sgt status` reports seven files, silently
omitting `README.md`, `check.py`, `bikecount/server.py` — which sgt already tracks
symbols in — and the new `bikecount/window.py`. `git status` lists all eleven.

`sgt save` records all eleven correctly, so nothing is lost; the defect is in what the
two summaries say beforehand, and they disagree with each other by a factor of seven.

This is the RQ1 weakness the rehearsal recorded, in its concrete form. The stage-1
tips for the sgt arm named `sgt now` and `sgt status` as the way to read the change,
which pointed the arm at two commands that under-report it. They now name `git diff`
and the editor's diff view for the reading and `sgt save` for the recording, which is
the step C1 is actually about. Both arms have git and both stage bodies already said
"in the editor or in the terminal", so this narrows the comparison to the recording
step rather than widening it — but it is a change to what the arms are given, and it
is recorded here so it is made deliberately.

### Finding 66: a bundle built from a rehearsed source repo ships a truncated history

`make-study-bundle.sh` copies the source repo's working tree, so whatever branch it
was left on is the branch the participant gets. It verified that the three stage tags
exist and never that `main` was at `study/full`. Walking the stages for the checks
above left all four source repos elsewhere — `baseline-footfall` on the three revert
commits of `study/removed` — and a build from that state would have shipped a project
already one or two pieces of work short of itself, silently. Every stage would still
"work", because each one resets first; what breaks is the participant reading a
history missing its own end, and stage 1 replaying a change that is already in it.

The build now refuses unless `HEAD == study/full` and the tree is clean, naming
`./stage 2` as the fix.

### Finding 67 (open, sgt's own suite): the CLI-surface golden depends on whether a key is configured

`tests/golden/test_cli_golden.py::test_cli_surface_matches_golden` fails on a clean
checkout of `main`. Regenerating it and diffing against the committed fixture shows
that every changed line is a feature or checkpoint *label*: the fixture was captured
with no LLM key present, so it records `"label": "baz qux"` and
`"why": "Auto-derived from b.py (no LLM label available)."`, and the capture now runs
the labeller.

Worse than stale: the labeller is not deterministic, and one run produced four
different names for the same feature across the fixtures it appears in —
`File Change Detection`, `Unrelated Change Detection`, `Binary File Tracking`,
`Change Classification`, `Commit Change Analysis`. So the test cannot be made to pass
by refreshing the snapshot; it would fail again on the next run.

The fix is for the capture to pin the labeller off (the same way the fixture was
originally recorded) rather than for the snapshot to be updated. Left alone here: it
is sgt's test infrastructure, not the study, and refreshing the golden would bake
non-deterministic output into a fixture whose whole purpose is to be stable.

Recorded because a red golden invites exactly that refresh. The four fixes above
(findings 60 to 63) are golden-neutral — none of their output appears anywhere in
that diff, which is how they were confirmed not to be the cause.
