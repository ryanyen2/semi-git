// Sequence analysis over the action alphabet.
//
// A count of how many times someone ran `git show` says what they did. A count
// of how often `inspect` was followed by `history_op` without a `verify` in
// between says how they worked, and that is the difference the process figure
// is trying to show.

import { weightedLogOdds, type LogOddsRow } from '../lib/stats'
import { CATEGORY_LABEL, type Category } from '../study/taxonomy'
import type { Condition } from '../lib/types'
import type { Dataset, RequestMetrics } from './pipeline'

export function ngramsOf(seq: Category[], n: number): string[] {
  const out: string[] = []
  for (let i = 0; i + n <= seq.length; i++) out.push(seq.slice(i, i + n).join('>'))
  return out
}

export function countNgrams(sequences: Category[][], n: number): Map<string, number> {
  const counts = new Map<string, number>()
  for (const seq of sequences) {
    for (const g of ngramsOf(seq, n)) counts.set(g, (counts.get(g) ?? 0) + 1)
  }
  return counts
}

export function prettyNgram(key: string): string {
  return key
    .split('>')
    .map((c) => CATEGORY_LABEL[c as Category] ?? c)
    .join(' → ')
}

export interface NgramComparison {
  n: number
  rows: LogOddsRow[]
  dropped: number
  totalA: number
  totalB: number
  minTotal: number
  sequencesA: number
  sequencesB: number
}

/**
 * Compare action n-grams between the two conditions.
 *
 * `countA` is git and `countB` is sgt throughout, so a positive z means the
 * sequence leans towards sgt. Sequences seen fewer than `minTotal` times across
 * the whole corpus are dropped, and the count of dropped sequences travels with
 * the result so the figure can print it. Silent truncation reads as "we looked
 * at everything" when we did not.
 */
export function compareNgrams(dataset: Dataset, n: number, minTotal = 5): NgramComparison {
  const seqs = (condition: Condition): Category[][] =>
    dataset.participants.flatMap((p) =>
      p.requests.filter((r) => r.condition === condition).map((r) => r.sequence),
    )

  const a = seqs('git')
  const b = seqs('sgt')
  const countsA = countNgrams(a, n)
  const countsB = countNgrams(b, n)
  const { rows, dropped } = weightedLogOdds(countsA, countsB, minTotal)

  return {
    n,
    rows,
    dropped,
    totalA: [...countsA.values()].reduce((x, y) => x + y, 0),
    totalB: [...countsB.values()].reduce((x, y) => x + y, 0),
    minTotal,
    sequencesA: a.length,
    sequencesB: b.length,
  }
}

// ---------------------------------------------------------------------------
// Where the time went
// ---------------------------------------------------------------------------

export interface TimeBin {
  /** Bin centre in normalized request time, 0 to 1. */
  t: number
  /** Share of events in this bin per category, summing to 1. */
  shares: Record<Category, number>
  total: number
}

/**
 * Category share across normalized request time, pooled over a condition.
 *
 * Normalized rather than absolute because requests have different caps and
 * people finish at different points, and the question the figure asks is about
 * shape, not duration: does orientation happen up front and stay there, or does
 * it keep interrupting the work?
 */
export function timeProfile(
  dataset: Dataset,
  condition: Condition,
  bins = 20,
): TimeBin[] {
  const acc: Array<Partial<Record<Category, number>>> = Array.from({ length: bins }, () => ({}))
  const totals = new Array(bins).fill(0)

  for (const p of dataset.participants) {
    for (const e of p.events) {
      if (e.condition !== condition) continue
      const i = Math.min(bins - 1, Math.floor(e.tRel * bins))
      acc[i][e.category] = (acc[i][e.category] ?? 0) + 1
      totals[i]++
    }
  }

  return acc.map((counts, i) => {
    const shares = {} as Record<Category, number>
    for (const [k, v] of Object.entries(counts)) {
      shares[k as Category] = totals[i] > 0 ? v / totals[i] : 0
    }
    return { t: (i + 0.5) / bins, shares, total: totals[i] }
  })
}

export interface Strip {
  pid: string
  label: string
  condition: Condition
  requestId: string
  /** One segment per event, positioned in normalized request time. */
  segments: Array<{ t0: number; t1: number; category: Category }>
  events: number
}

/**
 * One strip per participant-half, for the individual half of the process
 * figure. Twelve strips per condition shown individually is the honest way to
 * plot twelve people; an average alone hides whether one person did something
 * unusual or everybody shifted a little.
 */
export function strips(dataset: Dataset, condition: Condition): Strip[] {
  const out: Strip[] = []
  for (const p of dataset.participants) {
    const events = p.events.filter((e) => e.condition === condition).sort((a, b) => a.ts - b.ts)
    if (events.length === 0) continue

    // Lay events out on a shared 0..1 axis by rank, so a strip reads as "what
    // came after what" rather than being dominated by one long pause.
    const segments = events.map((e, i) => ({
      t0: i / events.length,
      t1: (i + 1) / events.length,
      category: e.category,
    }))
    out.push({
      pid: p.pid,
      label: p.label,
      condition,
      requestId: 'all',
      segments,
      events: events.length,
    })
  }
  return out.sort((a, b) => a.label.localeCompare(b.label))
}

// ---------------------------------------------------------------------------
// Simple aggregates the dashboard shows as numbers
// ---------------------------------------------------------------------------

export function conditionTotals(dataset: Dataset, condition: Condition) {
  const rows: RequestMetrics[] = dataset.participants.flatMap((p) =>
    p.requests.filter((r) => r.condition === condition),
  )
  const sum = (pick: (r: RequestMetrics) => number) => rows.reduce((n, r) => n + pick(r), 0)
  const finite = (pick: (r: RequestMetrics) => number | null) =>
    rows.map(pick).filter((v): v is number => v != null && Number.isFinite(v))
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN)

  return {
    requests: rows.length,
    prompts: sum((r) => r.prompts),
    meanPromptChars: avg(finite((r) => (r.prompts ? r.meanPromptChars : null))),
    meanSpecificity: avg(finite((r) => r.meanSpecificity)),
    verificationRatio: avg(finite((r) => r.verificationRatio)),
    wrongTurns: sum((r) => r.wrongTurns),
    medianTimeToFirstOpMs: avg(finite((r) => r.timeToFirstHistoryOpMs)),
    hitCap: rows.filter((r) => r.hitCap).length,
  }
}
