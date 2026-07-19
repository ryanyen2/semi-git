// The always-visible oracle chip (plan: "Titlebar composition selector + oracle chip → Workbench
// titlebar + always-visible status-bar oracle chip"). A sibling of `plan.ts`'s `PlanStatusBar`
// rather than a shared class -- unrelated concerns (oracle/fork state vs. plan-step progress), and
// this one never hides: an unconfigured oracle is itself worth surfacing, not worth hiding.

import * as vscode from "vscode";
import { Store } from "./store";

const ORACLE_GLYPH: Record<string, string> = {
  pass: "✓",
  fail: "✗",
  pending: "…",
  unconfigured: "○",
};

export class GitStatusBar implements vscode.Disposable {
  private item: vscode.StatusBarItem;
  private disposables: vscode.Disposable[] = [];

  constructor(private store: Store) {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 99);
    this.item.command = "sgt.openWorkbench";
    this.disposables.push(this.item, this.store.onDidChange(() => void this.refresh()));
  }

  async refresh(): Promise<void> {
    let status;
    try {
      status = await this.store.status();
    } catch {
      this.item.text = "sgt: error";
      this.item.tooltip = "Failed to read sgt status. Click to open the workbench.";
      this.item.show();
      return;
    }
    const glyph = ORACLE_GLYPH[status.oracle.status] ?? "?";
    const forkFlag = status.forks.open > 0 ? ` · ◊${status.forks.open}` : "";
    const indexingPrefix = status.sync_status.complete ? "" : "$(sync~spin) indexing · ";
    this.item.text = `${indexingPrefix}oracle: ${glyph}${forkFlag}`;
    const tooltipBase = status.oracle.configured
      ? `Oracle: ${status.oracle.status}${status.forks.open ? ` — ${status.forks.open} open fork(s)` : ""}`
      : "Oracle not configured. Click to open the workbench.";
    this.item.tooltip = status.sync_status.complete
      ? tooltipBase
      : `Indexing repository history — some results may be incomplete. ${tooltipBase}`;
    this.item.show();
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
  }
}
