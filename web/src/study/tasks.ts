// The four cards, in both projects.
//
// Wording is the participant's handout, verbatim. The footfall text is the
// bikecount text with the nouns swapped per the isomorphism map in
// docs/study/testbed-spec.md §1: course->talk, section->session,
// student->attendee, enroll->register, drop->unregister, instructor->speaker,
// timetable->program, department->track, prerequisite->series dependency.
//
// Nothing here names a git or an sgt verb. A card states a goal in product
// terms and the participant chooses the mechanism, which is the whole point:
// naming the verb would tell them which tool we expect them to reach for.
//
// WHAT THIS BLOCK MEASURES, AND WHY IT IS SHAPED THIS WAY
//
// The claim is not that intent-aligned history helps you understand a codebase.
// It is that it lets you *reverse* work an agent did, at the unit the work was
// done in. So nothing here is a comprehension quiz. Every card ends in an
// observable state of the running program, and the two that matter are scored
// by what the program does afterwards, not by what the participant knew.
//
// Control is spent at the two ends and withheld in the middle:
//
//   - Seeing the defect is prescribed, down to the command line. Whether
//     somebody thinks to run the program is not the claim, it is variance.
//   - Locating the work and reversing it are wide open. That *is* the claim,
//     and telling them where to look would answer the question for them.
//   - Verifying is prescribed again, for the same reason as seeing.
//
// Every prescribed step is a script that ships in the workspace, so the two
// arms see byte-identical output and nobody's result depends on their typing.
// The sheets print what each script runs, so nothing is hidden behind it.
//
// The defect the block opens on is real and already in both repositories
// (episode 17 in docs/study/testbed-spec.md). A human fixed back-to-back slots
// in episode 13; three episodes later an agent "normalised slot comparison",
// added a second comparison helper one character different from the first, and
// repointed both callers at it. The commit message does not mention conflicts.
// The test that guards the behaviour still passes, because it calls the helper
// the agent left behind rather than the one the program now uses. Green suite,
// broken program -- which is why the first card runs the program and not pytest.

import type { Project, RequestId } from '../lib/types'

/**
 * One thing a person does with the app, and the command that does it.
 *
 * The command is named on purpose. Without it "cancelling a registration" and
 * "filling a freed seat from the queue" are two descriptions a participant has to
 * guess the boundary between; with it there is exactly one thing each option
 * means, and both arms can run `--help`. It is still product language -- no git
 * or sgt verb appears anywhere in this list.
 *
 * Ids are the stored answer, so this wording can change between pilots without
 * orphaning what has already been collected. They are also the ids
 * scripts/study/measure_reach_key.py measures, and it fails if a command handler
 * behind one of them disappears.
 */
export interface Behaviour {
  id: string
  label: Record<Project, string>
  command: Record<Project, string>
}

/**
 * Twelve, and the same twelve in both trials.
 *
 * One list learned once, so the second trial costs no reading, and the two trials
 * are directly comparable. Twelve because both correct answers have to sit well
 * inside it -- one reaches one behaviour and the other reaches four -- so neither
 * "tick everything" nor "tick almost nothing" is close to right.
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
 * A reach prediction, attached to the card that performs a destructive
 * operation rather than standing on its own.
 *
 * It used to be two four-minute cards of its own, answered blind and then after
 * looking. The looking stage is now the operation itself: the participant ticks
 * what they think their revert will affect, runs it, and finds out. That costs
 * one card instead of two, and the second answer is grounded in something that
 * happened rather than in a second read of the same screen.
 *
 * `checked - blind` is still the measurement, still a within-participant
 * difference, and still needs no rubric and no second coder.
 */
export interface ReachTrial {
  /** The work, in product terms. Names no git or sgt verb. */
  work: Record<Project, string>
  /**
   * Seconds for the blind stage, hard, auto-submitting whatever is ticked.
   *
   * Short on purpose. `blind` measures what the representation makes available
   * at a glance; given three minutes a participant stops reading and starts
   * reasoning from what they already know about software, which is a real skill
   * and not the one under test. Announcing the limit before the stage opens is
   * part of it: a surprise cutoff would measure typing speed.
   */
  blindSec: number
  /** Seconds for the second answer, after the operation has run. Advisory. */
  checkedSec: number
}

/**
 * Commands the participant is told to run exactly as written.
 *
 * `script` is what they type. `does` is what the sheet prints underneath it, so
 * a prescribed step is never a black box -- a participant who wants to know
 * what they just ran can read it, and a facilitator can check the output is the
 * output everyone else got.
 */
export interface PrescribedRun {
  script: Record<Project, string>
  does: Record<Project, string[]>
}

export interface RequestSpec {
  id: RequestId
  /** Requests sharing a card share one timer. */
  card: string
  /** "Step 1", "Step 4a". Written out rather than derived from the id. */
  heading: string
  title: Record<Project, string>
  body: Record<Project, string>
  /** Minutes. Requests on the same card share the first one's cap. */
  capMin: number | null
  optional: boolean
  /** A prescribed step. Absent on the open ones, which is most of them. */
  run?: PrescribedRun
  /**
   * A free-text box, recorded and never scored.
   *
   * Pilots graded prose written under time pressure against a rubric, and two
   * graders differed on it more than the two conditions differed from each
   * other, so the writing became part of what was measured. These boxes exist
   * so the experimenter can see the participant understood what they were
   * looking at, and so the interview has something to quote. The scored
   * measures all come from what the program does afterwards.
   */
  note?: Record<Project, string>
  /**
   * A box holding one identifier -- a commit hash under git, a feature name or
   * id under sgt -- compared against the key after the session rather than in
   * the browser. Free text because the two arms name work differently and
   * offering a list would tell each arm what shape of answer to look for.
   */
  identify?: Record<Project, string>
  /** Present on the card that reverts. Absent everywhere else. */
  reach?: ReachTrial
  /** What the card is testing. Never shown to the participant. */
  archetype: string
  serves: string
}

export interface TaskCard {
  id: string
  title: string
  heading: string
  capMin: number | null
  requests: RequestSpec[]
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

export const REQUESTS: RequestSpec[] = [
  {
    id: 'd1',
    card: 'c1',
    heading: 'Card 1',
    capMin: 3,
    optional: false,
    archetype: 'see what the program does today, before being asked to change it',
    serves: 'grounding for cards 2, 3 and 4; observation only',
    title: {
      bikecount: 'What does it leave out?',
      footfall: 'What does it leave out?',
    },
    body: {
      bikecount: 'Open the dashboard and look at the hour of day page.\n\n    python3 -m bikecount.server\n\nThe averages on that page do not count every day in the file. Some days are left out on purpose. Use the app, and the wording on the page itself, to work out which days those are and why somebody decided to leave them out.\n\nNothing here is scored and there is no expected wording.',
      footfall: 'Open the dashboard and look at the hour of day page.\n\n    python3 -m footfall.server\n\nThe averages on that page do not count every day in the file. Some days are left out on purpose. Use the app, and the wording on the page itself, to work out which days those are and why somebody decided to leave them out.\n\nNothing here is scored and there is no expected wording.',
    },
    note: {
      bikecount: 'Which days are left out, and what reason is given?',
      footfall: 'Which days are left out, and what reason is given?',
    },
  },
  {
    id: 'd2',
    card: 'c2',
    heading: 'Card 2',
    capMin: 5,
    optional: false,
    archetype: 'locate the piece of work behind a behaviour',
    serves: 'RQ1, claim C1',
    title: {
      bikecount: 'Who did that, and when?',
      footfall: 'Who did that, and when?',
    },
    body: {
      bikecount: 'Leaving those days out was a decision somebody made at some point in this project\'s history. Find the piece of work that made it.\n\n**How you do that is entirely up to you.** There is no script and no suggested route. This is the part we are watching.\n\nPut its name in the box: a commit hash, a feature name, a chapter name, an id, whatever your setup calls the thing you found. If you are not certain, write down what you have and say so. That is a real answer and it beats a guess.',
      footfall: 'Leaving those days out was a decision somebody made at some point in this project\'s history. Find the piece of work that made it.\n\n**How you do that is entirely up to you.** There is no script and no suggested route. This is the part we are watching.\n\nPut its name in the box: a commit hash, a feature name, a chapter name, an id, whatever your setup calls the thing you found. If you are not certain, write down what you have and say so. That is a real answer and it beats a guess.',
    },
    identify: {
      bikecount: 'The piece of work that did it',
      footfall: 'The piece of work that did it',
    },
  },
  {
    id: 'd3',
    card: 'c3',
    heading: 'Card 3',
    capMin: 6,
    optional: false,
    archetype: 'remove one piece of work without disturbing the rest',
    serves: 'RQ1b and RQ2, claim C2',
    title: {
      bikecount: 'Take it out',
      footfall: 'Take it out',
    },
    body: {
      bikecount: 'The committee has been clear that it wants the averages to count every day the sensors recorded, including the unusual ones. They never asked for days to be dropped.\n\nTake that piece of work out. Every other part of the dashboard has to keep working.\n\nBefore you run anything that changes the project, tick which parts of the dashboard you think this will change. One minute, then it submits itself. You are not graded on it and you will not be shown an answer. You are about to find out for yourself.\n\nThen do it, and run the smoke check to see where you ended up:\n\n    python3 check.py',
      footfall: 'The committee has been clear that it wants the averages to count every day the sensors recorded, including the unusual ones. They never asked for days to be dropped.\n\nTake that piece of work out. Every other part of the dashboard has to keep working.\n\nBefore you run anything that changes the project, tick which parts of the dashboard you think this will change. One minute, then it submits itself. You are not graded on it and you will not be shown an answer. You are about to find out for yourself.\n\nThen do it, and run the smoke check to see where you ended up:\n\n    python3 check.py',
    },
    reach: {
      work: {
        bikecount: 'The work you found in card 2: the one that keeps unusual days out of the averages.',
        footfall: 'The work you found in card 2: the one that keeps unusual days out of the averages.',
      },
      blindSec: 60,
      checkedSec: 120,
    },
  },
  {
    id: 'd4',
    card: 'c4',
    heading: 'Card 4',
    capMin: 5,
    optional: false,
    archetype: 'put a removed piece of work back',
    serves: 'RQ2, claim C2',
    title: {
      bikecount: 'Put it back',
      footfall: 'Put it back',
    },
    body: {
      bikecount: 'The committee has changed its mind. Having seen the averages with every day counted, they now agree with Dana: a snowstorm that shut the city says nothing about how many people cycle to work, and it should come out of the averages after all.\n\nPut the work you just removed back, exactly as it was, and check the dashboard matches what it showed at the start.\n\nIf you run out of clock, stop where you are. Not finishing is a normal outcome here and it is recorded as one.',
      footfall: 'The committee has changed its mind. Having seen the averages with every day counted, they now agree with Dana: a public holiday when the offices are shut says nothing about how many people walk to work, and it should come out of the averages after all.\n\nPut the work you just removed back, exactly as it was, and check the dashboard matches what it showed at the start.\n\nIf you run out of clock, stop where you are. Not finishing is a normal outcome here and it is recorded as one.',
    },
  },
]

/**
 * Look up a request, or undefined.
 *
 * Undefined is a real answer, not a defect. The experimenter dashboard renders
 * whatever request documents a participant's collection holds, and the pilots
 * ran earlier designs -- so their collections still hold `r1` to `r6`, `f1` and
 * `f2`. Throwing here took the whole "Requests & scoring" tab down mid-render
 * whenever one was opened, and took the live requests down with it.
 */
export function requestById(id: RequestId): RequestSpec | undefined {
  return REQUESTS.find((r) => r.id === id)
}

/** What the participant calls one step: "Step 1", "Step 4b". */
export function requestHeading(r: RequestSpec): string {
  return r.heading
}

const uncapitalise = (t: string) => t.charAt(0).toLowerCase() + t.slice(1)

/** Requests grouped into cards. Requests on one card share a single timer. */
export function taskCards(project: Project): TaskCard[] {
  const byCard = new Map<string, RequestSpec[]>()
  for (const r of REQUESTS) {
    const list = byCard.get(r.card) ?? []
    list.push(r)
    byCard.set(r.card, list)
  }
  return [...byCard.entries()].map(([id, requests]) => {
    // The card's heading spans its requests: "Step 1", or "Steps 4a to 4c".
    // Numbering the card separately from its steps was how request 3 ended up
    // opening with "One correction to the last request" under a heading that
    // gave it nothing to be a correction to.
    const first = requests[0].heading
    const last = requests[requests.length - 1].heading
    return {
      id,
      heading: requests.length === 1 ? first : `${first} to ${last.replace(/^Step /, '')}`,
      // Every title is written to open a heading, so joining them mid-sentence
      // capitalised the second one: "Take the waitlist out, then Drops still
      // need to work".
      title:
        requests.length === 1
          ? requests[0].title[project]
          : requests
              .map((r, i) => (i ? uncapitalise(r.title[project]) : r.title[project]))
              .join(', then '),
      capMin: requests[0].capMin,
      requests,
    }
  })
}

/**
 * Minutes a half's task block is allowed, and what the participant sees their
 * elapsed time measured against.
 *
 * 3 to see the defect, 5 to locate it, 6 to reverse it, 10 shared by the removal
 * and its correction. Summed rather than written down, because it was written
 * down before and a card's cap could change without it.
 */
export const BLOCK_CAP_MIN = REQUESTS.reduce((sum, r) => sum + (r.capMin ?? 0), 0)

/** The steps carrying a reach prediction. One, on the card that reverts. */
export const REACH_TRIALS = REQUESTS.filter(
  (r): r is RequestSpec & { reach: ReachTrial } => r.reach !== undefined,
)

/**
 * Cards the participant is told to expect. Counted rather than written down:
 * the task preamble used to say "three requests, about twenty minutes in total"
 * and stayed that way through two redesigns of what was under it.
 */
export const CARD_COUNT = new Set(REQUESTS.map((r) => r.card)).size
