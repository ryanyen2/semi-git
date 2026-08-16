// Every questionnaire the study administers, with wording fixed.
//
// Wording lives in code, not in a spreadsheet, because the paper has to report
// it and because an instrument edited between participants is an instrument
// with no scale. Each battery carries a version; if an item ever changes, the
// version changes with it and the old responses stay interpretable.
//
// Source of truth for the design decisions here: docs/study/protocol.md §5.

export type ItemType =
  | 'checkbox'
  | 'text'
  | 'textarea'
  | 'number'
  | 'likert'
  | 'slider'
  | 'select'
  | 'multi'
  | 'grid'
  | 'tlx'
  | 'statement'

export interface Option {
  value: string
  label: string
}

export interface GridRow {
  id: string
  label: string
}

export interface Item {
  id: string
  type: ItemType
  label: string
  /** Compact label used on figure axes. Likert items only. */
  shortLabel?: string
  help?: string
  required?: boolean
  options?: Option[]
  rows?: GridRow[]
  min?: number
  max?: number
  step?: number
  /** Left and right anchor text for scales. */
  anchors?: [string, string]
  /** Scored in the opposite direction when totalled. */
  reverse?: boolean
  placeholder?: string
  /** Which claim or RQ this item serves. Shown in the dashboard, not to the participant. */
  serves?: string
}

export interface Instrument {
  id: string
  version: string
  title: string
  intro?: string
  items: Item[]
  estimateMin: number
  /** Repeated once per half rather than once per session. */
  perHalf: boolean
}

// ---------------------------------------------------------------------------
// Consent
// ---------------------------------------------------------------------------

export const CONSENT: Instrument = {
  id: 'consent',
  version: 'consent-v1',
  title: 'Consent',
  perHalf: false,
  estimateMin: 3,
  intro:
    'Please read each line and tick it if you agree. The first five are needed to take part. The last one is genuinely optional and ticking or not ticking it makes no difference to anything else.',
  items: [
    {
      id: 'read',
      type: 'checkbox',
      required: true,
      label: 'I have read the information sheet and had my questions answered.',
    },
    {
      id: 'recording',
      type: 'checkbox',
      required: true,
      label: 'I agree to my screen and voice being recorded for this session.',
    },
    {
      id: 'telemetry',
      type: 'checkbox',
      required: true,
      label:
        'I agree that the commands I run and the messages I send to the AI assistant during the session are recorded.',
    },
    {
      id: 'deidentified',
      type: 'checkbox',
      required: true,
      label: 'I understand my data will be de-identified and reported in aggregate.',
    },
    {
      id: 'withdraw',
      type: 'checkbox',
      required: true,
      label: 'I understand I can stop at any time, without giving a reason, and still be paid.',
    },
    {
      id: 'quotes',
      type: 'checkbox',
      required: false,
      label:
        'Optional: I agree to short anonymized quotes from my session appearing in a publication.',
    },
    {
      id: 'name',
      type: 'text',
      required: true,
      label: 'Type your name to sign',
      placeholder: 'Your name',
      help: 'Stored with your consent record and separated from your task data before analysis.',
    },
  ],
}

// ---------------------------------------------------------------------------
// Background
// ---------------------------------------------------------------------------

const FREQ: Option[] = [
  { value: '0', label: 'Never' },
  { value: '1', label: 'Rarely' },
  { value: '2', label: 'Sometimes' },
  { value: '3', label: 'Often' },
]

export const BACKGROUND: Instrument = {
  id: 'background',
  version: 'background-v1',
  title: 'About you',
  perHalf: false,
  estimateMin: 5,
  intro:
    'None of this is a test and none of it affects what you are asked to do. It lets us describe who took part, and it lets us tell apart differences caused by the tools from differences caused by experience.',
  items: [
    {
      id: 'yearsCoding',
      type: 'number',
      required: true,
      label: 'Roughly how many years have you been writing code seriously?',
      min: 0,
      max: 50,
    },
    {
      id: 'yearsGit',
      type: 'number',
      required: true,
      label: 'Roughly how many years have you used git?',
      min: 0,
      max: 30,
    },
    {
      id: 'gitVerbs',
      type: 'grid',
      required: true,
      label: 'How often do you use each of these?',
      help: 'There is no right answer here. Plenty of good engineers have never run bisect.',
      options: FREQ,
      serves: 'git expertise composite, 0-24',
      rows: [
        { id: 'log', label: 'git log' },
        { id: 'blame', label: 'git blame' },
        { id: 'bisect', label: 'git bisect' },
        { id: 'revert', label: 'git revert' },
        { id: 'reset', label: 'git reset' },
        { id: 'rebasei', label: 'git rebase -i' },
        { id: 'reflog', label: 'git reflog' },
        { id: 'cherrypick', label: 'git cherry-pick' },
      ],
    },
    {
      id: 'agentTools',
      type: 'multi',
      required: true,
      label: 'Which of these have you used in agent mode, where the tool edits files itself?',
      options: [
        { value: 'claude-code', label: 'Claude Code' },
        { value: 'cursor', label: 'Cursor' },
        { value: 'copilot', label: 'GitHub Copilot agent mode' },
        { value: 'codex', label: 'Codex' },
        { value: 'windsurf', label: 'Windsurf' },
        { value: 'other', label: 'Something else' },
        { value: 'none', label: 'None of these' },
      ],
    },
    {
      id: 'agentFrequency',
      type: 'select',
      required: true,
      label: 'How often do you work with an AI coding assistant?',
      options: [
        { value: 'daily', label: 'Most days' },
        { value: 'weekly', label: 'Most weeks' },
        { value: 'monthly', label: 'Some months' },
        { value: 'rarely', label: 'Rarely' },
        { value: 'never', label: 'Never' },
      ],
    },
    {
      id: 'aiShare',
      type: 'slider',
      required: true,
      label: 'Of the code you shipped last month, roughly what share did an assistant write?',
      min: 0,
      max: 100,
      step: 5,
      anchors: ['None of it', 'All of it'],
    },
    {
      id: 'languages',
      type: 'text',
      required: true,
      label: 'Which languages do you work in most?',
      placeholder: 'Python, TypeScript, ...',
    },
    {
      id: 'priorSgt',
      type: 'select',
      required: true,
      label: 'Have you used a tool called sgt or semi-git before?',
      help: 'If yes, say so. It does not disqualify you from anything, it just has to be recorded.',
      options: [
        { value: 'no', label: 'No' },
        { value: 'heard', label: 'Heard of it, never used it' },
        { value: 'yes', label: 'Yes, I have used it' },
      ],
    },
  ],
}

// ---------------------------------------------------------------------------
// NASA-TLX, raw
// ---------------------------------------------------------------------------

const tlxItem = (
  id: string,
  label: string,
  help: string,
  anchors: [string, string],
  reverse = false,
): Item => ({
  id,
  type: 'tlx',
  label,
  help,
  anchors,
  reverse,
  required: true,
  min: 0,
  max: 100,
  step: 5,
})

export const TLX: Instrument = {
  id: 'tlx',
  version: 'tlx-v1',
  title: 'How that felt',
  perHalf: true,
  estimateMin: 2,
  intro:
    'Six sliders about the half you just finished. Answer quickly, first instinct. There is no good or bad score.',
  items: [
    tlxItem(
      'mental',
      'Mental demand',
      'How much thinking, deciding, looking and remembering did it take?',
      ['Very low', 'Very high'],
    ),
    tlxItem(
      'physical',
      'Physical demand',
      'How much physical activity did it take? For desk work this is usually low, and that is fine.',
      ['Very low', 'Very high'],
    ),
    tlxItem(
      'temporal',
      'Temporal demand',
      'How much time pressure did you feel?',
      ['Very low', 'Very high'],
    ),
    tlxItem(
      'performance',
      'Performance',
      'How successful were you at what you were asked to do?',
      ['Perfect', 'Failure'],
      true,
    ),
    tlxItem(
      'effort',
      'Effort',
      'How hard did you have to work to get to the level of performance you reached?',
      ['Very low', 'Very high'],
    ),
    tlxItem(
      'frustration',
      'Frustration',
      'How irritated, stressed or annoyed did you feel?',
      ['Very low', 'Very high'],
    ),
  ],
}

// ---------------------------------------------------------------------------
// SUS
// ---------------------------------------------------------------------------

const susItem = (id: string, label: string, reverse: boolean): Item => ({
  id,
  type: 'likert',
  label,
  reverse,
  required: true,
  min: 1,
  max: 5,
  anchors: ['Strongly disagree', 'Strongly agree'],
})

export const SUS: Instrument = {
  id: 'sus',
  version: 'sus-v1',
  title: 'This setup',
  perHalf: true,
  estimateMin: 3,
  intro:
    'Ten quick statements about the setup you just used. Rate each one on how much you agree. If you are unsure, pick the middle.',
  items: [
    susItem('s1', 'I think that I would like to use this setup frequently.', false),
    susItem('s2', 'I found this setup unnecessarily complex.', true),
    susItem('s3', 'I thought this setup was easy to use.', false),
    susItem(
      's4',
      'I think that I would need the support of a technical person to be able to use this setup.',
      true,
    ),
    susItem('s5', 'I found the various functions in this setup were well integrated.', false),
    susItem('s6', 'I thought there was too much inconsistency in this setup.', true),
    susItem(
      's7',
      'I would imagine that most people would learn to use this setup very quickly.',
      false,
    ),
    susItem('s8', 'I found this setup very awkward to use.', true),
    susItem('s9', 'I felt very confident using this setup.', false),
    susItem(
      's10',
      'I needed to learn a lot of things before I could get going with this setup.',
      true,
    ),
  ],
}

// ---------------------------------------------------------------------------
// History legibility and agent collaboration. This is Figure 1.
// ---------------------------------------------------------------------------

const hlacItem = (
  id: string,
  shortLabel: string,
  label: string,
  serves: string,
  reverse = false,
): Item => ({
  id,
  type: 'likert',
  label,
  shortLabel,
  serves,
  reverse,
  required: true,
  min: 1,
  max: 7,
  anchors: ['Strongly disagree', 'Strongly agree'],
})

export const HLAC: Instrument = {
  id: 'hlac',
  version: 'hlac-v1',
  title: 'Working with this project history',
  perHalf: true,
  estimateMin: 3,
  intro:
    'Ten statements about the half you just finished. Think about what it was actually like, not what you think it should have been like.',
  items: [
    hlacItem('q1', 'Found when it changed', 'I could find when a behavior changed.', 'C1'),
    hlacItem('q2', 'Found why it changed', 'I could find out why a change was made.', 'C1'),
    hlacItem(
      'q3',
      'Saw the whole piece of work',
      'When I found a change, I could see what larger piece of work it belonged to.',
      'C1',
    ),
    hlacItem(
      'q4',
      'Knew what else it would touch',
      'Before I changed anything, I could tell what else would be affected.',
      'C2',
    ),
    hlacItem(
      'q5',
      'Removed a feature safely',
      'I could take a feature out without worrying about breaking what came after it.',
      'C2',
    ),
    hlacItem(
      'q6',
      'Recovered from mistakes',
      'When something went wrong, I could get back to a good state.',
      'C2',
    ),
    hlacItem(
      'q7',
      'Clear picture of the project',
      'I ended up with a clear picture of how this project got to where it is.',
      'C3',
    ),
    hlacItem(
      'q8',
      'Directed the assistant precisely',
      'I could point the assistant at exactly the part of the history I meant.',
      'Q4',
    ),
    hlacItem(
      'q9',
      "Checked the assistant's work",
      'I could check what the assistant did against what I asked for.',
      'Q4',
    ),
    hlacItem(
      'q10',
      'Fought the tool',
      'I spent effort fighting the tool rather than doing the task.',
      'attention check',
      true,
    ),
  ],
}

// ---------------------------------------------------------------------------
// Quiz. Free text plus confidence; grading happens in the dashboard against a
// key that never reaches this bundle.
// ---------------------------------------------------------------------------

const quizItem = (id: string, label: string, help?: string): Item[] => [
  { id, type: 'textarea', label, help, required: true, placeholder: 'A sentence is plenty.' },
  {
    id: `${id}_conf`,
    type: 'slider',
    label: 'How sure are you?',
    min: 0,
    max: 100,
    step: 5,
    required: true,
    anchors: ['Guessing', 'Certain'],
  },
]

export const QUIZ: Instrument = {
  id: 'quiz',
  version: 'quiz-v1',
  title: 'Five questions, project closed',
  perHalf: true,
  estimateMin: 3,
  intro:
    'Close the project and your editor before you start. Answer from memory. Getting these wrong is expected and useful, so please do not go and look.',
  items: [
    ...quizItem('q1', 'Which feature was added and then deliberately removed?'),
    ...quizItem(
      'q2',
      'Which of these came first: conflict detection, capacity limits, or the waitlist?',
    ),
    ...quizItem('q3', 'Did the previous maintainer work alone?'),
    ...quizItem(
      'q4',
      'Name one change that was later corrected, and say what the correction was.',
    ),
    ...quizItem('q5', 'Which single change touched the most unrelated concerns?'),
  ],
}

// ---------------------------------------------------------------------------
// Summary task
// ---------------------------------------------------------------------------

export const SUMMARY: Instrument = {
  id: 'summary',
  version: 'summary-v1',
  title: 'Tell the story',
  perHalf: true,
  estimateMin: 3,
  intro:
    'Three minutes, project still closed. Say it out loud as you type, so we have both. Bullet points are fine.',
  items: [
    {
      id: 'story',
      type: 'textarea',
      required: true,
      label:
        'Without looking at the project, tell the story of it. What was built, in what order, what went wrong, and what was undone?',
      placeholder: 'Start anywhere.',
    },
  ],
}

// ---------------------------------------------------------------------------
// Preference and close
// ---------------------------------------------------------------------------

const prefOptions: Option[] = [
  { value: 'A', label: 'Setup A' },
  { value: 'B', label: 'Setup B' },
  { value: 'none', label: 'No real difference' },
]

export const PREFERENCE: Instrument = {
  id: 'preference',
  version: 'preference-v1',
  title: 'Comparing the two setups',
  perHalf: false,
  estimateMin: 6,
  intro:
    'Now that you have used both, which one would you rather have had for each kind of job? There is no expected answer and "no real difference" is a real answer.',
  items: [
    {
      id: 'archFind',
      type: 'select',
      required: true,
      options: prefOptions,
      label: 'Finding when and why something changed',
    },
    { id: 'archFindWhy', type: 'textarea', required: true, label: 'Why?', placeholder: 'One or two sentences.' },
    {
      id: 'archRemove',
      type: 'select',
      required: true,
      options: prefOptions,
      label: 'Taking a feature out without breaking things',
    },
    { id: 'archRemoveWhy', type: 'textarea', required: true, label: 'Why?' },
    {
      id: 'archRegression',
      type: 'select',
      required: true,
      options: prefOptions,
      label: 'Finding what caused a regression',
    },
    { id: 'archRegressionWhy', type: 'textarea', required: true, label: 'Why?' },
    {
      id: 'archAgent',
      type: 'select',
      required: true,
      options: prefOptions,
      label: 'Working with the AI assistant',
    },
    { id: 'archAgentWhy', type: 'textarea', required: true, label: 'Why?' },
    {
      id: 'overall',
      type: 'select',
      required: true,
      options: prefOptions,
      label: 'Overall, which setup would you rather work in?',
    },
    {
      id: 'wouldUseA',
      type: 'likert',
      required: true,
      min: 1,
      max: 7,
      anchors: ['Strongly disagree', 'Strongly agree'],
      label: 'I would want Setup A on my own projects.',
    },
    {
      id: 'wouldUseB',
      type: 'likert',
      required: true,
      min: 1,
      max: 7,
      anchors: ['Strongly disagree', 'Strongly agree'],
      label: 'I would want Setup B on my own projects.',
    },
    {
      id: 'missing',
      type: 'textarea',
      required: false,
      label: 'Anything you wanted to ask the project history and could not?',
      help: 'Answer this from what you remember wanting, not from what either tool offered.',
    },
  ],
}

export const ALL_INSTRUMENTS: Instrument[] = [
  CONSENT,
  BACKGROUND,
  TLX,
  SUS,
  HLAC,
  QUIZ,
  SUMMARY,
  PREFERENCE,
]

export function instrumentById(id: string): Instrument | undefined {
  return ALL_INSTRUMENTS.find((i) => i.id === id)
}

/** Response doc id. Per-half instruments get one doc per half. */
export function responseDocId(instrumentId: string, half: number | null): string {
  return half ? `${instrumentId}-h${half}` : instrumentId
}
