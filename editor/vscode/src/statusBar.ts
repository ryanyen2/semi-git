// Unsaved files, pending choices, and the check's verdict on HEAD. A click opens the workbench.

import * as vscode from "vscode";
import { Snapshot } from "./types";

const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;

export class StatusBar implements vscode.Disposable {
  private item = vscode.window.createStatusBarItem("ugh.status", vscode.StatusBarAlignment.Left, 99);

  constructor() {
    this.item.name = "ugh";
    this.item.command = "ugh.openWorkbench";
  }

  render(s: Snapshot): void {
    if (s.uninitialized || (!s.status && !s.error)) return void this.item.hide();
    if (s.error) {
      this.item.text = "ugh $(error)";
      this.item.tooltip = s.error;
      return void this.item.show();
    }
    const status = s.status!;
    const unsaved = status.drift.paths.length;
    const choices = status.choices.length;
    const head = status.observed?.head;
    const text = ["ugh"];
    const tip: string[] = [];
    if (unsaved) {
      text.push(`$(edit) ${unsaved}`);
      tip.push(plural(unsaved, "unsaved file"));
    }
    if (choices) {
      text.push(`$(git-merge) ${choices}`);
      tip.push(plural(choices, "two-version choice"));
    }
    if (head) {
      text.push(head.verdict === "pass" ? "$(pass)" : "$(error)");
      tip.push(`check ${head.verdict === "pass" ? "passes" : "fails"} on HEAD`);
    } else if (status.observed) {
      tip.push("check not observed on HEAD");
    }
    this.item.text = text.join("  ");
    this.item.tooltip = tip.join(" · ") || "Open workbench";
    this.item.show();
  }

  dispose(): void {
    this.item.dispose();
  }
}
