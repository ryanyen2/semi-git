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
its way past — because the thing that broke is often not the thing the change
was for.

You do not have to answer in that order, and there is no expected route. Read
the code, read the history, ask your assistant, or all three.`,
      confplan: `A ticket like this is really three questions. Someone reports that
something looks different. You want to know **which piece of work** changed it,
**when** that work landed, and **what else** the same piece of work touched on
its way past — because the thing that broke is often not the thing the change
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
            'Yes — a change to how day names are read when a slot is parsed.',
            'Yes — a change to how capacity limits are enforced.',
            'Yes — a change to the export format.',
            'I could not tell.',
          ],
          confplan: [
            'No, just the search command and its tests.',
            'Yes — a change to how day names are read when a slot is parsed.',
            'Yes — a change to how capacity limits are enforced.',
            'Yes — a change to the export format.',
            'I could not tell.',
          ],
        },
      },
    ],
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

export const BLOCK_CAP_MIN = 20
