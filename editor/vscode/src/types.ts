// The JSON the `ugh` CLI prints under `--json` (`ugh/api.py`, `ugh/core/planner.py`), written from
// the real output in `dev/fixtures/`. Every step carries its address.

export interface Step {
  address: string;
  kind: "create" | "change" | "delete";
  name: string | null;
  out: boolean;
}

export interface SaveRow {
  sha: string;
  subject: string;
  date: string | null;
}

export interface Save extends SaveRow {
  steps: Step[];
}

/** `ugh log --json`: the saves, newest first. ugh's own commits (take-outs, resolves) are not saves. */
export interface LogView {
  saves: Save[];
}

export interface Observation {
  verdict: "pass" | "fail";
  lines_recorded: boolean;
}

export interface Choice {
  address: string;
  unit: string;
  merge: string;
}

export interface PlanState {
  baseline: string | null;
  names: { name: string; saves: SaveRow[] }[];
  other: Step[];
}

/** `ugh status --json`. `observed` is null when the project configures no check. */
export interface StatusView {
  files: number;
  symbols: number;
  coverage_fraction: number;
  observed: { head: Observation | null } | null;
  drift: { any: boolean; paths: string[] };
  choices: Choice[];
  sync_status: { complete: boolean };
  plan: PlanState | null;
}

export interface PlanStep {
  address: string;
  save: string;
  subject: string;
  unit: string;
  ran: boolean | null;
}

export interface HeldStep extends PlanStep {
  reason: "uses" | "changed_later" | "same_lines" | "check";
  holders: { address: string; save: string; subject: string }[];
  now?: string | null;
  would?: string | null;
}

export interface CheckVerdict {
  judge: "parser" | "check";
  passed: boolean;
  rounds?: number;
  output?: string;
}

export interface Counts {
  added: number;
  removed: number;
}

/** `head` before apply; after it, the output's change and whether it reverses the saves' own. */
export interface PlanObserved {
  head: Observation | null;
  delta?: Counts | null;
  saves_delta?: Counts | null;
  reverses?: boolean | null;
  not_recorded?: string;
}

/** The planner's answer: `revert`/`restore` with `--emit` (a preview) or `--yes` (applied). */
export interface Plan {
  ok: boolean;
  applied: boolean;
  verb: "revert" | "restore";
  target: string;
  message: string;
  undone: PlanStep[];
  held: HeldStep[];
  removal_set: { saves: { sha: string; subject: string; steps: number }[]; steps: number } | null;
  files: Record<string, { before: string | null; after: string | null }>;
  check: "not_run" | CheckVerdict;
  observed: PlanObserved | null;
  out?: { ok: boolean; path: string; written: number; deleted: number; op_count: number };
}

export interface DetailStep extends Step {
  unit: string;
  before: string | null;
  after: string | null;
  /** First and last line (1-based) the unit spans at HEAD; null when it is not in HEAD's tree. */
  lines: [number, number] | null;
}

export interface ShowSave extends SaveRow {
  ask: { text: string } | null;
  observed: (Observation & { delta: Counts | null }) | null;
}

/** `ugh show <address> --json`. A refused address comes back `ok: false` with the nearest hits. */
export type ShowView =
  | { ok: true; address: string; steps: DetailStep[]; saves: ShowSave[]; plan: Plan }
  | { ok: false; address: string; message: string; hits?: string[] };

export interface FindHit {
  address: string;
  matched: "name" | "definition" | "subject" | "ask";
  text: string;
}

export interface FindView {
  query: string;
  hits: FindHit[];
}

/** `ugh undo --emit --json`. */
export interface UndoPreview {
  applied: false;
  kind: "commit" | "names" | null;
  ok: boolean;
  message: string;
  undone: number;
  held: number;
  steps: string[];
}

export interface SaveResult {
  ok: boolean;
  saved?: boolean;
  commit?: string;
  message?: string;
  error?: string;
  steps?: (Step & { ran: boolean | null })[];
  observed?: (Observation & { delta: Counts | null }) | { not_recorded: string } | null;
}

export interface ResolveVersions {
  ok: boolean;
  choices: { address: string; merge: string; ours: string | null; theirs: string | null }[];
}

export interface SyncResult {
  ok: boolean;
  remote: string;
  branch: string;
  message: string;
  head: string;
  choices: string[];
  name_conflicts: { address: string; ours: string; theirs: string }[];
}

export interface PushResult {
  ok: boolean;
  remote?: string;
  branch?: string;
  pushed_sha?: string;
  error?: string;
  rejected?: boolean;
  remedy?: string;
}

/** What the store holds and the workbench receives as `{type: "state", ...}`. */
export interface Snapshot {
  log: LogView | null;
  status: StatusView | null;
  uninitialized?: true;
  error?: string;
}
