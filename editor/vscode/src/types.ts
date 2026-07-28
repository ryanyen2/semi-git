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
  // The feature's member entities as `file::qualname` (sgt.api.map_view). The majority-prefix `dir`
  // can exclude a feature's own production file when its members span dirs, so file filters union
  // the member file-set with the dir prefix.
  members: string[];
  why: string;
  split_reason: string | null;
  // Present (an `af-` id) only when this leaf is claimed by a user-authored feature; absent for
  // a purely clustered leaf (sgt.api.map_view, U6). Lets the tree mark authored vs. proposed.
  authored_id?: string;
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

// Whether this ref's witness has caught up to head and any in-progress genesis backfill has
// finished (sgt.core.lens.sync_status) -- a pure read, never triggers mining itself.
export interface SyncStatus {
  complete: boolean;
  reached_genesis: boolean;
}

export interface MapView {
  nodes: MapNode[];
  roots: string[];
  identity_events: IdentityEvent[];
  feature_count: number;
  edges: MapEdge[];
  sync_status: SyncStatus;
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

export interface SelectionView {
  ok: boolean;
  message: string;
  feature_ids: string[];
  files: string[];
  direct_op_count: number;
  closure_op_count: number;
  pulled: { feature_id: string | null; op_count: number; chain: unknown[] }[];
  hub: { symbol: string; pulled_op_count: number } | null;
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
  sync_status: SyncStatus;
}

// One dependent on the revert/edit frontier (sgt.api._frontier_rows, U3/R4). `bucket`:
// `blast` = a direct dependent that needs rework if kept (drafts a continuation hollow);
// `carry` = a transitive dependent that repoints mechanically for free; `foundation` = an
// upstream prerequisite the reverted core is built on, which cannot be dropped (`toggleable`
// false). Only populated for `verb === "revert"` on an ok preview.
export interface FrontierRow {
  op_id: string;
  bucket: "blast" | "carry" | "foundation";
  toggleable: boolean;
}

// A feature touched by the edit, rolled up (sgt.api._affected_rows). `direction`: `blast` =
// downstream dependent, `foundation` = upstream prerequisite.
export interface AffectedRow {
  feature_id: string;
  direction: "blast" | "foundation";
  op_count: number;
}

// One node of the "Focus & Morph" consequence subgraph (sgt.api.focus_subgraph): a feature the
// edit touches, carrying its op-count before and after so the webview can morph just this lane
// (N → M) against a dimmed field. `role` is hue-free — `target` acted-on, `blast` losing ops,
// `foundation` gaining ops or a kept prerequisite.
export interface FocusNode {
  feature_id: string;
  label: string;
  role: "target" | "blast" | "foundation";
  ops_before: number;
  ops_after: number;
}

// The affected subgraph only (not the whole graph): the nodes above, their co-change edges, and a
// tally of the unaffected features that stay dimmed as context. `nodes` is empty when no feature
// map is built, so a renderer falls back to the `so_what` headline.
export interface FocusView {
  so_what: string;
  nodes: FocusNode[];
  edges: { a: string; b: string }[];
  context_count: number;
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
  // U3 additions -- the selectable dependent frontier and the rolled-up affected features.
  frontier?: FrontierRow[];
  affected?: AffectedRow[];
  // The "Focus & Morph" subgraph the webview dims-and-morphs from (sgt.api.focus_subgraph).
  focus?: FocusView;
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
  // Derived in `sgt.api.plan_view` (not stored): "building" | "stalled" | "complete". A stalled
  // plan has unbuilt steps, no work in flight, and has gone quiet -- the Resume affordance targets it.
  derived_status?: "building" | "stalled" | "complete";
  pending_count?: number;
  remaining_titles?: string[];
  // Best-effort Claude Code session id captured at intake; when present, Resume relaunches this
  // exact conversation via `claude --resume <id>`, else the bare `claude --resume` picker.
  claude_session_id?: string | null;
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
// One feature-scoped intent segment -- a "checkpoint": a contiguous chapter of a feature's
// history sharing one intent, addressable as `<feature_id>@<seg_index>` and revertable as a unit.
// `intent` is the chapter's label in the developer's language; `novelty` (0..1) weights how much
// behavior it changed, so trivial chapters can be dimmed. From `sgt.api.intent_view`.
export interface IntentSegment {
  feature_id: string;
  feature_label: string;
  seg_index: number;
  checkpoint: string; // `<feature_id>@<seg_index>`
  intent: string;
  rationale: string;
  op_ids: string[];
  op_count: number;
  commit_shas: string[];
  first_index: number;
  last_index: number;
  novelty: number;
  tier: "coupled" | "co-changed" | "thematic";
  source: "llm" | "fallback";
}

export interface IntentView {
  themes: unknown[];
  atoms: unknown[];
  segments: IntentSegment[];
}

// `sgt log --json` == `sgt.api.grid_view(repo)` (plan U1/U3): the canonical lane×commit cell join
// -- the one projection every surface (CLI, TUI, this webview) renders from, so the (op -> cell)
// join is computed once server-side and never re-derived per surface. A cell carries the ops one
// feature touched in one commit; unattributed ops have no cell.
export interface GridCell {
  feature_id: string;
  commit_index: number;
  op_ids: string[];
  op_count: number;
  kinds: Record<string, number>;
  fidelity: "full" | "partial";
}

export interface GridView {
  commits: { index: number; sha: string; subject: string }[];
  cells: GridCell[];
  features: Record<string, { label: string; op_count: number }>;
  ghosts: unknown[];
  partial_commits: number[];
  commit_count: number;
  op_count: number;
  feature_count: number;
}

// `sgt.api.save_preview_view` -- the in-situ "what would a save land" read (feature-granular):
// which existing features would gain ops if you saved now (`affected`), how many pending ops
// belong to no built feature (`new_work_count`), and the total pending op count. Drives the
// workbench's dashed ghost-checkpoint cars. Empty `affected` + zero counts == nothing pending.
export interface SavePreviewAffected {
  feature_id: string;
  op_count: number;
  op_ids: string[];
}

export interface SavePreviewView {
  affected: SavePreviewAffected[];
  new_work_count: number;
  total_op_count: number;
}

export interface ComposeView {
  map: MapView;
  history: HistoryView;
  status: StatusView;
  forks: ForksView;
  plan: PlanView;
  drift: DriftView;
  sessions: SessionsView;
  trust: TrustView;
  intent: IntentView;
  save_preview: SavePreviewView;
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

// One feature this save's new ops landed in -- the label feedforward
// (`sgt.cli.porcelain._save_attribution`): its id, current label, a typeable handle, the real
// symbols the save touched in it, its edit count, and whether the save minted the lane (still unnamed).
export interface SaveFeature {
  feature_id: string;
  label: string;
  handle: string;
  symbols: string[];
  edits: number;
  new: boolean;
}

// `sgt save --as "<label>"`'s name-at-encode result -- present only when the save named a feature.
export interface SaveRename {
  ok: boolean;
  feature_id?: string;
  label?: string;
  message?: string;
}

// `sgt save [-m <message>] [--as "<label>"] [--json]` (D3). `features`/`renamed` are additive: which
// feature(s) the save's new ops landed in, and any inline `--as` rename result.
export interface SaveResult {
  ok: boolean;
  saved?: boolean;
  message?: string;
  commit?: string;
  ops?: number;
  error?: string;
  features?: SaveFeature[];
  renamed?: SaveRename;
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

// `sgt propose publish <id> [--remote origin] --json` (plan U32, D7).
export interface PublishResult {
  ok: boolean;
  action?: "created" | "updated";
  branch?: string;
  gh_output?: string;
  error?: string;
}
