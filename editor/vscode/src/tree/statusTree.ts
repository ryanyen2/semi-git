// `ughStatus`: what needs attention now, from `ugh status`. The check on HEAD, the files with
// unsaved edits (each opens its diff against HEAD), the two-version choices (each opens its
// resolve), and the plan: each declared name built or missing, then the steps carrying none.

import * as path from "node:path";
import * as vscode from "vscode";
import { Store } from "../store";
import { SaveRow, StatusView } from "../types";

type Node =
  | { kind: "line"; label: string; icon: vscode.ThemeIcon }
  | { kind: "group"; id: "unsaved" | "choices" | "plan" | "other"; label: string; count?: number }
  | { kind: "file"; path: string }
  | { kind: "choice"; address: string }
  | { kind: "declared"; address: string; saves: SaveRow[] }
  | { kind: "step"; address: string; name: string | null };

function checkLine(status: StatusView): Node {
  const head = status.observed?.head;
  if (!status.observed) return { kind: "line", label: "No check", icon: new vscode.ThemeIcon("circle-slash") };
  if (!head) return { kind: "line", label: "Check not observed on HEAD", icon: new vscode.ThemeIcon("circle-outline") };
  return head.verdict === "pass"
    ? { kind: "line", label: "Check passes on HEAD", icon: new vscode.ThemeIcon("pass", new vscode.ThemeColor("testing.iconPassed")) }
    : { kind: "line", label: "Check fails on HEAD", icon: new vscode.ThemeIcon("error", new vscode.ThemeColor("testing.iconFailed")) };
}

export class StatusTree implements vscode.TreeDataProvider<Node>, vscode.Disposable {
  private changed = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.changed.event;
  private sub: { dispose(): void };

  constructor(private readonly root: string, private readonly store: Store) {
    this.sub = store.onChange(() => this.changed.fire());
  }

  getChildren(node?: Node): Node[] {
    const { status, error } = this.store.snapshot;
    if (!node) {
      if (error) return [{ kind: "line", label: error, icon: new vscode.ThemeIcon("error") }];
      if (!status) return [];
      const rows: Node[] = [checkLine(status)];
      if (status.drift.paths.length) rows.push({ kind: "group", id: "unsaved", label: "Unsaved", count: status.drift.paths.length });
      if (status.choices.length) rows.push({ kind: "group", id: "choices", label: "Two-version choices", count: status.choices.length });
      if (status.plan) rows.push({ kind: "group", id: "plan", label: "Plan" });
      return rows;
    }
    if (node.kind !== "group" || !status) return [];
    switch (node.id) {
      case "unsaved":
        return status.drift.paths.map((p) => ({ kind: "file", path: p }));
      case "choices":
        return status.choices.map((c) => ({ kind: "choice", address: c.address }));
      case "plan": {
        const plan = status.plan!;
        const rows: Node[] = plan.names.map((n) => ({ kind: "declared", address: n.name, saves: n.saves }));
        if (plan.other.length) rows.push({ kind: "group", id: "other", label: "No declared name", count: plan.other.length });
        return rows;
      }
      case "other":
        return (status.plan?.other ?? []).map((s) => ({ kind: "step", address: s.address, name: s.name }));
    }
  }

  getTreeItem(node: Node): vscode.TreeItem {
    switch (node.kind) {
      case "line": {
        const item = new vscode.TreeItem(node.label);
        item.iconPath = node.icon;
        item.tooltip = node.label;
        return item;
      }
      case "group": {
        const item = new vscode.TreeItem(node.label, vscode.TreeItemCollapsibleState.Expanded);
        item.id = `group.${node.id}`;
        if (node.count !== undefined) item.description = String(node.count);
        return item;
      }
      case "file": {
        const item = new vscode.TreeItem(vscode.Uri.file(path.join(this.root, node.path)));
        item.description = path.dirname(node.path) === "." ? "" : path.dirname(node.path);
        item.tooltip = node.path;
        item.command = { command: "ugh.openUnsaved", title: "Open diff", arguments: [node.path] };
        return item;
      }
      case "choice": {
        const [file, def] = node.address.split("::");
        const item = new vscode.TreeItem(def ?? file);
        item.description = def ? file : "";
        item.tooltip = node.address;
        item.iconPath = new vscode.ThemeIcon("git-merge", new vscode.ThemeColor("list.warningForeground"));
        item.contextValue = "ughChoice";
        item.command = { command: "ugh.resolve", title: "Resolve", arguments: [node.address] };
        return item;
      }
      case "declared": {
        const item = new vscode.TreeItem(node.address);
        const shas = node.saves.map((s) => s.sha.slice(0, 7));
        item.description = shas.length ? shas.join(", ") : "missing";
        item.tooltip = shas.length ? node.saves.map((s) => `${s.sha.slice(0, 7)} ${s.subject}`).join("\n") : "missing";
        if (shas.length) {
          item.command = { command: "ugh.revealInWorkbench", title: "Reveal", arguments: [node.address] };
        }
        return item;
      }
      case "step": {
        const item = new vscode.TreeItem(node.address);
        item.description = node.name ?? "";
        item.tooltip = node.address;
        item.command = { command: "ugh.openStep", title: "Open step", arguments: [node.address] };
        return item;
      }
    }
  }

  dispose(): void {
    this.sub.dispose();
    this.changed.dispose();
  }
}
