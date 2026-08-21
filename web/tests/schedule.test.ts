// What the welcome page promises the participant about their afternoon.
//
// Both numbers on that page used to be typed out. They disagreed with each other
// by three minutes and with the timers by more, and nothing failed: the schedule
// is prose, the caps are code, and the only place they met was in a participant
// discovering the session ran long.

import { describe, expect, it } from 'vitest'
import { BLOCK_CAP_MIN } from '../src/study/tasks'
import { STEPS, TOTAL_ESTIMATE_MIN, stepById } from '../src/study/flow'
import { PLAN_FOR, WELCOME_MD } from '../src/study/content'

describe('the session schedule', () => {
  it('estimates a task block as the sum of its cards caps', () => {
    for (const id of ['tasks-1', 'tasks-2']) {
      expect(stepById(id)?.estimateMin, `${id} does not follow the caps`).toBe(BLOCK_CAP_MIN)
    }
  })

  // The phrase at the top of the welcome page is what someone blocks out in a
  // calendar, and it is the one figure they consented to. It is written in words,
  // so what can be checked from here is that the work still fits inside it with
  // room for breaks. If this fails, the sentence in content.ts is what changes --
  // not this bound.
  it('fits the work inside the time the page asks people to set aside', () => {
    expect(PLAN_FOR).toBe('two and a half hours')
    expect(TOTAL_ESTIMATE_MIN).toBeLessThanOrEqual(150 - 15)
  })

  it('shows the participant the same total it computes', () => {
    expect(WELCOME_MD).toContain(`about ${TOTAL_ESTIMATE_MIN} minutes of work`)
    expect(WELCOME_MD).toContain(`| ${BLOCK_CAP_MIN} |`)
  })

  // A step with a clock and no estimate is missing from the schedule the
  // participant is shown, which makes the total quietly too small.
  it('estimates every step that takes time', () => {
    const untimed = ['welcome', 'done']
    for (const step of STEPS) {
      if (untimed.includes(step.id)) continue
      expect(step.estimateMin, `${step.id} has no estimate`).toBeGreaterThan(0)
    }
  })
})
