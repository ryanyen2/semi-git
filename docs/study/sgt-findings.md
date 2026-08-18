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
