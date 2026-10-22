// Mirror of the JSON shapes from `ugh.api` (the canonical projection). Keep in sync with
// ugh/api.py — the CLI `--json` mode, MCP, and this extension all consume the same schema.

export interface MapNode {
  id: string;
  label: string;
  kind: "feature" | "subsystem";
  parent: string | null;
  children: string[];
  size: number;
  op_count: number;
  dir: string;
  // The feature's member entities as `file::qualname` (ugh.api.map_view). The majority-prefix `dir`
  // can exclude a feature's own production file when its members span dirs, so file filters union
  // the member file-set with the dir prefix.
  members: string[];
  // What this feature's OWN ops touch, sentinels excluded (ugh.api.map_view) -- the set `ugh show`
  // counts, which is not `members` (the clustering's assignment). A leaf with an empty one is a husk:
  // nothing to show and nothing a revert would remove, so listing surfaces drop it (`mapFilter.ts`).
  own_symbols?: string[];
  why: string;
  split_reason: string | null;
  // Present (an `af-` id) only when this leaf is claimed by a user-authored feature; absent for
  // a purely clustered leaf (ugh.api.map_view, U6). Lets the tree mark authored vs. proposed.
  authored_id?: string;
}

export interface IdentityEvent {
  event: string;
  feature_id: string;
}

// A cross-feature structural dependency edge (`ugh.lens.tree.feature_edges`) -- the fused
// structural/co-change/scope coupling graph rolled up to leaf-feature pairs.
export interface MapEdge {
  a: string;
  b: string;
  weight: number;
}

// Whether this ref's witness has caught up to head and any in-progress genesis backfill has
// finished (ugh.core.lens.sync_status) -- a pure read, never triggers mining itself.
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

// `ugh history --json`: the feature-map webview's shared commit-index axis.
export interface HistoryCommit {
  sha: string;
  subject: string;
  index: number;
  // Committer time (`%ct`) and whether the commit carries only bookkeeping -- both emitted by
  // `history_view` for a long time, and pinned in the `log` golden; this mirror just lacked them.
  ts?: number | null;
  bookkeeping?: boolean;
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

// `ugh preview <verb> <args...> --json` -- a side-effect-free preview shared by every feature
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

/** `ugh find --json`. `mode` says which rung answered: meaning, or word overlap. */
export interface FindView {
  ok: boolean;
  mode: "semantic" | "lexical" | "none";
  query?: string;
  message?: string;
  hits: { kind: string; id: string; label: string; detail: string; score: number }[];
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
  // Paths carrying a staged rewrite candidate (U6): planned divergence, disjoint from `drift`.
  // `fsck --tree` has drawn this line since U6; this view only started drawing it when the workbench
  // was found titling a candidate "Working changes" and offering a Save the lens refuses.
  staged: { any: boolean; paths: string[] };
  unmanaged: string[];
  backstop_kept: string[];
  // Files ugh holds no op for at all (`.gitignore` in every repo these tools
  // build). Kept on disk, but not damage and not repairable, so anything that
  // renders `backstop_kept` as something to fix must leave these out.
  never_recorded?: string[];
  forks: { open: number; records: ForkRecord[] };
  sync_status: SyncStatus;
}

// One dependent on the revert/edit frontier (ugh.api._frontier_rows, U3/R4). `bucket`:
// `blast` = a direct dependent that needs rework if kept (drafts a continuation hollow);
// `carry` = a transitive dependent that repoints mechanically for free; `foundation` = an
// upstream prerequisite the reverted core is built on, which cannot be dropped (`toggleable`
// false). Only populated for `verb === "revert"` on an ok preview.
export interface FrontierRow {
  op_id: string;
  bucket: "blast" | "carry" | "foundation";
  toggleable: boolean;
}

// A feature touched by the edit, rolled up (ugh.api._affected_rows). `direction`: `blast` =
// downstream dependent, `foundation` = upstream prerequisite.
export interface AffectedRow {
  feature_id: string;
  direction: "blast" | "foundation";
  op_count: number;
}

// One node of the "Focus & Morph" consequence subgraph (ugh.api.focus_subgraph): a feature the
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

// One entity's line range in one side of a verb projection (`ugh.api._side_entity_spans`), from
// the same tree-sitter extraction `blame` runs. Ranges are 1-based inclusive and are against THAT
// SIDE's own text: `before` and `after` disagree about line numbers the moment an entity is added
// or removed, so a span read against the wrong side lands on the wrong function.
export interface ChangeSpan {
  symbol: string; // `file::qualname`
  kind: string; // "function" | "class" | "method"
  start_line: number;
  end_line: number;
}

// `ugh revert <ref> --emit --json` — a sandboxed dry-run preview, shared by single-op and
// feature-grouped revert (both resolve to the same `ugh.api._project_verb_preview` shape).
export interface EmitView {
  ok: boolean;
  verb: string;
  target: string;
  removed: string[];
  added: string[];
  affected_symbols: string[];
  forked: boolean;
  // `*_spans` are optional because a pinned older CLI has no `_side_entity_spans` and answers
  // the pair alone; a client without them falls back to file granularity rather than guessing
  // where an entity begins.
  files: Record<
    string,
    { before: string; after: string; before_spans?: ChangeSpan[]; after_spans?: ChangeSpan[] }
  >;
  message: string;
  // U3 additions -- the selectable dependent frontier and the rolled-up affected features.
  frontier?: FrontierRow[];
  affected?: AffectedRow[];
  // Present only when the target resolved as a `<feature>@<n>` / `<feature>:<slug>` checkpoint
  // (cli/ideal_edit.py's `extra={"checkpoint": label}`): that segment's own display label, e.g.
  // `Waitlist Priority@2: Promote On Cancel`. Lets a confirm name the chapter the user clicked
  // instead of the opaque ref they never typed.
  checkpoint?: string;
  // The "Focus & Morph" subgraph the webview dims-and-morphs from (ugh.api.focus_subgraph).
  focus?: FocusView;
  // The one-line consequence (`ugh.api.so_what_for`) and the ops the user actually named, as
  // opposed to what the closure swept up; `removed` is empty for the ordinary forward-subtraction
  // revert, so a count read off it says "0 edits".
  so_what?: string;
  target_ops?: string[];
  // A forked refusal, structured: the live op the edit collides with (what a swap takes out), the
  // symbol's live tip, and the op that wanted in; `[]` on every ok preview.
  conflicts?: { symbol: string; live_op: string; live_tip: string; requested_op: string }[];
  // The subtraction report (`ugh.core.subtract`): symbols rewritten in place with the target's
  // contribution cut out, symbols removed entirely, symbols left with two overlapping versions, and
  // survivors still naming removed code. For a checkpoint, feature or theme target these -- plus
  // `focus.nodes` -- are the consequence; `frontier` is always empty for one.
  subtracted_symbols?: string[];
  pruned_symbols?: string[];
  kept_conflicts?: string[];
  broken_references?: string[];
}

// `ugh merge <survivor> <absorbed> --json`.
export interface MergeResult {
  ok: boolean;
  survivor?: string;
  absorbed?: string;
  op_count?: number;
  member_count?: number;
  message?: string;
}

// `ugh rename <feature> "<label>" --json`.
export interface RenameResult {
  ok: boolean;
  feature?: string;
  old_label?: string;
  new_label?: string;
  message?: string;
}

// `ugh move <op>... --to <feature> --json`.
export interface MoveResult {
  ok: boolean;
  op_ids?: string[];
  target?: string;
  message?: string;
}

// `ugh split <feature> --json` (preview, no `--apply`).
export interface SplitPreviewResult {
  ok: boolean;
  feature?: string;
  applied: boolean;
  groups?: string[][];
  message?: string;
  // Additive (older CLIs omit them): the id the split will mint for the new group, and the
  // recorded ops that follow it -- what lets the workbench draw the cut at chunk grain.
  new_id?: string;
  moving_op_ids?: string[];
}

// `ugh split <feature> --apply --json`.
export interface SplitApplyResult {
  ok: boolean;
  feature?: string;
  new_feature?: string;
  applied: boolean;
  message?: string;
}

// `ugh plan status --json` / `ugh checkpoint --json` (plan U14): one file's current line spans
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
  // Pending steps only (`ugh.api.plan_view --full`): whether the step's predicted files already saw
  // edits since intake, and a one-line "why" -- so a stall can be explained ("built in server.py,
  // not the predicted connection.py") without re-deriving it. Absent on matched steps.
  covered?: boolean;
  coverage_reason?: string;
}

export interface PlanSession {
  session_id: string;
  plan_text: string;
  status: string;
  created_ts: number;
  last_activity_ts: number;
  steps: PlanStep[];
  // Derived in `ugh.api.plan_view` (not stored): "building" | "stalled" | "complete". A stalled
  // plan has unbuilt steps, no work in flight, and has gone quiet -- the Resume affordance targets it.
  derived_status?: "building" | "stalled" | "complete";
  pending_count?: number;
  remaining_titles?: string[];
  // Best-effort Claude Code session id captured at intake; when present, Resume relaunches this
  // exact conversation via `claude --resume <id>`, else the bare `claude --resume` picker.
  claude_session_id?: string | null;
  // True when a live `ugh session` shares this plan's `claude_session_id`: its steps also match
  // against that session's work, so the matched fraction counts work not yet landed.
  matched_in_session?: boolean;
}

export interface CheckpointGroup {
  session_id: string;
  hollow_ids: string[];
  op_ids: string[];
  files: PlanFileSpan[];
}

// `ugh plan status --json`.
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

// `ugh drift --json`.
export interface DriftView {
  entries: DriftEntry[];
}

// `ugh.core.oracle.verdict_for` -- the stored record for one exact ideal, or `null` (pending, no
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

// `ugh forks [--json]` / `ugh forks <symbol> --json` (C4).
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

// `ugh fold --at <spec> [--json]` -- a side-effect-free fold of an arbitrary frontier; the
// draggable-playhead primitive. Never all three of `files`/`forked`/`error` at once.
export interface FoldView {
  op_count?: number;
  files?: Record<string, string>;
  // `--out` syncs a directory instead of returning the tree, so it reports a count (`written`)
  // where the bare form returns `files`. A reader that only knows `files` shows "0 files" for
  // every materialized fold.
  written?: number;
  oracle_verdict?: OracleVerdict;
  forked?: boolean;
  message?: string;
  error?: string;
}

// `ugh session start|status --json` (plan U30, D5).
export interface SessionInfo {
  name: string;
  branch: string;
  target_branch: string;
  scratch: string;
  new_op_count: number;
  owner_pid: number | null;
  // The process fact: the process that started it still exists (for an agent, the whole Claude
  // Code process). Not "someone is working on it" -- that is `last_change_ts`.
  alive: boolean;
  // The Claude Code session that started it; joins `PlanSession.claude_session_id`.
  claude_session_id: string | null;
  // "claude" when started under Claude Code, "terminal" otherwise, null for an older record.
  started_by: "claude" | "terminal" | null;
  // Commits on the session branch its target branch lacks, and whether the scratch tree has
  // uncommitted source. A session with neither is what `ugh session gc` cleans up once it is dead.
  unlanded: number;
  uncommitted: boolean;
  // Unix seconds: the newest of its start, its own commits, and its dirty files' mtimes.
  last_change_ts: number;
  // K9: the lanes this session's own work touched, by the symbols a reader sees (never a
  // `__residue__`/`__anchor__`/`__import__` sentinel), each list capped at 24.
  touched: SessionTouch[];
  symbols_truncated: boolean;
  // Each touched symbol counted once per save that touched it; uncommitted work is one save.
  change_count: number;
}

// `feature_id` is a `compose.map.nodes[].id`, or null for new code no lane owns yet.
export interface SessionTouch {
  feature_id: string | null;
  symbols: string[];
}

// Two sessions that changed the same symbols, or (`branch === true`) a session `a` and its target
// branch `b`, which changed `symbols` since the session's base: what landing `a` must reconcile. A
// session pair carries no `branch` and raw footprints, sentinels included; a branch entry carries
// only symbols a reader sees.
export interface SessionOverlap {
  a: string;
  b: string;
  branch?: true;
  symbols: string[];
}

export interface SessionsView {
  sessions: SessionInfo[];
  overlaps: SessionOverlap[];
}

// `ugh review-queue list --json` / the U31 trust queue.
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

// `ugh propose create/status/land/render/publish --json`.
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

// `ugh compose [--json]` -- one aggregate refresh, collapsing ~9 shell-outs into one.
// One feature-scoped intent segment -- a "checkpoint": a contiguous chapter of a feature's
// history sharing one intent, addressable as `<feature_id>@<seg_index>` and revertable as a unit.
// `intent` is the chapter's label in the developer's language; `novelty` (0..1) weights how much
// behavior it changed, so trivial chapters can be dimmed. From `ugh.api.intent_view`.
// One captured ask, as every surface renders it (`ugh.intent.stint.ask_record`). `gist` is the
// excerpt to put on a line -- the request inside a real prompt, which opens with throat-clearing
// and carries its reasoning after the ask (`ugh.intent.gist`); `source` is whose words they were,
// in words, so the reader can tell their own typing from an agent's paraphrase. `text` is the
// verbatim prompt and arrives only on demand (`ugh show <ref> --asked`), because a panel listing
// every chapter of every feature would otherwise carry every prompt in the repository.
export interface CapturedAsk {
  gist: string;
  trimmed: boolean;
  chars: number;
  channel: string;
  source: string;
  actor: string;
  ts: number | null;
  claude_session_id: string | null;
  resumable: boolean;
  claimed: number;
  scope: "stint" | "save";
  text?: string;
}

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
  source: "llm" | "fallback" | "words";
  asks: CapturedAsk[];
  // The named symbols the chapter touched (`ugh.api._segment_symbols`): a function, class or
  // whole file, never residue/anchor/import bookkeeping, with the kind read off the symbol's own
  // chain inside the chapter and the saves (commit indices) that touched it. `change_count` is the
  // sum of those saves, so "6 changes · 4 functions · 3 saves" is true by construction; an op
  // count is mostly bookkeeping and never reaches a reader. `state` is read per symbol: a
  // forward-subtraction revert keeps ops in the ideal under a masking rework, so
  // `present_op_count === 0` is not "taken out" (15 of 22 reverted footfall chapters read live).
  symbols?: { symbol: string; file: string; kind: "added" | "changed" | "removed" | "moved"; saves: number[]; live: boolean }[];
  symbol_count?: number;
  change_count?: number;
  symbols_truncated?: number;
  state?: "live" | "reverted" | "partial" | null;
  // What stands on the chapter, without a preview (`ugh.api._segment_dependents`): the later live
  // chapters a revert of it would rewrite or leave naming removed code, sorted by their first
  // commit, each with up to 8 shared names a reader sees. A dependent can start before this
  // chapter when one of its later saves builds on it. `clean` is true when nothing stands on it
  // and the revert removes its work outright; false when later work stands on it or the revert
  // would layer a rework (a chapter linked only through layout is false with no dependent); null
  // when the chapter has no live work to take out.
  dependents?: { ref: string; symbols: string[] }[];
  clean?: boolean | null;
}

// One commit-keyed `IntentAtom` from `ugh.api.intent_view` (only the fields the hover reads are
// typed; the projection carries more -- prompt, session ids, tier). `feature_span` is the set of
// feature ids the atom's ops land in; `rationale` is the live intent-ledger reason(s) for those
// ops -- the "why" the hover joins to a feature.
export interface IntentAtom {
  commit_sha: string;
  subject: string;
  feature_span: string[];
  rationale: string[];
}

export interface IntentView {
  themes: unknown[];
  atoms: IntentAtom[];
  segments: IntentSegment[];
}

// `ugh log --json` == `ugh.api.grid_view(repo)` (plan U1/U3): the canonical lane×commit cell join
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
  // Work a revert took out of the ideal that no cell holds. Clustering keeps only alive symbols, so
  // a reverted symbol's ops lose their leaf and with it their cell and their chapter -- every lane
  // then draws whole while the code is off disk. These ops have no row by construction, so a surface
  // has to name them out of band (the drift/fork indicator does, alongside its other unplaced marks).
  reverted_unaccounted?: { op_count: number; symbols: string[] };
  commit_count: number;
  op_count: number;
  feature_count: number;
}

// `ugh.api.save_preview_view` -- the in-situ "what would a save land" read (feature-granular):
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

// `ugh advanced rewrite status --json` (plan U11), also bundled into `ComposeView`. `staged` is the
// candidate awaiting a landing decision, `null` when there is none; its `oracle_status` is the gate
// `ugh advanced commit` refuses on, and is the candidate's own verdict rather than the current
// ideal's -- so a surface offering Land has to read it here, not off the titlebar oracle chip.
export interface RewriteView {
  drafts: {
    draft_id: string;
    verb: string;
    target: string;
    message: string;
    hollow_ops: { id: string; symbol: string; kind: string; intent: string }[];
  }[];
  staged: {
    verb: string;
    target: string;
    op_count: number;
    oracle_verdict: OracleVerdict;
    oracle_status: "pending" | "pass" | "fail";
  } | null;
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
  rewrite: RewriteView;
  save_preview: SavePreviewView;
  oracle_verdict: OracleVerdict;
  proposals: ComposeProposalSummary[];
}

// `ugh sync [remote] [branch] --json` (U15/U20).
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

// `ugh land <branch> [--json]` (U23). Also reused for `ugh propose land <id> [--subset ...]`
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

// `ugh switch <branch> [--json]` (D3) — or the `{ok:false, error}` envelope shared by all three
// daily-loop verbs below.
export interface SwitchResult {
  ok: boolean;
  branch?: string;
  ops?: number;
  error?: string;
}

// One feature this save's new ops landed in -- the label feedforward
// (`ugh.cli.porcelain._save_attribution`): its id, current label, a typeable handle, the real
// symbols the save touched in it, its edit count, and whether the save minted the lane (still unnamed).
export interface SaveFeature {
  feature_id: string;
  label: string;
  handle: string;
  symbols: string[];
  edits: number;
  new: boolean;
}

// `ugh save --as "<label>"`'s name-at-encode result -- present only when the save named a feature.
export interface SaveRename {
  ok: boolean;
  feature_id?: string;
  label?: string;
  message?: string;
}

// `ugh save [-m <message>] [--as "<label>"] [--json]` (D3). `features`/`renamed` are additive: which
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

// `ugh undo [--json]` (D3).
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

// `ugh undo --emit --json` (oplog.preview): what the next undo *would* do. `ok: false` means undo
// would refuse -- the F3 guard's "this would drop work committed since" -- and `applied` is always
// false, which is how a caller tells this report apart from the mutation's own `ok: true`.
export interface UndoPreview {
  applied: false;
  ok: boolean;
  kind: string | null;
  message: string;
  restored: string[];
  dropped: string[];
  symbols: string[];
  error?: string;
}

// `ugh merge-op <a> <b>` / `split-op <op>` / `transplant <op>... --onto <ref>` (plan U11, R14) --
// all three share this printer, drafting a hollow op for a human/agent to fulfill.
export interface RewriteDraft {
  ok: boolean;
  verb?: string;
  target?: string;
  draft_id?: string;
  hollow_ids?: string[];
  message?: string;
}

// `ugh fulfill <draft-id> --from-tree [--json]` (plan U11): supplies a drafted hollow's image
// from the working tree -- stages, no commit. Note: unlike most verbs, a `RewriteError` here
// prints plain text even with `--json`, so callers should expect the promise to reject (not
// resolve with `ok: false`) on that path.
export interface FulfillResult {
  ok: boolean;
  op_ids?: string[];
}

// `ugh advanced commit [--json]` (plan U11): commits the staged rewrite candidate. Distinct from
// `ugh land <branch> --json` (`LandReport`, the U23 CAS shared-branch advance). Same caveat as
// `FulfillResult` -- a refusal prints plain text even with `--json`.
export interface LandCandidateResult {
  ok: boolean;
  sha?: string;
}

// `ugh advanced unstage --json` (plan U6): the other exit from a staged candidate -- drop it and
// restore the committed ideal to the working tree. `op_ids` is what was restored, not what was
// discarded, which is why the toast counts ops rather than claiming a number of ops lost.
export interface UnstageResult {
  ok: boolean;
  op_ids?: string[];
}

// `ugh propose publish <id> [--remote origin] --json` (plan U32, D7).
export interface PublishResult {
  ok: boolean;
  action?: "created" | "updated";
  branch?: string;
  gh_output?: string;
  error?: string;
}

// `ugh now --json` (`ugh.api.now_view`): the "state of actions" the Now tree renders -- what's in
// flight, what needs me, what was recently done, and the single next action. A thin assembler that
// deliberately avoids the heavy intent_view; rationale/feature_span stay a drill-down.
export interface NowInFlightRow {
  feature_id: string;
  op_count: number;
  op_ids: string[];
}

export interface NowReview {
  id: string;
  subject: string;
  reason: string;
}

export interface NowStalledPlan {
  session_id: string;
  claude_session_id: string | null;
  pending_count: number;
  remaining_titles: string[];
}

export interface NowActivity {
  seq: number;
  tool: string;
  file: string | null;
  session_id: string | null;
  ts: number;
}

// The single "do this next" recommendation as a structured action (not a rendered string): each
// surface phrases it in its own idiom. `command` is a copy-pasteable shell line, or null (clean,
// or a fork with no recorded remedy).
export interface NextAction {
  kind: "resolve_fork" | "resume_plan" | "save" | "review" | "clean";
  command: string | null;
  target: string | null;
  label: string;
}

// A plan being actively built. Deliberately not part of `needs_you`: an agent making progress
// needs nothing from the developer, it just needs to be visible. Before this existed, only
// *stalled* plans surfaced, so a working agent was invisible until it had been quiet for an hour.
export interface NowInProgressPlan {
  session_id: string;
  claude_session_id: string | null;
  matched_count: number;
  step_count: number;
  pending_count: number;
  current_title: string | null;
}

// The current task in the developer's own words, derived from the prompt hook rather than declared
// via `ugh plan intake` -- the common way to work (Claude Code plan mode, a planning plugin) never
// calls intake, so nothing else supplies it. `source` distinguishes a stated plan step from a raw
// prompt, because the two carry different amounts of deliberation and blurring them would make the
// surface look like it knows more than it does. Null when nothing has been asked for.
export interface NowWorkingOn {
  title: string;
  full_title: string;
  source: "plan" | "prompt";
  ts: number | null;
  session_id: string | null;
}

export interface NowView {
  working_on: NowWorkingOn | null;
  in_flight: { affected: NowInFlightRow[]; new_work_count: number; total_op_count: number };
  in_progress: NowInProgressPlan[];
  needs_you: { forks: ForkRecord[]; reviews: NowReview[]; stalled_plans: NowStalledPlan[] };
  recently_done: HistoryCommit[];
  context: { turns: { text: string; actor: string; ts: number }[]; activity: NowActivity[] };
  next_action: NextAction;
}
