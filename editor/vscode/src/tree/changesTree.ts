// `sgtChanges`: drift (mined but unpredicted by any active plan session), unmanaged paths (no
// entity-granular coverage), and the trust queue (unattributed/drifting ops awaiting review) --
// three read-only sections, sourced from one `compose_view` call. `sgt.reviewAck` (commands.ts)
// acts on the trust queue's `trustGroup`/`trustOp` items via their `view/item/context` menu.

import * as vscode from "vscode";
import { Store } from "../store";
import { ComposeView, DriftEntry, TrustGroup, TrustOpEntry } from "../types";

type SectionId = "drift" | "unmanaged" | "trust";

export type ChangesNode =
  | { kind: "section"; sectionId: SectionId; label: string; count: number }
  | { kind: "drift"; entry: DriftEntry }
  | { kind: "unmanaged"; path: string }
  | { kind: "trustGroup"; group: TrustGroup }
  | { kind: "trustOp"; op: TrustOpEntry };

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
      item.contextValue = "sgtDrift";
      const loc = firstSpan(node.entry);
      if (loc) {
        item.command = { command: "sgt.jumpToLocation", title: "Jump to Drift", arguments: [loc] };
      }
      return item;
    }
    if (node.kind === "unmanaged") {
      const item = new vscode.TreeItem(node.path, vscode.TreeItemCollapsibleState.None);
      item.iconPath = new vscode.ThemeIcon("file");
      item.contextValue = "sgtUnmanaged";
      item.command = {
        command: "sgt.jumpToLocation", title: "Open",
        arguments: [{ path: node.path, startLine: 1, endLine: 1 }],
      };
      return item;
    }
    if (node.kind === "trustGroup") {
      const item = new vscode.TreeItem(
        node.group.provenance, vscode.TreeItemCollapsibleState.Collapsed
      );
      item.description = `${node.group.op_ids.length} op(s)`;
      item.contextValue = "sgtTrustGroup";
      return item;
    }
    const item = new vscode.TreeItem(node.op.op_id.slice(0, 12), vscode.TreeItemCollapsibleState.None);
    item.description = node.op.kind;
    item.iconPath = node.op.drift ? new vscode.ThemeIcon("diff-modified") : undefined;
    item.contextValue = "sgtTrustOp";
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
        { kind: "section", sectionId: "drift", label: "Drift", count: compose.drift.entries.length },
        { kind: "section", sectionId: "unmanaged", label: "Unmanaged", count: compose.status.unmanaged.length },
        { kind: "section", sectionId: "trust", label: "Trust queue", count: compose.trust.total_ops },
      ];
      // drift always shown -- it's the common "clean" case the user checks first.
      return sections.filter((s) => s.kind !== "section" || s.count > 0 || s.sectionId === "drift");
    }

    if (node.kind === "section" && node.sectionId === "drift") {
      return compose.drift.entries.map((entry) => ({ kind: "drift", entry }));
    }
    if (node.kind === "section" && node.sectionId === "unmanaged") {
      return compose.status.unmanaged.map((path) => ({ kind: "unmanaged", path }));
    }
    if (node.kind === "section" && node.sectionId === "trust") {
      return compose.trust.groups.map((group) => ({ kind: "trustGroup", group }));
    }
    if (node.kind === "trustGroup") {
      return node.group.ops.map((op) => ({ kind: "trustOp", op, group: node.group }));
    }
    return [];
  }

  dispose(): void {
    this.disposable.dispose();
  }
}
