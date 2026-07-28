// Plan review, subtle by construction: a CodeLens above matched-step/drift lines only
// (invisible everywhere else) plus a status-bar item that exists only while a plan session is
// active. No sidebar view, no gutter color, no webview — this surface speaks in glyphs and
// one-line text, leaving color.ts's hue channel reserved for feature identity as today.

import * as path from "node:path";
import * as vscode from "vscode";
import { Store } from "./store";
import { PlanView } from "./types";

const SCHEME = "sgt-plan";
const STATUS_ICON: Record<string, string> = { pending: "○", matched: "●" };

export interface PlanLensTarget {
  kind: "match" | "drift";
  title: string;
  rationale: string;
  predictedFootprint: string[];
  path: string;
  startLine: number; // 1-based inclusive
  endLine: number; // 1-based inclusive
}

// -- CodeLens: one per matched-step span and per drift span in the active file only ---------------

export class PlanCodeLensProvider implements vscode.CodeLensProvider, vscode.Disposable {
  private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;
  private disposables: vscode.Disposable[] = [];

  constructor(private store: Store) {
    this.disposables.push(
      this.store.onDidChange(() => this._onDidChangeCodeLenses.fire()),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("sgt.plan")) {
          this._onDidChangeCodeLenses.fire();
        }
      })
    );
  }

  private enabled(): boolean {
    return vscode.workspace.getConfiguration("sgt").get<boolean>("plan.enabled", true);
  }

  async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
    if (!this.enabled()) {
      return [];
    }
    let plan: PlanView;
    try {
      plan = await this.store.planView();
    } catch {
      return [];
    }
    if (plan.sessions.length === 0) {
      return []; // no active session -- render nothing (zero cost, the common case)
    }
    const rel = vscode.workspace.asRelativePath(document.uri, false);
    const lenses: vscode.CodeLens[] = [];

    for (const session of plan.sessions) {
      session.steps.forEach((step, idx) => {
        if (step.status !== "matched") {
          return;
        }
        for (const file of step.files) {
          if (file.path !== rel) {
            continue;
          }
          for (const span of file.spans) {
            lenses.push(this.lens(span.start_line, `✦ matches plan step ${idx + 1}`, {
              kind: "match", title: step.title, rationale: step.rationale,
              predictedFootprint: step.predicted_footprint,
              path: file.path, startLine: span.start_line, endLine: span.end_line,
            }));
          }
        }
      });
    }

    let drift;
    try {
      drift = await this.store.driftView();
    } catch {
      drift = { entries: [] };
    }
    for (const entry of drift.entries) {
      for (const file of entry.files) {
        if (file.path !== rel) {
          continue;
        }
        for (const span of file.spans) {
          lenses.push(this.lens(span.start_line, "◇ unplanned change (not in the active plan)", {
            kind: "drift", title: `${entry.kind}: ${entry.footprint.join(", ")}`, rationale: "",
            predictedFootprint: [], path: file.path, startLine: span.start_line, endLine: span.end_line,
          }));
        }
      }
    }
    return lenses;
  }

  private lens(startLine: number, title: string, target: PlanLensTarget): vscode.CodeLens {
    const range = new vscode.Range(startLine - 1, 0, startLine - 1, 0);
    return new vscode.CodeLens(range, { title, command: "sgt.showPlanDiff", arguments: [target] });
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
    this._onDidChangeCodeLenses.dispose();
  }
}

// -- status bar: hidden until >=1 active session ----------------------------------------------

export class PlanStatusBar implements vscode.Disposable {
  private item: vscode.StatusBarItem;
  private disposables: vscode.Disposable[] = [];

  constructor(private store: Store) {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    this.item.command = "sgt.showPlanQuickPick";
    this.disposables.push(this.item, this.store.onDidChange(() => void this.refresh()));
  }

  async refresh(): Promise<void> {
    let plan: PlanView;
    try {
      plan = await this.store.planView();
    } catch {
      this.item.hide();
      return;
    }
    if (plan.sessions.length === 0) {
      this.item.hide();
      return;
    }
    const matched = plan.sessions.reduce(
      (n, s) => n + s.steps.filter((st) => st.status === "matched").length, 0
    );
    const total = plan.sessions.reduce((n, s) => n + s.steps.length, 0);
    // One quiet aggregate: a platform codicon and matched/total, no per-step glyph run. With more
    // than one active plan (concurrent agents) lead with the plan count. Drift lives in the
    // tooltip, not as an always-visible badge -- it's a read-only diff, not a chore to clear.
    const plans = plan.sessions.length;
    const base = plans === 1
      ? `$(checklist) Plan ${matched}/${total}`
      : `$(checklist) ${plans} plans ${matched}/${total}`;
    // A stalled plan (interrupted, resumable) gets one quiet suffix -- deferential, no loud badge;
    // the click-through quick pick is where the Resume action lives.
    const stalled = plan.sessions.filter((s) => s.derived_status === "stalled");
    this.item.text = stalled.length ? `${base} · ${stalled.length} stalled` : base;
    const driftCount = plan.checkpoint.drift_op_ids.length;
    const stepLines = plan.sessions
      .flatMap((s) => s.steps.map((st) => `${STATUS_ICON[st.status] ?? "?"} ${st.title}`));
    const stalledLines = stalled.length
      ? `\n\n⏸ ${stalled.length} stalled — click to resume`
      : "";
    this.item.tooltip = stepLines.join("\n") + stalledLines +
      (driftCount ? `\n\n${driftCount} unplanned change(s)` : "");
    this.item.show();
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
  }
}

// -- diff: left = synthetic step/drift text, right = the real file at that span -----------------

export class PlanDiffProvider implements vscode.TextDocumentContentProvider, vscode.Disposable {
  private contents = new Map<string, string>();
  private seq = 0;
  private registration: vscode.Disposable;

  constructor(private repoRoot: string) {
    this.registration = vscode.workspace.registerTextDocumentContentProvider(SCHEME, this);
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.contents.get(uri.toString()) ?? "";
  }

  async showDiff(target: PlanLensTarget): Promise<void> {
    const token = String(this.seq++);
    const left = vscode.Uri.parse(`${SCHEME}:/${token}/${encodeURIComponent(target.title)}.md`);
    const body = [
      `# ${target.title}`,
      "",
      target.rationale || "(no rationale)",
      "",
      target.predictedFootprint.length
        ? `Predicted footprint:\n${target.predictedFootprint.map((s) => `- ${s}`).join("\n")}`
        : "(no predicted footprint)",
    ].join("\n");
    this.contents.set(left.toString(), body);

    const right = vscode.Uri.file(path.join(this.repoRoot, target.path));
    const label = target.kind === "match" ? "Plan step" : "Unplanned change";
    await vscode.commands.executeCommand(
      "vscode.diff", left, right, `${label}: ${target.title} (${target.path})`,
      {
        preview: true,
        selection: new vscode.Range(target.startLine - 1, 0, target.endLine - 1, 0),
      } as vscode.TextDocumentShowOptions
    );
  }

  dispose(): void {
    this.registration.dispose();
  }
}

// -- quick pick: every step across active sessions; selecting a matched one opens its diff ------

export async function showPlanQuickPick(store: Store, diff: PlanDiffProvider): Promise<void> {
  let plan: PlanView;
  try {
    plan = await store.planView();
  } catch (e: any) {
    vscode.window.showErrorMessage(e.message);
    return;
  }
  type Item = vscode.QuickPickItem & { target?: PlanLensTarget; resumeSessionId?: string };
  const items: Item[] = [];
  for (const session of plan.sessions) {
    // A stalled session leads with a single Resume action -- the one clear next step for an
    // interrupted plan (selecting it hands the conversation back to Claude Code via `claude --resume`).
    if (session.derived_status === "stalled") {
      items.push({
        label: `$(debug-continue) Resume stalled plan`,
        description: session.session_id,
        detail: session.remaining_titles?.length
          ? `${session.remaining_titles.length} step(s) not built: ${session.remaining_titles.join(", ")}`
          : undefined,
        resumeSessionId: session.session_id,
      });
    }
    session.steps.forEach((step, idx) => {
      const item: Item = {
        label: `${STATUS_ICON[step.status] ?? "?"} step ${idx + 1}: ${step.title}`,
        description: session.session_id,
      };
      const file = step.files[0];
      const span = file?.spans[0];
      if (step.status === "matched" && file && span) {
        item.target = {
          kind: "match", title: step.title, rationale: step.rationale,
          predictedFootprint: step.predicted_footprint,
          path: file.path, startLine: span.start_line, endLine: span.end_line,
        };
      }
      items.push(item);
    });
  }
  if (items.length === 0) {
    vscode.window.showInformationMessage("No active plan sessions.");
    return;
  }
  const pick = await vscode.window.showQuickPick(items, { placeHolder: "Plan steps" });
  if (pick?.resumeSessionId) {
    await vscode.commands.executeCommand("sgt.resumePlan", pick.resumeSessionId);
  } else if (pick?.target) {
    await diff.showDiff(pick.target);
  }
}
