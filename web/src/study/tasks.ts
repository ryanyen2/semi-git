// The six requests, in both projects.
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

import type { Project, RequestId } from '../lib/types'

export interface RequestSpec {
  id: RequestId
  /** Requests sharing a card share one timer. */
  card: string
  title: Record<Project, string>
  body: Record<Project, string>
  /** Minutes. Requests on the same card share the first one's cap. */
  capMin: number | null
  optional: boolean
  /** Asks for a written answer. */
  wantsAnswer: boolean
  /** Asks for a confidence rating. */
  wantsConfidence: boolean
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

export const REQUESTS: RequestSpec[] = [
  {
    id: 'r1',
    card: 'c1',
    capMin: 7,
    optional: false,
    wantsAnswer: true,
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
> accepting lowercase day names, like \`mon 09:00-10:30\`. Were those one change
> or two? What was the actual piece of work, when did it land, and did anything
> else come along with it?

Write two or three sentences answering the ticket.`,
      confplan: `A committee support ticket says this:

> Talk search lists session times in a format I don't recognise, like
> \`[Mon 09:00-10:30, Tue 13:00-14:30]\`. Around the same time the app started
> accepting lowercase day names, like \`mon 09:00-10:30\`. Were those one change
> or two? What was the actual piece of work, when did it land, and did anything
> else come along with it?

Write two or three sentences answering the ticket.`,
    },
  },
  {
    id: 'r2',
    card: 'c2',
    capMin: 15,
    optional: false,
    wantsAnswer: false,
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
  },
  {
    id: 'r3',
    card: 'c2',
    capMin: null,
    optional: false,
    wantsAnswer: false,
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
  {
    id: 'r4',
    card: 'c3',
    capMin: 10,
    optional: false,
    wantsAnswer: true,
    wantsConfidence: true,
    archetype: 'regression localization and repair',
    serves: 'RQ1 + RQ2 / C1 + C2',
    title: {
      coursecraft: 'Back to back enrollment broke',
      confplan: 'Back to back registration broke',
    },
    body: {
      coursecraft: `Advisers report that since late July students can no longer enroll in back to
back sections. If a student is in a section that runs 09:00 to 10:30, the app
now refuses to enroll them in one that runs 10:30 to 12:00, and calls it a time
conflict. It used to work, and there was even a fix that specifically made back
to back sections legal.

Find what changed it and restore the old behavior. Keep whatever else that
change was doing. In particular the room audit has to keep working.`,
      confplan: `Track chairs report that since late July attendees can no longer register for
back to back sessions. If an attendee is in a session that runs 09:00 to 10:30,
the app now refuses to register them for one that runs 10:30 to 12:00, and calls
it a clash. It used to work, and there was even a fix that specifically made
adjacent sessions legal.

Find what changed it and restore the old behavior. Keep whatever else that
change was doing. In particular the room audit has to keep working.`,
    },
  },
  {
    id: 'r5',
    card: 'c4',
    capMin: 12,
    optional: true,
    wantsAnswer: true,
    wantsConfidence: false,
    archetype: 'parallel alternatives, discard one',
    serves: 'RQ4',
    title: {
      coursecraft: 'Two ways to swap',
      confplan: 'Two ways to swap',
    },
    body: {
      coursecraft: `Students want to swap between two sections of the same course in one step. There
are two reasonable ways to build it.

- Do the whole swap at once, and if the new section refuses the student, put
  them back in the old one.
- Drop the student first and hold their seat for a moment, then enroll them.

Build both with your assistant, as two separate attempts. Then keep whichever
one you prefer and get rid of the other cleanly. Tell us why you kept the one
you kept.`,
      confplan: `Attendees want to swap between two sessions of the same talk in one step. There
are two reasonable ways to build it.

- Do the whole swap at once, and if the new session refuses the attendee, put
  them back in the old one.
- Unregister the attendee first and hold their seat for a moment, then register
  them.

Build both with your assistant, as two separate attempts. Then keep whichever
one you prefer and get rid of the other cleanly. Tell us why you kept the one
you kept.`,
    },
  },
  {
    id: 'r6',
    card: 'c5',
    capMin: null,
    optional: true,
    wantsAnswer: false,
    wantsConfidence: false,
    archetype: 'history surgery, code unchanged',
    serves: 'RQ2',
    title: {
      coursecraft: 'Clean up that tangled change',
      confplan: 'Clean up that tangled change',
    },
    body: {
      coursecraft: `The change you looked at in request 1 bothers you. Two unrelated pieces of work
landed as one unit. Separate them in the history so each one has a clear name.
Don't change any of the current code.`,
      confplan: `The change you looked at in request 1 bothers you. Two unrelated pieces of work
landed as one unit. Separate them in the history so each one has a clear name.
Don't change any of the current code.`,
    },
  },
]

export function requestById(id: RequestId): RequestSpec {
  const spec = REQUESTS.find((r) => r.id === id)
  if (!spec) throw new Error(`unknown request ${id}`)
  return spec
}

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
    const numbers = requests.map((r) => r.id.slice(1))
    return {
      id,
      heading:
        numbers.length === 1
          ? `Request ${numbers[0]}`
          : `Requests ${numbers.slice(0, -1).join(', ')} and ${numbers[numbers.length - 1]}`,
      title:
        requests.length === 1
          ? requests[0].title[project]
          : requests.map((r) => r.title[project]).join(', then '),
      capMin: requests[0].capMin,
      requests,
    }
  })
}

export const BLOCK_CAP_MIN = 45
