// Mirror of the JSON shapes from `sgt.api` (the canonical projection). Keep in sync with
// sgt/api.py — the CLI `--json` mode, MCP, and this extension all consume the same schema.

export interface MapNode {
  id: string;
  label: string;
  kind: "feature" | "subsystem";
  parent: string | null;
  children: string[];
  size: number;
  op_count: number;
  dir: string;
  why: string;
  split_reason: string | null;
}

export interface IdentityEvent {
  event: string;
  feature_id: string;
}

// A cross-feature structural dependency edge (`sgt.lens.tree.feature_edges`) -- the fused
// structural/co-change/scope coupling graph rolled up to leaf-feature pairs.
export interface MapEdge {
  a: string;
  b: string;
  weight: number;
}

export interface MapView {
  nodes: MapNode[];
  roots: string[];
  identity_events: IdentityEvent[];
  feature_count: number;
  edges: MapEdge[];
}

// `sgt history --json`: the feature-map webview's shared commit-index axis.
export interface HistoryCommit {
  sha: string;
  subject: string;
  index: number;
}

export interface HistoryOp {
  id: string;
  kind: string;
  feature_id: string | null;
  commit_index: number;
}

export interface HistoryView {
  commits: HistoryCommit[];
  ops: HistoryOp[];
}

// `sgt preview <verb> <args...> --json` -- a side-effect-free preview shared by every feature
// verb + feature-grouped revert/restore; fields beyond `ok`/`verb`/`message`/`affected_features`
// vary by verb, so callers narrow on `verb` before reading them.
export interface FeatureVerbPreview {
  ok: boolean;
  verb: string;
  message: string;
  affected_features: string[];
  error?: string;
  [key: string]: unknown;
}

export interface BlameSpan {
  symbol: string;
  start_line: number; // 1-based inclusive
  end_line: number; // 1-based inclusive
  feature_id: string;
  label: string;
}

export interface BlameView {
  file: string;
  spans: BlameSpan[];
  features: Record<string, { label: string }>;
  error?: string;
}

export interface StatusView {
  files: number;
  symbols: number;
  features: number;
  coverage_fraction: number;
  oracle: { configured: boolean; status: "pending" | "pass" | "fail" | "unconfigured" };
  drift: { any: boolean; paths: string[] };
  unmanaged: string[];
  backstop_kept: string[];
  forks: { open: number; records: ForkRecord[] };
}

// `sgt revert <ref> --emit --json` — a sandboxed dry-run preview, shared by single-op and
// feature-grouped revert (both resolve to the same `sgt.api._project_verb_preview` shape).
export interface EmitView {
  ok: boolean;
  verb: string;
  target: string;
  removed: string[];
  added: string[];
  affected_symbols: string[];
  forked: boolean;
  files: Record<string, { before: string; after: string }>;
  message: string;
}

// `sgt merge <survivor> <absorbed> --json`.
export interface MergeResult {
  ok: boolean;
  survivor?: string;
  absorbed?: string;
  op_count?: number;
  member_count?: number;
  message?: string;
}

// `sgt rename <feature> "<label>" --json`.
export interface RenameResult {
  ok: boolean;
  feature?: string;
  old_label?: string;
  new_label?: string;
  message?: string;
}

// `sgt move <op>... --to <feature> --json`.
export interface MoveResult {
  ok: boolean;
  op_ids?: string[];
  target?: string;
  message?: string;
}

// `sgt split <feature> --json` (preview, no `--apply`).
export interface SplitPreviewResult {
  ok: boolean;
  feature?: string;
  applied: boolean;
  groups?: string[][];
  message?: string;
}

// `sgt split <feature> --apply --json`.
export interface SplitApplyResult {
  ok: boolean;
  feature?: string;
  new_feature?: string;
  applied: boolean;
  message?: string;
}

// `sgt plan status --json` / `sgt checkpoint --json` (plan U14): one file's current line spans
// for a set of symbols an op or a matched step touched.
export interface PlanFileSpan {
  path: string;
  spans: { symbol: string; start_line: number; end_line: number }[];
}

export interface PlanStep {
  hollow_id: string;
  title: string;
  predicted_footprint: string[];
  predicted_feature: string | null;
  rationale: string;
  status: "pending" | "matched";
  matched_op_ids: string[];
  files: PlanFileSpan[];
}

export interface PlanSession {
  session_id: string;
  plan_text: string;
  status: string;
  created_ts: number;
  last_activity_ts: number;
  steps: PlanStep[];
}

export interface CheckpointGroup {
  session_id: string;
  hollow_ids: string[];
  op_ids: string[];
  files: PlanFileSpan[];
}

// `sgt plan status --json`.
export interface PlanView {
  sessions: PlanSession[];
  checkpoint: { matches: CheckpointGroup[]; drift_op_ids: string[] };
}

export interface DriftEntry {
  op_id: string;
  kind: string;
  footprint: string[];
  files: PlanFileSpan[];
}

// `sgt drift --json`.
export interface DriftView {
  entries: DriftEntry[];
}

// `sgt.core.oracle.verdict_for` -- the stored record for one exact ideal, or `null` (pending, no
// tier has run and no override recorded yet). `overall_status` reduces this to one status string.
export interface OracleTierResult {
  status: string;
  exit_code: number;
  output_tail: string;
}

export interface OracleOverride {
  status: "pass" | "fail";
  reason: string;
  by: string | null;
  ts: number;
}

export interface OracleVerdictRecord {
  tiers: Record<string, OracleTierResult>;
  override: OracleOverride | null;
}

export type OracleVerdict = OracleVerdictRecord | null;

// `sgt forks [--json]` / `sgt forks <symbol> --json` (C4).
export interface ForkRecord {
  symbol: string;
  tips: [string, string];
  remedy: string;
  file: string;
}

export interface ForksView {
  open: number;
  forks: ForkRecord[];
}

export interface ForkDetailTip {
  op_id: string;
  files: Record<string, string>;
}

export interface ForkDetailView {
  symbol: string;
  tips: ForkDetailTip[];
  remedy: string;
  error?: string;
}

// `sgt fold --at <spec> [--json]` -- a side-effect-free fold of an arbitrary frontier; the
// draggable-playhead primitive. Never all three of `files`/`forked`/`error` at once.
export interface FoldView {
  op_count?: number;
  files?: Record<string, string>;
  oracle_verdict?: OracleVerdict;
  forked?: boolean;
  message?: string;
  error?: string;
}

// `sgt session start|status --json` (plan U30, D5).
export interface SessionInfo {
  name: string;
  branch: string;
  target_branch: string;
  scratch: string;
  new_op_count: number;
  owner_pid: number | null;
  alive: boolean;
}

export interface SessionOverlap {
  a: string;
  b: string;
  symbols: string[];
}

export interface SessionsView {
  sessions: SessionInfo[];
  overlaps: SessionOverlap[];
}

// `sgt review-queue list --json` / the U31 trust queue.
export interface TrustOpEntry {
  op_id: string;
  kind: string;
  footprint: string[];
  attribution: unknown[];
  drift: boolean;
}

export interface TrustGroup {
  provenance: string;
  op_ids: string[];
  ops: TrustOpEntry[];
}

export interface TrustView {
  groups: TrustGroup[];
  total_ops: number;
}

// `sgt propose create/status/land/render/publish --json`.
export interface PinContradiction {
  kind: string;
  members: string[];
  detail: string;
}

export interface ProposalStatus {
  state: "current" | "clean-reunion" | "fork";
  note: string | null;
  base_ref: string;
  base_moved: boolean;
  feature_delta: string[];
  delta_op_count: number;
  forks: { symbol: string; tips: [string, string]; remedy: string }[];
  remedy: string | null;
  claim: unknown[];
}

export interface ProposalFeatureDelta {
  feature_id: string;
  label: string;
  op_count: number;
}

export interface ProposalView {
  id: string;
  base_ref?: string;
  title?: string;
  description?: string;
  feature_delta?: ProposalFeatureDelta[];
  delta_op_count?: number;
  claim?: unknown[];
  provenance?: unknown[];
  status?: ProposalStatus;
  error?: string;
}

export interface ProposalChecklistEntry extends ProposalFeatureDelta {
  op_ids: string[];
  requires: string[];
}

export interface ProposalReviewView extends ProposalView {
  approvals?: string[];
  feature_checklist?: ProposalChecklistEntry[];
}

// The compose-view's own lightweight proposal summary -- deliberately shallower than
// `ProposalView` (no claim/provenance computation), since `compose_view` is a cheap aggregate.
export interface ComposeProposalSummary {
  id: string;
  title: string;
  base_ref: string;
  created_ts: number;
  delta_op_count: number;
  feature_delta: string[];
}

// `sgt compose [--json]` -- one aggregate refresh, collapsing ~9 shell-outs into one.
export interface ComposeView {
  map: MapView;
  history: HistoryView;
  status: StatusView;
  forks: ForksView;
  plan: PlanView;
  drift: DriftView;
  sessions: SessionsView;
  trust: TrustView;
  oracle_verdict: OracleVerdict;
  proposals: ComposeProposalSummary[];
}

// `sgt sync [remote] [branch] --json` (U15/U20).
export interface SyncReport {
  ok: boolean;
  remote: string;
  branch: string;
  merged: boolean;
  message: string;
  fetched_sha: string | null;
  merge_sha: string | null;
  ops_added: number;
  forks: [string, string, string][];
  open_fork_count: number;
  base_recovery: string;
  theirs_recovery: string;
  pin_contradictions: PinContradiction[];
  declared_cycles: [string, string][];
  identity_events: IdentityEvent[];
}

// `sgt land <branch> [--json]` (U23). Also reused for `sgt propose land <id> [--subset ...]`
// (same `land_view` projection) -- which, unlike a branch land, can fail before ever running
// (an invalid `--subset` ref) and report that through `error` rather than `blocked_reason`.
export interface LandReport {
  ok: boolean;
  branch: string;
  landed: boolean;
  land_sha: string | null;
  blocked_reason: string | null;
  ops_added: number;
  attempts: number;
  forks: [string, string, string][];
  open_fork_count: number;
  pin_contradictions: PinContradiction[];
  declared_cycles: [string, string][];
  identity_events: IdentityEvent[];
  error?: string;
}

// `sgt switch <branch> [--json]` (D3) — or the `{ok:false, error}` envelope shared by all three
// daily-loop verbs below.
export interface SwitchResult {
  ok: boolean;
  branch?: string;
  ops?: number;
  error?: string;
}

// `sgt save [-m <message>] [--json]` (D3).
export interface SaveResult {
  ok: boolean;
  saved?: boolean;
  message?: string;
  commit?: string;
  ops?: number;
  error?: string;
}

// `sgt undo [--json]` (D3).
export interface UndoResult {
  ok: boolean;
  undone?: boolean;
  message?: string;
  commit?: string;
  restored_ops?: number;
  removed?: string[];
  added?: string[];
  error?: string;
}

// `sgt merge-op <a> <b>` / `split-op <op>` / `transplant <op>... --onto <ref>` (plan U11, R14) --
// all three share this printer, drafting a hollow op for a human/agent to fulfill.
export interface RewriteDraft {
  ok: boolean;
  verb?: string;
  target?: string;
  draft_id?: string;
  hollow_ids?: string[];
  message?: string;
}

// `sgt fulfill <draft-id> --from-tree [--json]` (plan U11): supplies a drafted hollow's image
// from the working tree -- stages, no commit. Note: unlike most verbs, a `RewriteError` here
// prints plain text even with `--json`, so callers should expect the promise to reject (not
// resolve with `ok: false`) on that path.
export interface FulfillResult {
  ok: boolean;
  op_ids?: string[];
}

// `sgt commit [--json]` (plan U11): commits the staged rewrite candidate. Distinct from
// `sgt land <branch> --json` (`LandReport`, the U23 CAS shared-branch advance). Same caveat as
// `FulfillResult` -- a refusal prints plain text even with `--json`.
export interface LandCandidateResult {
  ok: boolean;
  sha?: string;
}

// `sgt review-queue ack <op-id>... [--session <name>] [--note "..."] --json` (plan U31, S7): the
// trust queue's dequeue mechanism.
export interface ReviewAckResult {
  ok: boolean;
  id?: string;
  op_ids?: string[];
  scope?: string;
  note?: string | null;
  error?: string;
}

// `sgt propose publish <id> [--remote origin] --json` (plan U32, D7).
export interface PublishResult {
  ok: boolean;
  action?: "created" | "updated";
  branch?: string;
  gh_output?: string;
  error?: string;
}
