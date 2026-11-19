// The decisions this extension makes about the CLI that are pure functions of their inputs: which
// argv each verb needs, and what a failed run said. They live apart from `ugh.ts`, which imports
// `vscode`, so `cliSeam.test.ts` can run them under plain `node --test`.
//
// Every argv puts `--json` and the flags before a `--`, so an address or a label that starts with
// a dash is never read as a flag.

export type Verb = "revert" | "restore";

/** A queued read superseded by a newer one of its kind before its turn came. Nothing ran. */
export class StaleRequestError extends Error {
  constructor() {
    super("superseded");
  }
}
export type Keep = "theirs" | "ours" | "apply";

const opt = (flag: string, value: string | undefined): string[] => (value ? [flag, value] : []);
const where = (remote?: string, branch?: string): string[] =>
  remote ? ["--", remote, ...(branch ? [branch] : [])] : [];

export const args = {
  log: () => ["log", "--json"],
  status: () => ["status", "--json"],
  show: (address: string) => ["show", "--json", "--", address],
  find: (query: string) => ["find", "--json", "--", query],
  /** `--emit` is the dry run: it plans and writes nothing. */
  preview: (verb: Verb, addresses: string[], whole = false) =>
    [verb, "--emit", "--json", ...(whole && verb === "revert" ? ["--whole"] : []), "--", ...addresses],
  /** `--yes` applies without the tty's [y/N]; the click that sent this was the confirmation. */
  apply: (verb: Verb, addresses: string[], whole = false) =>
    [verb, "--yes", "--json", ...(whole && verb === "revert" ? ["--whole"] : []), "--", ...addresses],
  /** The previewed tree written onto `dir`, for the render panel. */
  emitTo: (verb: Verb, address: string, dir: string) =>
    [verb, "--emit", "--json", "--out", dir, "--", address],
  save: (message?: string, label?: string) =>
    ["save", "--json", ...opt("-m", message), ...opt("--as", label)],
  name: (address: string, label: string) => ["name", "--json", "--", address, label],
  rename: (label: string, next: string) => ["rename", "--json", "--", label, next],
  undoPreview: () => ["undo", "--emit", "--json"],
  undo: () => ["undo", "--json"],
  versions: (address: string) => ["resolve", "--json", "--", address],
  resolve: (address: string, keep: Keep) => ["resolve", `--${keep}`, "--json", "--", address],
  sync: (remote?: string, branch?: string) => ["sync", "--json", ...where(remote, branch)],
  push: (remote?: string, branch?: string) => ["push", "--json", ...where(remote, branch)],
  init: () => ["init", "--json"],
};

/** A rejected `promisify(execFile)` call. Only the fields we read. */
export interface ExecFailure {
  killed?: boolean;
  code?: string | number | null;
  stdout?: string;
  stderr?: string;
  message?: string;
}

/** stdout as a JSON object, or undefined. A refusal under `--json` still prints one, and exits 1 or 2. */
export function jsonOf(stdout: string | undefined): Record<string, unknown> | undefined {
  try {
    const o = JSON.parse(stdout || "");
    return o && typeof o === "object" && !Array.isArray(o) ? o : undefined;
  } catch {
    return undefined;
  }
}

/** The one line a JSON refusal carries: `_fail_json` puts it in `error`, a refused verb in `message`. */
export function refusal(o: Record<string, unknown>): string {
  return String(o.error ?? o.message ?? "refused").trim();
}

/** True when we never reached the CLI at all, so the fix is the configured path, not the command. */
export function isSpawnFailure(err: ExecFailure): boolean {
  return (
    !err.killed &&
    !(err.stderr || "").trim() &&
    ["ENOENT", "ENOTDIR", "EACCES"].includes(String(err.code))
  );
}

const tail = (s: string) => s.split("\n").filter((l) => l.trim()).slice(-12).join("\n").trim();

/**
 * What to tell the user about a run that failed: the JSON refusal's line, else the end of stderr
 * (where dispatch errors go), else of stdout. A spawn failure and a timeout leave both streams
 * empty, so the errno or the budget is the story.
 */
export function failureDetail(err: ExecFailure, bin: string, timeout: number): string {
  if (err.killed) return `ugh did not answer within ${Math.round(timeout / 1000)}s`;
  if (isSpawnFailure(err)) return `could not run '${bin}' (${err.code})`;
  const json = jsonOf(err.stdout);
  if (json) return refusal(json);
  return tail(err.stderr || "") || tail(err.stdout || "") || err.message || "failed with no output";
}
