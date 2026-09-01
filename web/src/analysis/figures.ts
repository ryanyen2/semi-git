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
    // Stage 1: after five minutes of orienting in the project, what the newest
    // piece of work in it reaches -- F1 of the ticked behaviours against the
    // measured key.
    build(
      's1',
      'S1 orient',
      'what the newest work reaches, F1',
      (p, c) => conditionValue(p, c, (m) => m.quizPicksF1, 'mean', ['s1']),
      true,
      [0, 1],
      'F1',
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
    // The stage 2 prediction against the stage 3 outcome report, both F1
    // against the same measured key. `gain` is what doing the removal taught
    // them beyond what the representation had already shown.
    build(
      'blind',
      'Predicted before operating',
      'reach guessed from the representation alone, F1',
      (p, c) => conditionValue(p, c, (m) => m.reach?.blind ?? null, 'mean', ['s3']),
      true,
      [0, 1],
      'F1',
    ),
    build(
      'gain',
      'What operating taught',
      'F1 after the removal, minus F1 predicted',
      (p, c) => conditionValue(p, c, (m) => m.reach?.gain ?? null, 'mean', ['s3']),
      true,
      [-0.5, 1],
      'F1 gained',
    ),
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
