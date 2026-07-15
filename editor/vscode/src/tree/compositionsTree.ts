// `sgtCompositions`: the discrete "intermediate decisions" over the ideal -- active plan sessions
// (`sgt session status`) and open proposals (`sgt propose status`) -- the switch/land/publish
// surface. Sourced from one `compose_view` call, same as `sgtChanges`.

import * as vscode from "vscode";
import { Store } from "../store";
import { ComposeProposalSummary, ComposeView, SessionInfo } from "../types";

type SectionId = "sessions" | "proposals";

export type CompositionsNode =
  | { kind: "section"; sectionId: SectionId; label: string; count: number }
  | { kind: "session"; session: SessionInfo }
  | { kind: "proposal"; proposal: ComposeProposalSummary };

export class CompositionsTreeProvider
  implements vscode.TreeDataProvider<CompositionsNode>, vscode.Disposable
{
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

  getTreeItem(node: CompositionsNode): vscode.TreeItem {
    if (node.kind === "section") {
      const item = new vscode.TreeItem(
        `${node.label} (${node.count})`,
        node.count ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None
      );
      item.contextValue = "sgtCompositionsSection";
      return item;
    }
    if (node.kind === "session") {
      const s = node.session;
      const item = new vscode.TreeItem(s.name, vscode.TreeItemCollapsibleState.None);
      item.description = `${s.branch} · ${s.new_op_count} op(s)`;
      item.iconPath = new vscode.ThemeIcon(s.alive ? "circle-filled" : "circle-outline");
      item.tooltip = new vscode.MarkdownString(
        `**${s.name}** on \`${s.branch}\` (target \`${s.target_branch}\`)\n\n` +
          `${s.new_op_count} new op(s) · scratch \`${s.scratch}\`\n\n` +
          (s.alive ? `owner pid ${s.owner_pid}` : "_not running_")
      );
      item.contextValue = "sgtSession";
      return item;
    }
    const p = node.proposal;
    const item = new vscode.TreeItem(p.title || p.id, vscode.TreeItemCollapsibleState.None);
    item.description = `${p.delta_op_count} op(s)`;
    item.iconPath = new vscode.ThemeIcon("git-pull-request");
    item.tooltip = new vscode.MarkdownString(
      `**${p.title || p.id}**\n\nbase \`${p.base_ref}\`\n\nfeatures: ${p.feature_delta.join(", ") || "(none)"}`
    );
    item.contextValue = "sgtProposal";
    item.command = { command: "sgt.viewProposal", title: "View Proposal", arguments: [p.id] };
    return item;
  }

  private async load(): Promise<ComposeView> {
    if (!this.compose) {
      this.compose = await this.store.composeView();
    }
    return this.compose;
  }

  async getChildren(node?: CompositionsNode): Promise<CompositionsNode[]> {
    let compose: ComposeView;
    try {
      compose = await this.load();
    } catch {
      return [];
    }

    if (!node) {
      return [
        { kind: "section", sectionId: "sessions", label: "Sessions", count: compose.sessions.sessions.length },
        { kind: "section", sectionId: "proposals", label: "Proposals", count: compose.proposals.length },
      ];
    }
    if (node.kind === "section" && node.sectionId === "sessions") {
      return compose.sessions.sessions.map((session) => ({ kind: "session", session }));
    }
    if (node.kind === "section" && node.sectionId === "proposals") {
      return compose.proposals.map((proposal) => ({ kind: "proposal", proposal }));
    }
    return [];
  }

  dispose(): void {
    this.disposable.dispose();
  }
}
