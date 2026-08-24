// `sgtFeatures`: the feature/subsystem tree from `map_view`, mirrored in the activity bar.
// Native tree virtualization + type-to-filter handle huge trees for free; the workbench (Phase 3)
// mirrors the same data with a windowed custom renderer for the timeline pane.

import * as vscode from "vscode";
import { colorForNode } from "../color";
import { isVisibleNode } from "../mapFilter";
import { Store } from "../store";
import { IntentSegment, MapNode } from "../types";

// The tree shows features and, under a leaf feature, its chapters.
//
// A feature can be months of work; a chapter is normally one afternoon's job, and it is the unit
// people actually want to remove or put back. The terminal has shown chapters for a while
// (`sgt log --map`, `sgt intent list`), and this panel did not, so the two surfaces disagreed
// about what the repository contains and only one of them could answer "which piece of work was
// that". The segments arrive with the intent view the store already fetches.
type TreeNode = MapNode | ChapterNode;

export interface ChapterNode {
  kind: "chapter";
  segment: IntentSegment;
  parentId: string;
}

function isChapter(node: TreeNode): node is ChapterNode {
  return (node as ChapterNode).kind === "chapter";
}

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
      // Expanded by default so the rail shows the whole feature tree at a glance -- the common
      // repo has a handful of features and a collapsed subsystem read as an empty rail. VS Code's
      // tree virtualization, type-to-filter, and remembered per-tree collapse state absorb the
      // rare large tree; the user collapses what they don't want.
      node.children.length ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None
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

export class ChapterItem extends vscode.TreeItem {
  constructor(public readonly node: ChapterNode) {
    super(node.segment.intent || `@${node.segment.seg_index}`, vscode.TreeItemCollapsibleState.None);
    const seg = node.segment;
    this.id = `${seg.feature_id}@${seg.seg_index}`;
    this.description = `@${seg.seg_index} · ${seg.op_count} edit(s)`;
    // The typeable selector, spelled out. Someone reading this panel and then reaching for the
    // terminal should not have to guess how to name what they are looking at.
    this.tooltip = new vscode.MarkdownString(
      `**${seg.intent || `@${seg.seg_index}`}**\n\n` +
        `${seg.rationale || "(no rationale recorded)"}\n\n` +
        "```\n" +
        `sgt revert "${seg.feature_label}@${seg.intent}"\n` +
        "```"
    );
    this.iconPath = new vscode.ThemeIcon("git-commit");
    this.contextValue = "sgtChapter";
  }
}

export class FeaturesTreeProvider implements vscode.TreeDataProvider<TreeNode>, vscode.Disposable {
  private _onDidChangeTreeData = new vscode.EventEmitter<void | TreeNode>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event as vscode.Event<void | TreeNode>;
  private disposable: vscode.Disposable;

  constructor(private store: Store) {
    this.disposable = store.onDidChange(() => this._onDidChangeTreeData.fire());
  }

  getTreeItem(node: TreeNode): vscode.TreeItem {
    return isChapter(node) ? new ChapterItem(node) : new FeatureItem(node);
  }

  async getChildren(node?: TreeNode): Promise<TreeNode[]> {
    if (node && isChapter(node)) return [];
    let map;
    try {
      map = await this.store.map();
    } catch {
      return [];
    }
    // A leaf feature lists its chapters. Anything with sub-features keeps listing those, so the
    // shape of the tree still matches the terminal's.
    if (node && node.children.length === 0) {
      try {
        const intent = await this.store.intentView();
        return intent.segments
          .filter((s) => s.feature_id === node.id)
          .sort((a, b) => a.seg_index - b.seg_index)
          .map((segment) => ({ kind: "chapter" as const, segment, parentId: node.id }));
      } catch {
        return [];
      }
    }
    const ids = node ? node.children : map.roots;
    // Same drop rule the terminal map and the workbench timeline apply: a husk leaf (no symbols of
    // its own) and any subsystem left holding only husks are not listed. Without it this tree was the
    // one surface still showing features `sgt log --map` did not, which is most of why the sidebar,
    // the workbench and the terminal could not be read against each other.
    const nodeOf = (id: string) => this.store.node(id);
    return ids
      .map(nodeOf)
      .filter((n): n is MapNode => !!n && isVisibleNode(n, nodeOf));
  }

  // `map_view` nodes carry their own `parent` id, so no separate index is needed for `reveal()`.
  getParent(node: TreeNode): TreeNode | undefined {
    if (isChapter(node)) return this.store.node(node.parentId);
    return node.parent ? this.store.node(node.parent) : undefined;
  }

  dispose(): void {
    this.disposable.dispose();
  }
}
