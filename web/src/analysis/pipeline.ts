// Raw telemetry in, analyzable structures out.
//
// Nothing here is authoritative. The raw event stream in Firestore is the
// record; this file is a pure function over it, so the whole analysis can be
// re-derived after the last session by changing the code and pressing
// recompute. Any preprocessing done on the participant's machine, where it
// could not be revisited, would be a decision locked in before we knew what the
// data looked like.

import type {
  Condition,
  EventDoc,
  Half,
  Participant,
  Project,
  RequestDoc,
  RequestId,
  ResponseDoc,
  ScoringDoc,
} from '../lib/types'
import {
  classify,
  isMutating,
  promptSpecificity,
  type Category,
  type ClassifyContext,
  type Specificity,
} from '../study/taxonomy'
import { gitExpertise, tlxScore, tlxSubscales, umuxLiteScore } from '../lib/stats'
import { HLAC } from '../study/instruments'

export interface CategorizedEvent {
  id: string
  ts: number
  category: Category
  kind: string
  name: string | null
  text: string | null
  ok: boolean | null
  /** Position within its request, 0 at open and 1 at close. */
  tRel: number
  requestId: RequestId | null
  /**
   * Which half and condition this event belongs to.
   *
   * Both are carried explicitly rather than looked up from `requestId`,
   * because a request id is not unique within a participant: everybody does r1
   * twice, once per condition. Resolving the condition by searching for the
   * first request with a matching id silently attributed half the events to
   * the wrong condition.
   */
  half: Half | null
  condition: Condition | null
  inferred?: boolean
  /** Where the action happened: the terminal, the editor, or the assistant. */
  surface: Surface
}

/**
 * `terminal` is the default for anything recorded before the editor was part
 * of the study, and for any source that does not say.
 */
export type Surface = 'terminal' | 'editor' | 'agent'

function surfaceOf(ev: EventDoc): Surface {
  const raw = ev.extra?.surface
  if (raw === 'editor' || raw === 'agent' || raw === 'terminal') return raw
  if (ev.kind === 'tool' || ev.kind === 'prompt' || ev.extra?.agent === true) return 'agent'
  return 'terminal'
}

export interface RequestMetrics {
  requestId: RequestId
  half: Half
  condition: Condition
  project: Project
  activeMs: number
  hitCap: boolean
  selfReport: RequestDoc['selfReport']
  confidence: number | null
  counts: Record<Category, number>
  /** How many of those actions happened in each place. */
  surfaces: Record<Surface, number>
  sequence: Category[]
  prompts: number
  meanPromptChars: number
  meanSpecificity: number | null
  specificityCounts: Record<Specificity, number>
  verificationRatio: number | null
  timeToFirstHistoryOpMs: number | null
  wrongTurns: number
  /** Facilitator scoring, folded in when present. */
  score: number | null
  outOf: number | null
  collateralDamage: number | null
  /** Closed questions answered correctly, where the request asks any (r1). */
  choiceScore: number | null
  choiceOutOf: number | null
  /**
   * Stated confidence minus proportion correct, both on 0-1. Positive is
   * overconfidence: surer than they were right. Null unless both halves are
   * there, because a missing confidence is not a confident zero.
   */
  calibration: number | null
}

/**
 * Which option is the right one for a request's closed questions:
 * request id -> project -> question id -> index into the option list in
 * study/tasks.ts.
 *
 * The indexes live in docs/study/answer-key.json rather than beside the
 * questions, because tasks.ts is compiled into the bundle the participant
 * downloads and anything in it is readable from devtools.
 */
export type ChoiceKey = Record<string, Partial<Record<Project, Record<string, number>>>>

/**
 * Read the closed-question key out of the answer-key document.
 *
 * Takes `unknown` and picks it apart by hand because that document is a file a
 * person loads into the console by hand. A key that is missing, older than the
 * questions, or half-edited has to leave the questions unscored rather than
 * mark every one of them wrong.
 */
export function choiceKeyFrom(truth: unknown): ChoiceKey {
  const keys = (truth as { requestKeys?: Record<string, unknown> } | null)?.requestKeys
  if (!keys) return {}
  const out: ChoiceKey = {}
  for (const [requestId, entry] of Object.entries(keys)) {
    const choices = (entry as { choices?: ChoiceKey[string] } | null)?.choices
    if (choices) out[requestId] = choices
  }
  return out
}

export interface HalfSummary {
  half: Half
  condition: Condition
  project: Project
  tlx: number | null
  /** The six TLX subscales, all in workload direction (higher = more load). */
  tlxSubscales: Record<string, number> | null
  umux: number | null
  hlac: Record<string, number>
  /** Manipulation checks that ride along on the HLAC block: task realism, and whether the cap bound. */
  checks: Record<string, number>
}

export interface ParticipantAnalysis {
  pid: string
  label: string
  ordinal: number
  group: number
  gitExpertise: number | null
  events: CategorizedEvent[]
  requests: RequestMetrics[]
  halves: HalfSummary[]
  /** Which condition ran first. Order is a factor in every model. */
  firstCondition: Condition | null
  complete: boolean
}

export interface Dataset {
  participants: ParticipantAnalysis[]
  builtAt: number
  /** Events that landed outside any request window, so the count is visible. */
  unassignedEvents: number
}

export interface RawParticipantData {
  participant: Participant
  responses: Array<ResponseDoc & { id: string }>
  requests: Array<RequestDoc & { id: string }>
  events: EventDoc[]
  scoring: Array<Record<string, unknown> & { id: string }>
}

const ZERO_COUNTS = (): Record<Category, number> => ({
  orient: 0,
  inspect: 0,
  search: 0,
  prompt: 0,
  agent_edit: 0,
  manual_edit: 0,
  history_op: 0,
  verify: 0,
  recover: 0,
})

interface Window {
  requestId: RequestId
  half: Half
  condition: Condition
  project: Project
  from: number
  to: number
}

/**
 * Request windows come from the web app's own clocks, not from the machine.
 * The machine has no idea what a request is, and asking it to guess from
 * timestamps it never saw would put a second source of truth in play.
 */
function windowsFor(requests: RequestDoc[]): Window[] {
  return requests
    .filter((r) => r.openedAt)
    .map((r) => ({
      requestId: r.requestId,
      half: r.half,
      condition: r.condition,
      project: r.project,
      from: r.openedAt!,
      to: r.submittedAt ?? r.openedAt! + Math.max(r.capMs, 60 * 60_000),
    }))
    .sort((a, b) => a.from - b.from)
}

function windowAt(windows: Window[], ts: number): Window | null {
  // Cards can share a window (r2 and r3), so the first match wins and the
  // request-level split for those two comes from the facilitator's scoring, not
  // from the clock.
  for (const w of windows) if (ts >= w.from && ts <= w.to) return w
  return null
}

const DEDUPE_WINDOW_MS = 15_000

function normalizeCommand(text: string | null): string {
  return (text ?? '').trim().replace(/\s+/g, ' ')
}

/**
 * A command the assistant runs is seen twice: once by the PreToolUse hook, and
 * once by the PATH shim, because the assistant inherits the session shell's
 * PATH. Both records are worth having -- the hook knows it was the assistant's
 * idea, the shim knows the exit code and how long it took -- but counting the
 * same action twice would inflate exactly the categories the process figure is
 * about.
 *
 * Where the two can be matched on command text within a few seconds, the shim
 * record wins and the hook record is dropped. Unmatched records on either side
 * survive: the assistant runs plenty of commands we do not shim, and the
 * participant types plenty the assistant never saw.
 */
function dropDoubleCountedBashEvents(sorted: EventDoc[]): EventDoc[] {
  const shimByCommand = new Map<string, number[]>()
  for (const ev of sorted) {
    if (ev.kind !== 'command') continue
    const key = normalizeCommand(ev.text)
    if (!key) continue
    const list = shimByCommand.get(key) ?? []
    list.push(ev.ts)
    shimByCommand.set(key, list)
  }

  const consumed = new Set<string>()
  return sorted.filter((ev) => {
    const isBashHook = ev.kind === 'tool' && (ev.name === 'Bash' || ev.name === 'BashOutput')
    if (!isBashHook) return true
    const key = normalizeCommand(ev.text)
    const times = shimByCommand.get(key)
    if (!times) return true
    const hit = times.find(
      (t) => Math.abs(t - ev.ts) <= DEDUPE_WINDOW_MS && !consumed.has(`${key}@${t}`),
    )
    if (hit == null) return true
    consumed.add(`${key}@${hit}`)
    return false
  })
}

const HOVER_WINDOW_MS = 2_000

/**
 * Collapse a run of identical reads the editor made in quick succession.
 *
 * Both editor extensions preview on hover: moving across a feature in the
 * workbench rail emits `sgt advanced preview revert <feature>`, and moving
 * across a blame annotation emits a `git log`, several times a second. One
 * pass over a list is one look, not nine. Applied to the editor only, and only
 * to identical text, because the same command typed twice in a terminal is
 * somebody deciding to run it twice.
 */
function collapseHoverRepeats(sorted: EventDoc[]): EventDoc[] {
  const lastSeen = new Map<string, number>()
  return sorted.filter((ev) => {
    if (ev.kind !== 'command' || ev.extra?.surface !== 'editor') return true
    const key = normalizeCommand(ev.text)
    if (!key) return true
    const previous = lastSeen.get(key)
    lastSeen.set(key, ev.ts)
    return previous == null || ev.ts - previous > HOVER_WINDOW_MS
  })
}

function analyzeEvents(
  events: EventDoc[],
  windows: Window[],
): { categorized: CategorizedEvent[]; unassigned: number } {
  const sorted = collapseHoverRepeats(
    dropDoubleCountedBashEvents([...events].sort((a, b) => a.ts - b.ts)),
  )
  const out: CategorizedEvent[] = []
  let unassigned = 0

  const ctx: ClassifyContext = { dirtySinceCheck: false, lastOpFailed: false }
  let lastTreeHash: string | null = null
  let editSinceTreeChange = false

  for (const ev of sorted) {
    // An editor keeps its own views in step by running git on a timer. Those
    // are the editor's moves, not the participant's, and there are hundreds of
    // them in an hour: counting them would swamp every sequence measure here.
    // They stay in the raw stream, which is what "the editor was open" is read
    // from.
    if (ev.extra?.auto === true) continue

    const w = windowAt(windows, ev.ts)

    // A repo snapshot whose tree moved with no assistant edit to account for it
    // is the participant having edited by hand. Inferred, and flagged as such
    // wherever it is shown, because it is the one category we never observe
    // directly.
    if (ev.kind === 'repo') {
      const hash = (ev.extra?.treeHash as string) ?? null
      if (hash && lastTreeHash && hash !== lastTreeHash && !editSinceTreeChange && w) {
        out.push({
          id: `${ev.id}-inferred-edit`,
          ts: ev.ts,
          category: 'manual_edit',
          kind: 'inferred',
          name: 'manual edit',
          text: null,
          ok: true,
          tRel: (ev.ts - w.from) / Math.max(1, w.to - w.from),
          requestId: w.requestId,
          half: w.half,
          condition: w.condition,
          inferred: true,
          surface: 'terminal',
        })
        ctx.dirtySinceCheck = true
      }
      if (hash) {
        lastTreeHash = hash
        editSinceTreeChange = false
      }
      continue
    }

    const category = classify(ev, ctx)
    if (!category) continue
    if (!w) {
      unassigned++
      continue
    }

    if (category === 'agent_edit' || category === 'manual_edit') editSinceTreeChange = true
    if (isMutating(category)) ctx.dirtySinceCheck = true
    if (category === 'verify') ctx.dirtySinceCheck = false
    if (category === 'history_op') ctx.lastOpFailed = ev.ok === false || (ev.exitCode ?? 0) !== 0

    out.push({
      id: ev.id,
      ts: ev.ts,
      category,
      kind: ev.kind,
      name: ev.name,
      text: ev.text,
      ok: ev.ok ?? null,
      tRel: Math.max(0, Math.min(1, (ev.ts - w.from) / Math.max(1, w.to - w.from))),
      requestId: w.requestId,
      half: w.half,
      condition: w.condition,
      surface: surfaceOf(ev),
    })
  }

  return { categorized: out, unassigned }
}

const WRONG_TURN_WINDOW_MS = 120_000

function metricsFor(
  req: RequestDoc,
  events: CategorizedEvent[],
  scoring: ScoringDoc | undefined,
  choiceKey: ChoiceKey,
): RequestMetrics {
  const mine = events.filter((e) => e.requestId === req.requestId && e.half === req.half)
  const counts = ZERO_COUNTS()
  for (const e of mine) counts[e.category]++

  // Where the work happened. Both conditions now offer the same three places to
  // work -- a terminal, an editor view, and the assistant -- so "did they read
  // history in the editor or in the shell" is a question about the person, not
  // about which condition they were in.
  const surfaces: Record<Surface, number> = { terminal: 0, editor: 0, agent: 0 }
  for (const e of mine) surfaces[e.surface]++

  const prompts = mine.filter((e) => e.category === 'prompt')
  const promptChars = prompts.map((p) => (p.text ?? '').length)
  const specificityCounts: Record<Specificity, number> = { 0: 0, 1: 0, 2: 0, 3: 0 }
  const specs = prompts.map((p) => promptSpecificity(p.text ?? ''))
  for (const s of specs) specificityCounts[s]++

  const mutations = counts.agent_edit + counts.manual_edit + counts.history_op
  const firstOp = mine.find((e) => e.category === 'history_op')

  // The closed questions are scored here rather than by a person, so r1 has no
  // rubric. Both halves have to be present: a request answered before the key
  // was loaded is unscored, which is a different thing from all three wrong.
  //
  // `choices` is SEEDED as `{}` when a request is opened, and `{}` is truthy, so
  // a participant who ran out of time having picked nothing scored 0 of 3 --
  // indistinguishable from three wrong answers, and pulling the condition mean
  // toward zero. Worse with a confidence rating attached: nothing answered plus
  // a moved slider recorded as maximum overconfidence.
  const wanted = choiceKey[req.requestId]?.[req.project] ?? null
  const answered = req.choices && Object.keys(req.choices).length > 0 ? req.choices : null
  const questions = wanted ? Object.keys(wanted) : []
  const choiceOutOf = wanted && answered ? questions.length : null
  const choiceScore =
    wanted && answered ? questions.filter((q) => answered[q] === wanted[q]).length : null

  let wrongTurns = 0
  for (let i = 0; i < mine.length; i++) {
    if (mine[i].category !== 'history_op') continue
    for (let j = i + 1; j < mine.length; j++) {
      if (mine[j].ts - mine[i].ts > WRONG_TURN_WINDOW_MS) break
      if (mine[j].category === 'recover') {
        wrongTurns++
        break
      }
    }
  }

  return {
    requestId: req.requestId,
    half: req.half,
    condition: req.condition,
    project: req.project,
    activeMs: req.activeMs || req.elapsedMs || 0,
    hitCap: req.hitCap,
    selfReport: req.selfReport,
    confidence: req.confidence,
    counts,
    surfaces,
    sequence: mine.map((e) => e.category),
    prompts: prompts.length,
    meanPromptChars: promptChars.length
      ? promptChars.reduce((a, b) => a + b, 0) / promptChars.length
      : 0,
    meanSpecificity: specs.length
      ? specs.reduce<number>((a, b) => a + b, 0) / specs.length
      : null,
    specificityCounts,
    verificationRatio: mutations > 0 ? counts.verify / mutations : null,
    timeToFirstHistoryOpMs:
      firstOp && req.openedAt ? firstOp.ts - req.openedAt : null,
    wrongTurns,
    score: scoring?.score ?? null,
    outOf: scoring?.outOf ?? null,
    collateralDamage: scoring?.collateralDamage ?? null,
    choiceScore,
    choiceOutOf,
    calibration:
      choiceScore != null && choiceOutOf && req.confidence != null
        ? req.confidence / 100 - choiceScore / choiceOutOf
        : null,
  }
}

function halfSummary(
  half: Half,
  condition: Condition,
  project: Project,
  responses: Array<ResponseDoc & { id: string }>,
): HalfSummary {
  const find = (instrument: string) =>
    responses.find((r) => r.id === `${instrument}-h${half}`)?.values ?? null

  // The HLAC block carries two manipulation checks that are not part of what it
  // measures, so they are split out here rather than averaged into it. They also
  // need coercing: `timePressure` is a select, so its five fully-labelled
  // options arrive as strings and a `typeof v === 'number'` filter dropped the
  // item entirely -- collected every half, surfaced nowhere.
  const checkIds = new Set(HLAC.items.filter((i) => i.check).map((i) => i.id))
  const hlacVals = find('hlac')
  const hlac: Record<string, number> = {}
  const checks: Record<string, number> = {}
  if (hlacVals) {
    for (const [k, v] of Object.entries(hlacVals)) {
      const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN
      if (!Number.isFinite(n)) continue
      if (checkIds.has(k)) checks[k] = n
      else if (typeof v === 'number') hlac[k] = v
    }
  }

  return {
    half,
    condition,
    project,
    tlx: find('tlx') ? tlxScore(find('tlx')!) : null,
    // Carried beside the aggregate so a per-subscale figure never has to reach
    // into the stored responses, where Performance still runs the other way.
    tlxSubscales: find('tlx') ? tlxSubscales(find('tlx')!) : null,
    umux: find('umux') ? umuxLiteScore(find('umux')!) : null,
    hlac,
    checks,
  }
}

export function analyzeParticipant(
  raw: RawParticipantData,
  choiceKey: ChoiceKey = {},
): ParticipantAnalysis {
  const { participant, responses, requests, events, scoring } = raw
  const windows = windowsFor(requests)
  const { categorized } = analyzeEvents(events, windows)

  const metrics = requests
    .filter((r) => r.openedAt)
    .map((r) =>
      metricsFor(
        r,
        categorized.filter((e) => {
          const w = windows.find((x) => x.requestId === r.requestId && x.half === r.half)
          return w ? e.ts >= w.from && e.ts <= w.to : false
        }),
        scoring.find((s) => s.id === `${r.requestId}-h${r.half}`) as unknown as ScoringDoc | undefined,
        choiceKey,
      ),
    )

  const halves = participant.blocks.map((b) =>
    halfSummary(b.half, b.condition, b.project, responses),
  )

  const background = responses.find((r) => r.id === 'background')?.values ?? null

  return {
    pid: participant.code,
    label: participant.label,
    ordinal: participant.ordinal,
    group: participant.group,
    gitExpertise: background ? gitExpertise(background) : null,
    events: categorized,
    requests: metrics,
    halves,
    firstCondition: participant.blocks.find((b) => b.half === 1)?.condition ?? null,
    complete: participant.status === 'completed',
  }
}

export function buildDataset(raws: RawParticipantData[], choiceKey: ChoiceKey = {}): Dataset {
  let unassigned = 0
  const participants = raws.map((r) => {
    const windows = windowsFor(r.requests)
    unassigned += analyzeEvents(r.events, windows).unassigned
    return analyzeParticipant(r, choiceKey)
  })
  participants.sort((a, b) => a.ordinal - b.ordinal)
  return { participants, builtAt: Date.now(), unassignedEvents: unassigned }
}

// ---------------------------------------------------------------------------
// Views the charts ask for
// ---------------------------------------------------------------------------

/** All request metrics for one participant under one condition. */
export function byCondition(p: ParticipantAnalysis, condition: Condition): RequestMetrics[] {
  return p.requests.filter((r) => r.condition === condition)
}

export function halfOf(p: ParticipantAnalysis, condition: Condition): HalfSummary | undefined {
  return p.halves.find((h) => h.condition === condition)
}

/** Sum or mean of one numeric metric over a condition, for a paired plot. */
export function conditionValue(
  p: ParticipantAnalysis,
  condition: Condition,
  pick: (m: RequestMetrics) => number | null,
  agg: 'sum' | 'mean' = 'sum',
  onlyRequests?: RequestId[],
): number {
  let rows = byCondition(p, condition)
  if (onlyRequests) rows = rows.filter((r) => onlyRequests.includes(r.requestId))
  const vals = rows.map(pick).filter((v): v is number => v != null && Number.isFinite(v))
  if (vals.length === 0) return NaN
  const total = vals.reduce((a, b) => a + b, 0)
  return agg === 'sum' ? total : total / vals.length
}
