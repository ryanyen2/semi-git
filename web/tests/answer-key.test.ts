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
import { BEHAVIOURS, REQUESTS } from '../src/study/tasks'
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
    for (const spec of REQUESTS.filter((r) => r.identify || r.scoredLocate)) {
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
    for (const spec of REQUESTS.filter((r) => r.identify || r.scoredLocate)) {
      for (const project of PROJECTS) {
        expect(key.requestKeys[spec.id].locate![project].length).toBeGreaterThan(1)
      }
    }
  })

  // The one checklist option no page-level diff can produce, kept out of every
  // key on purpose. The reach sets are measured by removing a piece of work and
  // diffing the rendered pages, and the date window is the same control on all of
  // them -- so it is the option that makes the checklist discriminate at all. The
  // event-day work reaches nine of the eleven parts in footfall; without at least
  // one option outside every key, ticking everything would score close to
  // perfect. If a future target does move the window, this fails and the
  // generator needs a within-page probe (see write_answer_key.py).
  it('keeps the date window out of every reach set', () => {
    for (const [requestId, entry] of Object.entries(key.requestKeys)) {
      if (!entry.reach) continue
      const perProject = Array.isArray(entry.reach)
        ? [['both', entry.reach as string[]] as const]
        : Object.entries(entry.reach as Record<string, string[]>)
      for (const [project, ids] of perProject) {
        expect(ids, `${requestId} on ${project} names the date window`).not.toContain('dateWindow')
      }
    }
  })

  // A key whose reach set is most of the checklist scores a participant who ticks
  // everything almost perfectly, which is a measure of nothing. This does not
  // pick a threshold for what is acceptable -- the sets are measured and the
  // study reports the tick-everything baseline alongside them -- it only refuses
  // the degenerate end, which the validator also does.
  it('leaves room in the checklist for a wrong answer', () => {
    for (const [requestId, entry] of Object.entries(key.requestKeys)) {
      if (!entry.reach) continue
      const perProject = Array.isArray(entry.reach)
        ? [['both', entry.reach as string[]] as const]
        : Object.entries(entry.reach as Record<string, string[]>)
      for (const [project, ids] of perProject) {
        expect(ids.length, `${requestId} on ${project} is empty`).toBeGreaterThan(0)
        expect(
          ids.length,
          `${requestId} on ${project} names ${ids.length} of ${BEHAVIOURS.length}`,
        ).toBeLessThan(BEHAVIOURS.length)
      }
    }
  })

  // Well-formedness only. That the strings are shaped like answers is checkable
  // here; that they name the RIGHT work is a claim about the built testbed repos
  // and cannot be settled from this side. The shas in particular are regenerated
  // after every bundle build, and this test would keep passing over stale ones.
  it('accepts nothing blank or duplicated', () => {
    for (const spec of REQUESTS.filter((r) => r.identify || r.scoredLocate)) {
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

  it('rejects a key from an earlier design of the task block', () => {
    const old = clone()
    for (const entry of Object.values(old.requestKeys)) delete entry.locate
    expect(() => validateGroundTruth(old)).toThrow(/no locate answer for s2/)
  })

  it('rejects a key that answers only one project', () => {
    const half = clone()
    delete half.requestKeys.s2.locate!.footfall
    expect(() => validateGroundTruth(half)).toThrow(/not footfall/)
  })

  it('rejects a key that accepts nothing for a locate step', () => {
    const empty = clone()
    empty.requestKeys.s2.locate!.footfall = []
    expect(() => validateGroundTruth(empty)).toThrow(/accepts nothing for s2/)
  })

  it('rejects a key with no behaviour set for a scored checklist', () => {
    const noReach = clone()
    delete noReach.requestKeys.s2.reach
    expect(() => validateGroundTruth(noReach)).toThrow(/no behaviour set for s2/)
  })

  it('rejects a behaviour set naming a behaviour the checklist does not offer', () => {
    const drifted = clone()
    drifted.requestKeys.s2.reach = ['agenda', 'refund']
    expect(() => validateGroundTruth(drifted)).toThrow(/does not offer: agenda, refund/)
  })

  it('rejects a behaviour set that names every behaviour', () => {
    const everything = clone()
    everything.requestKeys.s2.reach = BEHAVIOURS.map((b) => b.id)
    expect(() => validateGroundTruth(everything)).toThrow(/placeholder/)
  })

  // No stage carries a scored multiple choice today. The validator still checks
  // them, so these stay and light up on their own if a stage adds one, rather
  // than being deleted and rewritten later from memory.
  const scoredChoice = REQUESTS.flatMap((r) =>
    r.quiz.filter((q) => q.kind === 'choice' && q.scored).map((q) => [r.id, q.id] as const),
  )[0]

  it.skipIf(!scoredChoice)('rejects a key with no correct value for a scored choice', () => {
    const [rid] = scoredChoice!
    const noChoice = clone()
    delete noChoice.requestKeys[rid].choices
    expect(() => validateGroundTruth(noChoice)).toThrow(/no correct value for/)
  })

  it.skipIf(!scoredChoice)('rejects a choice value the stage does not offer', () => {
    const [rid, qid] = scoredChoice!
    const drifted = clone()
    drifted.requestKeys[rid].choices = { [qid]: 'not-an-offered-value' }
    expect(() => validateGroundTruth(drifted)).toThrow(/not one of the options/)
  })
})
