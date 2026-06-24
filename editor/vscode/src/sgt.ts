// The single seam to the `sgt` CLI. Every read goes through `--json`; mutations return the
// human report. We shell out per call (stateless, mirrors the CLI surface) and cache reads
// keyed by the .sgt mtime so cursor moves don't re-spawn the process needlessly.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import * as vscode from "vscode";
import { BlameView, DecisionGraphView, EmitView, GraphView, StatusView } from "./types";

const pExecFile = promisify(execFile);

export class Sgt {
  private out: vscode.OutputChannel;
  constructor(private repoRoot: string, out: vscode.OutputChannel) {
    this.out = out;
  }

  private bin(): string {
    return vscode.workspace.getConfiguration("sgt").get<string>("path", "sgt");
  }

  private async run(args: string[]): Promise<string> {
    try {
      const { stdout } = await pExecFile(this.bin(), args, {
        cwd: this.repoRoot,
        maxBuffer: 32 * 1024 * 1024,
        timeout: 30_000,
        env: process.env,
      });
      return stdout;
    } catch (err: any) {
      const detail = (err.stderr || "").trim() || err.message;
      this.out.appendLine(`sgt ${args.join(" ")} failed: ${detail}`);
      throw new Error(detail);
    }
  }

  private async json<T>(args: string[]): Promise<T> {
    const stdout = await this.run(args);
    return JSON.parse(stdout) as T;
  }

  // The webview/tree consume the full payload (per-node effects + witness), so we always read
  // `export`; the lighter `graph --json` shape exists for the CLI, not for us.
  export(): Promise<GraphView> {
    return this.json<GraphView>(["export"]);
  }

  blame(file: string): Promise<BlameView> {
    return this.json<BlameView>(["blame", "--json", file]);
  }

  status(): Promise<StatusView> {
    return this.json<StatusView>(["status", "--json"]);
  }

  // The decision DAG: decisions, lifecycle + derived builds-on edges, clashes, and the frontier.
  decisions(): Promise<DecisionGraphView> {
    return this.json<DecisionGraphView>(["decisions", "--json"]);
  }

  emit(action: "revert" | "switch", ref: string, on?: boolean): Promise<EmitView> {
    const args = ["emit", action, ref];
    if (action === "switch") {
      args.push(on ? "on" : "off");
    }
    return this.json<EmitView>(args);
  }

  // Mutations return the human report; surface it verbatim.
  async mutate(args: string[]): Promise<string> {
    return this.run(args);
  }
}

/** The workspace folder that contains a `.sgt/` store, or undefined. */
export async function findSgtRoot(): Promise<string | undefined> {
  const folders = vscode.workspace.workspaceFolders ?? [];
  for (const f of folders) {
    const marker = vscode.Uri.joinPath(f.uri, ".sgt", "graph.json");
    try {
      await vscode.workspace.fs.stat(marker);
      return f.uri.fsPath;
    } catch {
      // not here
    }
  }
  return undefined;
}
