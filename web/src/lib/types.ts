// Document shapes for every collection the study writes. Kept in one file
// because the participant app, the dashboard, the analysis pipeline and the
// command-line uploader all have to agree, and a shape that drifts between
// writer and reader is how a study loses a measure it thought it had.

export type Condition = 'git' | 'sgt'
export type Project = 'coursecraft' | 'confplan'
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

export type RequestId = 'r1' | 'r2' | 'r3' | 'r4' | 'r5' | 'r6'

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
   * questionId -> index of the option picked, where the request asks closed
   * questions. Indices rather than the option text, so re-wording an option
   * between pilots does not orphan the answers already collected.
   */
  choices?: Record<string, number>
  /** 0-100. Asked wherever there is a right answer. */
  confidence: number | null
  /** Participant-declared outcome. Scoring is the facilitator's, not this. */
  selfReport: 'done' | 'partial' | 'gave-up' | 'blocked' | null
  notes: string
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
    coursecraft: string
    confplan: string
  }>
  requestKeys: Record<
    string,
    {
      coursecraft: string
      confplan: string
      /** Correct option INDEX per closed question, per project. Present on r1 only. */
      choices?: Record<string, Record<string, number>>
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
