// The one seam to the `ugh` CLI. Every call passes `--json`, runs in the repo root, and goes through
// one queue: each verb mines on contact under the store's lock, so two at once only wait on each
// other. A queued read can say it is no longer wanted, and is then dropped before it spawns.

import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { promisify } from "node:util";
import * as vscode from "vscode";
import { args, failureDetail, isSpawnFailure, jsonOf, Keep, refusal, StaleRequestError, Verb } from "./cliSeam";
import {
  FindView,
  LogView,
  Plan,
  PushResult,
  ResolveVersions,
  SaveResult,
  ShowView,
  StatusView,
  SyncResult,
  UndoPreview,
} from "./types";

const pExecFile = promisify(execFile);

const READ_TIMEOUT = 120_000;

interface CallOptions {
  stillWanted?: () => boolean;
  /** Mutating verbs carry no timeout: killing an apply mid-write would leave the files half done. */
  busy?: string;
}

function isExecutable(candidate: string): boolean {
  try {
    if (!fs.statSync(candidate).isFile()) return false;
    fs.accessSync(candidate, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

// Our own PATH walk, so a malformed PATH entry reads as "not found" rather than `spawn ENOTDIR`.
function whichUgh(): string | undefined {
  const names = process.platform === "win32" ? ["ugh.exe", "ugh.cmd", "ugh"] : ["ugh"];
  for (const dir of (process.env.PATH ?? "").split(path.delimiter)) {
    if (!dir) continue;
    for (const name of names) {
      const candidate = path.join(dir, name);
      if (isExecutable(candidate)) return candidate;
    }
  }
  return undefined;
}

export class Ugh implements vscode.Disposable {
  private queue: Promise<unknown> = Promise.resolve();
  private resolvedBin: string | undefined;
  private notifiedMissing = false;
  private busy = new vscode.EventEmitter<string | null>();
  private mutating = 0;
  /** Fires the verb when a mutating call starts, and null once none is left. */
  readonly onBusy = this.busy.event;
  private config: vscode.Disposable;

  constructor(private readonly root: string, private readonly out: vscode.OutputChannel) {
    this.config = vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("ugh.path")) this.resolvedBin = undefined;
    });
  }

  // An explicit `ugh.path`, then PATH, then a virtualenv in the repo. A VS Code started from the
  // Dock has the login shell's PATH, not the terminal's, which is why `ugh init --agent` writes
  // `ugh.path`.
  private bin(): string {
    if (this.resolvedBin) return this.resolvedBin;
    const configured = vscode.workspace.getConfiguration("ugh").get<string>("path", "ugh") || "ugh";
    const candidates = [
      configured === "ugh" ? undefined : configured,
      whichUgh(),
      path.join(this.root, ".venv", "bin", "ugh"),
    ];
    this.resolvedBin = candidates.find((c) => c && isExecutable(c)) ?? configured;
    return this.resolvedBin;
  }

  private reportMissingCli(code: string): void {
    if (this.notifiedMissing) return;
    this.notifiedMissing = true;
    this.out.appendLine(`could not run '${this.bin()}' (${code}). PATH: ${process.env.PATH ?? ""}`);
    void vscode.window
      .showErrorMessage(`Cannot run ugh at '${this.bin()}'.`, "Set ugh.path")
      .then((choice) => {
        if (choice) void vscode.commands.executeCommand("workbench.action.openSettings", "ugh.path");
      });
  }

  /** The JSON a run printed, whatever it exited with; a run that printed none throws its detail. */
  private call(argv: string[], opts: CallOptions = {}): Promise<Record<string, unknown>> {
    if (opts.busy) {
      this.mutating++;
      this.busy.fire(opts.busy);
    }
    const task = this.queue.catch(() => undefined).then(() => {
      if (opts.stillWanted && !opts.stillWanted()) throw new StaleRequestError();
      return this.exec(argv, opts.busy ? 0 : READ_TIMEOUT);
    });
    this.queue = task;
    if (opts.busy) {
      const done = () => {
        if (--this.mutating === 0) this.busy.fire(null);
      };
      void task.then(done, done);
    }
    return task;
  }

  private async exec(argv: string[], timeout: number): Promise<Record<string, unknown>> {
    const bin = this.bin();
    try {
      const { stdout } = await pExecFile(bin, argv, {
        cwd: this.root,
        maxBuffer: 64 * 1024 * 1024,
        timeout,
        env: process.env,
      });
      const json = jsonOf(stdout);
      if (json) return json;
      throw Object.assign(new Error("not JSON"), { stdout, stderr: "", code: 0 });
    } catch (err: any) {
      const json = jsonOf(err.stdout);
      if (json && !err.killed) return json;
      const detail = failureDetail(err, bin, timeout);
      this.out.appendLine(
        `ugh ${argv.join(" ")} failed (exit ${err.code}): ${detail}` +
          (err.stdout ? `\n--- stdout ---\n${err.stdout}` : "") +
          (err.stderr ? `\n--- stderr ---\n${err.stderr}` : ""),
      );
      if (isSpawnFailure(err)) this.reportMissingCli(String(err.code));
      throw new Error(detail);
    }
  }

  /** A read: a refusal throws its one line. */
  private async read<T>(argv: string[], opts: CallOptions = {}): Promise<T> {
    const json = await this.call(argv, opts);
    if (json.ok === false) throw new Error(refusal(json));
    return json as T;
  }

  /** A planner answer, applied or not. A refusal with no plan (an address, a dirty tree) throws. */
  private async plan(argv: string[], opts: CallOptions = {}): Promise<Plan> {
    const json = await this.call(argv, opts);
    if (!Array.isArray(json.undone)) throw new Error(refusal(json));
    return json as unknown as Plan;
  }

  log(): Promise<LogView> {
    return this.read(args.log());
  }

  status(): Promise<StatusView> {
    return this.read(args.status());
  }

  /** Returned as printed: a refused address is `ok: false` with its message. */
  async show(address: string, stillWanted?: () => boolean): Promise<ShowView> {
    return (await this.call(args.show(address), { stillWanted })) as unknown as ShowView;
  }

  find(query: string, stillWanted?: () => boolean): Promise<FindView> {
    return this.read(args.find(query), { stillWanted });
  }

  preview(verb: Verb, addresses: string[], whole = false, stillWanted?: () => boolean): Promise<Plan> {
    return this.plan(args.preview(verb, addresses, whole), { stillWanted });
  }

  apply(verb: Verb, addresses: string[], whole = false): Promise<Plan> {
    return this.plan(args.apply(verb, addresses, whole), { busy: verb });
  }

  emitTo(verb: Verb, address: string, dir: string, stillWanted?: () => boolean): Promise<Plan> {
    return this.plan(args.emitTo(verb, address, dir), { stillWanted });
  }

  async save(message?: string, label?: string): Promise<SaveResult> {
    return (await this.call(args.save(message, label), { busy: "save" })) as unknown as SaveResult;
  }

  name(address: string, label: string): Promise<{ ok: true; name: string; steps: unknown[] }> {
    return this.read(args.name(address, label), { busy: "name" });
  }

  rename(label: string, next: string): Promise<{ ok: true; steps: number; merged: boolean }> {
    return this.read(args.rename(label, next), { busy: "rename" });
  }

  undoPreview(): Promise<UndoPreview> {
    return this.read(args.undoPreview());
  }

  /** `ok: false` with a `message` when the undo was refused. */
  async undo(): Promise<{ ok: boolean; undone?: boolean; message?: string; error?: string }> {
    return (await this.call(args.undo(), { busy: "undo" })) as { ok: boolean; message?: string; error?: string };
  }

  versions(address: string): Promise<ResolveVersions> {
    return this.read(args.versions(address));
  }

  resolve(address: string, keep: Keep): Promise<{ ok: true; commit: string; resolved: string[] }> {
    return this.read(args.resolve(address, keep), { busy: "resolve" });
  }

  sync(remote?: string, branch?: string): Promise<SyncResult> {
    return this.read(args.sync(remote, branch), { busy: "sync" });
  }

  /** A rejected push is `ok: false` with `rejected` and the `remedy`, returned rather than thrown. */
  async push(remote?: string, branch?: string): Promise<PushResult> {
    return (await this.call(args.push(remote, branch), { busy: "push" })) as unknown as PushResult;
  }

  async init(): Promise<void> {
    const json = await this.call(args.init(), { busy: "init" });
    if (json.ok !== true) throw new Error(refusal(json));
  }

  dispose(): void {
    this.busy.dispose();
    this.config.dispose();
  }
}
