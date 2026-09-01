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
  GroundTruth,
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
import { tlxScore, tlxSubscales, umuxLiteScore } from '../lib/stats'
import { AFTER_HALF, HLAC } from '../study/instruments'

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
  /** The prediction-against-outcome pair. Protocol v1 stored both answers on
   * the reverting card; protocol v2 predicts on s2 and reports on s3, so this
   * lives on the s3 metric and is null everywhere else. */
  reach: ReachMetrics | null
  /**
   * F1 of a stage quiz's behaviour checklist against the measured key, where
   * the stage has one (s1: what the newest piece of work reaches; s2: what the
   * found work reaches; s3: what the removal changed). Null when the stage
   * has no checklist, the key has no entry, or the quiz was never submitted.
   *
   * The three are not equally hard, and the analysis says so rather than
   * averaging them: s1's measured set is two of the eleven options, s2's and
   * s3's are eight or nine, so ticking everything scores about 0.31 on s1 and
   * about 0.86 on s2 and s3.
   */
  quizPicksF1: number | null
  /** Whether a scored multiple-choice quiz item matched the key. No stage asks
   * one today; the field stays because the key and the validator still carry
   * them. Null when unasked, unanswered, or unkeyed. */
  quizChoiceCorrect: boolean | null
  /**
   * Whether the locate step named the right piece of work, where the request has
   * one. PROVISIONAL: a normalised containment test against the strings the key
   * accepts, so the dashboard has something live. The authority is the
   * experimenter reading the answer, which is why `locateAnswer` is carried
   * beside it rather than thrown away once matched.
   */
  locateCorrect: boolean | null
  locateAnswer: string | null
  /**
   * Stated confidence minus proportion correct, both on 0-1. Positive is
   * overconfidence: surer than they were right. Taken from the blind stage of
   * the reach prediction, which is the one place in the block where somebody
   * commits to an answer and rates it before finding out. Null unless both
   * halves are there, because a missing confidence is not a confident zero.
   */
  calibration: number | null
}

/**
 * Every string that names the work a locate step is looking for:
 * request id -> project -> accepted strings.
 *
 * A list rather than one string because the two arms name work in different
 * vocabularies -- a sha under git, a feature label or id under sgt -- and a
 * single correct answer would mark one arm wrong for being right in its own
 * terms. It lives in docs/study/answer-key.json rather than beside the task,
 * because tasks.ts is compiled into the bundle the participant downloads and
 * anything in it is readable from devtools.
 */
export type LocateKey = Record<string, Partial<Record<Project, string[]>>>

/**
 * Read the closed-question key out of the answer-key document.
 *
 * Takes `unknown` and picks it apart by hand because that document is a file a
 * person loads into the console by hand. A key that is missing, older than the
 * questions, or half-edited has to leave the questions unscored rather than
 * mark every one of them wrong.
 */
/**
 * Which behaviours a stage's target actually reaches: request id -> behaviour
 * ids. Generated by scripts/study/harvest/write_answer_key.py, which measures it
 * on a built bundle -- remove the target on a copy, run the app's own check,
 * re-render every page, map what moved -- and read out of the same document as
 * the closed-question key.
 */
/**
 * Per request, and then per project: which parts of the dashboard the work actually reaches.
 *
 * It used to be one list per request, shared by both projects. That only works if the two
 * testbeds are isomorphic all the way down to which page each change touches, and they are not:
 * they are harvested from real work, so the same job lands on the by-month page in one and the
 * hour-of-day page in the other. A single shared list would have scored one arm against the other
 * arm's answer, which lowers that arm's F1 for being right about its own project.
 *
 * A plain array is still accepted, and read as "the same for both", so an older key still loads.
 */
export type ReachKey = Record<string, string[] | Partial<Record<Project, string[]>>>

export function reachFor(key: ReachKey, requestId: string, project: Project): string[] | undefined {
  const entry = key[requestId]
  if (!entry) return undefined
  return Array.isArray(entry) ? entry : entry[project]
}

export interface AnswerKeys {
  locate: LocateKey
  reach: ReachKey
  /** Correct option values for scored multiple-choice quiz items:
   * request id -> item id -> option value. Same in both projects by
   * construction (the build gate verifies it), so it is not per project.
   * Optional so a key literal or an older uploaded key without one still
   * reads; a missing map leaves choice items unscored, never wrong. */
  choices?: Record<string, Record<string, string>>
}

/**
 * Both keys together, because they come from one document and are needed by one
 * call. They used to be read separately, which meant a caller could pass the
 * closed-question key and leave the reach key at its default -- and an empty reach
 * key does not fail, it scores every prediction trial as unscored, which looks
 * exactly like a study that did not run them.
 */
export function keysFrom(truth: GroundTruth | null): AnswerKeys {
  const choices: Record<string, Record<string, string>> = {}
  const keys: AnswerKeys = { locate: {}, reach: {}, choices }
  for (const [requestId, entry] of Object.entries(truth?.requestKeys ?? {})) {
    if (entry.locate) keys.locate[requestId] = entry.locate
    if (entry.reach) keys.reach[requestId] = entry.reach
    if (entry.choices) choices[requestId] = entry.choices
  }
  return keys
}

/**
 * Whether a typed answer names the work the key is looking for.
 *
 * Deliberately lenient, because it is provisional and a person checks it after:
 * case and surrounding punctuation are ignored, and a sha is matched by prefix
 * from seven characters, which is what `git log --oneline` prints and what a
 * participant copies. `f-8068d4e` typed for `8068d4e...` therefore matches, and
 * so does the full forty. Under seven characters nothing matches, because a
 * three-character prefix would match half the repository.
 */
export function locateMatches(typed: string, accepted: string): boolean {
  const squash = (t: string) => t.toLowerCase().replace(/[^a-z0-9]/g, '')
  const a = squash(typed)
  const b = squash(accepted)
  if (!a || !b) return false
  if (a.includes(b) || b.includes(a)) return true
  if (/^[0-9a-f]{7,}$/.test(b)) {
    // Hex runs are pulled from the text WITH its punctuation intact, not from
    // the squashed copy. sgt prefixes a feature id with `f-`, and squashing
    // first glued that `f` onto the front of the sha, so `f-25e91a9` -- what the
    // sgt arm actually copies -- failed to match `25e91a9a1d22...` while the
    // bare sha matched fine. The punctuation is the token boundary.
    const shas = typed.toLowerCase().match(/[0-9a-f]{7,}/g) ?? []
    return shas.some((sha) => b.startsWith(sha) || sha.startsWith(b))
  }
  return false
}

/**
 * Overconfidence on the one answer in the block that is committed and rated
 * before the participant finds out: the blind stage of the reach prediction.
 * Positive means surer than they were right.
 */
function calibrationOf(reach: ReachMetrics | null): number | null {
  if (!reach || reach.blindConfidence == null) return null
  return confidenceFraction(reach.blindConfidence, reach.blindConfidenceScale) - reach.blind
}

/**
 * A stated confidence as a proportion, 0 to 1, whichever scale it was given on.
 *
 * Protocol v2.1 asks for it on seven points; everything collected before that
 * used a 0-100 slider. The scale is read from the document rather than guessed
 * from the value, because 5 is a legal answer on both and means opposite things.
 */
export function confidenceFraction(value: number, scale: 7 | undefined): number {
  return scale === 7 ? (value - 1) / 6 : value / 100
}

/**
 * How close a set of ticks is to the right set, as F1 over the two sets.
 *
 * F1 rather than raw agreement because the answer is sparse: with one behaviour of
 * twelve in the key, ticking nothing scores 11/12 on agreement and looks like near
 * perfect knowledge. F1 gives it zero, which is what it is worth.
 */
export function setF1(picked: string[], wanted: string[]): number {
  if (wanted.length === 0) return 0
  const want = new Set(wanted)
  const hit = new Set(picked).size === 0 ? 0 : [...new Set(picked)].filter((p) => want.has(p)).length
  if (hit === 0) return 0
  const precision = hit / new Set(picked).size
  const recall = hit / want.size
  return (2 * precision * recall) / (precision + recall)
}

export interface ReachMetrics {
  blind: number
  checked: number
  /** What the representation bought them. The measure the trials exist for. */
  gain: number
  blindConfidence: number | null
  checkedConfidence: number | null
  /** Which scale both confidences above are on. See `confidenceFraction`. */
  blindConfidenceScale?: 7
  /** Seconds inside the blind stage. Short means read off; long means reasoned out. */
  blindActiveMs: number
  blindPicked: number
  checkedPicked: number
  outOf: number
}

/**
 * Score one reach trial, or nothing if it was not both asked and answered.
 *
 * Unlike the closed questions, an empty pick set is scored rather than treated as
 * unanswered. `choices` is seeded as `{}` the moment a request opens, so an empty
 * one there means "never touched"; a stage document is only written when a stage is
 * submitted, by the participant or by the deadline, so an empty one means the
 * participant looked and ticked nothing. That is a real, wrong answer and it counts.
 */
export function reachMetricsFor(req: RequestDoc, key: ReachKey): ReachMetrics | null {
  const wanted = reachFor(key, req.requestId, req.project)
  const blind = req.stages?.blind
  const checked = req.stages?.checked
  if (!wanted || !blind || !checked) return null
  const blindF1 = setF1(blind.picks, wanted)
  const checkedF1 = setF1(checked.picks, wanted)
  return {
    blind: blindF1,
    checked: checkedF1,
    gain: checkedF1 - blindF1,
    blindConfidence: blind.confidence,
    checkedConfidence: checked.confidence,
    // The retired two-stage reach trial. Its sliders were always 0-100.
    blindConfidenceScale: undefined,
    blindActiveMs: blind.activeMs,
    blindPicked: blind.picks.length,
    checkedPicked: checked.picks.length,
    outOf: wanted.length,
  }
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
  /** Background self-rating, 1 to 5. The covariate the models control for. */
  gitConfidence: number | null
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
  keys: AnswerKeys,
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

  // The locate step, scored provisionally. Both halves have to be present: an
  // answer given before the key was loaded is unscored, which is a different
  // thing from a wrong answer, and an empty box is unanswered rather than wrong
  // -- a participant who ran out of clock having typed nothing is not somebody
  // who named the wrong work.
  const accepted = keys.locate[req.requestId]?.[req.project] ?? null
  const locateAnswer = (req.locate ?? '').trim() || null
  const locateCorrect =
    accepted && locateAnswer ? accepted.some((a) => locateMatches(locateAnswer, a)) : null

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

  const reach = reachMetricsFor(req, keys.reach)

  // The stage quiz, where the request has one. An absent `quiz` field means a
  // v1 document or an unfinished stage, and stays unscored; a present
  // behaviours array is scored even when empty, because ticking nothing is a
  // committed answer, not a missing one.
  const wanted = reachFor(keys.reach, req.requestId, req.project)
  const quizPicks = Array.isArray(req.quiz?.['behaviours'])
    ? (req.quiz!['behaviours'] as string[])
    : null
  const quizPicksF1 = wanted && quizPicks ? setF1(quizPicks, wanted) : null
  const choiceKey = keys.choices?.[req.requestId]
  let quizChoiceCorrect: boolean | null = null
  if (choiceKey && req.quiz) {
    for (const [itemId, want] of Object.entries(choiceKey)) {
      const got = req.quiz[itemId]
      if (typeof got === 'string') quizChoiceCorrect = got === want
    }
  }

  return {
    reach,
    quizPicksF1,
    quizChoiceCorrect,
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
    locateCorrect,
    locateAnswer,
    calibration: calibrationOf(reach),
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
  // measures, so they are split out here rather than averaged into it.
  //
  // This reads the CURRENT instrument to classify a STORED response, which is
  // the one place this file is not a pure function of the raw stream. It is safe
  // only because both check ids are new in hlac-v3, so no earlier response can
  // carry one. If a later version marks an existing item `check: true`, this
  // would retroactively move it out of `hlac` for people who answered it as a
  // construct item -- read `r.version` here rather than widening the set. They also
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

  // Protocol v2's per-half battery: the two UMUX-Lite items and the six TLX
  // subscales, all under their published ids, in one response document.
  //
  // Earlier designs put TLX in its own `tlx` document and hung two
  // study-invented manipulation checks off this one. Both still read, because
  // pilot responses carry them: a stored `tlx-hN` wins where it exists, and a
  // stored check keeps landing in the `checks` bag under its own id.
  const afterVals = find('after')
  if (afterVals) {
    const afterCheckIds = new Set(AFTER_HALF.items.filter((i) => i.check).map((i) => i.id))
    for (const [k, v] of Object.entries(afterVals)) {
      if (!afterCheckIds.has(k)) continue
      const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN
      if (Number.isFinite(n)) checks[k] = n
    }
  }
  const tlxVals = find('tlx') ?? afterVals

  return {
    half,
    condition,
    project,
    tlx: tlxVals ? tlxScore(tlxVals) : null,
    // Carried beside the aggregate so a per-subscale figure never has to reach
    // into the stored responses, where Performance still runs the other way.
    tlxSubscales: tlxVals ? tlxSubscales(tlxVals) : null,
    umux: find('umux')
      ? umuxLiteScore(find('umux')!)
      : afterVals
        ? umuxLiteScore(afterVals)
        : null,
    hlac,
    checks,
  }
}

export function analyzeParticipant(
  raw: RawParticipantData,
  keys: AnswerKeys = { locate: {}, reach: {}, choices: {} },
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
        keys,
      ),
    )

  // Protocol v2's prediction-against-outcome pair spans two stages: the
  // prediction is s2's checklist (answered before anything was operated on)
  // and the outcome report is s3's, both scored against the same measured key
  // of what the removal actually moves. v1 stored both answers on one card
  // and reachMetricsFor has already handled those documents above; this fills
  // the same slot for v2 documents, on the s3 metric.
  for (const m of metrics) {
    if (m.requestId !== 's3' || m.reach) continue
    const s2 = requests.find((r) => r.requestId === 's2' && r.half === m.half)
    const s3 = requests.find((r) => r.requestId === 's3' && r.half === m.half)
    const wanted = reachFor(keys.reach, 's2', m.project)
    const blindPicks = Array.isArray(s2?.quiz?.['behaviours'])
      ? (s2!.quiz!['behaviours'] as string[])
      : null
    const checkedPicks = Array.isArray(s3?.quiz?.['behaviours'])
      ? (s3!.quiz!['behaviours'] as string[])
      : null
    if (!wanted || !blindPicks || !checkedPicks) continue
    const blind = setF1(blindPicks, wanted)
    const checked = setF1(checkedPicks, wanted)
    m.reach = {
      blind,
      checked,
      gain: checked - blind,
      blindConfidence: s2?.confidence ?? null,
      checkedConfidence: s3?.confidence ?? null,
      blindConfidenceScale: s2?.confidenceScale,
      // The v2 prediction is untimed within its stage (protocol v2 section 4),
      // so there is no blind-stage clock to report.
      blindActiveMs: 0,
      blindPicked: blindPicks.length,
      checkedPicked: checkedPicks.length,
      outOf: wanted.length,
    }
    m.calibration =
      m.reach.blindConfidence == null
        ? null
        : confidenceFraction(m.reach.blindConfidence, m.reach.blindConfidenceScale) - blind
  }

  const halves = participant.blocks.map((b) =>
    halfSummary(b.half, b.condition, b.project, responses),
  )

  const background = responses.find((r) => r.id === 'background')?.values ?? null

  return {
    pid: participant.code,
    label: participant.label,
    ordinal: participant.ordinal,
    group: participant.group,
    gitConfidence: typeof background?.gitConfidence === 'number' ? background.gitConfidence : null,
    events: categorized,
    requests: metrics,
    halves,
    firstCondition: participant.blocks.find((b) => b.half === 1)?.condition ?? null,
    complete: participant.status === 'completed',
  }
}

export function buildDataset(
  raws: RawParticipantData[],
  keys: AnswerKeys = { locate: {}, reach: {}, choices: {} },
): Dataset {
  let unassigned = 0
  const participants = raws.map((r) => {
    const windows = windowsFor(r.requests)
    unassigned += analyzeEvents(r.events, windows).unassigned
    return analyzeParticipant(r, keys)
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
