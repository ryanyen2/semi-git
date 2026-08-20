# What each artifact in this directory is, and which of them may be pooled

Written 2026-08-17. Every claim in `docs/eval/ledger.md` about V4 should be traceable to a file named here.

**No artifact in this directory may be pooled into a §6.2 table yet.** They were produced across five
successive edits to `sgt/` and three `HARNESS_VERSION` bumps. The pre-registered re-run (F92) supersedes all
of them; these survive as the record of how the defects were found, not as measurements.

| file | ops | system | pool? |
|---|---|---|---|
| `run-linear_history-seed{1..5}.json` | 25–60 | pre-F94/F95, no `system` stamp | no — five system versions behind |
| `run-class_with_methods-seed12.json` | 867, **stopped** | pre-F94/F95 | no — the original hard stop, kept as evidence |
| `run-class_with_methods-seed12-postF97a.json` | 250, clean | post-F97a, single version | no (short) — but it is the clean re-run showing the stop gone |
| `run-ts_export_decorated-seed14.json` | 199, **stopped** | pre-F94/F95 | no — the original hard stop, kept as evidence |
| `run-ts_export_decorated-seed14-postF97a-VERSION-MIXED.json` | 250 | **in flight across the F97a edit** | **never** — exercised two systems |
| `run-linear_history-seed1-h5-f97c.json` | 24 | post-F97a, `HARNESS_VERSION` 5 | no (short) — the F97c specimen, `--replay /tmp/replay-f97c.json --prefix 24` |
| `frozen-baseline-partial/` | — | see its own note | no |

Three sweeps at 2,500 ops (seeds 11, 13, 14) ran for 16 hours and were **killed unfinished on 2026-08-17**
without writing here. They spanned multiple `sgt/` edits, so their numbers were void by construction; the
seed-14 one would also have overwritten the preserved stopped record above. Defects discovered from their
logs (`/tmp/sweepf-{a,c,d}.log`) remain valid as defects; nothing counted from them is a measurement.

## 08-17: the probes, and why they cannot be pooled with the sweep

| file | ops | system | pool? |
|---|---|---|---|
| `run-removed_paths-seed21.json` | 300, complete | `HARNESS_VERSION` 6, `version_mixed: false` | no — instrument two versions behind |
| `run-residue_fork-seed22.json` | 300, complete | `HARNESS_VERSION` 6, `version_mixed: false` | no — same |
| `run-fastapi__asyncer-seed999.json` (smoke 1-4) | 5 each | `HARNESS_VERSION` 6, 7, 8, 9 | no — smoke tests of the `--repo` path, one per instrument fix |

The two probes are the last artifacts written at `HARNESS_VERSION` 6. They are sound *as probes* -- 600
operations, 17 anomalies, every one the layout seam or F42's phantom, all classified by `target_kind` -- and
they are the evidence behind F98 and F99. They are not poolable with anything the sweep produces, because
`HARNESS_VERSION` moved 6 -> 9 the same morning for three instrument fixes:

- **7** -- `fsck --tree` violations were attributed to operations that did not cause them (calibration #9).
- **8** -- `chain_gaps` advisories re-fired on every operation because their dedupe key embedded a commit sha
  (calibration #9b). On the first real repo this alone flagged 5 of 5 operations.
- **9** -- advisory violations now record the live set size, so "advisory and self-healing" is checkable from
  the artifact rather than taken on trust.

All three only ever *reduce* a violation count, and all three were invisible on fixtures. Read together they
are one lesson: every dedupe in this instrument was written against fixture behaviour, where the confounding
state is a sticky constant. On real repositories it grows.

**The sweep runs at `HARNESS_VERSION` 9 and pools only artifacts written at 9.** `aggregate.py` refuses a
mixed pool; that refusal is the point, not an obstacle to route around.

## Two fields to check before believing any future artifact

- `version_mixed` — true means `sgt/` changed while the run was in flight. Do not pool it. Artifacts older
  than instrument error #16 lack the field entirely, which is not the same as `false`.
- `records[i].target_kind` — `entity`, `layout`, or `null` (the target was a feature id or a filename).
  Added at `HARNESS_VERSION` 5. Roughly two thirds of uniform target draws are `layout` ops, which are
  whitespace and ordering facts no user reverts; see harness calibration error #8. A pooled rate that does
  not split on this field is not a statement about user-issuable operations.
