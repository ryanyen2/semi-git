// Security rules, tested against the emulator.
//
// These are the properties the study depends on, not general hygiene. A
// participant who can edit their own condition, read the answer key mid-session,
// or overwrite an event that already landed would produce data that looks fine
// and is not. Each test below names the thing it is protecting.

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
  type RulesTestEnvironment,
} from '@firebase/rules-unit-testing'
import { doc, getDoc, setDoc, updateDoc, deleteDoc, collection, getDocs } from 'firebase/firestore'
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const OWNER = 'ryanyen2@mit.edu'
const CODE = 'testcode000000000000abcd'
const OTHER_CODE = 'othercode00000000000wxyz'

let env: RulesTestEnvironment

const owner = () => env.authenticatedContext('owner-uid', { email: OWNER, email_verified: true })
const stranger = () =>
  env.authenticatedContext('stranger-uid', { email: 'nobody@example.org', email_verified: true })
const participant = () => env.authenticatedContext('participant-uid')
const otherAnon = () => env.authenticatedContext('other-anon-uid')

const blocks = [
  { half: 1, condition: 'git', project: 'bikecount', label: 'Setup A' },
  { half: 2, condition: 'sgt', project: 'footfall', label: 'Setup B' },
]

beforeAll(async () => {
  env = await initializeTestEnvironment({
    projectId: 'sem-git-rules-test',
    firestore: {
      rules: readFileSync(join(here, '..', 'firestore.rules'), 'utf8'),
      host: '127.0.0.1',
      port: 8080,
    },
  })
})

afterAll(async () => {
  await env?.cleanup()
})

beforeEach(async () => {
  await env.clearFirestore()
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore()
    await setDoc(doc(db, 'participants', CODE), {
      code: CODE,
      studyId: 'main',
      ordinal: 1,
      label: 'P01',
      group: 1,
      blocks,
      status: 'created',
      currentStep: 'welcome',
      claimedUid: null,
    })
    await setDoc(doc(db, 'participants', OTHER_CODE), {
      code: OTHER_CODE,
      ordinal: 2,
      label: 'P02',
      group: 2,
      blocks,
      status: 'created',
      claimedUid: null,
    })
    await setDoc(doc(db, 'study', 'groundTruth'), { version: 'v1', episodes: [] })
    await setDoc(doc(db, 'study', 'credentials'), { anthropicApiKey: 'sk-ant-secret' })
    await setDoc(doc(db, 'participants', CODE, 'secrets', 'session'), {
      anthropicApiKey: 'sk-ant-issued',
      openaiApiKey: 'sk-issued',
    })
    await setDoc(doc(db, 'participants', CODE, 'scoring', 'r1-h1'), { score: 2 })
    await setDoc(doc(db, 'public', 'config'), { studyTitle: 'x', active: true })
  })
})

/** Claim the record as the participant, the way the app does on first load. */
async function claim(uid = 'participant-uid') {
  const db = env.authenticatedContext(uid).firestore()
  await updateDoc(doc(db, 'participants', CODE), {
    claimedUid: uid,
    claimedAt: Date.now(),
    status: 'claimed',
    startedAt: Date.now(),
    updatedAt: Date.now(),
  })
}

describe('the experimenter console', () => {
  it('lets the named owner in without any setup step', async () => {
    const db = owner().firestore()
    await assertSucceeds(getDocs(collection(db, 'participants')))
    await assertSucceeds(getDoc(doc(db, 'study', 'groundTruth')))
  })

  it('keeps everyone else out of the roster and the answer key', async () => {
    const db = stranger().firestore()
    await assertFails(getDocs(collection(db, 'participants')))
    await assertFails(getDoc(doc(db, 'study', 'groundTruth')))
    await assertFails(getDoc(doc(db, 'study', 'credentials')))
  })

  it('lets the owner add another experimenter, and that person in', async () => {
    await assertSucceeds(
      setDoc(doc(owner().firestore(), 'admins', 'colleague@example.org'), { role: 'experimenter' }),
    )
    const colleague = env.authenticatedContext('colleague-uid', {
      email: 'colleague@example.org',
      email_verified: true,
    })
    await assertSucceeds(getDocs(collection(colleague.firestore(), 'participants')))
  })

  it('does not let a stranger make themselves an experimenter', async () => {
    await assertFails(
      setDoc(doc(stranger().firestore(), 'admins', 'nobody@example.org'), { role: 'experimenter' }),
    )
  })

  it('only lets the owner create participants', async () => {
    await assertSucceeds(
      setDoc(doc(owner().firestore(), 'participants', 'newcode00000000000000ab'), {
        code: 'newcode00000000000000ab',
        ordinal: 3,
        group: 3,
        blocks,
      }),
    )
    await assertFails(
      setDoc(doc(participant().firestore(), 'participants', 'selfmade0000000000000ab'), {
        code: 'selfmade0000000000000ab',
        ordinal: 99,
        group: 1,
        blocks,
      }),
    )
  })
})

describe('the participant link is the capability', () => {
  it('opens the record for anyone holding the code', async () => {
    await assertSucceeds(getDoc(doc(participant().firestore(), 'participants', CODE)))
  })

  it('does not let codes be enumerated', async () => {
    await assertFails(getDocs(collection(participant().firestore(), 'participants')))
  })

  it('lets the first browser claim it, and locks out a second person', async () => {
    await claim()
    const intruder = otherAnon().firestore()
    await assertFails(
      updateDoc(doc(intruder, 'participants', CODE), {
        claimedUid: 'other-anon-uid',
        status: 'in-progress',
      }),
    )
  })
})

describe('a participant cannot move their own condition', () => {
  it('allows ordinary progress', async () => {
    await claim()
    const db = participant().firestore()
    await assertSucceeds(
      updateDoc(doc(db, 'participants', CODE), {
        currentStep: 'consent',
        status: 'consented',
        lastSeenAt: Date.now(),
        updatedAt: Date.now(),
      }),
    )
  })

  it('refuses a change to the assignment', async () => {
    await claim()
    const db = participant().firestore()
    await assertFails(
      updateDoc(doc(db, 'participants', CODE), {
        group: 2,
        currentStep: 'consent',
      }),
    )
    await assertFails(
      updateDoc(doc(db, 'participants', CODE), {
        blocks: [
          { half: 1, condition: 'sgt', project: 'footfall', label: 'Setup A' },
          { half: 2, condition: 'git', project: 'bikecount', label: 'Setup B' },
        ],
      }),
    )
    await assertFails(updateDoc(doc(db, 'participants', CODE), { ordinal: 7 }))
  })

  it('refuses a change to which study they are in', async () => {
    // `studyId` decides whether a record reaches the analysis at all. A
    // participant who could set it to `pilot` could remove their own session
    // from the results after the fact, and one who could set it to `main` could
    // inject a rehearsal into them -- both invisible in the dashboard, because
    // every other field would still look exactly right.
    await claim()
    const db = participant().firestore()
    await assertFails(updateDoc(doc(db, 'participants', CODE), { studyId: 'pilot' }))
    await assertFails(
      updateDoc(doc(db, 'participants', CODE), { studyId: 'pilot', currentStep: 'consent' }),
    )
  })
})

describe('questionnaire answers', () => {
  it('are writable by the person who claimed the record', async () => {
    await claim()
    const db = participant().firestore()
    await assertSucceeds(
      setDoc(doc(db, 'participants', CODE, 'responses', 'consent'), {
        instrumentId: 'consent',
        values: { read: true },
      }),
    )
    await assertSucceeds(getDoc(doc(db, 'participants', CODE, 'responses', 'consent')))
  })

  it('are not writable by anyone else, even with the code', async () => {
    await claim()
    const other = otherAnon().firestore()
    await assertFails(
      setDoc(doc(other, 'participants', CODE, 'responses', 'consent'), { values: {} }),
    )
  })

  it('can never be deleted', async () => {
    await claim()
    const db = participant().firestore()
    await setDoc(doc(db, 'participants', CODE, 'responses', 'background'), { values: {} })
    await assertFails(deleteDoc(doc(db, 'participants', CODE, 'responses', 'background')))
  })
})

describe('telemetry is append-only', () => {
  const event = { id: 'e1', kind: 'command', ts: 1, name: 'git', text: 'git log' }

  it('accepts events from a machine holding the code', async () => {
    // The bundle signs in on its own, with a different uid than the browser.
    const machine = env.authenticatedContext('machine-uid').firestore()
    await assertSucceeds(setDoc(doc(machine, 'participants', CODE, 'events', 'e1'), event))
  })

  it('refuses to let a landed event be rewritten or removed', async () => {
    const machine = env.authenticatedContext('machine-uid').firestore()
    await setDoc(doc(machine, 'participants', CODE, 'events', 'e1'), event)
    await assertFails(
      setDoc(doc(machine, 'participants', CODE, 'events', 'e1'), { ...event, text: 'rewritten' }),
    )
    await assertFails(deleteDoc(doc(machine, 'participants', CODE, 'events', 'e1')))
  })

  it('refuses malformed events', async () => {
    const machine = env.authenticatedContext('machine-uid').firestore()
    await assertFails(
      setDoc(doc(machine, 'participants', CODE, 'events', 'bad'), { note: 'no ts, no kind' }),
    )
  })

  it('is not readable by the participant', async () => {
    const machine = env.authenticatedContext('machine-uid').firestore()
    await setDoc(doc(machine, 'participants', CODE, 'events', 'e1'), event)
    await claim()
    await assertFails(getDoc(doc(participant().firestore(), 'participants', CODE, 'events', 'e1')))
  })

  it('is readable by the experimenter', async () => {
    const machine = env.authenticatedContext('machine-uid').firestore()
    await setDoc(doc(machine, 'participants', CODE, 'events', 'e1'), event)
    await assertSucceeds(getDocs(collection(owner().firestore(), 'participants', CODE, 'events')))
  })
})

describe('what a participant must never see', () => {
  it('hides their own scores while the session is running', async () => {
    await claim()
    await assertFails(
      getDoc(doc(participant().firestore(), 'participants', CODE, 'scoring', 'r1-h1')),
    )
  })

  it('hides the answer key', async () => {
    await claim()
    await assertFails(getDoc(doc(participant().firestore(), 'study', 'groundTruth')))
  })

  it('hides the study-wide key pool', async () => {
    await claim()
    await assertFails(getDoc(doc(participant().firestore(), 'study', 'credentials')))
  })
})

describe('session credentials', () => {
  it('are fetchable by the setup script, which only has the code', async () => {
    const machine = env.authenticatedContext('machine-uid').firestore()
    await assertSucceeds(getDoc(doc(machine, 'participants', CODE, 'secrets', 'session')))
  })

  it('cannot be written by the machine that reads them', async () => {
    const machine = env.authenticatedContext('machine-uid').firestore()
    await assertFails(
      setDoc(doc(machine, 'participants', CODE, 'secrets', 'session'), { anthropicApiKey: 'x' }),
    )
  })

  it('cannot be listed', async () => {
    const machine = env.authenticatedContext('machine-uid').firestore()
    await assertFails(getDocs(collection(machine, 'participants', CODE, 'secrets')))
  })
})

describe('the public settings the setup page needs', () => {
  it('are readable before anyone has signed in', async () => {
    await assertSucceeds(getDoc(doc(env.unauthenticatedContext().firestore(), 'public', 'config')))
  })

  it('are only writable by an experimenter', async () => {
    await assertFails(
      setDoc(doc(participant().firestore(), 'public', 'config'), { studyTitle: 'hijacked' }),
    )
    await assertSucceeds(
      setDoc(doc(owner().firestore(), 'public', 'config'), { studyTitle: 'ok' }),
    )
  })
})

it('the counterbalancing stays balanced at every prefix', async () => {
  const { groupForOrdinal } = await import('../src/study/flow')
  for (const n of [4, 8, 12]) {
    const counts = new Map<number, number>()
    for (let i = 1; i <= n; i++) {
      const g = groupForOrdinal(i)
      counts.set(g, (counts.get(g) ?? 0) + 1)
    }
    expect([...counts.values()]).toEqual(Array(4).fill(n / 4))
  }
})
