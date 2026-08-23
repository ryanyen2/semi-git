// The panel definitions for Figure 2, in one place.
//
// Its own module because two callers need the same figure: the experimenter
// dashboard, which draws it on screen, and the test that renders it to the SVG
// that goes into the paper. They used to hold a panel list each. Nothing failed
// when the two drifted -- the test went on rendering a publishable figure out of
// its own older list, which is the one property it was there to check.

import type { Condition, RequestId } from '../lib/types'
import { conditionValue, type Dataset, type ParticipantAnalysis } from './pipeline'
import type { PairedPanel } from '../charts/PairedEstimation'
import { REACH_TRIALS } from '../study/tasks'

/** Derived, so a third prediction trial cannot be added and left out of the figure. */
const REACH_IDS: RequestId[] = REACH_TRIALS.map((r) => r.id)

type Picker = (p: ParticipantAnalysis, c: Condition) => number

/**
 * Panel order carries the argument: the top row is what people could tell, the
 * bottom row is what they managed to do.
 *
 * Pickers are functions of the metric rather than positions in a list, because
 * panels come and go as the requests change and an index-based picker goes on
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

  const scoreOf = (rs: RequestId[]): Picker => (p, c) =>
    conditionValue(p, c, (m) => m.score, 'sum', rs)

  return [
    // The locate step is scored from the answer key rather than by a person, so
    // it has no rubric. One work to find, so the bar is a proportion of
    // participants who found it rather than a count out of several.
    build(
      'd2',
      'D2 locate',
      'name the work behind the defect',
      (p, c) => conditionValue(p, c, (m) => (m.locateCorrect ? 1 : 0), 'sum', ['d2']),
      true,
      [0, 1],
      'found it',
    ),
    // The two prediction trials, averaged over the pair rather than plotted
    // separately: they were built to point in opposite directions, so one of them
    // on its own says as much about which target it was as about the tool.
    build(
      'blind',
      'Predicted at a glance',
      'reach guessed from the representation alone, F1',
      (p, c) => conditionValue(p, c, (m) => m.reach?.blind ?? null, 'mean', REACH_IDS),
      true,
      [0, 1],
      'F1',
    ),
    build(
      'gain',
      'What checking bought',
      'F1 after checking, minus F1 at a glance',
      (p, c) => conditionValue(p, c, (m) => m.reach?.gain ?? null, 'mean', REACH_IDS),
      true,
      [-0.5, 1],
      'F1 gained',
    ),
    build(
      'w23',
      'R2+R3 removal',
      'take the waitlist out, keep drops',
      scoreOf(['w2', 'w3']),
      true,
      [0, 4],
      'rubric points',
    ),
    build(
      'damage',
      'Collateral damage',
      'tests broken outside the target',
      (p, c) => conditionValue(p, c, (m) => m.collateralDamage, 'sum'),
      false,
      undefined,
      'failing tests',
    ),
  ]
}
