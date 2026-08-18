import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { REQUESTS, requestById } from '../src/study/tasks'
import type { RequestId } from '../src/lib/types'

// Mirrors the validation in Settings.load, so a change to one fails the other.
function validate(parsed: any) {
  if (!parsed.episodes || !parsed.requestKeys) throw new Error('no episodes or requestKeys')
  if (Object.values(parsed.requestKeys).filter((k: any) => k.choices).length === 0)
    throw new Error('no closed-question answers')
  for (const [requestId, entry] of Object.entries<any>(parsed.requestKeys)) {
    const asked = requestById(requestId as RequestId)?.choices ?? []
    if (!entry.choices || asked.length === 0) continue
    for (const [project, answers] of Object.entries<any>(entry.choices)) {
      const missing = asked.map((q) => q.id).filter((id) => !(id in answers))
      if (missing.length > 0) throw new Error(`missing ${requestId} (${project}): ${missing}`)
    }
  }
}

describe('the shipped answer key', () => {
  const key = JSON.parse(readFileSync('../docs/study/answer-key.json', 'utf8'))

  it('passes the upload validation', () => {
    expect(() => validate(key)).not.toThrow()
  })

  it('answers every question request 1 actually asks, in both projects', () => {
    const asked = requestById('r1')!.choices.map((q) => q.id)
    for (const project of ['coursecraft', 'confplan'] as const) {
      expect(Object.keys(key.requestKeys.r1.choices[project]).sort()).toEqual([...asked].sort())
    }
  })

  it('indexes an option that exists', () => {
    for (const q of requestById('r1')!.choices) {
      for (const project of ['coursecraft', 'confplan'] as const) {
        const i = key.requestKeys.r1.choices[project][q.id]
        expect(q.options[project][i]).toBeDefined()
      }
    }
  })

  it('rejects a key whose question ids do not match', () => {
    const stale = { ...key, requestKeys: { r1: { ...key.requestKeys.r1,
      choices: { coursecraft: { qX: 0 }, confplan: { qX: 0 } } } } }
    expect(() => validate(stale)).toThrow(/missing r1/)
  })

  it('rejects a key with no choices block at all', () => {
    const old = { ...key, requestKeys: Object.fromEntries(
      Object.entries<any>(key.requestKeys).map(([k, v]) => [k, { ...v, choices: undefined }])) }
    expect(() => validate(old)).toThrow(/no closed-question answers/)
  })

  it('covers every request the study still asks', () => {
    for (const r of REQUESTS) expect(key.requestKeys[r.id]).toBeDefined()
  })
})
