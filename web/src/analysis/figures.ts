// The panel definitions for Figure 2, in one place.
//
// Its own module because two callers need the same figure: the experimenter
// dashboard, which draws it on screen, and the test that renders it to the SVG
// that goes into the paper. They used to hold a panel list each. Nothing failed
// when the two drifted -- the test went on rendering a publishable figure out of
// its own older list, which is the one property it was there to check.

import type { Condition } from '../lib/types'
import { conditionValue, type Dataset, type ParticipantAnalysis } from './pipeline'
import type { PairedPanel } from '../charts/PairedEstimation'

type Picker = (p: ParticipantAnalysis, c: Condition) => number

/**
 * The primary tier of protocol v2, one panel per measure, in stage order: what
 * people could read at save time, whether they found the work, what operating
 * taught them beyond the prediction, whether the removal and the restore
 * landed, and what got broken along the way.
 *
 * Pickers are functions of the metric rather than positions in a list, because
 * panels come and go as the stages change and an index-based picker goes on
 * rendering a plausible figure out of the wrong column when they do.
 */
export function figure2Panels(dataset: Dataset): PairedPanel[] {
  const build = (
    id: string,
    title: string,
    subtitle: string,
    pick: Picker,
    higherIsBetter: boolean,
    domain?: [number, number],
    unit?: string,
  ): PairedPanel => ({
    id,
    title,
    subtitle,
    higherIsBetter,
    domain,
    unit,
    values: dataset.participants.map((p) => ({
      pid: p.pid,
      label: p.label,
      git: pick(p, 'git'),
      sgt: pick(p, 'sgt'),
    })),
  })

  return [
    // Stage 1: orientation, which asks no checklist -- the map is on the card,
    // and asking afterwards what it said tested the reading rather than the
    // representation. What the stage reports is the three C1 statements it ends
    // on, meaned with the reverse-keyed one flipped. Self-report, and the only
    // self-report in the primary tier; protocol v2 section 11 says so.
    build(
      's1',
      'S1 orient',
      'understood the project and where its parts came from, 1-7',
      (p, c) => conditionValue(p, c, (m) => m.ratingsMean, 'mean', ['s1']),
      true,
      [1, 7],
      'agreement',
    ),
    // Stage 2: scored from the answer key rather than by a person, so it has
    // no rubric. One work to find, so the bar is a proportion of participants
    // who found it rather than a count out of several.
    build(
      's2',
      'S2 locate',
      'name the work behind the wrong number',
      (p, c) => conditionValue(p, c, (m) => (m.locateCorrect ? 1 : 0), 'sum', ['s2']),
      true,
      [0, 1],
      'found it',
    ),
    // The stage 2 prediction and the stage 3 outcome report -- `blind` and `gain`
    // -- were panels here. Both came from the eleven-option reach checklist, and
    // that checklist is gone from both stages (see tasks.ts): P01 left it
    // untouched three of the four times it was asked. The pipeline still computes
    // `reach` and `quizPicksF1` so the pilot halves that DID answer can be scored
    // after the fact, but a panel that draws an empty column for every participant
    // from here on claims a measure the study no longer collects.
    build(
      's3',
      'S3 removal',
      'take the work out, keep the rest',
      (p, c) => conditionValue(p, c, (m) => m.score, 'sum', ['s3']),
      true,
      [0, 2],
      'rubric points',
    ),
    build(
      's4',
      'S4 restore',
      'every page back to the pre-removal snapshot',
      (p, c) => conditionValue(p, c, (m) => m.score, 'sum', ['s4']),
      true,
      [0, 1],
      'restored',
    ),
    build(
      'damage',
      'Collateral damage',
      'pages moved and tests broken outside the target',
      (p, c) => conditionValue(p, c, (m) => m.collateralDamage, 'sum'),
      false,
      undefined,
      'breakages',
    ),
  ]
}
