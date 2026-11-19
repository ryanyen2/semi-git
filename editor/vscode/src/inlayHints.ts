// The name at the end of a definition's first line, when it has one (`ugh.inlayHints.enabled`).

import * as vscode from "vscode";
import { Documents } from "./documents";
import { Store } from "./store";
import { headLine, nameOf } from "./units";

export class InlayHints implements vscode.InlayHintsProvider, vscode.Disposable {
  private changed = new vscode.EventEmitter<void>();
  readonly onDidChangeInlayHints = this.changed.event;
  private subs: { dispose(): void }[];

  constructor(private readonly docs: Documents, store: Store) {
    this.subs = [
      store.onChange(() => this.changed.fire()),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("ugh.inlayHints")) this.changed.fire();
      }),
    ];
  }

  async provideInlayHints(doc: vscode.TextDocument, range: vscode.Range): Promise<vscode.InlayHint[]> {
    if (!vscode.workspace.getConfiguration("ugh").get<boolean>("inlayHints.enabled", false)) return [];
    const all = await this.docs.units(doc);
    if (!all) return [];
    const lines = doc.getText().split("\n");
    const hints: vscode.InlayHint[] = [];
    for (const unit of all.units) {
      if (unit.kind !== "definition") continue;
      const name = nameOf(unit.steps);
      const line = headLine(unit, lines) - 1;
      if (!name || line < range.start.line || line > range.end.line) continue;
      const hint = new vscode.InlayHint(doc.lineAt(line).range.end, name);
      hint.paddingLeft = true;
      hints.push(hint);
    }
    return hints;
  }

  dispose(): void {
    this.subs.forEach((s) => s.dispose());
    this.changed.dispose();
  }
}
