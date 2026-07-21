// `sgtFeatures`: the feature/subsystem tree from `map_view`, mirrored in the activity bar.
// Native tree virtualization + type-to-filter handle huge trees for free; the workbench (Phase 3)
// mirrors the same data with a windowed custom renderer for the timeline pane.

import * as vscode from "vscode";
import { colorForNode } from "../color";
import { Store } from "../store";
import { MapNode } from "../types";

/** A tiny colored-square SVG, inlined as a data URI -- `iconPath` can't take a raw hex color. */
function colorIcon(color: string): vscode.Uri {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">` +
    `<rect x="3" y="3" width="10" height="10" rx="2" fill="${color}"/></svg>`;
  return vscode.Uri.parse(`data:image/svg+xml;utf8,${encodeURIComponent(svg)}`);
}

export class FeatureItem extends vscode.TreeItem {
  constructor(public readonly node: MapNode) {
    super(
      node.label || node.id,
      node.children.length
        ? node.kind === "subsystem"
          ? vscode.TreeItemCollapsibleState.Collapsed // subsystems collapsed by default (scale)
          : vscode.TreeItemCollapsibleState.Expanded
        : vscode.TreeItemCollapsibleState.None
    );
    this.id = node.id;
    // An `authored_id` marks a user-authored feature (a named selection you own) vs. a purely
    // clustering-proposed one (U6/U7) -- surface that distinction so the tree reflects authority.
    const authored = !!node.authored_id;
    this.description = `${node.op_count} op(s)${authored ? " · authored" : ""}`;
    this.tooltip = new vscode.MarkdownString(
      `**${node.label || node.id}**${authored ? " _(authored)_" : ""}\n\n` +
        `${node.why || "(no rationale recorded)"}` +
        (node.split_reason ? `\n\n_split: ${node.split_reason}_` : "")
    );
    this.iconPath = colorIcon(colorForNode(node.id));
    this.contextValue = node.kind === "feature" ? "sgtFeature" : "sgtSubsystem";
  }
}

export class FeaturesTreeProvider implements vscode.TreeDataProvider<MapNode>, vscode.Disposable {
  private _onDidChangeTreeData = new vscode.EventEmitter<void | MapNode>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private disposable: vscode.Disposable;

  constructor(private store: Store) {
    this.disposable = store.onDidChange(() => this._onDidChangeTreeData.fire());
  }

  getTreeItem(node: MapNode): vscode.TreeItem {
    return new FeatureItem(node);
  }

  async getChildren(node?: MapNode): Promise<MapNode[]> {
    let map;
    try {
      map = await this.store.map();
    } catch {
      return [];
    }
    const ids = node ? node.children : map.roots;
    return ids.map((id) => this.store.node(id)).filter((n): n is MapNode => !!n);
  }

  // `map_view` nodes carry their own `parent` id, so no separate index is needed for `reveal()`.
  getParent(node: MapNode): MapNode | undefined {
    return node.parent ? this.store.node(node.parent) : undefined;
  }

  dispose(): void {
    this.disposable.dispose();
  }
}
