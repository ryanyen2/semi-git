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

export interface MapView {
  nodes: MapNode[];
  roots: string[];
  identity_events: IdentityEvent[];
  feature_count: number;
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
