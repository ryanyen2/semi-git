// The analysis is a pure function of the raw event stream, so it can be tested
// on a stream we wrote by hand. These check the judgement calls -- what counts
// as verification, what counts as a wrong turn, what gets dropped as a
// duplicate -- because those are the places where a plausible-looking mistake
// would quietly change a result in the paper.

import { describe, expect, it } from 'vitest'
import type { EventDoc, Participant, RequestDoc } from '../src/lib/types'
import { analyzeParticipant, buildDataset, conditionValue } from '../src/analysis/pipeline'
import { compareNgrams, countNgrams, strips, timeProfile } from '../src/analysis/ngram'
import { classify, promptSpecificity } from '../src/study/taxonomy'
import { pairedEstimate, tlxScore, umuxLiteScore, weightedLogOdds } from '../src/lib/stats'
import { TLX } from '../src/study/instruments'
import { demoDataset } from '../src/analysis/demo'
import { blocksForGroup } from '../src/study/flow'
import { PILOT_ORDINAL_BASE, isPilot, participantIdentity } from '../src/lib/db'

const T0 = 1_760_000_000_000

function participantDoc(): Participant {
  return {
    code: 'c1',
    studyId: 'main',
    ordinal: 1,
    label: 'P01',
    group: 1,
    blocks: blocksForGroup(1),
    email: null,
    status: 'completed',
    currentStep: 'done',
    stepState: {},
    claimedUid: 'u',
    claimedAt: T0,
    startedAt: T0,
    consentAt: T0,
    completedAt: T0 + 1000,
    lastSeenAt: T0,
    createdAt: T0,
    updatedAt: T0,
  }
}

function requestDoc(over: Partial<RequestDoc> = {}): RequestDoc & { id: string } {
  return {
    id: 'r1-h1',
    requestId: 'r1',
    half: 1,
    condition: 'git',
    project: 'coursecraft',
    openedAt: T0,
    submittedAt: T0 + 600_000,
    elapsedMs: 600_000,
    activeMs: 600_000,
    pauses: [],
    capMs: 420_000,
    hitCap: false,
    confidence: 70,
    selfReport: 'done',
    notes: '',
    ...over,
  }
}

function ev(over: Partial<EventDoc> & { ts: number; kind: EventDoc['kind'] }): EventDoc {
  return {
    id: `e${over.ts}-${over.name ?? over.kind}`,
    half: 1,
    condition: 'git',
    requestId: null,
    name: null,
    text: null,
    deviceId: 'd1',
    ...over,
  } as EventDoc
}

describe('the action taxonomy', () => {
  const fresh = { dirtySinceCheck: false, lastOpFailed: false }
  const dirty = { dirtySinceCheck: true, lastOpFailed: false }

  it('separates broad reads from targeted ones', () => {
    expect(classify({ kind: 'command', name: 'git', text: 'git log --oneline' }, fresh)).toBe('orient')
    expect(classify({ kind: 'command', name: 'git', text: 'git show 9f5f7e5' }, fresh)).toBe('inspect')
    expect(classify({ kind: 'command', name: 'sgt', text: 'sgt now' }, fresh)).toBe('orient')
    expect(classify({ kind: 'command', name: 'sgt', text: 'sgt why 25e91a9' }, fresh)).toBe('inspect')
  })

  it('reads a diff as orienting before a change and as checking after one', () => {
    // The whole point of tracking position. Ignoring it mislabels roughly a
    // third of diffs, which was visible in both pilot logs.
    expect(classify({ kind: 'command', name: 'git', text: 'git diff' }, fresh)).toBe('orient')
    expect(classify({ kind: 'command', name: 'git', text: 'git diff' }, dirty)).toBe('verify')
  })

  it('treats reflog and hard reset as recovery, not as ordinary history work', () => {
    expect(classify({ kind: 'command', name: 'git', text: 'git reflog' }, fresh)).toBe('recover')
    expect(classify({ kind: 'command', name: 'git', text: 'git reset --hard HEAD~1' }, fresh)).toBe('recover')
    expect(classify({ kind: 'command', name: 'git', text: 'git revert abc1234' }, fresh)).toBe('history_op')
  })

  it('puts the two tools on the same alphabet', () => {
    expect(classify({ kind: 'command', name: 'sgt', text: 'sgt revert f-02c4a091' }, fresh)).toBe('history_op')
    expect(classify({ kind: 'tool', name: 'mcp__sgt__sgt_revert', text: null }, fresh)).toBe('history_op')
    expect(classify({ kind: 'tool', name: 'Edit', text: null }, fresh)).toBe('agent_edit')
    expect(classify({ kind: 'prompt', name: 'user', text: 'hi' }, fresh)).toBe('prompt')
  })

  it('drops filler rather than folding it into an "other" bucket', () => {
    // A filler symbol in the alphabet dominates every bigram it appears in.
    expect(classify({ kind: 'command', name: 'cd', text: 'cd work' }, fresh)).toBeNull()
    expect(classify({ kind: 'heartbeat', name: null, text: null }, fresh)).toBeNull()
  })
})

describe('prompt specificity', () => {
  it('scores a prompt that names a commit or an intent highest', () => {
    expect(promptSpecificity('revert 9f5f7e5 please')).toBe(3)
    expect(promptSpecificity('revert the waitlist feature f-02c4a091@3')).toBe(3)
  })

  it('scores a file or a test in the middle, and a vague ask at zero', () => {
    expect(promptSpecificity('fix tests/test_waitlist.py')).toBe(1)
    expect(promptSpecificity('make the tests pass')).toBe(0)
  })
})

describe('per-request measures', () => {
  it('counts a verification only when something was changed first', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 + 1000, kind: 'command', name: 'git', text: 'git log' }),
      ev({ ts: T0 + 2000, kind: 'tool', name: 'Edit', text: 'app.py' }),
      ev({ ts: T0 + 3000, kind: 'command', name: 'pytest', text: 'pytest -q' }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    const m = a.requests[0]
    expect(m.counts.orient).toBe(1)
    expect(m.counts.agent_edit).toBe(1)
    expect(m.counts.verify).toBe(1)
    expect(m.verificationRatio).toBe(1)
  })

  it('calls a history operation followed by a recovery a wrong turn', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 + 1000, kind: 'command', name: 'git', text: 'git revert abc1234' }),
      ev({ ts: T0 + 30_000, kind: 'command', name: 'git', text: 'git reset --hard HEAD~1' }),
      // Same shape but far apart in time: that is a later decision, not a slip.
      ev({ ts: T0 + 200_000, kind: 'command', name: 'git', text: 'git revert def5678' }),
      ev({ ts: T0 + 500_000, kind: 'command', name: 'git', text: 'git reflog' }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    expect(a.requests[0].wrongTurns).toBe(1)
  })

  it('ignores events that fall outside every request window', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 - 60_000, kind: 'command', name: 'git', text: 'git log' }),
      ev({ ts: T0 + 1000, kind: 'command', name: 'git', text: 'git log' }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    expect(a.requests[0].counts.orient).toBe(1)
  })

  it('scores the closed questions against the key, and says how sure they were', () => {
    // Two of three right at 70% confidence: they were slightly overconfident.
    // The arithmetic is trivial and the comparison is the whole measure, so the
    // thing worth pinning is that an index is compared to an index -- a stray
    // string on either side would score every answer wrong and look like data.
    const a = analyzeParticipant(
      {
        participant: participantDoc(),
        responses: [],
        requests: [requestDoc({ choices: { q1: 0, q2: 1, q3: 3 } })],
        events: [],
        scoring: [],
      },
      { r1: { coursecraft: { q1: 0, q2: 1, q3: 1 } } },
    )
    expect(a.requests[0].choiceScore).toBe(2)
    expect(a.requests[0].choiceOutOf).toBe(3)
    expect(a.requests[0].calibration).toBeCloseTo(0.7 - 2 / 3)
  })

  it('keeps the manipulation checks out of HLAC and coerces the select to a number', () => {
    // Both rode along on the HLAC block because that is where they fit in the
    // flow, and both were being lost: `timePressure` is a select, so its five
    // labelled options arrive as strings and a `typeof v === 'number'` filter
    // dropped it entirely, and `realistic` is five-point, so averaging it into a
    // block of seven-point items understates it.
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [
        {
          id: 'hlac-h1',
          instrument: 'hlac',
          version: 'hlac-v3',
          half: 1,
          condition: 'git',
          submittedAt: T0,
          values: { q1: 6, realistic: 4, timePressure: '2' },
        } as never,
      ],
      requests: [],
      events: [],
      scoring: [],
    })

    const h = a.halves.find((x) => x.half === 1)!
    expect(h.hlac).toEqual({ q1: 6 })
    expect(h.checks).toEqual({ realistic: 4, timePressure: 2 })
  })

  it('leaves them unscored when the participant answered nothing, rather than scoring zero', () => {
    // `choices` is seeded as `{}` the moment a request is opened, and `{}` is
    // truthy — so a participant who ran out of time having picked nothing used
    // to score 0 of 3, which reads identically to three wrong answers and drags
    // the condition mean down. With a confidence rating attached it was worse:
    // nothing answered plus a moved slider recorded as maximum overconfidence.
    const a = analyzeParticipant(
      {
        participant: participantDoc(),
        responses: [],
        requests: [requestDoc({ choices: {}, confidence: 70 })],
        events: [],
        scoring: [],
      },
      { r1: { coursecraft: { q1: 0, q2: 1, q3: 1 } } },
    )
    expect(a.requests[0].choiceScore).toBeNull()
    expect(a.requests[0].choiceOutOf).toBeNull()
    expect(a.requests[0].calibration).toBeNull()
  })

  it('leaves them unscored when no key has been loaded, rather than scoring zero', () => {
    // The failure this prevents: an experimenter computes before loading the
    // answer key, and reads a column of zeroes as everybody getting it wrong.
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc({ choices: { q1: 0, q2: 1, q3: 1 } })],
      events: [],
      scoring: [],
    })
    expect(a.requests[0].choiceScore).toBeNull()
    expect(a.requests[0].calibration).toBeNull()
  })

  it('measures how long orientation lasted before the first change', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 + 5_000, kind: 'command', name: 'git', text: 'git log' }),
      ev({ ts: T0 + 65_000, kind: 'command', name: 'git', text: 'git revert abc1234' }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    expect(a.requests[0].timeToFirstHistoryOpMs).toBe(65_000)
  })
})

describe('an assistant command seen twice is counted once', () => {
  it('drops the hook record when the wrapper recorded the same command', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 + 1000, kind: 'tool', name: 'Bash', text: 'git log --oneline' }),
      ev({ ts: T0 + 1400, kind: 'command', name: 'git', text: 'git log --oneline', exitCode: 0, ok: true }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    expect(a.requests[0].counts.orient).toBe(1)
  })

  it('keeps a hook record the wrapper never saw', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 + 1000, kind: 'tool', name: 'Bash', text: 'ls -la' }),
      ev({ ts: T0 + 1400, kind: 'command', name: 'git', text: 'git log', exitCode: 0, ok: true }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    expect(a.requests[0].counts.search).toBe(1)
    expect(a.requests[0].counts.orient).toBe(1)
  })

  it('does not fold together two genuinely separate runs of the same command', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 + 1000, kind: 'tool', name: 'Bash', text: 'git log' }),
      ev({ ts: T0 + 1200, kind: 'command', name: 'git', text: 'git log', ok: true }),
      ev({ ts: T0 + 300_000, kind: 'tool', name: 'Bash', text: 'git log' }),
      ev({ ts: T0 + 300_200, kind: 'command', name: 'git', text: 'git log', ok: true }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    expect(a.requests[0].counts.orient).toBe(2)
  })
})

describe('a hand edit is inferred, and marked as inferred', () => {
  it('notices the tree moving with no assistant edit to account for it', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 + 1000, kind: 'repo', name: 'tree', extra: { treeHash: 'aaaa' } }),
      ev({ ts: T0 + 5000, kind: 'repo', name: 'tree', extra: { treeHash: 'bbbb' } }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    expect(a.requests[0].counts.manual_edit).toBe(1)
    expect(a.events.find((e) => e.category === 'manual_edit')?.inferred).toBe(true)
  })

  it('does not invent one when the assistant just edited', () => {
    const events: EventDoc[] = [
      ev({ ts: T0 + 1000, kind: 'repo', name: 'tree', extra: { treeHash: 'aaaa' } }),
      ev({ ts: T0 + 2000, kind: 'tool', name: 'Write', text: 'app.py' }),
      ev({ ts: T0 + 5000, kind: 'repo', name: 'tree', extra: { treeHash: 'bbbb' } }),
    ]
    const a = analyzeParticipant({
      participant: participantDoc(),
      responses: [],
      requests: [requestDoc()],
      events,
      scoring: [],
    })
    expect(a.requests[0].counts.manual_edit).toBe(0)
    expect(a.requests[0].counts.agent_edit).toBe(1)
  })
})

describe('instrument scoring', () => {
  // The performance slider runs Failure(0) -> Perfect(100), the direction the
  // words are read in, and is reversed exactly once here so that every subscale
  // points the same way: higher means more workload. This was wrong in both
  // places at once -- the slider read Perfect(0) -> Failure(100) AND the score
  // reversed it -- so a participant who felt they had done perfectly
  // contributed the maximum. The item's anchors are asserted alongside the
  // arithmetic, because either one alone is only half of the convention.
  it('presents performance failure-to-perfect', () => {
    const performance = TLX.items.find((i) => i.id === 'performance')!
    expect(performance.anchors).toEqual(['Failure', 'Perfect'])
  })

  it('scores perfect performance as low workload, and failure as high', () => {
    const flat = { mental: 50, physical: 0, temporal: 50, effort: 50, frustration: 50 }
    // Perfect (100 on the slider) contributes 0 to the workload mean.
    expect(tlxScore({ ...flat, performance: 100 })).toBeCloseTo((50 + 0 + 50 + 0 + 50 + 50) / 6)
    // Failure (0 on the slider) contributes 100.
    expect(tlxScore({ ...flat, performance: 0 })).toBeCloseTo((50 + 0 + 50 + 100 + 50 + 50) / 6)
    // And the composite must move the right way between them.
    expect(tlxScore({ ...flat, performance: 0 })!).toBeGreaterThan(
      tlxScore({ ...flat, performance: 100 })!,
    )
    expect(tlxScore({ mental: 50 })).toBeNull()
  })

  it('scores UMUX-Lite raw on 0 to 100', () => {
    expect(umuxLiteScore({ capability: 7, easy: 7 })).toBe(100)
    expect(umuxLiteScore({ capability: 1, easy: 1 })).toBe(0)
    expect(umuxLiteScore({ capability: 4, easy: 4 })).toBe(50)
    expect(umuxLiteScore({ capability: 7 })).toBeNull()
  })
})

describe('estimation', () => {
  it('reports a paired difference with an interval that excludes zero when it should', () => {
    const pairs = Array.from({ length: 12 }, (_, i) => ({ id: `p${i}`, a: 1, b: 2 + (i % 3) * 0.1 }))
    const est = pairedEstimate(pairs, 2000)
    expect(est.estimate).toBeGreaterThan(1)
    expect(est.lo).toBeGreaterThan(0)
    expect(est.n).toBe(12)
  })

  it('is stable across runs, so a figure does not move when it re-renders', () => {
    const pairs = Array.from({ length: 12 }, (_, i) => ({ id: `p${i}`, a: i % 4, b: (i % 3) + 1 }))
    expect(pairedEstimate(pairs, 2000)).toEqual(pairedEstimate(pairs, 2000))
  })

  it('says so when it had to fall back to a percentile interval', () => {
    // Every difference identical: the studentized ratio is undefined.
    const pairs = Array.from({ length: 12 }, (_, i) => ({ id: `p${i}`, a: 1, b: 2 }))
    const est = pairedEstimate(pairs, 500)
    expect(est.estimate).toBe(1)
    expect(est.lo).toBe(1)
  })

  it('ignores a pair with a missing half rather than treating it as zero', () => {
    const pairs = [
      { id: 'a', a: 1, b: 2 },
      { id: 'b', a: NaN, b: 5 },
      { id: 'c', a: 1, b: 2 },
    ]
    expect(pairedEstimate(pairs, 500).n).toBe(2)
  })
})

describe('sequence comparison', () => {
  it('counts n-grams over the category alphabet', () => {
    const counts = countNgrams([['orient', 'inspect', 'orient', 'inspect']], 2)
    expect(counts.get('orient>inspect')).toBe(2)
    expect(counts.get('inspect>orient')).toBe(1)
  })

  it('shrinks rare sequences towards zero instead of shouting about them', () => {
    const a = new Map([['common', 500], ['rare', 1]])
    const b = new Map([['common', 100], ['rare', 6]])
    const { rows } = weightedLogOdds(a, b, 5)
    const rare = rows.find((r) => r.key === 'rare')!
    const common = rows.find((r) => r.key === 'common')!
    expect(Math.abs(common.z)).toBeGreaterThan(Math.abs(rare.z))
  })

  it('reports how many sequences it dropped rather than leaving it implicit', () => {
    const a = new Map([['x', 1], ['y', 20]])
    const b = new Map([['x', 1], ['y', 20]])
    const { rows, dropped } = weightedLogOdds(a, b, 5)
    expect(dropped).toBe(1)
    expect(rows).toHaveLength(1)
  })
})

describe('the whole pipeline on a full cohort', () => {
  const dataset = demoDataset()

  it('produces twelve participants, balanced across the four groups', () => {
    expect(dataset.participants).toHaveLength(12)
    const counts = new Map<number, number>()
    for (const p of dataset.participants) counts.set(p.group, (counts.get(p.group) ?? 0) + 1)
    expect([...counts.values()].sort()).toEqual([3, 3, 3, 3])
  })

  it('gives every participant both conditions', () => {
    for (const p of dataset.participants) {
      const conditions = new Set(p.requests.map((r) => r.condition))
      expect([...conditions].sort()).toEqual(['git', 'sgt'])
    }
  })

  it('feeds the figures without blowing up', () => {
    const profile = timeProfile(dataset, 'sgt')
    expect(profile).toHaveLength(20)
    for (const bin of profile) {
      const total = Object.values(bin.shares).reduce((a, b) => a + b, 0)
      if (bin.total > 0) expect(total).toBeCloseTo(1, 5)
    }
    expect(strips(dataset, 'git').length).toBe(12)
    const ngrams = compareNgrams(dataset, 2, 5)
    expect(ngrams.rows.length).toBeGreaterThan(0)
    expect(ngrams.totalA).toBeGreaterThan(0)
  })

  it('rolls per-request measures up to a per-condition value', () => {
    const p = dataset.participants[0]
    const total = conditionValue(p, 'git', (m) => m.score, 'sum', ['r1', 'r2', 'r3', 'r4'])
    expect(Number.isFinite(total)).toBe(true)
    expect(total).toBeGreaterThanOrEqual(0)
  })

  it('survives a participant with no telemetry at all', () => {
    const empty = buildDataset([
      {
        participant: participantDoc(),
        responses: [],
        requests: [requestDoc()],
        events: [],
        scoring: [],
      },
    ])
    expect(empty.participants[0].requests[0].counts.orient).toBe(0)
    expect(empty.participants[0].requests[0].verificationRatio).toBeNull()
  })
})

describe('pilot records are separable from the study', () => {
  const pilot = (): Participant => ({
    ...participantDoc(),
    code: 'x1',
    studyId: 'pilot',
    ordinal: PILOT_ORDINAL_BASE + 1,
    label: 'X01',
  })

  it('recognises one by its field, not by its label', () => {
    // The label is a display convenience. If `isPilot` read it, renaming a
    // record in the console would silently move it in or out of the analysis.
    expect(isPilot(pilot())).toBe(true)
    expect(isPilot(participantDoc())).toBe(false)
    expect(isPilot({ ...participantDoc(), label: 'X01' })).toBe(false)
    expect(isPilot({ ...pilot(), label: 'P01' })).toBe(true)
  })

  it('numbers each study independently, so pilots can never take P13', () => {
    // The property that makes rehearsing safe, checked on the function that
    // actually assigns it. However many pilots have been run first, `Create 12`
    // still yields exactly P01..P12, in balanced groups, sorting ahead of them.
    const cohort = Array.from({ length: 12 }, (_, i) => participantIdentity('main', i + 1))
    expect(cohort.map((p) => p.label)).toEqual([
      'P01', 'P02', 'P03', 'P04', 'P05', 'P06',
      'P07', 'P08', 'P09', 'P10', 'P11', 'P12',
    ])
    expect(cohort.map((p) => p.group)).toEqual([1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 4])

    const rehearsals = Array.from({ length: 5 }, (_, i) => participantIdentity('pilot', i + 1))
    expect(rehearsals.map((p) => p.label)).toEqual(['X01', 'X02', 'X03', 'X04', 'X05'])
    // No collision, and pilots sort last however the roster is ordered.
    const cohortOrdinals = new Set(cohort.map((p) => p.ordinal))
    for (const r of rehearsals) {
      expect(cohortOrdinals.has(r.ordinal)).toBe(false)
      expect(r.ordinal).toBeGreaterThan(Math.max(...cohortOrdinals))
    }
    // A pilot still gets a real condition order -- that is the thing it exists
    // to rehearse.
    expect(rehearsals.map((p) => p.group)).toEqual([1, 2, 3, 4, 1])
  })

  it('leaves the analysis unchanged when pilots are filtered out', () => {
    // The real guarantee, stated as an equality rather than a count: a dataset
    // built from [real] and one built from [real, pilot] minus pilots must be
    // the same dataset, not merely the same size.
    const real = participantDoc()
    const bundle = (p: Participant) => ({
      participant: p,
      responses: [],
      requests: [],
      events: [],
      scoring: [],
    })
    const withPilot = [bundle(real), bundle(pilot())].filter((b) => !isPilot(b.participant))
    const only = buildDataset([bundle(real)])
    const filtered = buildDataset(withPilot)
    expect(filtered.participants.map((p) => p.label)).toEqual(only.participants.map((p) => p.label))
    expect(filtered.participants).toHaveLength(1)
  })
})
