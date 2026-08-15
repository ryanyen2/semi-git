# sgt robustness findings from the study testbed build

Date started: 2026-08-09. Every entry came from building the coursecraft testbed by driving sgt
the way a participant or their agent would. Fixed entries name the fix; open entries carry a repro.

## Fixed during the build

1. Top-of-file insertion wedged every later save. Anchor facts were birth-only; the fold's
   alphabetical fallback misplaced any insertion whose name did not sort luckily, `put()` refused
   on the byte drift, and the repo stayed wedged. Fix: the miner now diffs anchor facts old-vs-new
   per file and emits revision ops (canonical names across renames). MINER_VERSION 6 -> 7.
   Tests: `tests/core/test_mine.py` anchor-revision block.
2. A miner upgrade did not invalidate the sync no-op gate, so a fixed sgt kept serving the broken
   mining result from `sync_cache.json`. Fix: MINER_VERSION folded into the gate fingerprint.
   Test: `tests/core/test_perf_caches.py::test_sync_no_op_gate_misses_after_miner_upgrade`.
3. Redo-after-undo wedged permanently. The undo excluded the save's ops; re-authoring identical
   content minted a rebirth chained onto the undo commit's salted bottom, whose prune never
   existed (bookkeeping commits are never mined), so reduction dropped the chain and every save
   refused. Fixes: (a) exclusion lift on re-authoring, matched by (symbol, after-version) content;
   (b) the rebirth lookback skips sgt bookkeeping commits. MINER_VERSION 7 -> 8.
   Test: `tests/core/test_verbs.py::test_redo_after_undo_saves_again`.

## Open bugs

4. A revert that removes a file's last live content ops leaves the file on the working tree. The
   R4 backstop keeps a path whose disk bytes the "maximal valid ideal" cannot regenerate, and the
   maximal reduce itself drops the file's legitimate birth op (cause not yet isolated; no fork or
   prune visibly involved). Live repro: `~/repos/sgt-study/coursecraft` at commit `12d911a`
   (`sgt revert f-10462e17@2` claimed to remove `tests/test_priority.py`; the file stayed and its
   test kept running). Also investigate: that revert's put minted an anchor add
   (`ffc9a925`, `tests/test_prereqs.py::__anchor__::data`) for a file the revert never touched.
   Direction discussed: paths inside the edit's own delta are deliberate deletions and should
   skip the backstop (committed bytes are recoverable from git); reduction collateral outside the
   delta keeps it.

## Interaction traps an agent or participant will hit

5. `sgt revert` takes exactly one selection. A multi-op save has no "revert this save" handle:
   git SHAs do not resolve, multiple ids are read as one NL phrase, and the NL resolver then
   offers a single too-narrow op. The workable route (trailer-diff the commit, or an intent
   segment `f-xxxx@n`) is discoverable only by reading internals.
6. Bare `sgt save --resolve-plan` re-prints the ambiguity with no next step; the
   `--confirm-hollow/--confirm-op` form exists but nothing in the output names it.
7. (FIXED 2026-08-14, with the matcher below.) Plan intake sometimes predicts bare filenames
   (`cli.py`) instead of repo paths (`coursecraft/cli.py`); those steps could never match and
   surfaced as permanent drift. A predicted path that names no touched file now resolves to the one
   touched file whose path ends in it; an ambiguous basename is left unresolved rather than guessed.
8. (Corrected 2026-08-09, later the same day: CLI revert consistently previews and requires
   `--yes` in every shape; the earlier "inconsistent gating" reading was wrong. The python-level
   `verbs.revert` applies directly, which is a library-vs-CLI difference, not a CLI bug.)
9. `sgt save --resolve-plan --confirm-*` accepts groups containing ops from much older commits
   without complaint, letting a sloppy caller inflate a plan's record (my own resolver did this
   twice before I scoped it to the commit's trailer diff).
10. `sgt plan status` rejects `--no-color` (other read verbs accept it).
11. `sgt show <NL phrase>` refuses ("not a known feature, checkpoint, op, or symbol") although
    the help's `<sel>` grammar advertises NL phrases; only `revert` resolves them. A
    participant's natural first question ("show me the waitlist feature") dead-ends.
12. BLOCKER for the study's entangled-revert task. In `~/repos/sgt-study/_trial` (a copy of
    coursecraft), `sgt revert "the waitlist feature and everything built on it"` resolves to op
    `d6123900`, previews "would remove 8 op(s)", and on `--yes` demolishes 13 files: every
    later edit of shared symbols (`cli.py::build_parser`, residue segments, later reworks)
    orphans when a mid-chain op is removed, and reduction drops those chains SILENTLY. Result:
    `scheduling.py::room_clashes`, `cmd_stats`, `cmd_instructor`, `ranges_clash`, and the E17+
    features vanish along with the waitlist. The preview number is a lie relative to the
    effective sweep. Repro preserved in `_trial` at commit `e5cfb18`.
13. After finding 12's sweep, `sgt restore` cannot recover the wrongly-swept keepers: both the
    NL form and the exact symbol (`coursecraft/scheduling.py::room_clashes`) answer "no live
    candidate survived re-planning". The only recovery is `sgt undo` of the entire revert, so
    "remove F, keep what came after" is not achievable today on histories whose features
    interleave inside shared symbols.

## FIXED 2026-08-09 (second pass): findings 12 and 13

Option (a) was implemented the same day. `sgt revert` now defaults to **semantic removal plus
forward subtraction** (`sgt/core/subtract.py` + `sgt/core/patch.py`):

- Only the target's own work is removed: ops that are upward-closed are excluded exactly as
  before; a target pinned mid-history has its per-symbol contribution subtracted at the live
  tip instead -- a prune op for symbols the target introduced (plus their residue/anchor
  artifacts), a `merge3` inverse-patch splice for shared symbols. Forward ops can never orphan
  a chain, so later work survives by construction.
- A subtraction that overlaps later edits is KEPT byte-identical and reported ("needs your
  edit"); surviving code that still names removed symbols is reported ("still references
  removed code"), found both by `requires` edges and by a byte scan over touched files.
- The old blanket demolition is still available, explicit only: `sgt revert --take-dependents`.
- Verified end to end on the study repo (`_trial3`..`_trial5`): reverting the waitlist now
  removes exactly the waitlist, reports `enrollment.enroll` (overlap) and the promotion chain
  (references), and after following the report the full suite is green with ZERO collateral --
  rooms, stats, instructor, export, conflicts all intact.
  Tests: `tests/core/test_patch.py`, the three revert tests in `tests/core/test_verbs.py`.

## FIXED 2026-08-13 (pilot 01): finding 14 was a symptom of a layout bug in the subtraction

Finding 14 is closed, and its diagnosis below was chasing the wrong thing. The save did not refuse
because two version constructors disagreed; it refused because **the file on disk did not parse**,
so the miner had nothing to ground a rework against.

The subtraction was writing syntactically invalid Python. A removal whose op-set owns the save that
first recorded a file's residue/anchor partition takes the *layout* chains of entities it otherwise
keeps (they are upward-closed inside the removal, so they are excluded, while the entities survive
on later ops). The fold synthesizes no separator bytes by design, so a kept entity with no residue
is concatenated onto its neighbour:

```python
class EnrollError(Exception):
    passdef find_section(data: dict, section_id: int) -> dict:
```

and a kept entity with no anchor lands in the sorted end-of-file fallback (`find_student` moved
three positions). On the study repo this took `pytest` to 13 collection errors under a green
`✓ revert applied`.

Fix: `sgt/core/subtract.py::_repair_layout` re-grounds the residue and anchor of every kept entity
after a subtraction, re-pointing anchors whose predecessor the removal took, replaying the recorded
images (no invented bytes). Tests: two in `tests/core/test_verbs.py`, both verified to fail without
it. `sgt save` after a hand edit to a spliced file now succeeds, verified end to end.

Full pilot write-up, including four other fixed defects and the open ones: `pilot-01-findings.md`.

## Superseded: finding 14's original analysis (kept for the record)

After a subtraction revert, a *hand edit* to a spliced file can refuse to save: the mined
rework fails to ground and `put()` reports "would overwrite uncommitted changes" for exactly
the hand-edited files. `sgt advanced resync` and cache clearing do not lift it. Repro:
`~/repos/sgt-study/_trial5` (cli.py and enrollment.py edited after the S2 reverts; suite green,
save refused). First debugging step next session: compare, for `cli.py::build_parser`, the
splice op's after-version (`_positional_version(sym, _content_version(image))`) against the
miner's derived before-version (`_positional_version(ent.id, ent.content_hash)` from extraction
of the committed blob) -- if they differ, the two version constructors disagree on span
boundaries (decorator widening or gap attribution) and the fix is to make the splice reuse the
extractor's hash of its own merged image. Interim workaround (pattern verified once during the
E16 build): commit the hand cleanup with plain `git add -A && git commit`; the foreign commit
is absorbed on the next sgt contact.

## Superseded analysis (kept for the record): paths considered for finding 12/13

- (a) Kernel fix: bridge chain gaps at revert time -- re-ground surviving later ops over the
  removed link, with segment-level inverse-patch application for residue text. The honest fix
  and the paper's strongest story, but real kernel work (image splicing, 3-way merges).
- (b) Testbed restructure: give each feature its own module and a per-feature CLI registration
  function (a `COMMANDS` registry) so chains rarely interleave inside one symbol; the
  entanglement then lives at the def-use level (drop -> promotion) where sgt's sweep is
  semantically right. Cheaper (rebuild both repos from the same episode scripts), keeps S2
  honest, but softens the hardest version of the entanglement claim.
- (c) Keep the build, redesign S2's rubric around current semantics: revert = demolition with
  full disclosure, participant inspects the sweep and restores... blocked by finding 13, so
  (c) is not viable until restore can reach orphaned chains.

Recommendation: (a) is the right long-term fix and what the paper ultimately claims; (b) is
the pragmatic unblock for pilot-ready testbeds this week. Doing (b) now and (a) before the
real study gives a working pilot plus the strong claim later.

## Build-quality notes for the answer keys

- E7 and E9 plan-session records over-claim ops (finding 9); the feature graph is unaffected.
- E16's revert needed a follow-up cleanup save to remove `tests/test_priority.py` (finding 4),
  so the history shows revert + cleanup, which is realistic but must be reflected in S1/S4
  ground truth.

## Open bugs found by pilot 01 (2026-08-13)

Full evidence and proposed fixes in `pilot-01-findings.md`. Listed here so the ledger stays the
one place to look.

15. **`sgt advanced fulfill --from-tree` overwrites uncommitted work.** BLOCKS piloting S4/S5 in
    the sgt condition. On a clean tree it rewrote 5 files (174+/220-) from a draft created to
    remove one symbol's edit, printing `✓ staged 348 op(s)`. In the participant's repo it also
    reverted their uncommitted edits and resurrected code they had deleted. `put()` already has
    the "would overwrite uncommitted changes" guard that (correctly) blocks `sgt save`; `fulfill`
    does not consult it. It should refuse, not warn, and it should preview like every other
    mutating verb. Repro: `sgt revert <symbol> --keep-dependents --yes`, then run the `sgt fulfill
    <draft> --from-tree` line the tool itself prints.
16. **The feature set on a fresh copy is provisional and collapses silently.** 34 features before
    any refresh, 21 after; 13 disappear (eleven labelled with bare file paths, duplicated, with
    `af-m…`-shaped ids), and 2 keep their id while being renamed — `15d99310` goes from
    `Promote Next` to `Course Scheduling`. The same id therefore means two different things
    depending on when it was read: pre-collapse it is a 10-symbol promotion feature, post-collapse
    a 23-symbol grab-bag whose revert removes 66 edits.
17. **`sgt feature regroup split` proposes a vacuous split.** On `Time Slots` it offers group 0 =
    the real symbols and group 1 = **only** `__residue__` sentinels. Applying it would separate
    code from bookkeeping artifacts, not one concern from another, and the internal `__residue__`
    marker is leaking into user-facing output. (The selector half of this — `regroup` accepting
    only the full 64-hex id, never the short handle or label every other surface prints — is
    fixed: `plan_move`/`plan_split` now resolve like `plan_merge` already did.)
18. **No verb renames a checkpoint.** Checkpoints are the level the S6 tangle actually lives at,
    and the level whose generated names are worst (`Parser Polish` for "accept lowercase day
    names"). `sgt feature rename` renames features only, and `regroup move --to` requires an
    already-existing leaf, so there is no way to give the split half a decent name. S6 is
    consequently only half-achievable in the sgt condition.
19. **`sgt session gc` leaves the branch behind.** `gc` reported "reaped session" but
    `sgt-session/swap-transactional` is still in `git branch`.
20. **sgt degrades the git repo it sits on.** `Sgt-Op:` trailers accumulate with the ideal — 94
    lines on the first commit, 344 by the 26th — so `git show` is unusable, and sgt's own commits
    use a 64-hex subject, so `git log --oneline` is too. The README promises "an ordinary git
    repo"; a colleague who does not use sgt inherits this.

## FIXED 2026-08-13 (pilot 01, second pass): findings 15, 19, and the plan-loop dead end

- **15 (`fulfill --from-tree` data loss) — fixed.** `stage` now makes the same
  "would overwrite uncommitted changes" refusal `put()` makes, scoped to exclude the paths the
  draft itself authors (`--from-tree` reads its image from those, so they are meant to be dirty)
  and checked *before* the ops are stored and the hollows unlinked, so a refusal leaves the draft
  re-runnable. Test in `tests/core/test_rewrite.py`.
- **19 (`session gc` branch leak) — fixed.** `gc` deletes the session's branch along with its
  worktree and record.
- **Finding 6 (`save --resolve-plan` dead end) — fixed.** It now prints the
  `--confirm-hollow/--confirm-op` line with the ids filled in; running it closes the plan session.
  The remaining problem there was the matcher, which reported 0/3 on work implementing its own
  plan — fixed separately, below.
- Also fixed, from the same pass: `plan`/`intent`/`session` now document their subcommands
  (participants abandoned two verbs because `--help` taught nothing); `sgt show`'s `next:` footer
  no longer suggests `sgt intent show <feature-id>`, which always failed; the top-level help no
  longer advertises `sgt feature why`, which does not exist.

## FIXED 2026-08-14: the plan matcher (pilot 01, O11) and finding 7

A plan built exactly as stated reported `0/3 step(s) matched`. The step<->op join was not at fault —
it found the work. The *grouping* discarded it: candidate edges were union-found into transitive
clusters, and a cluster holding more than one step never auto-confirms. One coarse save op carrying
two steps' disjoint work (`enrollment.py::swap` plus both functions of `tests/test_swap.py`) chained
those steps into a single blob, so a build with nothing ambiguous about it was reported ambiguous.
Reproduced end to end at `1/3` before the fix. In `sgt/loop/match.py`:

- A step carries the ops that matched *it*, not its cluster's. Steps become one n:m group only when
  they **compete** — their predictions share a match key. Two steps predicting the same symbol still
  group and still route to `save --resolve-plan`; two steps that merely share an op do not. Also
  ends finding 9's over-claim by construction: a step can no longer absorb its neighbour's ops.
- Bare-file predictions resolve against the files really touched (finding 7 above), in both the
  matcher and `session_coverage`.
- `confirm_match` merges `plan_matches.json` rather than overwriting, so an op fulfilling two steps
  records both instead of only the last one confirmed.

Tests: four in `tests/loop/test_match.py`, one in `tests/test_cli.py`; all fail without the change.

Still open in the same area: a step whose `predicted_footprint` is empty can never match, and its
work reads as drift. That is the offline path — `_fallback_decompose` cannot guess symbols — so it
bites whenever the LLM decomposer is unavailable *mid-session*, e.g. the OpenAI key expires. Nothing
guesses a match there, deliberately, but reporting the work as unplanned drift is still wrong.

## Test-suite defect found while verifying the above

Three tests — `tests/golden/test_cli_golden.py::test_cli_surface_matches_golden` and two label
tests in `tests/lens/test_tree.py` — fail **only when `OPENAI_API_KEY` is set**. The goldens record
the deterministic offline fallback label (`baz qux`); with a key the LLM labeler runs and returns
something different each time. They are green in CI and red for anyone with a key configured.
Regenerate goldens with the key unset (`env -u OPENAI_API_KEY SGT_UPDATE_GOLDEN=1 pytest
tests/golden/`) until the labeler is stubbed under test.
