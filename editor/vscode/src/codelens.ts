// A CodeLens at the top of each contiguous feature region: "◆ <intent> · <kind>". Clicking it
// inspects the node. Ranges are computed cheaply in provideCodeLenses; the title/command are
// filled lazily in resolveCodeLens (VSCode only resolves lenses scrolled into view).

import * as vscode from "vscode";
import { Store } from "./store";
import { truncate } from "./util";

interface SgtLens extends vscode.CodeLens {
  nodeId?: string;
}

export class SgtCodeLensProvider implements vscode.CodeLensProvider {
  private _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChange.event;

  constructor(private store: Store) {
    store.onDidChange(() => this._onDidChange.fire());
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("sgt.codeLens")) {
        this._onDidChange.fire();
      }
    });
  }

  async provideCodeLenses(document: vscode.TextDocument): Promise<vscode.CodeLens[]> {
    if (
      document.languageId !== "python" ||
      !vscode.workspace.getConfiguration("sgt").get<boolean>("codeLens.enabled", true)
    ) {
      return [];
    }
    const rel = vscode.workspace.asRelativePath(document.uri, false);
    let blame;
    try {
      blame = await this.store.blame(rel);
    } catch {
      return [];
    }
    if (blame.error) {
      return [];
    }
    // Anchor lenses to definition lines (def/class/decorator) to mirror GitLens's
    // "lens above each block" and avoid one lens per tiny fix span. Always include the first
    // span of each node so a feature that owns no def line still surfaces once.
    const lenses: SgtLens[] = [];
    const seen = new Set<string>();
    for (const s of blame.spans) {
      if (!s.node_id) {
        continue;
      }
      const line = Math.max(0, s.start - 1);
      const text = document.lineAt(Math.min(line, document.lineCount - 1)).text.trimStart();
      const isDef = /^(async\s+def|def|class|@)/.test(text);
      const firstOfNode = !seen.has(s.node_id);
      if (!isDef && !firstOfNode) {
        continue;
      }
      seen.add(s.node_id);
      const lens = new vscode.CodeLens(new vscode.Range(line, 0, line, 0)) as SgtLens;
      lens.nodeId = s.node_id;
      lenses.push(lens);
    }
    return lenses;
  }

  resolveCodeLens(codeLens: vscode.CodeLens): vscode.CodeLens {
    const lens = codeLens as SgtLens;
    const node = lens.nodeId ? this.store.node(lens.nodeId) : undefined;
    const intent = node ? node.intent : lens.nodeId ?? "feature";
    const kind = node ? ` · ${node.kind}` : "";
    const dependents = node && node.dependents.length ? ` · ${node.dependents.length} dependents` : "";
    lens.command = {
      title: `◆ ${truncate(intent, 60)}${kind}${dependents}`,
      command: "sgt.openNode",
      arguments: [lens.nodeId],
    };
    return lens;
  }
}
