// Document shapes for every collection the study writes. Kept in one file
// because the participant app, the dashboard, the analysis pipeline and the
// command-line uploader all have to agree, and a shape that drifts between
// writer and reader is how a study loses a measure it thought it had.

export type Condition = 'git' | 'sgt'
export type Project = 'bikecount' | 'footfall'

/** Both projects, for the checks that have to assert every one is covered. */
export const PROJECTS: readonly Project[] = ['bikecount', 'footfall']
export type Half = 1 | 2

/** Counterbalancing group. Determines condition order and project pairing. */
export type GroupId = 1 | 2 | 3 | 4

/**
 * Which study a record belongs to. `pilot` records are real in every other
 * respect -- same flow, same bundle, same telemetry -- and are excluded from
 * the analysis by default.
 *
 * A separate field rather than a naming convention or a `_test` email, because
 * both of those are things a tired person gets wrong at 9pm and nothing checks.
 * This one is enforceable: the analysis filters on it, the roster separates on
 * it, and the participant cannot change it (it is absent from the
 * participant-writable allowlist in firestore.rules, like `group` and `blocks`).
 */
export type StudyId = 'main' | 'pilot'

export interface BlockAssignment {
  half: Half
  condition: Condition
  project: Project
  /** What the participant is told. Never "git" or "sgt". */
  label: 'Setup A' | 'Setup B'
}

export type ParticipantStatus =
  | 'created'      // record exists, link not opened
  | 'claimed'      // participant opened the link
  | 'consented'
  | 'in-progress'
  | 'completed'
  | 'withdrawn'
  | 'excluded'

export interface Participant {
  /** Document id. 24-char random access code; the link is the capability. */
  code: string
  studyId: StudyId
  /**
   * Sort key and cohort position. 1..12 for the real cohort; pilots are issued
   * from a 1000+ band so they sort last, never collide with a real ordinal, and
   * cannot silently consume P13.
   */
  ordinal: number
  /** P01..P12 for the study, X01.. for a pilot. The id used in the paper. */
  label: string
  group: GroupId
  blocks: [BlockAssignment, BlockAssignment]
  email: string | null
  status: ParticipantStatus
  currentStep: string
  /** Free-form per-step scratch, so a refresh mid-form loses nothing. */
  stepState: Record<string, unknown>
  claimedUid: string | null
  claimedAt: number | null
  startedAt: number | null
  consentAt: number | null
  completedAt: number | null
  lastSeenAt: number | null
  createdAt: number
  updatedAt: number
  /** Facilitator's own notes field on the roster row. */
  adminNote?: string
}

/** One questionnaire submission. Id is `${instrumentId}` or `${instrumentId}-h${half}`. */
export interface ResponseDoc {
  instrumentId: string
  version: string
  half: Half | null
  condition: Condition | null
  /** itemId -> value. Numbers for scales, strings for text, arrays for multi. */
  values: Record<string, string | number | boolean | string[] | null>
  startedAt: number
  submittedAt: number | null
  /** Milliseconds the form was on screen, for detecting straight-lining. */
  dwellMs: number
}

/**
 * Every request id that has ever been STORED, not the six this study asks.
 *
 * This type describes what is in Firestore. `requestById` returns undefined for
 * every retired id on purpose.
 */
export type RequestId =
  /**
   * The live set: see what the dashboard does today, locate the work behind one
   * of its behaviours, take that work out, and put it back.
   */
  | 'd1' | 'd2' | 'd3' | 'd4'
  /**
   * Retired. `w1`-`w3` were the feature-removal block, `r1`-`r3` the three-request
   * design, `r4`-`r6` the six-request one before it, and `f1`/`f2` the two
   * standalone reach trials whose measurement now rides on `d3`. Kept so pilot
   * documents still read: narrowing this to the live set would make every read of
   * a pilot document a lie the compiler endorses, and would make `requestById`'s
   * undefined branch -- the one that stopped the dashboard throwing mid-render --
   * look unreachable to whoever reads it next.
   */
  | 'w1' | 'w2' | 'w3'
  | 'r1' | 'r2' | 'r3' | 'r4' | 'r5' | 'r6'
  | 'f1' | 'f2'

export interface PauseInterval {
  from: number
  to: number | null
  reason: 'break' | 'facilitator' | 'tool-failure' | 'other'
  note?: string
}

/** One request attempt. Id is `${requestId}-h${half}`. */
export interface RequestDoc {
  requestId: RequestId
  half: Half
  condition: Condition
  project: Project
  openedAt: number | null
  submittedAt: number | null
  /** Wall time from open to submit. Active time subtracts pauses. */
  elapsedMs: number
  activeMs: number
  pauses: PauseInterval[]
  capMs: number
  hitCap: boolean
  /**
   * Retired with the closed questions. Pilot documents still carry it, and the
   * dashboard still renders those, so the field stays described rather than
   * deleted: questionId -> index of the option picked.
   */
  choices?: Record<string, number>
  /**
   * What the participant wrote in a locate step's box -- a commit sha, a feature
   * name, an id, or a sentence saying they were not sure. Free text, compared
   * against `requestKeys[id].locate` after the session rather than in the
   * browser: the two arms name work in different vocabularies, and a
   * browser-side match would have to be lenient enough to be worthless or
   * strict enough to reject `f-8068d4e` for `8068d4e`.
   */
  locate?: string
  /** 0-100. Asked wherever there is a right answer. */
  confidence: number | null
  /**
   * The reach prediction, on the step that reverts. The same question answered
   * twice: once before the operation runs, once after it has run. `gain` is the
   * difference, and it is the whole point, so both are stored rather than only
   * the final answer. The second answer is grounded in something that happened
   * rather than in a second read of the same screen.
   *
   * Picks are behaviour ids, not indices, so an id survives a re-ordering of the
   * twelve that an index would not.
   */
  stages?: {
    blind?: ReachStage
    checked?: ReachStage
  }
  /** Participant-declared outcome. Scoring is the facilitator's, not this. */
  selfReport: 'done' | 'partial' | 'gave-up' | 'blocked' | null
  /**
   * The free-text observation box, where a step has one. Recorded and never
   * scored -- see `RequestSpec.note` in study/tasks.ts for why.
   */
  notes: string
}

export interface ReachStage {
  /** Behaviour ids checked. Order is the participant's click order, kept for no
   * analysis in particular -- it is cheap and unrecoverable later. */
  picks: string[]
  confidence: number | null
  submittedAt: number
  /** Active ms spent in this stage alone. Blind should be short; if it is not,
   * the participant was reasoning rather than reading, which changes what
   * `blind` means. */
  activeMs: number
}

export type EventKind =
  | 'prompt'          // message sent to the assistant
  | 'tool'            // assistant tool call
  | 'command'         // command the participant ran themselves
  | 'session'         // assistant session start/stop
  | 'repo'            // repo state snapshot
  | 'heartbeat'
  | 'marker'          // request boundary pushed from the web app

/** Telemetry event. Written by the bundle, read by the dashboard. */
export interface EventDoc {
  /** Content-addressed; re-running the sync cannot double-count. */
  id: string
  kind: EventKind
  ts: number
  half: Half | null
  condition: Condition | null
  requestId: RequestId | null
  /** Action taxonomy label, assigned by the analysis pipeline, not the source. */
  category?: string
  /** Tool name, or argv[0] for commands. */
  name: string | null
  /** Full command line, or the prompt text, truncated at 8000 chars. */
  text: string | null
  /** Files touched, where the source knows them. */
  paths?: string[]
  exitCode?: number | null
  durationMs?: number | null
  ok?: boolean | null
  sessionId?: string | null
  deviceId: string
  /** Anything the source knew that this schema does not name. */
  extra?: Record<string, unknown>
}

export interface DeviceDoc {
  deviceId: string
  half: Half | null
  condition: Condition | null
  project: Project | null
  os: string
  toolBuild: string | null
  /** Set by study-doctor: every precondition it checked and the result. */
  checks: Record<string, { ok: boolean; detail: string }>
  eventsUploaded: number
  firstSeenAt: number
  lastSeenAt: number
  bundleVersion: string | null
}

/** Facilitator scoring for one request. Id is `${requestId}-h${half}`. */
export interface ScoringDoc {
  requestId: RequestId
  half: Half
  /** Rubric points earned, out of `outOf`. */
  score: number | null
  outOf: number | null
  /** Tests failing outside the target feature, from score_study_repo.py. */
  collateralDamage: number | null
  /** Which of the four outcomes for r2/r3. */
  outcome: string | null
  /** Raw scorer stdout, pasted or uploaded, kept verbatim. */
  scorerOutput: string
  rubric: Record<string, boolean>
  scoredBy: string
  scoredAt: number
  note: string
}

export interface InterviewNote {
  id: string
  probeId: string
  ts: number
  text: string
  half: Half | null
}

/** Per-participant credentials, provisioned by the experimenter. */
export interface SecretsDoc {
  openaiApiKey: string
  anthropicApiKey: string
  /** Model pinned for the whole study; part of the condition. */
  claudeModel: string
  issuedAt: number
  revokedAt: number | null
}

/** Ground truth. Admin-only, never shipped in the participant bundle. */
export interface GroundTruth {
  version: string
  episodes: Array<{
    id: string
    shape: string
    author: 'human' | 'agent'
    bikecount: string
    footfall: string
  }>
  requestKeys: Record<
    string,
    {
      bikecount: string
      footfall: string
      /**
       * The work a locate step is looking for, per project: the commit sha in
       * the study repository, plus every other string that names the same work
       * and should be accepted. The two arms name work differently -- a sha
       * under git, a feature label or id under sgt -- so a single correct string
       * would mark one arm wrong for being right in its own vocabulary.
       *
       * Scored after the session by hand-comparison against this list, not in
       * the browser: the participant's box is free text, and a browser-side
       * match would have to be lenient enough to be worthless or strict enough
       * to reject `f-8068d4e` for `8068d4e`.
       */
      locate?: Record<string, string[]>
      /**
       * Behaviour ids the named work reaches. Present on the steps carrying a
       * reach prediction, and generated by scripts/study/measure_reach_key.py
       * rather than written by hand -- a designer choosing this set would be
       * choosing the result.
       */
      reach?: string[]
    }
  >
  rubrics: Record<string, Array<{ id: string; label: string; points: number }>>
}

/** Operational settings the participant page reads before it knows the code. */
export interface PublicConfig {
  studyTitle: string
  supportEmail: string
  /** Download URL per condition+project, shown on the setup step. */
  bundleUrls: Record<string, string>
  /** Shown verbatim on the consent page. */
  consentBodyMarkdown: string
  compensation: string
  irbProtocol: string
  active: boolean
}
