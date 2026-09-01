// Counterbalancing, and the ordered list of steps a participant walks through.

import type { BlockAssignment, Condition, GroupId, Half, Project } from '../lib/types'
import { BLOCK_ESTIMATE_MIN } from './tasks'

// docs/study/participant-materials.md, "Which condition, which project".
const GROUPS: Record<GroupId, Array<{ condition: Condition; project: Project }>> = {
  1: [
    { condition: 'git', project: 'bikecount' },
    { condition: 'sgt', project: 'footfall' },
  ],
  2: [
    { condition: 'sgt', project: 'bikecount' },
    { condition: 'git', project: 'footfall' },
  ],
  3: [
    { condition: 'git', project: 'footfall' },
    { condition: 'sgt', project: 'bikecount' },
  ],
  4: [
    { condition: 'sgt', project: 'footfall' },
    { condition: 'git', project: 'bikecount' },
  ],
}

/**
 * Group for the nth participant, 1-indexed, assigned round-robin.
 *
 * Round-robin rather than a shuffled block of twelve, so that any prefix of the
 * cohort is still balanced. Studies stop early. A cohort that only balances at
 * exactly n=12 is unanalyzable at n=9, and the first thing that goes wrong in a
 * study is that somebody does not show up.
 */
export function groupForOrdinal(ordinal: number): GroupId {
  return ((ordinal - 1) % 4 + 1) as GroupId
}

export function blocksForGroup(group: GroupId): [BlockAssignment, BlockAssignment] {
  const spec = GROUPS[group]
  return [
    { half: 1, ...spec[0], label: 'Setup A' },
    { half: 2, ...spec[1], label: 'Setup B' },
  ]
}

export function blockFor(
  blocks: [BlockAssignment, BlockAssignment] | BlockAssignment[],
  half: Half,
): BlockAssignment {
  const b = blocks.find((x) => x.half === half)
  if (!b) throw new Error(`no assignment for half ${half}`)
  return b
}

// ---------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------

export type StepKind =
  | 'welcome'
  | 'consent'
  | 'form'
  | 'setup'
  | 'tutorial'
  | 'tasks'
  | 'preference'
  | 'interview'
  | 'handover'
  | 'done'

export interface Step {
  id: string
  phase: string
  title: string
  kind: StepKind
  half: Half | null
  /** For `form` steps, which instrument to render. */
  instrumentId?: string
  /** Shown in the rail as a rough time cost. */
  estimateMin?: number
}

export const STEPS: Step[] = [
  { id: 'welcome', phase: 'Getting started', title: 'Welcome', kind: 'welcome', half: null },
  { id: 'consent', phase: 'Getting started', title: 'Consent', kind: 'consent', half: null, estimateMin: 2 },
  {
    id: 'background',
    phase: 'Getting started',
    title: 'Pre-Study Questionnaire',
    kind: 'form',
    half: null,
    instrumentId: 'background',
    estimateMin: 2,
  },
  // Setup for the first half also unpacks the shared tooling and, with the
  // consent line ticked, starts building the view of the participant's own
  // repository in the background, so it is ready for the interview at the end.
  { id: 'setup-1', phase: 'First half', title: 'Set up your machine', kind: 'setup', half: 1, estimateMin: 6 },
  { id: 'tutorial-1', phase: 'First half', title: 'Practice', kind: 'tutorial', half: 1, estimateMin: 5 },
  // The estimate for a task block IS the caps plus the answering, read from
  // the stages themselves. Written out as a number it drifts the moment a
  // stage is added, and the drift is invisible: the participant plans their
  // afternoon around the old total while the timers enforce the new one.
  {
    id: 'tasks-1',
    phase: 'First half',
    title: 'The stages',
    kind: 'tasks',
    half: 1,
    estimateMin: BLOCK_ESTIMATE_MIN,
  },
  {
    id: 'after-1',
    phase: 'First half',
    title: 'This setup',
    kind: 'form',
    half: 1,
    instrumentId: 'after',
    estimateMin: 2,
  },

  // The second setup is short: the tooling is already on the machine, so this
  // is unpacking the second project and running its checks.
  { id: 'setup-2', phase: 'Second half', title: 'Set up the second project', kind: 'setup', half: 2, estimateMin: 2 },
  { id: 'tutorial-2', phase: 'Second half', title: 'Practice', kind: 'tutorial', half: 2, estimateMin: 4 },
  {
    id: 'tasks-2',
    phase: 'Second half',
    title: 'The stages',
    kind: 'tasks',
    half: 2,
    estimateMin: BLOCK_ESTIMATE_MIN,
  },
  {
    id: 'after-2',
    phase: 'Second half',
    title: 'This setup',
    kind: 'form',
    half: 2,
    instrumentId: 'after',
    estimateMin: 2,
  },

  {
    id: 'preference',
    phase: 'Finishing',
    title: 'Comparing the two',
    kind: 'preference',
    half: null,
    instrumentId: 'preference',
    estimateMin: 3,
  },
  {
    id: 'interview',
    phase: 'Finishing',
    title: 'Your own repository',
    kind: 'interview',
    half: null,
    estimateMin: 15,
  },
  { id: 'handover', phase: 'Finishing', title: 'Hand over your data', kind: 'handover', half: null, estimateMin: 2 },
  { id: 'done', phase: 'Finishing', title: 'Done', kind: 'done', half: null },
]

export const PHASES = [...new Set(STEPS.map((s) => s.phase))]

export function stepById(id: string): Step | undefined {
  return STEPS.find((s) => s.id === id)
}

export function stepIndex(id: string): number {
  const i = STEPS.findIndex((s) => s.id === id)
  return i < 0 ? 0 : i
}

export function nextStepId(id: string): string {
  const i = stepIndex(id)
  return STEPS[Math.min(i + 1, STEPS.length - 1)].id
}

export function prevStepId(id: string): string {
  const i = stepIndex(id)
  return STEPS[Math.max(i - 1, 0)].id
}

/** Total minutes we tell the participant to expect. */
export const TOTAL_ESTIMATE_MIN = STEPS.reduce((n, s) => n + (s.estimateMin ?? 0), 0)
