// Rich hover over any line: the owning feature's intent, kind/status, dependencies, dependents,
// any conflict witness, and trusted command links to preview a suspend/revert as a diff. This is
// the "detail on demand" layer — the heavy content lives here, not in the always-on annotation.

import * as vscode from "vscode";
import { Store } from "./store";
import { BlameView, NodeView } from "./types";
import { ownerAt } from "./util";

export class SgtHoverProvider implements vscode.HoverProvider {
  constructor(private store: Store) {}

  async provideHover(
    document: vscode.TextDocument,
    position: vscode.Position
  ): Promise<vscode.Hover | undefined> {
    if (document.languageId !== "python") {
      return undefined;
    }
    const rel = vscode.workspace.asRelativePath(document.uri, false);
    let blame: BlameView;
    try {
      blame = await this.store.blame(rel);
    } catch {
      return undefined;
    }
    if (blame.error) {
      return undefined;
    }
    const owner = ownerAt(blame, position.line + 1);
    if (!owner) {
      return undefined;
    }
    const node = this.store.node(owner);
    return new vscode.Hover(this.render(owner, node, blame.drift));
  }

  private render(id: string, node: NodeView | undefined, drift: boolean): vscode.MarkdownString {
    const md = new vscode.MarkdownString();
    md.isTrusted = {
      enabledCommands: [
        "sgt.previewSwitchOff",
        "sgt.previewSwitchOn",
        "sgt.previewRevert",
        "sgt.openNode",
      ],
    };
    md.supportThemeIcons = true;
    if (!node) {
      md.appendMarkdown(`**${id}**`);
      return md;
    }
    md.appendMarkdown(`**◆ ${escape(node.intent)}**\n\n`);
    md.appendMarkdown(`\`${node.kind}\` · \`${node.status}\` · \`${id}\`\n\n`);
    if (node.depends_on.length) {
      md.appendMarkdown(`Depends on: ${node.depends_on.map((d) => `\`${d}\``).join(", ")}\n\n`);
    }
    if (node.dependents.length) {
      md.appendMarkdown(`Dependents: ${node.dependents.map((d) => `\`${d}\``).join(", ")}\n\n`);
    }
    if (node.conflict) {
      md.appendMarkdown(`⚠ Conflict: ${escape(String(node.conflict))}\n\n`);
    }
    if (drift) {
      md.appendMarkdown(`⚠ _Working tree has drifted from the graph — blame may be stale._\n\n`);
    }
    const args = encodeURIComponent(JSON.stringify([id]));
    md.appendMarkdown(`---\n\n`);
    md.appendMarkdown(`[$(diff) Preview suspend](command:sgt.previewSwitchOff?${args}) · `);
    md.appendMarkdown(`[$(diff) Preview revert](command:sgt.previewRevert?${args}) · `);
    md.appendMarkdown(`[$(search) Inspect](command:sgt.openNode?${args})`);
    return md;
  }
}

function escape(s: string): string {
  return s.replace(/[<>]/g, (c) => (c === "<" ? "&lt;" : "&gt;"));
}
