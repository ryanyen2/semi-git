// The two decisions this extension makes about the CLI that are pure functions of their inputs:
// which argv a mutation needs, and what a failed run actually said. Both lived inline in `Sgt` and
// both were wrong for eight months (F125, F126) with nothing that could have caught it, because
// `sgt.ts` imports `vscode` and so cannot be loaded outside the extension host. They live here so
// `cliSeam.test.ts` can exercise them under plain `node --test`.

/** A rejected `promisify(execFile)` call. Only the fields we read. */
export interface ExecFailure {
  killed?: boolean;
  code?: string | number;
  stdout?: string;
  stderr?: string;
  message?: string;
}

/**
 * F125. `revert` and `restore` gate on a `[y/N]` prompt and refuse outright (exit 2) when stdin is
 * not a tty, which `execFile` never gives them. The modal the user already clicked *is* the
 * confirmation -- the tty gate exists for invocations nobody watched, which these are not -- so the
 * flag is supplied centrally rather than at each of the six call sites, two of which arrive through
 * `applyMutation` where a per-site flag would drift. Only these two verbs accept it; passing it to
 * `init` or `feature rename` would be a parse error.
 */
export function mutationArgs(args: string[]): string[] {
  const gated = args[0] === "revert" || args[0] === "restore";
  return gated && !args.includes("--yes") ? [...args, "--yes"] : args;
}

const tail = (s: string) => s.split("\n").filter((l) => l.trim()).slice(-12).join("\n").trim();

// A failing `--json` read puts its explanation in a field rather than in prose, so tailing that
// stdout would show the user the closing dozen lines of a JSON object. Read the field instead:
// `_fail_json` refusals carry `error`, a verb view that came back not-ok carries `message`.
const fromJson = (s: string): string => {
  try {
    const o = JSON.parse(s);
    return String((o as { error?: unknown; message?: unknown })?.error ?? (o as { message?: unknown })?.message ?? "").trim();
  } catch {
    return "";
  }
};

/**
 * True when we never reached the CLI at all. Kept separate from the message because the caller acts
 * on it (it offers to fix the configured path), and sniffing the message string for that would break
 * the next time the wording changes.
 */
export function isSpawnFailure(err: ExecFailure): boolean {
  return (
    !err.killed &&
    !(err.stderr || "").trim() &&
    ["ENOENT", "ENOTDIR", "EACCES"].includes(String(err.code))
  );
}

/**
 * What to tell the user about a run that failed.
 *
 * F126. The CLI splits its failures across both streams: dispatch errors (unknown verb, a flag in
 * the verb slot, not-a-git-repository) go to stderr, but every *semantic* refusal -- `✗ symbol not
 * live in the ideal`, the dirty-tree guard, `switch`'s unsaved edits, `restore`'s two-live-versions
 * -- is printed by the shared `_fail` on stdout with a non-zero exit. Reading stderr alone therefore
 * threw away the entire explanation and left `message`, which says `Command failed: <the argv we
 * already knew>`, so every refusal in this extension looked identical and said nothing.
 *
 * A spawn-level failure is kept distinct because we never reached the CLI at all, neither stream has
 * anything, and the errno is the whole story; conflating it with a CLI that ran and refused is what
 * makes "the sidebar is empty" hard to diagnose.
 */
export function failureDetail(err: ExecFailure, bin: string, timeout: number): string {
  if (err.killed) {
    return `timed out after ${timeout}ms (mining/rebuild likely still in progress -- try again once it finishes)`;
  }
  if (isSpawnFailure(err)) return `could not run the sgt CLI at '${bin}' (${err.code})`;
  return (
    tail(err.stderr || "") ||
    fromJson(err.stdout || "") ||
    tail(err.stdout || "") ||
    err.message ||
    "failed with no output"
  );
}
