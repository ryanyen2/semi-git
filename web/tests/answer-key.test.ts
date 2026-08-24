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

  it('accepts an answer for every locate step, in both projects', () => {
    for (const spec of REQUESTS.filter((r) => r.identify)) {
      for (const project of PROJECTS) {
        const accepted = key.requestKeys[spec.id].locate?.[project]
        expect(accepted, `${spec.id} has no accepted answers for ${project}`).toBeTruthy()
        expect(accepted!.length).toBeGreaterThan(0)
      }
    }
  })

  // The two arms name the same work differently, so the key has to accept both
  // vocabularies or it marks one arm wrong for being right in its own terms. A
  // key holding only a sha would do exactly that to the sgt arm.
  it('accepts more than one way of naming the work', () => {
    for (const spec of REQUESTS.filter((r) => r.identify)) {
      for (const project of PROJECTS) {
        expect(key.requestKeys[spec.id].locate![project].length).toBeGreaterThan(1)
      }
    }
  })

  // Well-formedness only. That the strings are shaped like answers is checkable
  // here; that they name the RIGHT work is a claim about the built testbed repos
  // and cannot be settled from this side. The shas in particular are regenerated
  // after every bundle build, and this test would keep passing over stale ones.
  it('accepts nothing blank or duplicated', () => {
    for (const spec of REQUESTS.filter((r) => r.identify)) {
      for (const project of PROJECTS) {
        const accepted = key.requestKeys[spec.id].locate![project]
        for (const a of accepted) expect(a.trim()).toBeTruthy()
        expect(new Set(accepted).size).toBe(accepted.length)
      }
    }
  })
})

describe('the validation itself', () => {
  const clone = () => JSON.parse(JSON.stringify(key)) as GroundTruth

  it('rejects a key from before the block became locate-and-reverse', () => {
    const old = clone()
    for (const entry of Object.values(old.requestKeys)) delete entry.locate
    expect(() => validateGroundTruth(old)).toThrow(/no locate answer for d2/)
  })

  it('rejects a key that answers only one project', () => {
    const half = clone()
    delete half.requestKeys.d2.locate!.footfall
    expect(() => validateGroundTruth(half)).toThrow(/not footfall/)
  })

  it('rejects a key that accepts nothing for a locate step', () => {
    const empty = clone()
    empty.requestKeys.d2.locate!.footfall = []
    expect(() => validateGroundTruth(empty)).toThrow(/accepts nothing for d2/)
  })

  it('rejects a key with no reach answer for the prediction', () => {
    const noReach = clone()
    delete noReach.requestKeys.d3.reach
    expect(() => validateGroundTruth(noReach)).toThrow(/no reach answer for d3/)
  })

  it('rejects a reach answer naming a behaviour the trial does not offer', () => {
    const drifted = clone()
    drifted.requestKeys.d3.reach = ['agenda', 'refund']
    expect(() => validateGroundTruth(drifted)).toThrow(/does not offer: agenda, refund/)
  })

  it('rejects a reach answer that names every behaviour', () => {
    const everything = clone()
    everything.requestKeys.d3.reach = BEHAVIOURS.map((b) => b.id)
    expect(() => validateGroundTruth(everything)).toThrow(/placeholder/)
  })
})
