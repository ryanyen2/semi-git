// Counterbalancing, and the ordered list of steps a participant walks through.

import type { BlockAssignment, Condition, GroupId, Half, Project } from '../lib/types'

// docs/study/participant-materials.md, "Which condition, which project".
const GROUPS: Record<GroupId, Array<{ condition: Condition; project: Project }>> = {
  1: [
    { condition: 'git', project: 'coursecraft' },
    { condition: 'sgt', project: 'confplan' },
  ],
  2: [
    { condition: 'sgt', project: 'coursecraft' },
    { condition: 'git', project: 'confplan' },
  ],
  3: [
    { condition: 'git', project: 'confplan' },
    { condition: 'sgt', project: 'coursecraft' },
  ],
  4: [
    { condition: 'sgt', project: 'confplan' },
    { condition: 'git', project: 'coursecraft' },
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
  | 'quiz'
  | 'summary'
  | 'preference'
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
  { id: 'consent', phase: 'Getting started', title: 'Consent', kind: 'consent', half: null, estimateMin: 3 },
  {
    id: 'background',
    phase: 'Getting started',
    title: 'About you',
    kind: 'form',
    half: null,
    instrumentId: 'background',
    estimateMin: 5,
  },

  { id: 'setup-1', phase: 'First half', title: 'Set up your machine', kind: 'setup', half: 1, estimateMin: 10 },
  { id: 'tutorial-1', phase: 'First half', title: 'Practice', kind: 'tutorial', half: 1, estimateMin: 10 },
  { id: 'tasks-1', phase: 'First half', title: 'The requests', kind: 'tasks', half: 1, estimateMin: 45 },
  { id: 'tlx-1', phase: 'First half', title: 'How that felt', kind: 'form', half: 1, instrumentId: 'tlx', estimateMin: 2 },
  { id: 'umux-1', phase: 'First half', title: 'This setup', kind: 'form', half: 1, instrumentId: 'umux', estimateMin: 1 },
  { id: 'hlac-1', phase: 'First half', title: 'The history', kind: 'form', half: 1, instrumentId: 'hlac', estimateMin: 3 },
  { id: 'quiz-1', phase: 'First half', title: 'Five questions', kind: 'quiz', half: 1, instrumentId: 'quiz', estimateMin: 3 },
  { id: 'summary-1', phase: 'First half', title: 'Tell the story', kind: 'summary', half: 1, instrumentId: 'summary', estimateMin: 3 },

  { id: 'setup-2', phase: 'Second half', title: 'Set up the second project', kind: 'setup', half: 2, estimateMin: 5 },
  { id: 'tutorial-2', phase: 'Second half', title: 'Practice', kind: 'tutorial', half: 2, estimateMin: 10 },
  { id: 'tasks-2', phase: 'Second half', title: 'The requests', kind: 'tasks', half: 2, estimateMin: 45 },
  { id: 'tlx-2', phase: 'Second half', title: 'How that felt', kind: 'form', half: 2, instrumentId: 'tlx', estimateMin: 2 },
  { id: 'umux-2', phase: 'Second half', title: 'This setup', kind: 'form', half: 2, instrumentId: 'umux', estimateMin: 1 },
  { id: 'hlac-2', phase: 'Second half', title: 'The history', kind: 'form', half: 2, instrumentId: 'hlac', estimateMin: 3 },
  { id: 'quiz-2', phase: 'Second half', title: 'Five questions', kind: 'quiz', half: 2, instrumentId: 'quiz', estimateMin: 3 },
  { id: 'summary-2', phase: 'Second half', title: 'Tell the story', kind: 'summary', half: 2, instrumentId: 'summary', estimateMin: 3 },

  {
    id: 'preference',
    phase: 'Finishing',
    title: 'Comparing the two',
    kind: 'preference',
    half: null,
    instrumentId: 'preference',
    estimateMin: 6,
  },
  { id: 'handover', phase: 'Finishing', title: 'Hand over your data', kind: 'handover', half: null, estimateMin: 3 },
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
