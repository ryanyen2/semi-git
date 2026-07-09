// The feature tree sidebar: `sgt map`'s hierarchical projection. Subsystems (structural groupings)
// expand to features (Greene-matched, `F<n>` ids); each is emitted uniformly by `map_view` and
// told apart here by `kind`. A feature's icon is a dot in its identity color (color.ts's OKLCH
// generator) so it reads the same as the blame gutter; a subsystem gets a plain folder icon.

import * as vscode from "vscode";
import { colorForNode } from "./color";
import { Store } from "./store";
import { MapNode } from "./types";

export class MapTreeProvider implements vscode.TreeDataProvider<string> {
  private _onDidChange = new vscode.EventEmitter<string | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private store: Store) {
    store.onDidChange(() => this._onDidChange.fire());
  }

  async getChildren(element?: string): Promise<string[]> {
    let map;
    try {
      map = await this.store.map();
    } catch {
      return [];
    }
    if (element) {
      const node = this.store.node(element);
      return node ? [...node.children].sort() : [];
    }
    return [...map.roots].sort();
  }

  getTreeItem(id: string): vscode.TreeItem {
    const node = this.store.node(id);
    if (!node) {
      return new vscode.TreeItem(id);
    }
    const hasChildren = node.children.length > 0;
    const item = new vscode.TreeItem(
      node.label || id,
      hasChildren ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None
    );
    const ops = `${node.op_count} op${node.op_count === 1 ? "" : "s"}`;
    item.description = `${ops} · ${id}`;
    item.contextValue = node.kind === "feature" ? "sgtFeature" : "sgtSubsystem";
    item.iconPath = node.kind === "feature" ? dotIcon(colorForNode(id)) : new vscode.ThemeIcon("folder");
    item.tooltip = this.tooltip(node);
    return item;
  }

  private tooltip(node: MapNode): vscode.MarkdownString {
    const md = new vscode.MarkdownString();
    md.appendMarkdown(`**${node.label}**\n\n\`${node.kind}\` · \`${node.id}\` · ${node.op_count} op(s)`);
    if (node.why) {
      md.appendMarkdown(`\n\n${node.why}`);
    }
    return md;
  }
}

/** A small filled circle in the feature's identity color, for the tree item's icon. */
function dotIcon(color: string): vscode.Uri {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><circle cx="8" cy="8" r="5" fill="${color}"/></svg>`;
  return vscode.Uri.parse(`data:image/svg+xml;utf8,${encodeURIComponent(svg)}`);
}
