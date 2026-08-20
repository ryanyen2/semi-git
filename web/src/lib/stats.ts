// Estimation statistics for a within-subject design with twelve people.
//
// Everything here reports an interval. There is no significance test in this
// file on purpose: twelve participants can only support claims about large
// effects, and a p-value on that sample is theatre. See protocol.md §7.

/** Deterministic RNG, so a figure does not shift every time it re-renders. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export function mean(xs: number[]): number {
  if (xs.length === 0) return NaN
  return xs.reduce((a, b) => a + b, 0) / xs.length
}

export function variance(xs: number[]): number {
  const n = xs.length
  if (n < 2) return NaN
  const m = mean(xs)
  return xs.reduce((a, b) => a + (b - m) ** 2, 0) / (n - 1)
}

export function sd(xs: number[]): number {
  return Math.sqrt(variance(xs))
}

export function se(xs: number[]): number {
  return sd(xs) / Math.sqrt(xs.length)
}

export function median(xs: number[]): number {
  if (xs.length === 0) return NaN
  const s = [...xs].sort((a, b) => a - b)
  const mid = s.length >> 1
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2
}

export function quantile(xs: number[], q: number): number {
  if (xs.length === 0) return NaN
  const s = [...xs].sort((a, b) => a - b)
  const pos = (s.length - 1) * q
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  return lo === hi ? s[lo] : s[lo] + (s[hi] - s[lo]) * (pos - lo)
}

export interface Estimate {
  /** Point estimate: the paired mean difference. */
  estimate: number
  lo: number
  hi: number
  n: number
  /** Standardized effect size, Cohen's dz for paired data. */
  dz: number
  resamples: number
  /** True when the sample was too small or too degenerate to studentize. */
  fellBackToPercentile: boolean
}

export interface Pair {
  /** Participant id, so resampling is over people, not observations. */
  id: string
  a: number
  b: number
}

const EMPTY: Estimate = {
  estimate: NaN,
  lo: NaN,
  hi: NaN,
  n: 0,
  dz: NaN,
  resamples: 0,
  fellBackToPercentile: false,
}

/**
 * Paired mean difference (b − a) with a studentized bootstrap interval.
 *
 * Studentized rather than percentile because the studentized interval is
 * second-order accurate and the percentile interval is not, and with n=12 that
 * difference is visible. When the resampled standard error collapses -- which
 * happens when a bootstrap sample draws the same participant twelve times -- the
 * t-ratio is undefined, so those draws are discarded and the function falls
 * back to the percentile interval if too few survive. The fallback is reported
 * rather than hidden, because an interval computed a different way than the
 * caption says is exactly the kind of thing that should not be silent.
 */
export function pairedEstimate(pairs: Pair[], resamples = 10000, seed = 20260815): Estimate {
  const usable = pairs.filter((p) => Number.isFinite(p.a) && Number.isFinite(p.b))
  const n = usable.length
  if (n < 2) return { ...EMPTY, n }

  const diffs = usable.map((p) => p.b - p.a)
  const dbar = mean(diffs)
  const sdDiff = sd(diffs)
  const seDiff = sdDiff / Math.sqrt(n)
  const dz = sdDiff === 0 ? 0 : dbar / sdDiff

  if (seDiff === 0) {
    return { estimate: dbar, lo: dbar, hi: dbar, n, dz, resamples: 0, fellBackToPercentile: false }
  }

  const rand = mulberry32(seed)
  const ts: number[] = []
  const means: number[] = []
  for (let r = 0; r < resamples; r++) {
    const draw: number[] = []
    for (let i = 0; i < n; i++) draw.push(diffs[Math.floor(rand() * n)])
    const m = mean(draw)
    means.push(m)
    const s = sd(draw) / Math.sqrt(n)
    if (s > 1e-12) ts.push((m - dbar) / s)
  }

  // Need enough surviving t-ratios for the tails to mean anything.
  if (ts.length < resamples * 0.5) {
    return {
      estimate: dbar,
      lo: quantile(means, 0.025),
      hi: quantile(means, 0.975),
      n,
      dz,
      resamples,
      fellBackToPercentile: true,
    }
  }

  // Note the crossed tails: the studentized interval is
  // [d̄ − t*_{1−α/2}·se, d̄ − t*_{α/2}·se].
  const tHi = quantile(ts, 0.975)
  const tLo = quantile(ts, 0.025)
  return {
    estimate: dbar,
    lo: dbar - tHi * seDiff,
    hi: dbar - tLo * seDiff,
    n,
    dz,
    resamples,
    fellBackToPercentile: false,
  }
}

/**
 * The resampled paired differences themselves, for drawing the distribution
 * beside the interval. Showing the bootstrap distribution rather than only its
 * two ends is what makes an estimation plot honest about shape.
 */
export function pairedBootstrapDistribution(
  pairs: Pair[],
  resamples = 5000,
  seed = 20260815,
): number[] {
  const usable = pairs.filter((p) => Number.isFinite(p.a) && Number.isFinite(p.b))
  const n = usable.length
  if (n < 2) return []
  const diffs = usable.map((p) => p.b - p.a)
  const rand = mulberry32(seed)
  const out: number[] = []
  for (let r = 0; r < resamples; r++) {
    let sum = 0
    for (let i = 0; i < n; i++) sum += diffs[Math.floor(rand() * n)]
    out.push(sum / n)
  }
  return out
}

/** Bootstrap interval for a single-sample statistic, resampling participants. */
export function bootstrapCI(
  values: number[],
  stat: (xs: number[]) => number = mean,
  resamples = 10000,
  seed = 20260815,
): { estimate: number; lo: number; hi: number } {
  const xs = values.filter(Number.isFinite)
  if (xs.length === 0) return { estimate: NaN, lo: NaN, hi: NaN }
  const rand = mulberry32(seed)
  const out: number[] = []
  for (let r = 0; r < resamples; r++) {
    const draw: number[] = []
    for (let i = 0; i < xs.length; i++) draw.push(xs[Math.floor(rand() * xs.length)])
    out.push(stat(draw))
  }
  return { estimate: stat(xs), lo: quantile(out, 0.025), hi: quantile(out, 0.975) }
}

// ---------------------------------------------------------------------------
// Weighted log-odds with an informative Dirichlet prior (Monroe et al. 2008)
// ---------------------------------------------------------------------------

export interface LogOddsRow {
  key: string
  countA: number
  countB: number
  total: number
  /** Positive means the sequence leans towards group B. */
  z: number
  delta: number
}

/**
 * Which sequences distinguish two corpora, shrunk toward the pooled corpus.
 *
 * Raw frequency differences and plain lift both over-report rare sequences, and
 * on twelve participants that mostly means reporting noise. Monroe's z-scores
 * shrink rare sequences toward zero, which is the standard fix and the one a
 * reviewer will expect to see named.
 *
 * @param minTotal sequences below this pooled count are dropped entirely; the
 *   caller is expected to report how many, rather than let the drop be implicit.
 */
export function weightedLogOdds(
  countsA: Map<string, number>,
  countsB: Map<string, number>,
  minTotal = 5,
  alpha0?: number,
): { rows: LogOddsRow[]; dropped: number } {
  const keys = new Set([...countsA.keys(), ...countsB.keys()])
  const pooled = new Map<string, number>()
  let pooledTotal = 0
  for (const k of keys) {
    const c = (countsA.get(k) ?? 0) + (countsB.get(k) ?? 0)
    pooled.set(k, c)
    pooledTotal += c
  }
  if (pooledTotal === 0) return { rows: [], dropped: 0 }

  const a0 = alpha0 ?? Math.min(1000, pooledTotal)
  const nA = [...countsA.values()].reduce((a, b) => a + b, 0)
  const nB = [...countsB.values()].reduce((a, b) => a + b, 0)

  const rows: LogOddsRow[] = []
  let dropped = 0
  for (const k of keys) {
    const total = pooled.get(k)!
    if (total < minTotal) {
      dropped++
      continue
    }
    const ai = (a0 * total) / pooledTotal
    const ya = countsA.get(k) ?? 0
    const yb = countsB.get(k) ?? 0
    const oddsA = (ya + ai) / (nA + a0 - ya - ai)
    const oddsB = (yb + ai) / (nB + a0 - yb - ai)
    const delta = Math.log(oddsB) - Math.log(oddsA)
    const varDelta = 1 / (ya + ai) + 1 / (yb + ai)
    rows.push({ key: k, countA: ya, countB: yb, total, delta, z: delta / Math.sqrt(varDelta) })
  }
  rows.sort((x, y) => Math.abs(y.z) - Math.abs(x.z))
  return { rows, dropped }
}

// ---------------------------------------------------------------------------
// Instrument scoring
// ---------------------------------------------------------------------------

export const TLX_SUBSCALES = [
  'mental', 'physical', 'temporal', 'performance', 'effort', 'frustration',
] as const

/**
 * The six subscales in workload direction, 0-100, or null if any is unanswered.
 *
 * This is the ONLY place Performance is reversed, and everything that reports a
 * subscale must come through here rather than reading the stored response.
 *
 * The stored response for Performance runs Failure(0) to Perfect(100), because
 * that is the direction its anchors are read in and presenting it the other way
 * is what produces the instrument's best-documented failure: a participant who
 * did well marking the high end and contributing maximum workload. Collecting
 * it reversed is explicitly allowed, on the condition that it is turned back
 * "before analysis or reporting" -- and reporting was the half we had not done.
 * `tlxScore` reversed it inside the average, so the aggregate was right while
 * every per-subscale number in the store still ran the other way from the other
 * five. Nothing had read them yet. The first per-subscale figure anyone drew
 * would have shown the arm that performed best carrying the highest performance
 * workload, and it would have looked plausible.
 */
export function tlxSubscales(values: Record<string, unknown>): Record<string, number> | null {
  const out: Record<string, number> = {}
  for (const k of TLX_SUBSCALES) {
    const v = values[k]
    if (typeof v !== 'number') return null
    out[k] = k === 'performance' ? 100 - v : v
  }
  return out
}

/** Raw (unweighted) TLX: the mean of the six subscales in workload direction. */
export function tlxScore(values: Record<string, unknown>): number | null {
  const subscales = tlxSubscales(values)
  return subscales === null ? null : mean(Object.values(subscales))
}

/**
 * UMUX-Lite, raw, 0 to 100.
 *
 * Two seven-point items, scored by the published formula: subtract 1 from each
 * (so each runs 0-6), sum, and express as a percentage of 12.
 *
 * Deliberately not converted to a SUS-equivalent score. That regression was
 * fitted to particular corpora, and the quantity this study reports is a
 * within-participant difference between two setups, which gains nothing from
 * the transformation while inheriting its error.
 */
export function umuxLiteScore(values: Record<string, unknown>): number | null {
  const capability = values.capability
  const easy = values.easy
  if (typeof capability !== 'number' || typeof easy !== 'number') return null
  return ((capability - 1 + (easy - 1)) / 12) * 100
}

/** Background git-verb grid, 0 to 24. */
export function gitExpertise(values: Record<string, unknown>): number | null {
  const verbs = ['log', 'blame', 'bisect', 'revert', 'reset', 'rebasei', 'reflog', 'cherrypick']
  let sum = 0
  for (const v of verbs) {
    const raw = values[`gitVerbs.${v}`]
    if (raw == null) return null
    sum += Number(raw)
  }
  return sum
}
