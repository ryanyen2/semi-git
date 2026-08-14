# coursecraft build log and ground truth

Built 2026-08-09. Repo: `~/repos/sgt-study/coursecraft`, tag `study-start` at `e7b2fb0`,
28 commits, 38 tests green, sgt graph genuine (built with sgt at MINER_VERSION 8, real plan
sessions, real revert). Author persona: Riley Chen <riley@cs.example.edu>; agent episodes carry
a `Co-Authored-By: Claude` trailer.

## Commit map (episode -> sha)

| Episode | Sha | Date | Save message | Author style |
|---|---|---|---|---|
| E0 seed | b51af3f | (build-dated) | init repo | human |
| E1 scaffold | b9e34c2 | 06-29 | scaffold coursecraft: models, JSON store, CLI init | agent, planned |
| E2 slots | 6db51d1 | 07-01 | add rooms and weekly time slots with parsing and validation | agent, planned |
| E3 catalog | d89bfbf | 07-02 | course and section commands | human |
| E4 enroll | b7e6bc4 | 07-03 | student registry and enroll command | agent |
| E5 id fix | 5d8f5ad | 07-06 | fix id reuse after removing a section | human |
| E6 capacity | 873373f | 07-07 | section capacity limits | human; preceded by a red save + `sgt undo` (3a5d614, build-dated) |
| E7 conflicts | 9d6260e | 07-08 | detect time conflicts when enrolling | agent, planned |
| E8 TANGLE | 079fa49 | 07-10 | add course search | human; also silently makes day names case-insensitive in `slots.parse_slot` |
| E9 prereqs | a9f2dc2 | 07-13 | enforce course prerequisites at enrollment | agent, planned |
| E10 refactor | 1590d8c | 07-14 | extract Repository class for persistence | agent, planned |
| E11 waitlist (F) | f939953 | 07-15 | waitlist for full sections with stable join order | agent, planned |
| E12 promotion | 2edf58a | 07-16 | promote from the waitlist when a seat frees | agent |
| E13 keeper fix | 6ac652c | 07-17 | back-to-back sections are not a conflict | human |
| E14 drop cascade | 9a5d940 | 07-20 | drop command frees the seat and promotes the queue | agent |
| E15 TANGLE keeper | 9e0c81b | 07-21 | timetable export | human; also embeds the section id in the full-section waitlist hint |
| E16 experiment | 94c228f | 07-22 | priority enrollment for seniors | agent, planned |
| E16 revert | 12d911a + b924e5a | 07-23 | sgt revert f-10462e17@2, then a plain-git cleanup commit | sgt bookkeeping + human |
| E17 REGRESSION | 25e91a9 | 07-27 | normalize slot comparison for cross-listed sections and room audits | agent, planned |
| E18 instructor view | 4aac0ba | 07-29 | instructor schedule view | agent |
| E19 stats | 4c2b057 | 07-31 | enrollment stats report | human |
| E20 export fix | 0ba22cf | 08-03 | escape pipes in markdown export | human |
| E21 notices | cce175a | 08-05 | seat notices when the waitlist promotes | agent |
| E22 polish | a9efe18 + e7b2fb0 | 08-07 | README and help text; expand the README | human |

## Subtask ground truth

- **S1 provenance (E8).** Save `079fa49` "add course search" carries two unrelated concerns:
  the search command (`cli.cmd_search`) and lowercase-day leniency in `slots.parse_slot`
  (`day.capitalize()`). Correct answer names the save, both concerns, and that the message
  mentions only search. sgt view: the ops untangle into separate features.
- **S2 entangled removal (waitlist).** Feature chain to remove: E11 `f939953` (join/show),
  E12 `2edf58a` (promote), the promotion half of E14 `9a5d940` (drop STAYS, its cascade goes),
  the waitlist-hint half of E15 `9e0c81b` (export STAYS), E21 `cce175a` (notices). Must remain
  green: markers catalog, enroll, capacity, conflicts, search, prereqs, storage, slots, export,
  rooms, instructor, stats, drop (minus promotion assertion). Expected removed markers:
  waitlist, promotion, notify.
- **S3 selective restore.** Bring back plain join/show (E11 slice) without promotion/notices.
- **S4 regression (E17).** Save `25e91a9` introduced `slots.ranges_clash` using `<` where
  `slots.overlaps` uses `<=` (E13's fix), and routed `enrollment.enroll` through it: back-to-back
  enrollment is wrongly rejected again, while the tested `overlaps` stays green. Minimal fix:
  boundary in `ranges_clash` (or route enroll back through `overlaps`) while keeping the room
  audit. The buried cue: E13's message and test name say back-to-back is legal.
- **E16 revert exemplar.** `sgt show`/log reveal the experiment and its removal; the leftover
  `year` plumbing in models/cli is a deliberate realistic crumb (S1-adjacent red herring).

## Known blemishes (answer keys must not contradict these)

- E0 and the E6 `sgt undo` bookkeeping commit are dated the build day, not the story day.
- E7/E9/E17 plan-session records over-claim ops (resolver defect during build, sgt accepted
  the groups; feature graph unaffected). E16's plan predicted two steps with directory-less
  paths that never matched.
- E16's revert left `tests/test_priority.py` on disk (open sgt bug, `docs/study/sgt-findings.md`
  finding 4); a plain-git cleanup commit `b924e5a` completed the removal, and sgt reported
  nothing-to-save for it (the ideal was already correct).
- pytest.ini gains a `priority` marker at E16 that survives the revert (a realistic crumb).
