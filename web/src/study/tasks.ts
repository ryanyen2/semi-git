// The four stages, in both projects. Protocol v2 (docs/study/protocol-v2.md).
//
// Wording is the participant's handout, verbatim. The footfall text is the
// bikecount text with the nouns swapped per the isomorphism map in
// docs/study/testbed-spec.md. Nothing here names a git or an sgt verb: a stage
// states what happened and what to do in product terms, and the participant
// chooses the mechanism inside the tool they were given.
//
// WHAT THE BLOCK MEASURES, AND WHY IT IS SHAPED THIS WAY
//
// Protocol v1 gave people an unfamiliar codebase and an open task, and pilots
// spent their timed minutes orienting and choosing strategies. Those costs
// landed on top of the thing under test, which is whether the representation
// of history helps at each step. So v2 prescribes everything except the step
// itself. Each stage starts from a scripted state (`./stage N`), tells the
// participant exactly what happened, and asks for one thing. A stage that goes
// wrong cannot spoil the next one, because the next one resets the state.
//
// Each stage runs in two phases. The work is capped at STAGE_CAP_MIN minutes
// with a visible countdown. The quiz and the three rating statements that
// follow are untimed: they are measurements of what the person took away, not
// more work to race through.
//
// There is no live AI assistant in the block. Stage 1's "assistant work" is a
// recorded agent session replayed by `./stage 1`, so every participant reads
// byte-identical changes. See protocol v2 section 3 for what that trades away.

import type { Project, RequestId } from '../lib/types'

/**
 * One thing a person can see the app do, and the page that shows it.
 *
 * The page is named on purpose. Without it, "the busiest hour" and "the
 * weekday chart" are two descriptions a participant has to guess the boundary
 * between; with it there is exactly one thing each option means. It is still
 * product language. No git or sgt verb appears anywhere in this list.
 *
 * Ids are the stored answer, so this wording can change between pilots without
 * orphaning what has already been collected. They are also the ids the key
 * generators measure, and generation fails if a page behind one disappears.
 */
export interface Behaviour {
  id: string
  label: Record<Project, string>
  command: Record<Project, string>
}

/**
 * Ten, and the same ten on every checklist in the block.
 *
 * One list learned once, so the later checklists cost no reading and the
 * answers are directly comparable. The keys have to sit well inside it, so
 * neither "tick everything" nor "tick nothing" is close to right; the key
 * upload refuses a key naming zero or all ten (`answerKey.ts`).
 */
export const BEHAVIOURS: Behaviour[] = [
  {
    id: 'busiestDay',
    label: {
      bikecount: 'The busiest day figure on the front page',
      footfall: 'The busiest day figure on the front page',
    },
    command: { bikecount: '/', footfall: '/' },
  },
  {
    id: 'recentChart',
    label: {
      bikecount: 'The last fortnight chart on the front page',
      footfall: 'The last fortnight chart on the front page',
    },
    command: { bikecount: '/', footfall: '/' },
  },
  {
    id: 'hourWeekday',
    label: {
      bikecount: 'The weekday hour-of-day chart',
      footfall: 'The weekday hour-of-day chart',
    },
    command: { bikecount: '/hourly', footfall: '/hourly' },
  },
  {
    id: 'hourWeekend',
    label: {
      bikecount: 'The weekend hour-of-day chart',
      footfall: 'The weekend hour-of-day chart',
    },
    command: { bikecount: '/hourly', footfall: '/hourly' },
  },
  {
    id: 'busiestHour',
    label: {
      bikecount: 'The busiest hour called out above that chart',
      footfall: 'The busiest hour called out above that chart',
    },
    command: { bikecount: '/hourly', footfall: '/hourly' },
  },
  {
    id: 'monthly',
    label: {
      bikecount: 'The month-by-month chart',
      footfall: 'The month-by-month chart',
    },
    command: { bikecount: '/monthly', footfall: '/monthly' },
  },
  {
    id: 'eventMarks',
    label: {
      bikecount: 'The marks that flag unusual days on the charts',
      footfall: 'The marks that flag unusual days on the charts',
    },
    command: { bikecount: '/monthly', footfall: '/monthly' },
  },
  {
    id: 'yearTable',
    label: {
      bikecount: 'The one-row-per-year table',
      footfall: 'The one-row-per-year table',
    },
    command: { bikecount: '/yearly', footfall: '/yearly' },
  },
  {
    id: 'sideSplit',
    label: {
      bikecount: 'The east against west comparison',
      footfall: 'The north against south comparison',
    },
    command: { bikecount: '/sides', footfall: '/sides' },
  },
  {
    id: 'csv',
    label: {
      bikecount: 'The daily totals csv download',
      footfall: 'The daily totals csv download',
    },
    command: { bikecount: '/daily.csv', footfall: '/daily.csv' },
  },
]

/**
 * Commands the participant is told to run exactly as written.
 *
 * `script` is what they type. `does` is what the sheet prints underneath it,
 * so a prescribed step is never a black box. A participant who wants to know
 * what they just ran can read it, and a facilitator can check the output is
 * the output everyone else got.
 */
export interface PrescribedRun {
  script: Record<Project, string>
  does: Record<Project, string[]>
}

/** One quiz item on a stage, answered after the work with no clock running. */
export type QuizItem =
  | {
      kind: 'behaviours'
      id: string
      /** Same wording in both projects on purpose: the checklist is the
       * measurement, and two wordings would be two measurements. */
      prompt: string
      /** Scored as set F1 against the measured key in answer-key.json. */
      scored: true
    }
  | {
      kind: 'choice'
      id: string
      prompt: string
      options: Array<{ value: string; label: string }>
      /** Scored exact against the key, or recorded as a self-report. */
      scored: boolean
    }
  | {
      kind: 'text'
      id: string
      prompt: string
      /** Never scored. Kept for the interview and the qualitative analysis. */
      scored: false
    }

/**
 * One of the three rating statements a stage ends with. Protocol v2's
 * replacement for the HLAC battery: the same kind of 7-point item, asked in
 * the minute after the experience it asks about instead of ten minutes later.
 * One of each stage's three is reverse-keyed, as the guard against
 * straight-lining.
 */
export interface StageRating {
  id: string
  label: string
  reverse?: boolean
  /** Which claim the item serves. Shown in the dashboard, never to the participant. */
  serves: string
}

export interface RequestSpec {
  id: RequestId
  /** "Stage 1". Written out rather than derived from the id. */
  heading: string
  title: Record<Project, string>
  body: Record<Project, string>
  /** Minutes for the work phase. The quiz that follows is untimed. */
  capMin: number
  optional: boolean
  /** The stage-reset command, printed first on every card. */
  run: PrescribedRun
  /**
   * A box holding one identifier -- a commit hash under git, a named piece of
   * work under sgt -- shown during the WORK phase, because finding it is the
   * work. Free text, compared against the key after the session rather than
   * in the browser (see protocol v2 section 4).
   */
  identify?: Record<Project, string>
  /** The quiz, in order. Rendered after the work phase, untimed. */
  quiz: QuizItem[]
  /** Whether the quiz ends with a 0-100 confidence slider. Only stages whose
   * quiz has a right answer carry one; calibration needs both halves. */
  quizConfidence: boolean
  /** The three rating statements. */
  ratings: StageRating[]
  /** What the stage is testing. Never shown to the participant. */
  archetype: string
  serves: string
}

export const SCENARIO: Record<Project, { app: string; maintainer: string; blurb: string }> = {
  bikecount: {
    app: 'bikecount',
    maintainer: 'Dana Whitfield',
    blurb:
      'a small web dashboard over the bicycle counter on the Fremont Bridge in Seattle',
  },
  footfall: {
    app: 'footfall',
    maintainer: 'Dana Whitfield',
    blurb:
      'a small web dashboard over the pedestrian counter on Spencer Street in Melbourne',
  },
}

const MECHANISM_REMOVE = [
  { value: 'clean', label: 'It applied cleanly in one step' },
  { value: 'conflicts', label: 'I had to resolve conflicts along the way' },
  { value: 'hand', label: 'I ended up editing files by hand' },
  { value: 'unfinished', label: 'I did not finish' },
]

const MECHANISM_RESTORE = [
  { value: 'undid', label: 'I undid the removal in one step' },
  { value: 'history', label: 'I brought it back from the history another way' },
  { value: 'hand', label: 'I re-made the change by hand' },
  { value: 'unfinished', label: 'I did not finish' },
]

// NOTE for the testbed build: stage 1's story below must match the recorded
// session that `./stage 1` replays, and the build gate in scripts/study/
// re-checks that the replayed change spans at least two files and contains
// exactly two distinguishable jobs (the requested one and one smaller
// unrequested fix). If the gate selects a different session, this wording is
// what changes. The quiz key for s1 is measured by rendering every page
// before and after the replay, never written by hand.
export const REQUESTS: RequestSpec[] = [
  {
    id: 's1',
    heading: 'Stage 1',
    capMin: 4,
    optional: false,
    archetype: 'read a multi-file assistant change and record it',
    serves: 'RQ1, claim C1',
    title: {
      bikecount: 'Record what the assistant did',
      footfall: 'Record what the assistant did',
    },
    body: {
      bikecount:
        'Start by putting the project in this stage\'s starting state:\n\n    ./stage 1\n\nEarlier today you asked the coding assistant to round the numbers on the dashboard\'s front page to the nearest ten, so they stop implying one-bike precision. The assistant has finished. Its changes are in your working copy, and nothing is recorded in the project\'s history yet.\n\nRead what it changed, in the editor or the terminal, until you could describe it to a colleague. Then record all of it, the way this setup records finished work.',
      footfall:
        'Start by putting the project in this stage\'s starting state:\n\n    ./stage 1\n\nEarlier today you asked the coding assistant to round the numbers on the dashboard\'s front page to the nearest ten, so they stop implying single-person precision. The assistant has finished. Its changes are in your working copy, and nothing is recorded in the project\'s history yet.\n\nRead what it changed, in the editor or the terminal, until you could describe it to a colleague. Then record all of it, the way this setup records finished work.',
    },
    run: {
      script: { bikecount: './stage 1', footfall: './stage 1' },
      does: {
        bikecount: [
          'resets the project to this stage\'s starting state',
          'replays the assistant\'s changes into your working copy, unrecorded',
        ],
        footfall: [
          'resets the project to this stage\'s starting state',
          'replays the assistant\'s changes into your working copy, unrecorded',
        ],
      },
    },
    quiz: [
      {
        kind: 'behaviours',
        id: 'behaviours',
        prompt: 'Which parts of the dashboard did the assistant\'s work change?',
        scored: true,
      },
      // Not "how many separate jobs was this". Both setups answer that from
      // the diff and neither helps, because a change is only grouped into
      // pieces of work once it is in the history -- until then sgt files it
      // against the features the touched code already belongs to. Asking it
      // would have scored a question the study's own claim does not cover.
      {
        kind: 'text',
        id: 'joined',
        prompt:
          'Your change is now part of the project\'s history. What else is in the same piece of work, if you can tell?',
        scored: false,
      },
      {
        kind: 'text',
        id: 'says',
        prompt: 'In one sentence, what does the history now say happened?',
        scored: false,
      },
    ],
    quizConfidence: true,
    ratings: [
      {
        id: 'recordKnown',
        label: 'I know what the record I just made contains.',
        serves: 'C1',
      },
      {
        id: 'changesLegible',
        label: 'I could tell what the assistant changed without reading every line.',
        serves: 'C1',
      },
      {
        id: 'unnoticed',
        label: 'Something could be in that record that I did not notice.',
        reverse: true,
        serves: 'C1',
      },
    ],
  },
  {
    id: 's2',
    heading: 'Stage 2',
    capMin: 4,
    optional: false,
    archetype: 'locate the piece of work behind a described defect',
    serves: 'RQ2, claim C2; the reach answer is C3\'s prediction',
    title: {
      bikecount: 'Find the work behind the wrong number',
      footfall: 'Find the work behind the wrong number',
    },
    body: {
      bikecount:
        'Reset first. Anything left over from the last stage is gone after this, which is deliberate:\n\n    ./stage 2\n\nThe cycling team published a report last year. It says the average day in 2018 saw **2,882** crossings. The dashboard\'s by-year page now says **2,900** for the same year. The reset script prints both numbers so you can see them side by side.\n\nHere is what happened. A colleague changed how the dashboard works out an average. Days on the project\'s list of unusual days, like the February 2019 snowstorm and Christmas, are now left out of every average. There was a reason for it, but the report was written when every day still counted, and the committee wants the two to agree again.\n\nYour job in this stage is only to find that work in the project\'s history. Put its name in the box: a commit hash, a named piece of work, an id, whatever this setup calls the thing you found. If you are not certain, write down what you have and say so. That beats a guess.',
      footfall:
        'Reset first. Anything left over from the last stage is gone after this, which is deliberate:\n\n    ./stage 2\n\nThe transport committee published a paper last year. It says the average day in 2018 saw **2,882** people walk past. The dashboard\'s by-year page now says **2,900** for the same year. The reset script prints both numbers so you can see them side by side.\n\nHere is what happened. A colleague changed how the dashboard works out an average. Days on the project\'s list of event days, like Grand Final Friday and Christmas, are now left out of every average. There was a reason for it, but the paper was written when every day still counted, and the committee wants the two to agree again.\n\nYour job in this stage is only to find that work in the project\'s history. Put its name in the box: a commit hash, a named piece of work, an id, whatever this setup calls the thing you found. If you are not certain, write down what you have and say so. That beats a guess.',
    },
    run: {
      script: { bikecount: './stage 2', footfall: './stage 2' },
      does: {
        bikecount: [
          'puts the project back to its full history, discarding anything from the last stage',
          'prints the number the report quotes next to the number the dashboard shows',
        ],
        footfall: [
          'puts the project back to its full history, discarding anything from the last stage',
          'prints the number the paper quotes next to the number the dashboard shows',
        ],
      },
    },
    identify: {
      bikecount: 'The piece of work that changed the averages',
      footfall: 'The piece of work that changed the averages',
    },
    quiz: [
      {
        kind: 'behaviours',
        id: 'behaviours',
        prompt:
          'Which parts of the dashboard run through the code that work touches? Tick what you would re-check if it were taken out. You have not taken it out yet; answer from what the history shows you.',
        scored: true,
      },
    ],
    quizConfidence: true,
    ratings: [
      {
        id: 'foundRight',
        label: 'I am confident I found the right piece of work.',
        serves: 'C2',
      },
      {
        id: 'why',
        label: 'I could tell why the change had been made.',
        serves: 'C2',
      },
      {
        id: 'guessedNames',
        label: 'I had to guess at names or ids to find it.',
        reverse: true,
        serves: 'C2',
      },
    ],
  },
  {
    id: 's3',
    heading: 'Stage 3',
    capMin: 4,
    optional: false,
    archetype: 'remove one piece of work that later work has landed on',
    serves: 'RQ3, claim C3',
    title: {
      bikecount: 'Take that work out',
      footfall: 'Take that work out',
    },
    body: {
      bikecount:
        'Reset first:\n\n    ./stage 3\n\nIt names the work to take out, so you have it even if the last stage ran out of time. Finding it was that stage\'s job. This one is about the removal.\n\nThe committee never approved the change. They want the averages to count every day the sensors recorded, including the unusual ones, so the dashboard reads **2,882** for 2018 again. Take that piece of work out. Everything else the dashboard shows has to keep working.\n\nWhen you think you are done:\n\n    ./check 3\n\nIt prints the same words for everyone and tells you what the dashboard shows now. It does not mark you, and a red line in it is information rather than a verdict.',
      footfall:
        'Reset first:\n\n    ./stage 3\n\nIt names the work to take out, so you have it even if the last stage ran out of time. Finding it was that stage\'s job. This one is about the removal.\n\nThe committee never approved the change. They want the averages to count every day the sensors recorded, including the unusual ones, so the dashboard reads **2,882** for 2018 again. Take that piece of work out. Everything else the dashboard shows has to keep working.\n\nWhen you think you are done:\n\n    ./check 3\n\nIt prints the same words for everyone and tells you what the dashboard shows now. It does not mark you, and a red line in it is information rather than a verdict.',
    },
    run: {
      script: { bikecount: './stage 3', footfall: './stage 3' },
      does: {
        bikecount: [
          'puts the project back to its full history, discarding anything from the last stage',
          'names the work to take out, in the words this setup uses for it',
        ],
        footfall: [
          'puts the project back to its full history, discarding anything from the last stage',
          'names the work to take out, in the words this setup uses for it',
        ],
      },
    },
    quiz: [
      {
        kind: 'behaviours',
        id: 'behaviours',
        prompt:
          'Which parts of the dashboard changed when the work came out? Tick what you saw change, not what you expected to change.',
        scored: true,
      },
      {
        kind: 'choice',
        id: 'mechanism',
        prompt: 'How did the removal go?',
        options: MECHANISM_REMOVE,
        scored: false,
      },
    ],
    quizConfidence: true,
    ratings: [
      {
        id: 'knewReach',
        label: 'Before I ran it, I knew what the removal would touch.',
        serves: 'C3',
      },
      {
        id: 'matchedIntent',
        label: 'The result matches what I intended.',
        serves: 'C3',
      },
      {
        id: 'worriedBroke',
        label: 'I was worried I had broken something else.',
        reverse: true,
        serves: 'C3',
      },
    ],
  },
  {
    id: 's4',
    heading: 'Stage 4',
    capMin: 4,
    optional: false,
    archetype: 'put a removed piece of work back exactly',
    serves: 'RQ3, claim C3',
    title: {
      bikecount: 'Put it back',
      footfall: 'Put it back',
    },
    body: {
      bikecount:
        'Reset first:\n\n    ./stage 4\n\nThis puts the project in the state where that work has already been taken out. It is the same state for everyone, whether or not your own removal worked, so nothing from the last stage follows you here.\n\nThe committee has changed its mind. Having seen the averages with every day counted, they now agree with your colleague: a snowstorm that shut the city says nothing about how many people cycle to work on an ordinary day, and it should stay out of the averages after all. Put the work back, exactly as it was, so 2018 reads **2,900** again.\n\nWhen you think you are done:\n\n    ./check 4',
      footfall:
        'Reset first:\n\n    ./stage 4\n\nThis puts the project in the state where that work has already been taken out. It is the same state for everyone, whether or not your own removal worked, so nothing from the last stage follows you here.\n\nThe committee has changed its mind. Having seen the averages with every day counted, they now agree with your colleague: a public holiday when the offices are shut says nothing about how many people walk to work on an ordinary day, and it should stay out of the averages after all. Put the work back, exactly as it was, so 2018 reads **2,900** again.\n\nWhen you think you are done:\n\n    ./check 4',
    },
    run: {
      script: { bikecount: './stage 4', footfall: './stage 4' },
      does: {
        bikecount: [
          'puts the project in the state where that work has already been taken out, the same for everyone',
        ],
        footfall: [
          'puts the project in the state where that work has already been taken out, the same for everyone',
        ],
      },
    },
    quiz: [
      {
        kind: 'choice',
        id: 'mechanism',
        prompt: 'How did you put it back?',
        options: MECHANISM_RESTORE,
        scored: false,
      },
      {
        kind: 'text',
        id: 'convince',
        prompt:
          'How would you convince a colleague the work is back, without showing them the code?',
        scored: false,
      },
    ],
    quizConfidence: false,
    ratings: [
      {
        id: 'backExact',
        label: 'The project is back exactly as it was before the removal.',
        serves: 'C3',
      },
      {
        id: 'historySays',
        label: 'I could tell from the history that the work was back.',
        serves: 'C3',
      },
      {
        id: 'recheckByHand',
        label: 'I would re-check everything by hand before trusting it.',
        reverse: true,
        serves: 'C3',
      },
    ],
  },
]

/**
 * Look up a stage, or undefined.
 *
 * Undefined is a real answer, not a defect. The experimenter dashboard renders
 * whatever request documents a participant's collection holds, and the pilots
 * ran earlier designs, so their collections still hold `d1` to `d4`, `r1` to
 * `r6`, `w1` to `w3`, `f1` and `f2`. Throwing here took the whole "Requests &
 * scoring" tab down mid-render whenever one was opened.
 */
export function requestById(id: RequestId): RequestSpec | undefined {
  return REQUESTS.find((r) => r.id === id)
}

/** What the participant calls one stage: "Stage 1". */
export function requestHeading(r: RequestSpec): string {
  return r.heading
}

/**
 * Minutes of capped work in a half. Summed rather than written down, because
 * it was written down before and a stage's cap could change without it.
 */
export const BLOCK_CAP_MIN = REQUESTS.reduce((sum, r) => sum + r.capMin, 0)

/**
 * Minutes a quiz-and-ratings pass is budgeted at, per stage. Untimed on
 * screen; this exists only so the schedule the participant reads includes the
 * answering, not just the work.
 */
export const QUIZ_EST_MIN = 1

/** What a task block costs in the schedule: the caps plus the answering. */
export const BLOCK_ESTIMATE_MIN = BLOCK_CAP_MIN + REQUESTS.length * QUIZ_EST_MIN

/**
 * Stages the participant is told to expect. Counted rather than written down:
 * an earlier design's preamble said "three requests, about twenty minutes"
 * and stayed that way through two redesigns of what was under it.
 */
export const STAGE_COUNT = REQUESTS.length
