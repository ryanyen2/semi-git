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
import { REQUESTS, requestById } from '../src/study/tasks'
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
})
