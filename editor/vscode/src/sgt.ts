// The single seam to the `sgt` CLI. Every read goes through `--json`; mutations return the
// human report. We shell out per call (stateless, mirrors the CLI surface); the `Store` layers a
// read-cache on top and invalidates it after every mutation.

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import * as vscode from "vscode";
import {
  BlameView,
  ComposeView,
  DriftView,
  EmitView,
  FeatureVerbPreview,
  FoldView,
  ForkDetailView,
  ForksView,
  FulfillResult,
  HistoryView,
  LandCandidateResult,
  LandReport,
  MapView,
  MergeResult,
  MoveResult,
  PlanView,
  ProposalReviewView,
  ProposalView,
  PublishResult,
  RenameResult,
  ReviewAckResult,
  RewriteDraft,
  SaveResult,
  SessionsView,
  SplitApplyResult,
  SplitPreviewResult,
  StatusView,
  SwitchResult,
  SyncReport,
  UndoResult,
} from "./types";

// A frontier spec for `sgt fold --at <spec>` (inspect.py's `_parse_at`): a commit-index position
// on `history_view`'s axis, an explicit op-id set, or a ref name (branch, HEAD, tag, ...).
export type FoldFrontier =
  | { commitIndex: number }
  | { opIds: string[] }
  | { ref: string };

function foldAtSpec(frontier: FoldFrontier): string {
  if ("commitIndex" in frontier) return String(frontier.commitIndex);
  if ("opIds" in frontier) return `op:${frontier.opIds.join(",")}`;
  return frontier.ref;
}

const pExecFile = promisify(execFile);

export class Sgt {
  private out: vscode.OutputChannel;
  constructor(private repoRoot: string, out: vscode.OutputChannel) {
    this.out = out;
  }

  private bin(): string {
    return vscode.workspace.getConfiguration("sgt").get<string>("path", "sgt");
  }

  // `sync`/`land`/`push` shell out to real git network I/O and CAS retry loops, well past the
  // default 30s budget for a local read.
  private async run(args: string[], timeout = 30_000): Promise<string> {
    try {
      const { stdout } = await pExecFile(this.bin(), args, {
        cwd: this.repoRoot,
        maxBuffer: 32 * 1024 * 1024,
        timeout,
        env: process.env,
      });
      return stdout;
    } catch (err: any) {
      const detail = (err.stderr || "").trim() || err.message;
      this.out.appendLine(`sgt ${args.join(" ")} failed: ${detail}`);
      throw new Error(detail);
    }
  }

  private async json<T>(args: string[], timeout = 30_000): Promise<T> {
    const stdout = await this.run(args, timeout);
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

  // Active plan sessions + the pure checkpoint preview (plan U14). A read, not a rebuild —
  // mine-on-contact only, same as `status()`.
  planStatus(): Promise<PlanView> {
    return this.json<PlanView>(["plan", "status", "--json"]);
  }

  // Ops mined that no active plan predicted (plan U14).
  drift(): Promise<DriftView> {
    return this.json<DriftView>(["drift", "--json"]);
  }

  // The feature-map webview's shared commit-index axis: mined commits in order + every op's
  // kind/feature/commit-index.
  history(): Promise<HistoryView> {
    return this.json<HistoryView>(["history", "--json"]);
  }

  // A side-effect-free preview of a feature verb or feature-grouped revert (the feature-map
  // webview's hover-preview primitive). `args` mirrors the mutating command's own usage, except
  // `move`'s target is passed as the trailing element of `args` rather than after `--to` -- this
  // method reshapes that one case onto the CLI's `--to` flag.
  previewVerb(verb: string, args: string[]): Promise<FeatureVerbPreview> {
    if (verb === "move") {
      const target = args[args.length - 1];
      const opIds = args.slice(0, -1);
      return this.json<FeatureVerbPreview>(["preview", "move", ...opIds, "--to", target, "--json"]);
    }
    return this.json<FeatureVerbPreview>(["preview", verb, ...args, "--json"]);
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

  // One aggregate refresh -- map+history+status+forks+plan+drift+sessions+trust+oracle_verdict+
  // proposals in a single shell-out (the workbench's primary poll, plan API addition #1).
  compose(): Promise<ComposeView> {
    return this.json<ComposeView>(["compose", "--json"]);
  }

  // A side-effect-free fold of an arbitrary frontier -- the draggable-playhead primitive (API
  // addition #2). Never materializes the working tree.
  foldAt(frontier: FoldFrontier): Promise<FoldView> {
    return this.json<FoldView>(["fold", "--at", foldAtSpec(frontier), "--json"]);
  }

  forksView(): Promise<ForksView> {
    return this.json<ForksView>(["forks", "--json"]);
  }

  forkDetail(symbol: string): Promise<ForkDetailView> {
    return this.json<ForkDetailView>(["forks", symbol, "--json"]);
  }

  // The fork-resolution wizard (plan Phase 6): draft a reconciling hollow, then, once the
  // working tree is hand-edited to match, stage it and commit. `sgt merge-op` is AE2's
  // refusal turned into a draft; `fulfill`/bare `land` are U11's stage/commit split.
  mergeOp(tipA: string, tipB: string, intent?: string): Promise<RewriteDraft> {
    const args = ["merge-op", tipA, tipB, "--json"];
    if (intent) args.push("--intent", intent);
    return this.json<RewriteDraft>(args);
  }

  fulfillDraft(draftId: string): Promise<FulfillResult> {
    return this.json<FulfillResult>(["fulfill", draftId, "--from-tree", "--json"]);
  }

  landCandidate(message?: string): Promise<LandCandidateResult> {
    const args = ["land", "--json"];
    if (message) args.push("--message", message);
    return this.json<LandCandidateResult>(args);
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

  // Dequeues an op-set from `trust_view` (plan U31, S7). `--json` before the variadic `op_ids`
  // so argparse doesn't try to swallow it into that list.
  reviewAck(opIds: string[], note?: string): Promise<ReviewAckResult> {
    const args = ["review-queue", "ack", "--json", ...(note ? ["--note", note] : []), ...opIds];
    return this.json<ReviewAckResult>(args);
  }

  // Partial-accept review + CAS advance (plan U24/C10/U32). `--json` before `--subset` for the
  // same reason as `reviewAck`.
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

  // Mutations return the human report; surface it verbatim.
  async mutate(args: string[], timeout = 30_000): Promise<string> {
    return this.run(args, timeout);
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
