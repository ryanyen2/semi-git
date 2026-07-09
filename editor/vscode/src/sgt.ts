// The single seam to the `sgt` CLI. Every read goes through `--json`; mutations return the
// human report. We shell out per call (stateless, mirrors the CLI surface); the `Store` layers a
// read-cache on top and invalidates it after every mutation.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import * as vscode from "vscode";
import {
  BlameView,
  EmitView,
  MapView,
  MergeResult,
  MoveResult,
  RenameResult,
  SplitApplyResult,
  SplitPreviewResult,
  StatusView,
} from "./types";

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

  // The feature tree (rebuilds it first — clustering, Greene identity, pins, labeling — then
  // reads the kernel-backed projection, same as `sgt map --json` on the command line).
  map(): Promise<MapView> {
    return this.json<MapView>(["map", "--json"]);
  }

  blame(file: string): Promise<BlameView> {
    return this.json<BlameView>(["blame", "--json", file]);
  }

  status(): Promise<StatusView> {
    return this.json<StatusView>(["status", "--json"]);
  }

  // Structured dry-run of a feature revert: `sgt revert <feature> --emit --json`.
  emit(feature: string): Promise<EmitView> {
    return this.json<EmitView>(["revert", feature, "--emit", "--json"]);
  }

  merge(survivorId: string, absorbedId: string): Promise<MergeResult> {
    return this.json<MergeResult>(["merge", "--json", survivorId, absorbedId]);
  }

  splitPreview(featureId: string): Promise<SplitPreviewResult> {
    return this.json<SplitPreviewResult>(["split", "--json", featureId]);
  }

  splitApply(featureId: string): Promise<SplitApplyResult> {
    return this.json<SplitApplyResult>(["split", "--json", featureId, "--apply"]);
  }

  rename(featureId: string, newLabel: string): Promise<RenameResult> {
    return this.json<RenameResult>(["rename", "--json", featureId, newLabel]);
  }

  move(opIds: string[], targetFeatureId: string): Promise<MoveResult> {
    return this.json<MoveResult>(["move", "--json", ...opIds, "--to", targetFeatureId]);
  }

  // Mutations return the human report; surface it verbatim.
  async mutate(args: string[]): Promise<string> {
    return this.run(args);
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
