// Symbol-scoped hover: richer than `blame.ts`'s whole-line hover (label + id only) -- label, id,
// the tree's own rationale (`MapNode.why`), op count, cross-feature coupling (`MapView.edges`,
// the same graph the workbench draws as connectors), and a few command links. VS Code stacks
// hovers from multiple providers, so this sits alongside blame.ts's decoration hover rather than
// replacing it.

import * as vscode from "vscode";
import { BlameSpan } from "./types";
import { Store } from "./store";

export class SymbolHoverProvider implements vscode.HoverProvider {
  constructor(private store: Store) {}

  async provideHover(document: vscode.TextDocument, position: vscode.Position): Promise<vscode.Hover | undefined> {
    const rel = vscode.workspace.asRelativePath(document.uri, false);
    let span: BlameSpan | undefined;
    try {
      const blame = await this.store.blame(rel);
      span = blame.spans.find((s) => position.line + 1 >= s.start_line && position.line + 1 <= s.end_line);
    } catch {
      return undefined;
    }
    if (!span) {
      return undefined;
    }

    let map;
    try {
      map = await this.store.map();
    } catch {
      map = undefined;
    }
    const node = map?.nodes.find((n) => n.id === span!.feature_id);

    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = true;
    md.appendMarkdown(`**${escapeMd(span.label)}** \`${span.feature_id}\`\n\n`);
    if (node) {
      if (node.why) {
        md.appendMarkdown(`${escapeMd(node.why)}\n\n`);
      }
      md.appendMarkdown(`${node.op_count} op(s) · ${node.size} symbol(s)\n\n`);
      const coupled = (map?.edges ?? [])
        .filter((e) => e.a === node.id || e.b === node.id)
        .map((e) => (e.a === node.id ? e.b : e.a));
      if (coupled.length) {
        const labels = coupled
          .slice(0, 5)
          .map((id) => map?.nodes.find((n) => n.id === id)?.label || id)
          .join(", ");
        md.appendMarkdown(`coupled with: ${escapeMd(labels)}${coupled.length > 5 ? ` (+${coupled.length - 5} more)` : ""}\n\n`);
      }
    }
    md.appendMarkdown(
      `[Preview Revert](command:sgt.previewRevert?${enc(span.feature_id)}) · ` +
        `[Open Workbench](command:sgt.revealInWorkbench?${enc(span.feature_id)})`
    );
    const range = new vscode.Range(span.start_line - 1, 0, span.end_line - 1, 0);
    return new vscode.Hover(md, range);
  }
}

function enc(id: string): string {
  return encodeURIComponent(JSON.stringify([id]));
}

function escapeMd(s: string): string {
  return s.replace(/([\\`*_{}[\]()#+\-.!])/g, "\\$1");
}
