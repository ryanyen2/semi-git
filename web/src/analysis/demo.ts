// A synthetic cohort, for checking that the figures render and the exports work
// before the first real participant sits down.
//
// It is generated from a fixed seed and clearly labelled everywhere it is used.
// It exists so that nobody discovers a broken axis at 9pm on the night of
// session one, and so the analysis code has something to run against while the
// study is still being piloted. It is never written to Firestore.

import type { Condition } from '../lib/types'
import { CATEGORY_ORDER, type Category } from '../study/taxonomy'
import { TLX_SUBSCALES, mulberry32 } from '../lib/stats'
import type { Dataset, HalfSummary, ParticipantAnalysis, RequestMetrics } from './pipeline'
import { blocksForGroup, groupForOrdinal } from '../study/flow'
import { HLAC } from '../study/instruments'

const REQUEST_IDS = ['s1', 's2', 's3', 's4'] as const

/** Rough transition weights, different enough between conditions to be visible. */
const CHAINS: Record<Condition, Partial<Record<Category, Partial<Record<Category, number>>>>> = {
  git: {
    orient: { orient: 3, inspect: 5, search: 3, prompt: 2 },
    inspect: { inspect: 5, orient: 2, search: 3, prompt: 2, history_op: 1 },
    search: { search: 3, inspect: 3, prompt: 2, agent_edit: 1 },
    prompt: { agent_edit: 4, search: 2, inspect: 2 },
    agent_edit: { verify: 3, agent_edit: 2, prompt: 2 },
    manual_edit: { verify: 2, agent_edit: 1 },
    history_op: { verify: 2, recover: 2, inspect: 2, orient: 1 },
    verify: { prompt: 2, inspect: 2, agent_edit: 2, orient: 1 },
    recover: { orient: 3, inspect: 2, prompt: 1 },
  },
  sgt: {
    orient: { inspect: 5, prompt: 3, orient: 2, history_op: 1 },
    inspect: { history_op: 3, prompt: 3, inspect: 3, search: 1 },
    search: { inspect: 2, prompt: 2, search: 1 },
    prompt: { agent_edit: 3, history_op: 2, inspect: 2 },
    agent_edit: { verify: 4, prompt: 2 },
    manual_edit: { verify: 2 },
    history_op: { verify: 5, inspect: 2, orient: 1 },
    verify: { prompt: 2, orient: 1, inspect: 2, history_op: 1 },
    recover: { orient: 2, inspect: 2 },
  },
}

function walk(rand: () => number, condition: Condition, n: number): Category[] {
  const out: Category[] = ['orient']
  for (let i = 1; i < n; i++) {
    const from = out[out.length - 1]
    const weights = CHAINS[condition][from] ?? { orient: 1 }
    const entries = Object.entries(weights) as Array<[Category, number]>
    const total = entries.reduce((a, [, w]) => a + w, 0)
    let r = rand() * total
    let pick: Category = entries[0][0]
    for (const [cat, w] of entries) {
      r -= w
      if (r <= 0) {
        pick = cat
        break
      }
    }
    out.push(pick)
  }
  return out
}

function counts(seq: Category[]): Record<Category, number> {
  const c = Object.fromEntries(CATEGORY_ORDER.map((k) => [k, 0])) as Record<Category, number>
  for (const s of seq) c[s]++
  return c
}

export function demoDataset(seed = 4242): Dataset {
  const rand = mulberry32(seed)
  const gauss = () => {
    // Box-Muller, so the synthetic data has tails rather than a flat box.
    const u = Math.max(rand(), 1e-9)
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rand())
  }
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

  const participants: ParticipantAnalysis[] = []

  for (let ordinal = 1; ordinal <= 12; ordinal++) {
    const group = groupForOrdinal(ordinal)
    const blocks = blocksForGroup(group)
    // A per-person ability offset, so the paired plot shows real between-person
    // spread and the within-person effect still has to survive it.
    const ability = gauss() * 0.6

    const requests: RequestMetrics[] = []
    const events: ParticipantAnalysis['events'] = []
    const halves: HalfSummary[] = []

    for (const block of blocks) {
      const sgt = block.condition === 'sgt'
      const lift = sgt ? 0.75 : 0

      for (const rid of REQUEST_IDS) {
        const n = Math.round(clamp(46 + gauss() * 14 + (sgt ? -6 : 4), 14, 110))
        const seq = walk(rand, block.condition, n)
        const c = counts(seq)
        const activeMs = clamp(
          (rid === 's3' ? 4 : rid === 's2' ? 4 : 3) * 60_000 +
            gauss() * 90_000 -
            lift * 40_000,
          60_000,
          20 * 60_000,
        )
        const openedAt = 1_760_000_000_000 + ordinal * 7_200_000 + block.half * 3_600_000
        seq.forEach((cat, i) => {
          events.push({
            id: `${ordinal}-${block.half}-${rid}-${i}`,
            ts: openedAt + (i / seq.length) * activeMs,
            category: cat,
            kind: cat === 'prompt' ? 'prompt' : 'command',
            name: cat,
            text: cat === 'prompt' ? demoPrompt(rand, sgt) : cat,
            ok: true,
            tRel: i / seq.length,
            requestId: rid,
            half: block.half,
            condition: block.condition,
            surface: cat === 'prompt' ? 'agent' : rand() < 0.35 ? 'editor' : 'terminal',
          })
        })

        const base = rid === 's2' ? 1.1 : 1.0
        const score = clamp(Math.round(base + ability + lift + gauss() * 0.55), 0, 2)
        const damage = Math.max(
          0,
          Math.round((sgt ? 0.6 : 1.9) - ability * 0.4 + gauss() * (sgt ? 0.7 : 1.4)),
        )
        // d2 is the locate step: scored from the key rather than by a person, so
        // it carries a found/missed and no facilitator score. Same latent
        // ability behind both, so a person who does well on one tends to do well
        // on the other.
        const closed = rid === 's2'
        const locateCorrect = closed ? score + (sgt ? 0.6 : 0) + gauss() * 0.5 > 1.2 : null
        const confidence = clamp(Math.round(55 + score * 12 + gauss() * 14), 0, 100)

        // The prediction, on the step that reverts. Both arms find out the truth
        // by running the operation, so the two conditions separate mostly on the
        // blind stage and therefore on `gain` -- which is the shape the figures
        // have to be able to draw, including the case where it is absent.
        const trial = rid === 's3'
        const blindF1 = trial
          ? clamp(0.24 + (sgt ? 0.2 : 0) + ability * 0.12 + gauss() * 0.14, 0, 1)
          : 0
        const checkedF1 = trial ? clamp(blindF1 + 0.3 + ability * 0.1 + gauss() * 0.16, 0, 1) : 0
        const reach = trial
          ? {
              blind: blindF1,
              checked: checkedF1,
              gain: checkedF1 - blindF1,
              blindConfidence: clamp(Math.round(48 + blindF1 * 40 + gauss() * 12), 0, 100),
              checkedConfidence: clamp(Math.round(64 + checkedF1 * 30 + gauss() * 10), 0, 100),
              blindActiveMs: clamp(38_000 + gauss() * 12_000, 8_000, 60_000),
              blindPicked: Math.round(clamp(3 + gauss() * 2, 0, 12)),
              checkedPicked: Math.round(clamp(4 + gauss() * 1.5, 0, 12)),
              outOf: 4,
            }
          : null

        // s1's measured key is two of the eleven options, so the plausible band
        // is wide and centred lower than the eight-or-nine-option stages: a
        // participant who ticks a third option drops to 0.8, and one who ticks
        // everything scores 0.31.
        const quizPicksF1 =
          rid === 's1' ? clamp(0.55 + (sgt ? 0.2 : 0) + ability * 0.12 + gauss() * 0.18, 0, 1) : null

        requests.push({
          reach,
          quizPicksF1,
          // No stage asks a scored multiple choice, so the demo does not invent
          // one: a column of synthetic values under a question nobody is asked
          // reads as a real measure on the dashboard.
          quizChoiceCorrect: null,
          requestId: rid,
          half: block.half,
          condition: block.condition,
          project: block.project,
          activeMs,
          hitCap: rand() < (sgt ? 0.18 : 0.3),
          selfReport: score >= 2 ? 'done' : score >= 1 ? 'partial' : 'gave-up',
          confidence,
          counts: c,
          surfaces: {
            terminal: Math.round(n * 0.45),
            editor: Math.round(n * 0.25),
            agent: c.prompt + c.agent_edit,
          },
          sequence: seq,
          prompts: c.prompt,
          meanPromptChars: clamp(120 + gauss() * 40 + (sgt ? 18 : 0), 30, 400),
          meanSpecificity: clamp(
            (sgt ? 1.9 : 1.0) + gauss() * 0.4,
            0,
            3,
          ),
          specificityCounts: { 0: 0, 1: 0, 2: 0, 3: 0 },
          verificationRatio:
            c.agent_edit + c.history_op > 0
              ? c.verify / (c.agent_edit + c.history_op + c.manual_edit)
              : null,
          timeToFirstHistoryOpMs: clamp(
            (sgt ? 95_000 : 190_000) + gauss() * 60_000,
            5_000,
            900_000,
          ),
          wrongTurns: Math.max(0, Math.round((sgt ? 0.4 : 1.2) + gauss() * 0.8)),
          score: closed ? null : score,
          outOf: closed ? null : 2,
          collateralDamage: rid === 's3' || rid === 's4' ? damage : null,
          locateCorrect,
          locateAnswer: locateCorrect == null ? null : locateCorrect ? 'e7f2a19' : 'd1a2bc5',
          calibration: trial ? confidence / 100 - blindF1 : null,
        })
      }

      const hlac: Record<string, number> = {}
      for (const item of HLAC.items.filter((i) => i.type === 'likert')) {
        const better = clamp(Math.round(4.1 + (sgt ? 1.35 : 0) + ability * 0.5 + gauss()), 1, 7)
        hlac[item.id] = item.reverse ? 8 - better : better
      }

      // Subscales scattered around the overall figure, all already in workload
      // direction, so a demo dataset can never be the thing that reintroduces a
      // reversed Performance into a figure.
      const tlx = clamp(58 - lift * 11 - ability * 3 + gauss() * 9, 5, 100)
      const tlxSubscales: Record<string, number> = {}
      for (const k of TLX_SUBSCALES) {
        tlxSubscales[k] = clamp(Math.round((tlx + gauss() * 12) / 5) * 5, 0, 100)
      }

      halves.push({
        half: block.half,
        condition: block.condition,
        project: block.project,
        tlx,
        tlxSubscales,
        umux: clamp(64 + (sgt ? 10 : 0) + ability * 4 + gauss() * 9, 10, 100),
        hlac,
        // Empty since the per-half battery became two published instruments and
        // nothing else. The bag stays because pilot responses still carry the
        // two study-written checks, and the dashboard still renders those.
        checks: {},
      })
    }

    participants.push({
      pid: `demo-${ordinal}`,
      label: `P${String(ordinal).padStart(2, '0')}`,
      ordinal,
      group,
      gitConfidence: clamp(Math.round(3.5 + ability * 0.7 + gauss() * 0.7), 1, 5),
      events,
      requests,
      halves,
      firstCondition: blocks[0].condition,
      complete: true,
    })
  }

  return { participants, builtAt: Date.now(), unassignedEvents: 0 }
}

function demoPrompt(rand: () => number, sgt: boolean): string {
  const git = [
    'find where the time conflict check changed',
    'remove the waitlist feature but keep drops working',
    'what commit broke back to back enrollment?',
    'revert the waitlist and run the tests',
  ]
  const sgtish = [
    'show me feature f-02c4a091 and what depends on it',
    'revert the waitlist feature f-7ab21c and keep the drop fix',
    'why does slots.py::overlaps look like this, sgt why 25e91a9',
    'what landed in the same save as the search feature?',
  ]
  const pool = sgt ? sgtish : git
  return pool[Math.floor(rand() * pool.length)]
}
