// The Feature DAG sidebar. A DAG can't render as a pure tree, so we root on the features
// nothing depends on (the "top" of the stack) and expand downward through depends_on edges.
// Status is encoded by codicon (active/planned/suspended/quarantined); kind + id sit in the
// description. The rich topology (multi-parent edges) lives in the graph webview.

import * as vscode from "vscode";
import { Store } from "./store";
import { NodeView } from "./types";

const STATUS_ICON: Record<string, string> = {
  active: "circle-filled",
  planned: "circle-outline",
  suspended: "circle-slash",
  quarantined: "warning",
};

export class GraphTreeProvider implements vscode.TreeDataProvider<string> {
  private _onDidChange = new vscode.EventEmitter<string | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private store: Store) {
    store.onDidChange(() => this._onDidChange.fire());
  }

  async getChildren(element?: string): Promise<string[]> {
    let graph;
    try {
      graph = await this.store.graph();
    } catch {
      return [];
    }
    if (element) {
      const node = this.store.node(element);
      return node ? node.depends_on : [];
    }
    // Roots: nodes nothing depends on, sorted with conflicts/planned surfaced first.
    const rank = (n: NodeView) => (n.conflict ? 0 : n.status === "planned" ? 1 : 2);
    const roots = graph.nodes.filter((n) => n.dependents.length === 0);
    const pool = roots.length ? roots : graph.nodes;
    return [...pool].sort((a, b) => rank(a) - rank(b)).map((n) => n.id);
  }

  getTreeItem(id: string): vscode.TreeItem {
    const node = this.store.node(id);
    if (!node) {
      return new vscode.TreeItem(id);
    }
    const hasDeps = node.depends_on.length > 0;
    const item = new vscode.TreeItem(
      node.intent || id,
      hasDeps ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None
    );
    item.description = `${node.kind} · ${id}`;
    item.contextValue = "sgtNode";
    item.iconPath = new vscode.ThemeIcon(STATUS_ICON[node.status] ?? "circle-filled");
    item.tooltip = this.tooltip(node);
    item.command = { command: "sgt.openNode", title: "Inspect", arguments: [id] };
    return item;
  }

  private tooltip(node: NodeView): vscode.MarkdownString {
    const md = new vscode.MarkdownString();
    md.appendMarkdown(`**${node.intent}**\n\n\`${node.kind}\` · \`${node.status}\` · \`${node.id}\``);
    if (node.conflict) {
      md.appendMarkdown(`\n\n⚠ ${node.conflict}`);
    }
    return md;
  }
}
