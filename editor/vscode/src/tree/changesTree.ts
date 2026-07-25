// `sgtChanges`: a read-only diff of what the working tree carries relative to the active plans.
// Two sections, both informational (nothing here is a chore to "resolve"): "Unplanned changes"
// (ops sgt mined that no active plan predicted) and "Untracked files" (paths with no
// entity-granular coverage). The materialized code is the source of truth -- these rows just tell
// you what happened outside a stated plan; you act on them by editing code, not by acknowledging a
// queue. Sourced from one `compose_view` call.

import * as vscode from "vscode";
import { Store } from "../store";
import { ComposeView, DriftEntry } from "../types";

type SectionId = "drift" | "unmanaged";

export type ChangesNode =
  | { kind: "section"; sectionId: SectionId; label: string; count: number }
  | { kind: "drift"; entry: DriftEntry }
  | { kind: "unmanaged"; path: string };

function firstSpan(entry: DriftEntry): { path: string; startLine: number; endLine: number } | undefined {
  const file = entry.files[0];
  const span = file?.spans[0];
  return file && span ? { path: file.path, startLine: span.start_line, endLine: span.end_line } : undefined;
}

export class ChangesTreeProvider implements vscode.TreeDataProvider<ChangesNode>, vscode.Disposable {
  private _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private disposable: vscode.Disposable;
  private compose: ComposeView | undefined;

  constructor(private store: Store) {
    this.disposable = store.onDidChange(() => {
      this.compose = undefined;
      this._onDidChangeTreeData.fire();
    });
  }

  getTreeItem(node: ChangesNode): vscode.TreeItem {
    if (node.kind === "section") {
      const item = new vscode.TreeItem(
        `${node.label} (${node.count})`,
        node.count ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None
      );
      item.contextValue = "sgtChangesSection";
      return item;
    }
    if (node.kind === "drift") {
      const item = new vscode.TreeItem(
        `${node.entry.kind}: ${node.entry.footprint.join(", ")}`,
        vscode.TreeItemCollapsibleState.None
      );
      item.iconPath = new vscode.ThemeIcon("diff-modified");
      item.tooltip = "Mined from the working tree, not predicted by any active plan. Read-only.";
      item.contextValue = "sgtDrift";
      const loc = firstSpan(node.entry);
      if (loc) {
        item.command = { command: "sgt.jumpToLocation", title: "Open", arguments: [loc] };
      }
      return item;
    }
    const item = new vscode.TreeItem(node.path, vscode.TreeItemCollapsibleState.None);
    item.iconPath = new vscode.ThemeIcon("file");
    item.tooltip = "No entity-granular coverage yet. Read-only.";
    item.contextValue = "sgtUnmanaged";
    item.command = {
      command: "sgt.jumpToLocation", title: "Open",
      arguments: [{ path: node.path, startLine: 1, endLine: 1 }],
    };
    return item;
  }

  private async load(): Promise<ComposeView> {
    if (!this.compose) {
      this.compose = await this.store.composeView();
    }
    return this.compose;
  }

  async getChildren(node?: ChangesNode): Promise<ChangesNode[]> {
    let compose: ComposeView;
    try {
      compose = await this.load();
    } catch {
      return [];
    }

    if (!node) {
      const sections: ChangesNode[] = [
        { kind: "section", sectionId: "drift", label: "Unplanned changes", count: compose.drift.entries.length },
        { kind: "section", sectionId: "unmanaged", label: "Untracked files", count: compose.status.unmanaged.length },
      ];
      // "Unplanned changes" always shows -- the "nothing drifted from plan" case is the one the
      // user checks first, and an empty (0) section states that honestly.
      return sections.filter((s) => s.kind !== "section" || s.count > 0 || s.sectionId === "drift");
    }

    if (node.kind === "section" && node.sectionId === "drift") {
      return compose.drift.entries.map((entry) => ({ kind: "drift", entry }));
    }
    if (node.kind === "section" && node.sectionId === "unmanaged") {
      return compose.status.unmanaged.map((path) => ({ kind: "unmanaged", path }));
    }
    return [];
  }

  dispose(): void {
    this.disposable.dispose();
  }
}
