// The three requests, in both projects.
//
// Wording is the participant's handout, verbatim. The confplan text is the
// coursecraft text with the nouns swapped per the isomorphism map in
// docs/study/testbed-spec.md §1: course->talk, section->session,
// student->attendee, enroll->register, drop->unregister, instructor->speaker,
// timetable->program, department->track, prerequisite->series dependency.
//
// Nothing here names a git or an sgt verb. The request states a goal in product
// terms and the participant chooses the mechanism, which is the whole point:
// naming the verb would tell them which tool we expect them to reach for.
//
// Three requests, not six, and twenty minutes a half rather than forty-five.
// The cut set was: a second regression-localization request, a build-two-
// alternatives-and-discard-one request, and a history-surgery request. Pilots
// ran out of time on all three, which produces a floor rather than a
// measurement: a request nobody finishes in either condition cannot separate
// the conditions. What is left is one question about the past and one change to
// the present with a correction on top of it, which is the smallest set that
// still exercises both claims. Finding the thing to change is most of the work
// in the second request, so search is measured twice and paid for once.

import type { Project, RequestId } from '../lib/types'

/**
 * One closed question with a fixed option list.
 *
 * Closed, not free text. Pilots wrote two or three sentences that had to be
 * graded against a rubric by hand, under time pressure, at the exact moment
 * they had just spent their budget -- so the answers were short, hedged and
 * hard to score, and the writing itself became part of what we were measuring.
 * A fixed list measures whether they found the answer, which is the thing the
 * request is about.
 *
 * There is no `correct` field here on purpose. This file is compiled into the
 * bundle the participant's browser downloads, so anything in it is readable
 * from devtools. The key lives in docs/study/answer-key.json, which the
 * experimenter loads into the console by hand.
 */
export interface ChoiceQuestion {
  id: string
  prompt: string
  options: Record<Project, string[]>
}

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
 * A reach trial: one named piece of work, the same twelve behaviours, answered
 * twice.
 *
 * Twice because the difference is the measurement. The first answer comes from
 * the representation alone and the second after checking, so `checked - blind` is
 * what the representation bought -- which is a within-participant difference, and
 * needs no rubric and no second coder.
 *
 * Nothing here is executed and nothing is modified, so the trials are independent
 * of each other and of the request that follows, and the project needs no reset
 * between them.
 */
export interface ReachTrial {
  /** The work, in product terms. Names no git or sgt verb. */
  work: Record<Project, string>
  /**
   * Seconds for the blind stage, hard, auto-submitting whatever is ticked.
   *
   * Short on purpose. `blind` is meant to measure what the representation makes
   * available at a glance, and given three minutes a participant stops reading
   * and starts reasoning from what they already know about software -- which is a
   * real skill, and not the one under test. A minute is enough to read a feature
   * list or a page of log and not enough to derive a call graph from memory.
   * Announcing the limit before the stage opens is part of it: a surprise cutoff
   * would measure typing speed.
   */
  blindSec: number
  /**
   * Seconds for the checked stage. Advisory rather than hard: the point of the
   * stage is that they got to the truth, and cutting somebody off two clicks
   * short would record a wrong answer they did not hold.
   *
   * `blindSec + checkedSec` has to equal the card's cap, or the card clock and
   * the stage clocks tell the participant two different things about how long
   * they have. A test holds them together.
   */
  checkedSec: number
  /**
   * One line of orientation. Deliberately honest about how the work looks rather
   * than how far it reaches: the export really does read most of the store, and
   * the slot comparison really is one helper plus one report. Whether that
   * appearance matches the truth is the thing being measured.
   */
  about: Record<Project, string>
}

export interface RequestSpec {
  id: RequestId
  /** Requests sharing a card share one timer. */
  card: string
  title: Record<Project, string>
  body: Record<Project, string>
  /** Minutes. Requests on the same card share the first one's cap. */
  capMin: number | null
  optional: boolean
  /** Closed questions to answer. Empty for a pure coding request. */
  choices: ChoiceQuestion[]
  /** Asks for a confidence rating. Only meaningful alongside `choices`. */
  wantsConfidence: boolean
  /** A worked example of the kind of question being asked. Shown in a callout. */
  tip?: Record<Project, string>
  /** Present on the reach trials, absent on everything else. */
  reach?: ReachTrial
  /** What the request is testing. Never shown to the participant. */
  archetype: string
  serves: string
}

export interface TaskCard {
  id: string
  title: string
  /** "Request 1", or "Requests 2 and 3" where a card carries two. */
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

/**
 * The reach trials' shared instructions, identical in both projects apart from
 * the app name. One copy, so the two trials cannot drift into asking subtly
 * different questions.
 */
const BLIND_THEN_CHECKED: Record<Project, string> = {
  coursecraft: `Below is one piece of work from this project's history.

Under it are twelve things people do with coursecraft. Tick every one that runs
through the code this piece of work added, the ones you would have to check if
somebody took the work out.

You answer twice. First from what you can already see, with **one minute** on the
clock. Then again after checking properly, with **three minutes**. You do not have
to change anything here, and nothing is graded on speed.`,
  confplan: `Below is one piece of work from this project's history.

Under it are twelve things people do with confplan. Tick every one that runs
through the code this piece of work added, the ones you would have to check if
somebody took the work out.

You answer twice. First from what you can already see, with **one minute** on the
clock. Then again after checking properly, with **three minutes**. You do not have
to change anything here, and nothing is graded on speed.`,
}

export const REQUESTS: RequestSpec[] = [
  {
    id: 'r1',
    card: 'c1',
    capMin: 5,
    optional: false,
    wantsConfidence: true,
    archetype: 'provenance in a tangled commit',
    serves: 'RQ1 / C1',
    title: {
      coursecraft: 'What changed course search?',
      confplan: 'What changed talk search?',
    },
    body: {
      coursecraft: `A student support ticket says this:

> Course search lists section times in a format I don't recognise, like
> \`[Mon 09:00-10:30, Wed 13:00-14:30]\`. Around the same time the app started
> accepting lowercase day names, like \`mon 09:00-10:30\`.

Go and find out what actually happened, then answer the three questions below.`,
      confplan: `A committee support ticket says this:

> Talk search lists session times in a format I don't recognise, like
> \`[Mon 09:00-10:30, Tue 13:00-14:30]\`. Around the same time the app started
> accepting lowercase day names, like \`mon 09:00-10:30\`.

Go and find out what actually happened, then answer the three questions below.`,
    },
    tip: {
      coursecraft: `A ticket like this is really three questions. Someone reports that
something looks different. You want to know **which piece of work** changed it,
**when** that work landed, and **what else** the same piece of work touched on
its way past, because the thing that broke is often not the thing the change
was for.

You do not have to answer in that order, and there is no expected route. Read
the code, read the history, ask your assistant, or all three.`,
      confplan: `A ticket like this is really three questions. Someone reports that
something looks different. You want to know **which piece of work** changed it,
**when** that work landed, and **what else** the same piece of work touched on
its way past, because the thing that broke is often not the thing the change
was for.

You do not have to answer in that order, and there is no expected route. Read
the code, read the history, ask your assistant, or all three.`,
    },
    choices: [
      {
        id: 'q1',
        prompt: 'Were the two things in the ticket one piece of work, or two?',
        options: {
          coursecraft: [
            'One piece of work. Both arrived together.',
            'Two, days apart.',
            'Two, on the same day.',
            'I could not tell.',
          ],
          confplan: [
            'One piece of work. Both arrived together.',
            'Two, days apart.',
            'Two, on the same day.',
            'I could not tell.',
          ],
        },
      },
      {
        id: 'q2',
        prompt: 'When did it land?',
        options: {
          coursecraft: [
            'The week of 29 June',
            'The week of 6 July',
            'The week of 20 July',
            'The week of 3 August',
            'I could not tell.',
          ],
          confplan: [
            'The week of 29 June',
            'The week of 6 July',
            'The week of 20 July',
            'The week of 3 August',
            'I could not tell.',
          ],
        },
      },
      {
        id: 'q3',
        prompt: 'Did anything else come along with it that the change was not advertised as doing?',
        options: {
          coursecraft: [
            'No, just the search command and its tests.',
            'Yes, a change to how day names are read when a slot is parsed.',
            'Yes, a change to how capacity limits are enforced.',
            'Yes, a change to the export format.',
            'I could not tell.',
          ],
          confplan: [
            'No, just the search command and its tests.',
            'Yes, a change to how day names are read when a slot is parsed.',
            'Yes, a change to how capacity limits are enforced.',
            'Yes, a change to the export format.',
            'I could not tell.',
          ],
        },
      },
    ],
  },
  {
    id: 'f1',
    card: 'f1',
    capMin: 4,
    optional: false,
    wantsConfidence: false,
    archetype: 'reach prediction, target narrower than it looks',
    serves: 'RQ1 / C1 / U1-U2',
    title: {
      coursecraft: 'What does the timetable export reach?',
      confplan: 'What does the agenda export reach?',
    },
    body: {
      coursecraft: BLIND_THEN_CHECKED.coursecraft,
      confplan: BLIND_THEN_CHECKED.confplan,
    },
    reach: {
      work: {
        coursecraft:
          'The timetable export, the work that added the command writing a timetable or the catalog out as Markdown or CSV.',
        confplan:
          'The agenda export, the work that added the command writing an agenda or the program out as Markdown or CSV.',
      },
      about: {
        coursecraft:
          'To do its job it reads courses, sections, rooms, students and time slots.',
        confplan:
          'To do its job it reads talks, sessions, rooms, attendees and time slots.',
      },
      blindSec: 60,
      checkedSec: 180,
    },
    choices: [],
  },
  {
    id: 'f2',
    card: 'f2',
    capMin: 4,
    optional: false,
    wantsConfidence: false,
    archetype: 'reach prediction, target broader than it looks',
    serves: 'RQ1 / C1 / U1-U2',
    title: {
      coursecraft: 'What does the time-comparison change reach?',
      confplan: 'What does the time-comparison change reach?',
    },
    body: {
      coursecraft: BLIND_THEN_CHECKED.coursecraft,
      confplan: BLIND_THEN_CHECKED.confplan,
    },
    reach: {
      work: {
        coursecraft:
          'The work that made two time ranges compare the same way everywhere, so a section ending exactly when another begins is treated consistently.',
        confplan:
          'The work that made two time ranges compare the same way everywhere, so a session ending exactly when another begins is treated consistently.',
      },
      about: {
        coursecraft: 'It is small: one comparison helper, and one report built on it.',
        confplan: 'It is small: one comparison helper, and one report built on it.',
      },
      blindSec: 60,
      checkedSec: 180,
    },
    choices: [],
  },
  {
    id: 'r2',
    card: 'c2',
    capMin: 15,
    optional: false,
    choices: [],
    wantsConfidence: false,
    archetype: 'entangled removal',
    serves: 'RQ2 / C2',
    title: {
      coursecraft: 'Take the waitlist out',
      confplan: 'Take the waitlist out',
    },
    body: {
      coursecraft: `The department has decided that waitlists are the registrar's job now. For the
next release the waitlist has to be gone. That means students can no longer join
a waitlist, nobody is promoted off it when a seat frees up, and the seat notices
stop.

Everything else has to keep working exactly as it does today. That includes
enrolling, capacity limits, conflict checks, course search, exports, statistics,
and the room audit. Back to back sections are legal and must stay legal.

The test suite is your safety net. When you think you are done, \`pytest -q\`
should pass, except for the waitlist's own tests, which may be gone.`,
      confplan: `The committee has decided that waitlists are the registration desk's job now. For
the next release the waitlist has to be gone. That means attendees can no longer
join a waitlist, nobody is promoted off it when a seat frees up, and the seat
notices stop.

Everything else has to keep working exactly as it does today. That includes
registering, capacity limits, clash checks, talk search, exports, statistics,
and the room audit. Adjacent sessions are legal and must stay legal.

The test suite is your safety net. When you think you are done, \`pytest -q\`
should pass, except for the waitlist's own tests, which may be gone.`,
    },
    tip: {
      coursecraft: `Most of this request is finding the right thing, not removing it.
The waitlist was not built in one go and other work landed on top of it, so the
first job is working out how far it reaches.`,
      confplan: `Most of this request is finding the right thing, not removing it.
The waitlist was not built in one go and other work landed on top of it, so the
first job is working out how far it reaches.`,
    },
  },
  {
    id: 'r3',
    card: 'c2',
    capMin: null,
    optional: false,
    choices: [],
    wantsConfidence: false,
    archetype: 'correction under time pressure',
    serves: 'RQ2 / C2',
    title: {
      coursecraft: 'Drops still need to work',
      confplan: 'Unregistering still needs to work',
    },
    body: {
      coursecraft: `One correction to the last request. Students must still be able to drop a
section themselves. Bring the drop command back, without any waitlist promotion
happening when a seat frees up.`,
      confplan: `One correction to the last request. Attendees must still be able to unregister
from a session themselves. Bring the unregister command back, without any
waitlist promotion happening when a seat frees up.`,
    },
  },
]

/**
 * Undefined, not a throw, for an id this study no longer asks.
 *
 * The dashboard resolves specs from the participant's STORED request documents,
 * not from `REQUESTS`, and pilots 01 to 03 ran the six-request design -- so
 * their collections still hold `r4`, `r5` and `r6`. Throwing took the whole
 * "Requests & scoring" tab down mid-render whenever one was opened, and took
 * r1 to r3 with it. `RequestId` still names all six, so nothing catches this at
 * compile time either.
 */
export function requestById(id: RequestId): RequestSpec | undefined {
  return REQUESTS.find((r) => r.id === id)
}

/**
 * What the participant calls one request: "Request 2", "Prediction 1".
 *
 * Requests 2 and 3 share a card, and the card is headed "Requests 2 and 3" while
 * each request under it was headed by its title alone. Request 3's own first line
 * is "One correction to the last request", which leaves a participant working out
 * from the prose which of the two headings they are looking at.
 */
export function requestHeading(r: RequestSpec): string {
  return `${r.reach ? 'Prediction' : 'Request'} ${r.id.slice(1)}`
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
    // Numbered by the request, not by the card. Requests 2 and 3 share one
    // clock, so they share one card, and calling that card "Request 2" would
    // make the next one "Request 3" while its own text talks about request 4.
    //
    // The reach trials are numbered in their own series and called something
    // else. Folding them into the request numbering would move the removal from
    // "request 2" to "request 4" in the participant's view while the answer key,
    // the rubrics and the facilitator sheet all still say R2 -- and `id.slice(1)`
    // would have labelled the first trial "Request 1" twice over.
    const noun = requests[0].reach ? 'Prediction' : 'Request'
    const numbers = requests.map((r) => r.id.slice(1))
    return {
      id,
      heading:
        numbers.length === 1
          ? `${noun} ${numbers[0]}`
          : `${noun}s ${numbers.slice(0, -1).join(', ')} and ${numbers[numbers.length - 1]}`,
      // Every title is written to open a heading, so joining them mid-sentence
      // capitalised the second one: "Take the waitlist out, then Unregistering
      // still needs to work".
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
 * 5 for the provenance question, 4 for each reach trial, 15 shared by the removal
 * and its correction. The eight minutes the reach trials add are added to the
 * session rather than taken off anything: setup is 10 minutes in the first half
 * and 5 in the second, so neither half's setup can give up 8, and pilots hit the
 * 15-minute cap on the removal in both conditions, so taking time off that would
 * have measured the cap rather than the condition. The session is 129 minutes of
 * work and the welcome page asks for two and a half hours.
 *
 * Summed rather than written down, because it was written down before and a
 * request's cap could change without it.
 */
export const BLOCK_CAP_MIN = REQUESTS.reduce((sum, r) => sum + (r.capMin ?? 0), 0)

/** The reach trials, in presentation order before counterbalancing. */
export const REACH_TRIALS = REQUESTS.filter(
  (r): r is RequestSpec & { reach: ReachTrial } => r.reach !== undefined,
)

/**
 * Requests that are not reach trials, i.e. the ones the participant is told to
 * expect as "requests". Counted rather than written down: the task preamble used
 * to say "three requests, about twenty minutes in total" and stayed that way
 * after the two reach trials and their eight minutes were added.
 */
export const REQUEST_COUNT = REQUESTS.length - REACH_TRIALS.length
