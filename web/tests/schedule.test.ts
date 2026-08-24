// What the welcome page promises the participant about their afternoon.
//
// Both numbers on that page used to be typed out. They disagreed with each other
// by three minutes and with the timers by more, and nothing failed: the schedule
// is prose, the caps are code, and the only place they met was in a participant
// discovering the session ran long.

import { describe, expect, it } from 'vitest'
import { readFileSync, writeFileSync } from 'node:fs'
import { basename } from 'node:path'
import { BLOCK_CAP_MIN, CARD_COUNT, taskCards } from '../src/study/tasks'
import { STEPS, TOTAL_ESTIMATE_MIN, stepById } from '../src/study/flow'
import { DEBRIEF_MD, PLAN_FOR, TASK_PREAMBLE, WELCOME_MD, spell } from '../src/study/content'
import { SHEETS } from '../scripts/gen-materials'

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
  // The three places the participant is told how much work there is: the welcome
  // page, the preamble before the first card, and the debrief afterwards. All
  // three used to be typed out, and the preamble was still promising "three
  // requests, about twenty minutes" two redesigns after that stopped being true.
  // Spelled words rather than digits, because that is what the prose uses and a
  // digit would pass this while reading wrong on the page.
  it('quotes the same card count and total everywhere it says one', () => {
    const preamble = TASK_PREAMBLE('footfall', 'Sam Park', 'a small tool')
    expect(preamble).toContain(`${BLOCK_CAP_MIN} minutes of work in total`)
    expect(preamble).toContain(`${spell(CARD_COUNT)} cards to work through in order`)
    expect(DEBRIEF_MD).toContain(`the same ${spell(CARD_COUNT)} cards`)
    expect(CARD_COUNT).toBe(taskCards('footfall').length)
  })

  it('estimates every step that takes time', () => {
    const untimed = ['welcome', 'done']
    for (const step of STEPS) {
      if (untimed.includes(step.id)) continue
      expect(step.estimateMin, `${step.id} has no estimate`).toBeGreaterThan(0)
    }
  })
})

// The printed sheets are generated from the copy the website shows, so this is the
// check that somebody remembered to regenerate them. Drift here is not cosmetic:
// the facilitator hands these over and the participant keeps them next to the
// keyboard, so a sheet is read as authoritative. The welcome pair used to disagree
// about the length of the session by half an hour, and the sgt practice sheet
// taught a `sgt revert` command that the website's own version had fixed.
//
// Writing the files is the same test, because `vite-node` is not a dependency and
// a generator nobody can run is worse than none: the drift is then reported by a
// failure with no fix attached. `npm run gen:materials` sets the flag.
describe('the printed sheets', () => {
  const update = process.env.UPDATE_MATERIALS === '1'

  for (const [path, expected] of Object.entries(SHEETS)) {
    it(`${basename(path)} is what the website says, regenerated`, () => {
      if (update) {
        writeFileSync(path, expected)
        return
      }
      expect(
        readFileSync(path, 'utf8'),
        `${path} has drifted from src/study/content.ts; run \`npm run gen:materials\``,
      ).toBe(expected)
    })
  }

  // The welcome sheet carries the two numbers the participant consented to. They
  // are computed, so this checks the printed copy is the computed one and not a
  // stale render of it.
  it('tells the participant the same total the website computes', () => {
    const text = readFileSync('../docs/study/materials/00-welcome.md', 'utf8')
    expect(text).toContain(`about ${TOTAL_ESTIMATE_MIN} minutes of work`)
    expect(text).toContain(`| ${BLOCK_CAP_MIN} |`)
  })
})
