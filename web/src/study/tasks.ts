// The four cards, in both projects.
//
// Wording is the participant's handout, verbatim. The confplan text is the
// coursecraft text with the nouns swapped per the isomorphism map in
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
    id: 'register',
    label: {
      coursecraft: 'Enrolling a student in a section',
      confplan: 'Registering an attendee for a session',
    },
    command: { coursecraft: 'coursecraft enroll', confplan: 'confplan register' },
  },
  {
    id: 'cancel',
    label: {
      coursecraft: 'Dropping a student from a section',
      confplan: 'Cancelling a registration',
    },
    command: { coursecraft: 'coursecraft drop', confplan: 'confplan cancel' },
  },
  {
    id: 'queue',
    label: {
      coursecraft: 'Queueing for the next free seat',
      confplan: 'Queueing for the next freed seat',
    },
    command: {
      coursecraft: 'coursecraft waitlist join',
      confplan: 'confplan waitlist join',
    },
  },
  {
    id: 'showQueue',
    label: {
      coursecraft: "Listing a section's waitlist in order",
      confplan: "Listing a session's queue in order",
    },
    command: {
      coursecraft: 'coursecraft waitlist show',
      confplan: 'confplan waitlist show',
    },
  },
  {
    id: 'promote',
    label: {
      coursecraft: 'Filling a freed seat from the queue',
      confplan: 'Filling a freed seat from the queue',
    },
    command: {
      coursecraft: 'coursecraft waitlist promote',
      confplan: 'confplan waitlist promote',
    },
  },
  {
    id: 'notices',
    label: {
      coursecraft: 'Showing pending student notices',
      confplan: 'Showing pending attendee notices',
    },
    command: { coursecraft: 'coursecraft notices', confplan: 'confplan notices' },
  },
  {
    id: 'search',
    label: {
      coursecraft: 'Finding courses by code or title',
      confplan: 'Finding talks by code or title',
    },
    command: { coursecraft: 'coursecraft search', confplan: 'confplan search' },
  },
  {
    id: 'agenda',
    label: {
      coursecraft: 'Exporting a timetable or the catalog',
      confplan: 'Exporting an agenda or the program',
    },
    command: { coursecraft: 'coursecraft export', confplan: 'confplan agenda' },
  },
  {
    id: 'rooms',
    label: {
      coursecraft: 'Finding double-booked rooms',
      confplan: 'Finding double-booked rooms',
    },
    command: {
      coursecraft: 'coursecraft room audit',
      confplan: 'confplan room audit',
    },
  },
  {
    id: 'stats',
    label: {
      coursecraft: 'Per-course enrollment statistics',
      confplan: 'Per-talk registration statistics',
    },
    command: { coursecraft: 'coursecraft stats', confplan: 'confplan stats' },
  },
  {
    id: 'speaker',
    label: {
      coursecraft: "An instructor's weekly schedule",
      confplan: "A speaker's weekend schedule",
    },
    command: { coursecraft: 'coursecraft instructor', confplan: 'confplan speaker' },
  },
  {
    id: 'scheduleSession',
    label: {
      coursecraft: 'Adding a section of a course',
      confplan: 'Scheduling a session of a talk',
    },
    command: {
      coursecraft: 'coursecraft section add',
      confplan: 'confplan session add',
    },
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
  coursecraft: {
    app: 'coursecraft',
    maintainer: 'Riley Chen',
    blurb:
      'a small command line tool that a university department uses to manage course registration',
  },
  confplan: {
    app: 'confplan',
    maintainer: 'Sam Park',
    blurb:
      'a small command line tool that a conference committee uses to plan a two-day program',
  },
}

export const REQUESTS: RequestSpec[] = [
  {
    id: 'd1',
    card: 'c1',
    heading: 'Step 1',
    capMin: 3,
    optional: false,
    archetype: 'observe an agent-introduced defect in the running program',
    serves: 'grounding for steps 2 and 3; observation only',
    title: {
      coursecraft: 'Two classes back to back',
      confplan: 'Two sessions back to back',
    },
    body: {
      coursecraft: `A support ticket came in this morning:

> A student is trying to take two sections of CS101 — one Monday 09:00–10:30,
> the other Monday 10:30–12:00. The system says they clash. They don't overlap.
> The room audit is doing the same thing to two bookings that run back to back
> in one room.

Run the script below. It works on a scratch copy of the data, so nothing you do
here touches the project.

Then say in your own words what is wrong and what the program should do instead.
There is no expected wording and this is not scored.`,
      confplan: `A support ticket came in this morning:

> An attendee is trying to register for two sessions of T1 — one Saturday
> 09:00–10:30, the other Saturday 10:30–12:00. The system says they clash. They
> don't overlap. The room audit is doing the same thing to two bookings that run
> back to back in one room.

Run the script below. It works on a scratch copy of the data, so nothing you do
here touches the project.

Then say in your own words what is wrong and what the program should do instead.
There is no expected wording and this is not scored.`,
    },
    run: {
      script: {
        coursecraft: './show-the-problem.sh',
        confplan: './show-the-problem.sh',
      },
      does: {
        coursecraft: [
          'makes a scratch store with one course and two sections, Mon 09:00–10:30 and Mon 10:30–12:00, in the same room',
          'enrols one student in the first section, then tries the second',
          'runs the room audit over the two bookings',
          'runs the project’s own tests for conflicts and rooms',
        ],
        confplan: [
          'makes a scratch store with one talk and two sessions, Sat 09:00–10:30 and Sat 10:30–12:00, in the same room',
          'registers one attendee for the first session, then tries the second',
          'runs the room audit over the two bookings',
          'runs the project’s own tests for clashes and rooms',
        ],
      },
    },
    note: {
      coursecraft:
        'What is wrong, and what should the program do instead? A sentence or two is plenty.',
      confplan:
        'What is wrong, and what should the program do instead? A sentence or two is plenty.',
    },
  },
  {
    id: 'd2',
    card: 'c2',
    heading: 'Step 2',
    capMin: 5,
    optional: false,
    archetype: 'localise the responsible unit of work from an observed symptom',
    serves: 'C1 -- the locate measure',
    title: {
      coursecraft: 'Where did that come from?',
      confplan: 'Where did that come from?',
    },
    body: {
      coursecraft: `Something in this project's past made the program behave that way. Find out which
piece of work it was.

**How you do that is entirely up to you.** This is the part we are watching, so
there is no script and no suggested route.

When you have it, put its identifier in the box: a commit hash, a feature name,
an id — whatever your setup calls the thing you found. If you are not certain,
write down what you have and say you are not certain. That is a real answer and
it is better than a guess.`,
      confplan: `Something in this project's past made the program behave that way. Find out which
piece of work it was.

**How you do that is entirely up to you.** This is the part we are watching, so
there is no script and no suggested route.

When you have it, put its identifier in the box: a commit hash, a feature name,
an id — whatever your setup calls the thing you found. If you are not certain,
write down what you have and say you are not certain. That is a real answer and
it is better than a guess.`,
    },
    identify: {
      coursecraft: 'The piece of work that caused it',
      confplan: 'The piece of work that caused it',
    },
  },
  {
    id: 'd3',
    card: 'c3',
    heading: 'Step 3',
    capMin: 6,
    optional: false,
    archetype: 'reverse one unit of agent work; reach predicted before and after',
    serves: 'C2 -- reversal outcome, collateral damage, and foresight',
    title: {
      coursecraft: 'Take it back out',
      confplan: 'Take it back out',
    },
    body: {
      coursecraft: `Take that piece of work out, so back-to-back sections behave the way you said they
should. Everything else in the program has to keep working.

**Before you run anything that changes the project**, tick what you think it will
affect. One minute, then it submits itself.

You are not being graded on this and you will not be shown an answer. You are
about to find out for yourself.

Then do it, and run \`./check.sh\` to see where you ended up.`,
      confplan: `Take that piece of work out, so back-to-back sessions behave the way you said they
should. Everything else in the program has to keep working.

**Before you run anything that changes the project**, tick what you think it will
affect. One minute, then it submits itself.

You are not being graded on this and you will not be shown an answer. You are
about to find out for yourself.

Then do it, and run \`./check.sh\` to see where you ended up.`,
    },
    reach: {
      work: {
        coursecraft:
          'The piece of work you found in step 2 — the one that changed how two time ranges are compared.',
        confplan:
          'The piece of work you found in step 2 — the one that changed how two time ranges are compared.',
      },
      blindSec: 60,
      checkedSec: 60,
    },
    run: {
      script: { coursecraft: './check.sh', confplan: './check.sh' },
      does: {
        coursecraft: [
          'repeats step 1’s two back-to-back cases and prints what the program says now',
          'runs the whole test suite and prints which feature areas pass',
          'starts the command line tool, because a suite can pass in a program that will not start',
        ],
        confplan: [
          'repeats step 1’s two back-to-back cases and prints what the program says now',
          'runs the whole test suite and prints which feature areas pass',
          'starts the command line tool, because a suite can pass in a program that will not start',
        ],
      },
    },
  },
  {
    id: 'w1',
    card: 'c4',
    heading: 'Step 4a',
    capMin: 10,
    optional: false,
    archetype: 'see the removal target working before removing it',
    serves: 'grounding for 4b and 4c',
    title: {
      coursecraft: 'See what the waitlist does today',
      confplan: 'See what the queue does today',
    },
    body: {
      coursecraft: `The next request is about the waitlist. Before it, see what the waitlist actually
does, so "gone" means something specific rather than something you have to guess
at.

The three parts of this card share one clock. Read all three before you start.`,
      confplan: `The next request is about the queue for full sessions. Before it, see what the
queue actually does, so "gone" means something specific rather than something you
have to guess at.

The three parts of this card share one clock. Read all three before you start.`,
    },
    run: {
      script: {
        coursecraft: './show-the-waitlist.sh',
        confplan: './show-the-waitlist.sh',
      },
      does: {
        coursecraft: [
          'fills a one-seat section and puts two students in the queue behind it',
          'shows the queue in order',
          'drops the enrolled student, so a seat frees up',
          'shows the freed seat being filled from the queue, and the notice that goes out',
        ],
        confplan: [
          'fills a one-seat session and puts two attendees in the queue behind it',
          'shows the queue in order',
          'cancels the registered attendee, so a seat frees up',
          'shows the freed seat being filled from the queue, and the notice that goes out',
        ],
      },
    },
  },
  {
    id: 'w2',
    card: 'c4',
    heading: 'Step 4b',
    capMin: null,
    optional: false,
    archetype: 'remove a feature and everything built on top of it',
    serves: 'C2 -- removal completeness and collateral damage',
    title: {
      coursecraft: 'Take the waitlist out',
      confplan: 'Take the queue out',
    },
    body: {
      coursecraft: `The department has decided that waitlists are the registrar's job now. For the
next release the waitlist has to be gone: joining a queue, the queue itself, the
automatic filling of a freed seat, and the notices that go with it.

Everything else in the program has to keep working.

If you run out of clock, stop where you are. **Not finishing is a normal
outcome here and it is recorded as one.** It is not a mark against you, and we
would rather see where you got to than have you rush the last step.`,
      confplan: `The committee has decided that queues are the registration desk's job now. For
the next release the queue has to be gone: joining a queue, the queue itself, the
automatic filling of a freed seat, and the notices that go with it.

Everything else in the program has to keep working.

If you run out of clock, stop where you are. **Not finishing is a normal
outcome here and it is recorded as one.** It is not a mark against you, and we
would rather see where you got to than have you rush the last step.`,
    },
  },
  {
    id: 'w3',
    card: 'c4',
    heading: 'Step 4c',
    capMin: null,
    optional: false,
    archetype: 'restore one part of what was just removed, under time pressure',
    serves: 'C2 -- selective restore',
    title: {
      coursecraft: 'Drops still need to work',
      confplan: 'Cancelling still needs to work',
    },
    body: {
      coursecraft: `One correction to the last request. Students must still be able to drop a section
themselves. Bring dropping back, with no automatic filling of the freed seat when
it happens.

When you are done, run \`./check.sh\` once more.`,
      confplan: `One correction to the last request. Attendees must still be able to cancel a
registration themselves. Bring cancelling back, with no automatic filling of the
freed seat when it happens.

When you are done, run \`./check.sh\` once more.`,
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
