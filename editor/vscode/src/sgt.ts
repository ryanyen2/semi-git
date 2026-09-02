// The single seam to the `sgt` CLI. Every read goes through `--json`; mutations return the
// human report. We shell out per call (stateless, mirrors the CLI surface); the `Store` layers a
// read-cache on top and invalidates it after every mutation.

import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { promisify } from "node:util";
import * as vscode from "vscode";
import { failureDetail, isSpawnFailure, mutationArgs } from "./cliSeam";
import {
  BlameView,
  ComposeView,
  EmitView,
  FeatureVerbPreview,
  FindView,
  FoldView,
  ForkDetailView,
  ForksView,
  FulfillResult,
  GridView,
  HistoryView,
  IntentView,
  LandCandidateResult,
  UnstageResult,
  LandReport,
  MapView,
  MergeResult,
  MoveResult,
  NowView,
  PlanView,
  ProposalReviewView,
  ProposalView,
  PublishResult,
  RenameResult,
  RewriteDraft,
  SaveResult,
  SelectionView,
  SessionsView,
  SplitApplyResult,
  SplitPreviewResult,
  StatusView,
  SwitchResult,
  SyncReport,
  UndoPreview,
  UndoResult,
} from "./types";

// A frontier spec for `sgt fold --at <spec>` (inspect.py's `_parse_at`): the current ideal, a
// commit-index position on `history_view`'s axis, an explicit op-id set, or a ref name (branch,
// HEAD, tag, ...).
//
// `{ current: true }` is the present, and is NOT interchangeable with `{ ref: "HEAD" }`. A ref
// folds the ops whose provenance sits in that ref's commit ancestry, which structurally cannot
// see a locally-applied revert -- `apply` mints its compensating ops with empty provenance -- so
// after `sgt revert <f> --yes` a HEAD fold returns the pre-revert tree. A panel showing "the code
// as it is now" must ask for `current`.
export type FoldFrontier =
  | { current: true }
  | { commitIndex: number }
  | { opIds: string[] }
  | { ref: string };

export function foldAtSpec(frontier: FoldFrontier): string {
  if ("current" in frontier) return "now";
  if ("commitIndex" in frontier) return String(frontier.commitIndex);
  if ("opIds" in frontier) return `op:${frontier.opIds.join(",")}`;
  return frontier.ref;
}

const pExecFile = promisify(execFile);

/** A queued interactive read (hover preview, scrub fold, find) that was superseded by a newer one
 * before its turn on the serialized queue came up. Thrown instead of spawning the subprocess: the
 * store flock serializes every `sgt` call, so a cursor sweeping ten rows used to run ten previews
 * back-to-back, nine of them for rows the cursor had already left -- seconds of dead lag per
 * sweep. Callers that pass `stillWanted` catch this and simply do not reply (the webview's own
 * seq-guard would have dropped the stale result anyway). */
export class StaleRequestError extends Error {
  constructor() {
    super("superseded by a newer request");
    this.name = "StaleRequestError";
  }
}

/** Is this an existing, executable file? Used to vet each `sgt` candidate before spawning it. */
function isExecutable(candidate: string): boolean {
  try {
    if (!fs.statSync(candidate).isFile()) return false;
    fs.accessSync(candidate, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

// Our own PATH walk, rather than letting `execFile("sgt", ...)` do it. Two reasons. First, we can
// say *what* we looked for when it fails, instead of surfacing a bare errno. Second, macOS
// `execvp` walks PATH and reports whatever errno the last candidate produced, so a single
// malformed PATH entry -- one that names a file where a directory belongs -- turns an ordinary
// "not found" into `spawn ENOTDIR` with no command name attached. That error is impossible to act
// on. Skipping non-directory entries ourselves means "not found" always reads as not found.
function whichSgt(env: NodeJS.ProcessEnv): string | undefined {
  const names = process.platform === "win32" ? ["sgt.exe", "sgt.cmd", "sgt"] : ["sgt"];
  for (const dir of (env.PATH ?? "").split(path.delimiter)) {
    if (!dir) continue;
    for (const name of names) {
      const candidate = path.join(dir, name);
      if (isExecutable(candidate)) return candidate;
    }
  }
  return undefined;
}

// The CLI daily surface is the ~7-verb spine (`save`/`status`/`log`/`undo`/`revert`/`restore`/
// `edit`) plus the daily navigation/inspection/loop/rewrite verbs kept at the top level
// (`switch`, `diff`, `map`, `blame`, `plan`, `checkpoint`, `drift`, `commit`, `fulfill`). Only
// rare/maintenance verbs live under `advanced` (`advanced compose`, `advanced identity`, ...);
// feature-reorg is under `feature` (`feature regroup {merge,split,move}`, `feature rename`). The
// JSON projections are unchanged (additive-only), so the view types below still match; only the
// invocation path moved.
export class Sgt {
  private out: vscode.OutputChannel;
  constructor(private repoRoot: string, out: vscode.OutputChannel) {
    this.out = out;
  }

  // Resolved once per session: an explicit `sgt.path` first, then PATH, then a virtualenv in the
  // repo itself. The winner is an absolute path wherever one can be found, so nothing downstream
  // depends on the extension host having inherited a usable PATH -- a VS Code launched from the
  // Dock gets the login shell's PATH, not the terminal's, and `sgt` typically lives in a
  // uv/venv bin dir that only the terminal knows about. `sgt init --agent` writes `sgt.path`
  // for exactly this reason; this is the fallback for repos where nobody ran it.
  private resolvedBin: string | undefined;

  private bin(): string {
    if (this.resolvedBin) return this.resolvedBin;
    const configured = vscode.workspace.getConfiguration("sgt").get<string>("path", "sgt");
    const candidates = [
      configured === "sgt" ? undefined : configured, // only when explicitly set to something else
      whichSgt(process.env),
      path.join(this.repoRoot, ".venv", "bin", "sgt"),
    ];
    // Falling back to the configured value (normally the bare `sgt`) keeps the failure path
    // honest: we still try to spawn it, and the error names what we tried.
    this.resolvedBin = candidates.find((c) => c && isExecutable(c)) ?? configured;
    return this.resolvedBin;
  }

  // A CLI we cannot run breaks every panel at once. The only trace used to be one line in an
  // output channel nobody has open, so the sidebar just sat there empty looking like a bug in the
  // extension. Say it once, out loud, with the two things that actually fix it.
  private notifiedMissing = false;

  private reportMissingCli(code: string): void {
    if (this.notifiedMissing) return;
    this.notifiedMissing = true;
    this.out.appendLine(
      `could not run '${this.bin()}' (${code}). PATH as seen by the extension host:\n  ` +
        (process.env.PATH ?? "").split(path.delimiter).join("\n  ")
    );
    const setPath = "Set sgt.path";
    const help = "Installation help";
    void vscode.window
      .showErrorMessage(
        "semi-git cannot run the `sgt` command, so its panels will stay empty. Install it with " +
          "`uv tool install semi-git`, then run `sgt init --agent` in this repo to point the " +
          "extension at it.",
        setPath,
        help
      )
      .then((choice) => {
        if (choice === setPath) {
          void vscode.commands.executeCommand("workbench.action.openSettings", "sgt.path");
        } else if (choice === help) {
          void vscode.env.openExternal(
            vscode.Uri.parse("https://github.com/ryanyen2/semi-git#install")
          );
        }
      });
  }

  // Every `sgt` invocation takes the store's exclusive flock (store.py's `_locked()`) for
  // mining/rebuild work, and that flock has no timeout -- a process queued behind another
  // just blocks. Activation fires several reads at once (blame, plan/git status, compose, ...),
  // so without serializing here they race for the same lock and can get killed by the timeout
  // below before any of them produce output, surfacing as a bare "Command failed" with no
  // stderr. Running them one at a time through this queue means each gets its own fresh budget.
  private queue: Promise<unknown> = Promise.resolve();

  // Loop guard for the `.sgt/**/*.json` watcher. A *read* like `sgt map` is not side-effect-free:
  // it rebuilds and rewrites tree.json/ideal.json/label_cache.json/... under `.sgt/`. Those writes
  // trip the watcher, which invalidates the cache, which re-issues the read -- a self-sustaining
  // rebuild loop (the "sidebar spins forever" bug). We track whether one of OUR subprocesses is
  // running (or just finished), so the watcher can ignore the writes we caused. Genuinely external
  // mutations (another terminal, an agent) land while we're idle and still invalidate; our own
  // mutations invalidate explicitly in the command handlers, not via the watcher.
  private inFlight = 0;
  private lastActiveAt = 0;

  /** True while our own `sgt` subprocess is running, or within `cooldownMs` of one finishing. */
  recentlyActive(cooldownMs = 1500): boolean {
    return this.inFlight > 0 || Date.now() - this.lastActiveAt < cooldownMs;
  }

  // `sync`/`land`/`push` shell out to real git network I/O and CAS retry loops, well past the
  // default 30s budget for a local read.
  // `stillWanted` is checked when the request's turn on the queue arrives (not when it was made):
  // a superseded interactive read throws StaleRequestError instead of spawning, so a burst of
  // hover previews costs one subprocess -- the one still under the cursor -- not one per row swept.
  private async run(args: string[], timeout = 30_000, stillWanted?: () => boolean): Promise<string> {
    const task = this.queue.catch(() => undefined).then(() => {
      if (stillWanted && !stillWanted()) throw new StaleRequestError();
      return this.exec(args, timeout);
    });
    this.queue = task;
    return task;
  }

  private async exec(args: string[], timeout: number): Promise<string> {
    this.inFlight++;
    try {
      const { stdout } = await pExecFile(this.bin(), args, {
        cwd: this.repoRoot,
        maxBuffer: 32 * 1024 * 1024,
        timeout,
        env: process.env,
      });
      return stdout;
    } catch (err: any) {
      // F126. Which of the two streams the explanation is on, and how much of it to show, is
      // `failureDetail`'s decision -- it is testable there and was untested here. The channel gets
      // both streams whole, so the cap `failureDetail` applies for the modal never loses anything.
      const detail = failureDetail(err, this.bin(), timeout);
      const spawnFailed = isSpawnFailure(err);
      this.out.appendLine(
        `sgt ${args.join(" ")} failed (exit ${err.code}): ${detail}` +
          (err.stdout ? `\n--- stdout ---\n${err.stdout}` : "") +
          (err.stderr ? `\n--- stderr ---\n${err.stderr}` : ""),
      );
      if (spawnFailed) this.reportMissingCli(err.code);
      throw new Error(detail);
    } finally {
      // Stamp on completion so the watcher keeps ignoring the trailing .sgt writes (and the fs
      // event latency behind them) for a cooldown after the process exits.
      this.inFlight--;
      this.lastActiveAt = Date.now();
    }
  }

  private async json<T>(args: string[], timeout = 30_000, stillWanted?: () => boolean): Promise<T> {
    const stdout = await this.run(args, timeout, stillWanted);
    return JSON.parse(stdout) as T;
  }

  // The feature tree (rebuilds it first — clustering, Greene identity, pins, labeling — then
  // reads the kernel-backed projection). U14 folded `sgt map` onto `sgt log --tree`; `--refresh`
  // preserves the rebuild-first contract this method relies on (a cached read would return an
  // empty tree on first build).
  map(): Promise<MapView> {
    return this.json<MapView>(["log", "--tree", "--refresh", "--json"]);
  }

  // The conversation behind a selection, in full: `asked.asks` carries each captured prompt
  // verbatim, which the light per-chapter payload deliberately does not (it ships excerpts). Read
  // on demand, when a reader opens one -- so the panel stays cheap and the words stay reachable.
  asked(ref: string, stillWanted?: () => boolean): Promise<any> {
    return this.json<any>(["show", ref, "--asked", "--json"], 30_000, stillWanted);
  }

  // U14 demoted `sgt blame` under `advanced` (same handler, re-homed path).
  blame(file: string): Promise<BlameView> {
    return this.json<BlameView>(["advanced", "blame", "--json", file]);
  }

  // U14 folded `sgt status` onto `sgt log --summary` (identical status_view projection).
  status(): Promise<StatusView> {
    return this.json<StatusView>(["log", "--summary", "--json"]);
  }

  // Structured dry-run of a feature revert: `sgt revert <feature> --emit --json`. Carries the
  // U3 `frontier`/`affected` blocks the quick-pick checklist consumes.
  // `--emit` is a dry run: it computes the exact before/after and writes nothing. Both exact ideal
  // edits support it, so the verb is a parameter -- `restore` used to confirm with prose alone
  // ("this rewrites the working tree and commits") while `revert` showed the actual diff, which
  // meant the more reassuring-sounding verb was the one you could not see before running.
  emit(feature: string, verb: "revert" | "restore" = "revert", stillWanted?: () => boolean): Promise<EmitView> {
    return this.json<EmitView>([verb, feature, "--emit", "--json"], 30_000, stillWanted);
  }

  // Apply a chosen revert frontier (U3/R4). `keepOpIds` are the toggleable dependents to keep:
  // each kept `blast` dependent drafts a continuation hollow (needs `fulfill`+`commit` after),
  // each kept `carry` dependent repoints mechanically for free. An empty keep set is a plain
  // full-upset revert that commits immediately. Returns the human report.
  revertKeep(sel: string, keepOpIds: string[]): Promise<string> {
    if (keepOpIds.length === 0) return this.mutate(["revert", sel]);
    return this.mutate(["revert", sel, "--keep", keepOpIds.join(",")]);
  }

  // The union closure a multi-select induces (`sgt select <feature>... --json` → selection_view):
  // the feature ids, direct + closure op counts, and the ops pulled in from other features. Feeds
  // the workbench's multi-select "selection" card + closure paint (Stage C). A report-only read.
  select(refs: string[]): Promise<SelectionView> {
    return this.json<SelectionView>(["select", ...refs, "--json"]);
  }

  // Active plan sessions + the pure checkpoint preview (plan U14). A read, not a rebuild —
  // mine-on-contact only, same as `status()`. `--full`: `sgt.api.plan_view` is compact by
  // default (step/match counts, no spans); the webview's `PlanView` type still expects the full
  // per-step detail and per-match file spans.
  planStatus(): Promise<PlanView> {
    return this.json<PlanView>(["plan", "status", "--json", "--full"]);
  }

  // Close a stalled plan whose work landed differently than predicted: `done` keeps the record as
  // `completed` history (so `sgt revert --session` can still attribute the work); `abandon` records
  // its unfinished steps as open intents and drops the record. Both leave the "needs you" surface.
  planDone(sessionId: string): Promise<{ ok: boolean }> {
    return this.json<{ ok: boolean }>(["plan", "done", sessionId, "--json"]);
  }

  planAbandon(sessionId: string): Promise<{ ok: boolean }> {
    return this.json<{ ok: boolean }>(["plan", "abandon", sessionId, "--json"]);
  }

  // The canonical lane×commit cell join (`grid_view`, plan U1/U3): the single projection the TUI
  // and this webview both render from, so the (op -> cell) join is computed once in `sgt.api` and
  // never re-derived per surface. `sgt log --json` == `grid_view(repo)`.
  grid(): Promise<GridView> {
    return this.json<GridView>(["log", "--json"]);
  }

  // The feature-map webview's shared commit-index axis: mined commits in order + every op's
  // kind/feature/commit-index. `--full`: compact `history_view` drops the per-op `ops` list this
  // extension's `HistoryView` type expects.
  history(): Promise<HistoryView> {
    return this.json<HistoryView>(["advanced", "history", "--json", "--full"]);
  }

  // A side-effect-free preview of a feature verb or feature-grouped revert (the feature-map
  // webview's hover-preview primitive). `args` mirrors the mutating command's own usage, except
  // `move`'s target is passed as the trailing element of `args` rather than after `--to` -- this
  // method reshapes that one case onto the CLI's `--to` flag.
  previewVerb(verb: string, args: string[], stillWanted?: () => boolean): Promise<FeatureVerbPreview> {
    if (verb === "move") {
      const target = args[args.length - 1];
      const opIds = args.slice(0, -1);
      return this.json<FeatureVerbPreview>(
        ["advanced", "preview", "move", ...opIds, "--to", target, "--json"], 30_000, stillWanted,
      );
    }
    return this.json<FeatureVerbPreview>(
      ["advanced", "preview", verb, ...args, "--json"], 30_000, stillWanted,
    );
  }

  // `sgt edit <selection> [--intent ...] --json` (U4/KTD5): chain-extend the target with a hollow
  // and mechanically repoint dependents. Drafts only -- the user changes the working tree, then
  // fulfills. `--repair` (LLM fill) is deliberately not exposed here; drafting is the safe default.
  edit(selection: string, intent?: string): Promise<RewriteDraft> {
    const args = ["edit", selection, "--json"];
    if (intent) args.push("--intent", intent);
    return this.json<RewriteDraft>(args);
  }

  merge(survivorId: string, absorbedId: string): Promise<MergeResult> {
    return this.json<MergeResult>(["feature", "regroup", "merge", "--json", survivorId, absorbedId]);
  }

  splitPreview(featureId: string, stillWanted?: () => boolean): Promise<SplitPreviewResult> {
    return this.json<SplitPreviewResult>(
      ["feature", "regroup", "split", "--json", featureId], 30_000, stillWanted,
    );
  }

  splitApply(featureId: string): Promise<SplitApplyResult> {
    return this.json<SplitApplyResult>([
      "feature", "regroup", "split", "--json", featureId, "--apply",
    ]);
  }

  rename(featureId: string, newLabel: string): Promise<RenameResult> {
    return this.json<RenameResult>(["feature", "rename", "--json", featureId, newLabel]);
  }

  move(opIds: string[], targetFeatureId: string): Promise<MoveResult> {
    return this.json<MoveResult>([
      "feature", "regroup", "move", "--json", ...opIds, "--to", targetFeatureId,
    ]);
  }

  // One aggregate refresh -- map+history+status+forks+plan+drift+sessions+trust+oracle_verdict+
  // proposals in a single shell-out (the workbench's primary poll, plan API addition #1).
  // `--full` threads into compose's history/plan/drift/trust children (a safe superset of the
  // new compact defaults) so this extension's `ComposeView` type keeps matching every child's
  // actual shape. Aggregates ~9 sub-reads behind one shell-out, so a cold mine/rebuild can run
  // well past a plain read's 30s budget -- same class of slow op as `sync`/`land` below.
  compose(): Promise<ComposeView> {
    return this.json<ComposeView>(["advanced", "compose", "--json", "--full"], 120_000);
  }

  // A side-effect-free fold of an arbitrary frontier -- the draggable-playhead primitive (API
  // addition #2). Never materializes the working tree.
  foldAt(frontier: FoldFrontier, stillWanted?: () => boolean): Promise<FoldView> {
    return this.json<FoldView>(
      ["advanced", "fold", "--at", foldAtSpec(frontier), "--json"], 30_000, stillWanted,
    );
  }

  // The same fold, additionally materialized onto `outDir` -- the render panel's primitive.
  // `--out` is a *sync*, not a wipe: files that left the frontier are deleted, and anything the
  // fold does not own (a `node_modules` symlink, a dev-server cache) is left alone. That is what
  // lets a running dev server be pointed at the directory and simply re-render, instead of being
  // torn down and restarted on every scrub step.
  //
  // Routed through the same serialized queue as every other call: a scrub fires faster than a
  // fold completes, and two `sgt` processes writing the same directory would interleave.
  foldTo(frontier: FoldFrontier, outDir: string, stillWanted?: () => boolean): Promise<FoldView> {
    return this.json<FoldView>(
      ["advanced", "fold", "--at", foldAtSpec(frontier), "--out", outDir, "--json"],
      60_000, stillWanted,
    );
  }

  forksView(): Promise<ForksView> {
    return this.json<ForksView>(["advanced", "forks", "--json"]);
  }

  // The intent overlay's one canonical projection (`sgt.api.intent_view`): per-commit atoms with
  // their `feature_span` and live intent-ledger `rationale`. A read, mine-on-contact only. The
  // hover joins an atom's rationale to a feature by `feature_span` membership.
  intentView(): Promise<IntentView> {
    return this.json<IntentView>(["intent", "list", "--json"]);
  }

  // The state-of-actions surface (`sgt.api.now_view`): in-flight / needs-you / recently-done /
  // next-action, one thin assembler the Now tree reads. Mine-on-contact (the in-flight preview
  // reflects the working tree), like the `sgt now` porcelain.
  nowView(): Promise<NowView> {
    return this.json<NowView>(["now", "--json"]);
  }

  forkDetail(symbol: string): Promise<ForkDetailView> {
    return this.json<ForkDetailView>(["advanced", "forks", symbol, "--json"]);
  }

  // The fork-resolution wizard (plan Phase 6): draft a reconciling hollow, then, once the
  // working tree is hand-edited to match, stage it and commit. `sgt merge-op` is AE2's
  // refusal turned into a draft; `fulfill`/`commit` are U11's stage/commit split.
  mergeOp(tipA: string, tipB: string, intent?: string): Promise<RewriteDraft> {
    const args = ["advanced", "merge-op", tipA, tipB, "--json"];
    if (intent) args.push("--intent", intent);
    return this.json<RewriteDraft>(args);
  }

  fulfillDraft(draftId: string): Promise<FulfillResult> {
    return this.json<FulfillResult>(["advanced", "fulfill", draftId, "--from-tree", "--json"]);
  }

  landCandidate(message?: string): Promise<LandCandidateResult> {
    const args = ["advanced", "commit", "--json"];
    if (message) args.push("--message", message);
    return this.json<LandCandidateResult>(args);
  }

  // A staged candidate has exactly two exits and this is the other one, so it sits next to Land:
  // any surface that offers one and not the other leaves the user in a state whose only escape is
  // the terminal (every materializing verb refuses while a stage is live).
  abandonCandidate(): Promise<UnstageResult> {
    return this.json<UnstageResult>(["advanced", "unstage", "--json"]);
  }

  sessionsView(): Promise<SessionsView> {
    return this.json<SessionsView>(["session", "status", "--json"]);
  }

  proposalView(id: string): Promise<ProposalView> {
    return this.json<ProposalView>(["propose", "status", id, "--json"]);
  }

  proposalReviewView(id: string): Promise<ProposalReviewView> {
    return this.json<ProposalReviewView>(["propose", "status", id, "--checklist", "--json"]);
  }

  // Partial-accept review + CAS advance (plan U24/C10/U32). `--json` before `--subset` so
  // argparse doesn't swallow it into that variadic list.
  proposeLand(id: string, subset?: string[]): Promise<LandReport> {
    const args = ["propose", "land", id, "--json"];
    if (subset && subset.length) args.push("--subset", ...subset);
    return this.json<LandReport>(args, 120_000);
  }

  proposePublish(id: string, remote?: string): Promise<PublishResult> {
    const args = ["propose", "publish", id, "--json"];
    if (remote) args.push("--remote", remote);
    return this.json<PublishResult>(args, 120_000);
  }

  // The daily-loop verbs (D3), each composed from existing lens machinery -- no new kernel call.
  // `switch` is a reserved word, hence `switchBranch`.
  switchBranch(branch: string): Promise<SwitchResult> {
    return this.json<SwitchResult>(["switch", branch, "--json"]);
  }

  save(message?: string): Promise<SaveResult> {
    const args = ["save", "--json"];
    if (message) args.push("-m", message);
    return this.json<SaveResult>(args);
  }

  undo(): Promise<UndoResult> {
    return this.json<UndoResult>(["undo", "--json"]);
  }

  // What the next undo would do, without doing it. A terminal gets this for free -- `sgt undo`
  // prints it and asks -- but that gate is tty-only, and the extension never is, so the confirm
  // dialog here is the only thing between a click and a re-materialized ideal. It cannot ask the
  // question honestly without this.
  undoPreview(): Promise<UndoPreview> {
    return this.json<UndoPreview>(["undo", "--emit", "--json"]);
  }

  // git-bridge verbs: real network I/O + CAS retry loops, given the longer timeout.
  sync(remote?: string, branch?: string): Promise<SyncReport> {
    const args = ["sync", "--json"];
    if (remote) args.push(remote);
    if (branch) args.push(branch);
    return this.json<SyncReport>(args, 120_000);
  }

  land(branch: string): Promise<LandReport> {
    return this.json<LandReport>(["land", branch, "--json"], 120_000);
  }

  push(remote?: string, branch?: string): Promise<{ ok: boolean; [key: string]: unknown }> {
    const args = ["push", "--json"];
    if (remote) args.push(remote);
    if (branch) args.push(branch);
    return this.json(args, 120_000);
  }

  /** `sgt find "<phrase>" --json`: ranked features/saves/symbols. Report-only. */
  find(query: string, stillWanted?: () => boolean): Promise<FindView> {
    return this.json<FindView>(["find", query, "--json"], 60_000, stillWanted);
  }

  // Mutations return the human report; surface it verbatim. `mutationArgs` supplies the `--yes` the
  // tty gate needs (F125) -- see `cliSeam.ts` for why it belongs here and not at the call sites.
  async mutate(args: string[], timeout = 30_000): Promise<string> {
    return this.run(mutationArgs(args), timeout);
  }
}

/** The workspace folder that contains a `.sgt` store, or undefined. */
export async function findSgtRoot(): Promise<string | undefined> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  for (const f of folders) {
    // Written by `sgt init` before anything else, so it's a reliable "this is an sgt repo" marker
    // even before `sgt map` has ever built a tree.
    const marker = vscode.Uri.joinPath(f.uri, ".sgt", "local", "ideal.json");
    try {
      await vscode.workspace.fs.stat(marker);
      return f.uri.fsPath;
    } catch {
      // not here
    }
  }
  return undefined;
}
