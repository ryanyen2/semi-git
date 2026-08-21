// The answer key that ships in docs/study/, checked against the questions the
// study actually asks.
//
// This calls the SAME validator the upload calls, rather than a copy of its
// rules: a test that reimplements what it is testing keeps passing against its
// own copy while the shipped code drifts away from it.

import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import type { GroundTruth } from '../src/lib/types'
import { PROJECTS } from '../src/lib/types'
import { BEHAVIOURS, REACH_TRIALS, REQUESTS, requestById } from '../src/study/tasks'
import { validateGroundTruth } from '../src/study/answerKey'

const key = JSON.parse(readFileSync('../docs/study/answer-key.json', 'utf8')) as GroundTruth

describe('the shipped answer key', () => {
  it('passes the validation the upload runs', () => {
    expect(() => validateGroundTruth(key)).not.toThrow()
  })

  it('covers every request the study still asks', () => {
    for (const r of REQUESTS) expect(key.requestKeys[r.id]).toBeDefined()
  })

  it('answers every question request 1 asks, in both projects', () => {
    const asked = requestById('r1')!.choices.map((q) => q.id)
    for (const project of PROJECTS) {
      expect(Object.keys(key.requestKeys.r1.choices![project]).sort()).toEqual([...asked].sort())
    }
  })

  // Well-formedness only. That an index points at an option is checkable here;
  // that it points at the RIGHT option is a claim about the built testbed repos
  // and cannot be settled from this side.
  it('indexes an option that exists', () => {
    for (const q of requestById('r1')!.choices) {
      for (const project of PROJECTS) {
        const i = key.requestKeys.r1.choices![project][q.id]
        expect(q.options[project][i]).toBeDefined()
      }
    }
  })
})

describe('the validation itself', () => {
  const clone = () => JSON.parse(JSON.stringify(key)) as GroundTruth

  it('rejects a key from before request 1 became multiple choice', () => {
    const old = clone()
    for (const entry of Object.values(old.requestKeys)) delete entry.choices
    expect(() => validateGroundTruth(old)).toThrow(/no closed-question answers/)
  })

  it('rejects a key whose question ids do not match the ones asked', () => {
    const stale = clone()
    stale.requestKeys.r1.choices = { coursecraft: { qX: 0 }, confplan: { qX: 0 } }
    expect(() => validateGroundTruth(stale)).toThrow(/missing answers for r1/)
  })

  it('rejects a key that answers only one project', () => {
    const half = clone()
    delete half.requestKeys.r1.choices!.confplan
    expect(() => validateGroundTruth(half)).toThrow(/not confplan/)
  })

  it('rejects a key with no reach answer for a prediction trial', () => {
    const noReach = clone()
    delete noReach.requestKeys.f1.reach
    expect(() => validateGroundTruth(noReach)).toThrow(/no reach answer for f1/)
  })

  it('rejects a reach answer naming a behaviour the trial does not offer', () => {
    const drifted = clone()
    drifted.requestKeys.f1.reach = ['agenda', 'refund']
    expect(() => validateGroundTruth(drifted)).toThrow(/does not offer: refund/)
  })

  it('rejects a reach answer that names every behaviour', () => {
    const everything = clone()
    everything.requestKeys.f1.reach = BEHAVIOURS.map((b) => b.id)
    expect(() => validateGroundTruth(everything)).toThrow(/placeholder/)
  })
})

// The reach trials score by set overlap between what the participant ticked and
// what the key says, so the two sides have to be talking about the same twelve
// behaviours. Nothing at runtime notices when they are not: an id in the key that
// no option offers is simply never ticked, so it counts as a miss for every
// participant, and an option with no counterpart in the key counts as a false
// positive for anyone honest enough to tick it. Both look exactly like people
// being bad at the task.
describe('the reach key', () => {
  const ids = BEHAVIOURS.map((b) => b.id)

  it('has an entry for every reach trial', () => {
    for (const trial of REACH_TRIALS) {
      expect(key.requestKeys[trial.id]?.reach, `no reach key for ${trial.id}`).toBeDefined()
    }
  })

  it('names only behaviours the trial actually offers', () => {
    for (const trial of REACH_TRIALS) {
      for (const behaviour of key.requestKeys[trial.id].reach!) {
        expect(ids, `${trial.id} keys "${behaviour}", which no option offers`).toContain(behaviour)
      }
    }
  })

  // Neither bound is reachable by ticking everything or nothing, which is what
  // makes the two trials scoreable at all. The pair is also asymmetric on
  // purpose -- one target reaches less than it looks like it does and the other
  // more -- so a participant who simply ticks more boxes gains on one and loses
  // the same on the other.
  it('leaves both trials scoreable, in opposite directions', () => {
    const sizes = REACH_TRIALS.map((t) => key.requestKeys[t.id].reach!.length)
    for (const n of sizes) {
      expect(n).toBeGreaterThan(0)
      expect(n).toBeLessThan(ids.length)
    }
    expect(Math.min(...sizes)).toBeLessThan(ids.length / 2)
    expect(sizes.some((n) => n > Math.min(...sizes))).toBe(true)
  })

  it('agrees with the card clock about how long the trial is', () => {
    // Two clocks, one card: the stage clocks the participant watches, and the cap
    // the card was sized with. If they disagree the participant is told two
    // different things about how much time they have, and the block overruns by the
    // difference times the number of trials -- silently, since neither clock knows
    // about the other.
    for (const spec of REACH_TRIALS) {
      expect(spec.reach.blindSec + spec.reach.checkedSec).toBe((spec.capMin ?? 0) * 60)
    }
  })

  it('offers twelve distinct behaviours, labelled in both projects', () => {
    expect(new Set(ids).size).toBe(ids.length)
    for (const b of BEHAVIOURS) {
      for (const project of PROJECTS) {
        expect(b.label[project], `${b.id} has no label for ${project}`).toBeTruthy()
        expect(b.command[project], `${b.id} has no command for ${project}`).toBeTruthy()
      }
    }
  })
})
