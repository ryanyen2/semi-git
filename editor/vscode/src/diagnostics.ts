// A pending two-version choice as a warning on its unit's lines, with the quick fix "Resolve".

import * as path from "node:path";
import * as vscode from "vscode";
import { Store } from "./store";
import { headLine, locate } from "./units";

export class Diagnostics implements vscode.CodeActionProvider, vscode.Disposable {
  private collection = vscode.languages.createDiagnosticCollection("ugh");
  /** Each diagnostic's choice, by document and line, for the quick fix. */
  private choices = new Map<string, { line: number; address: string }[]>();
  private subs: { dispose(): void }[];

  constructor(private readonly root: string, private readonly store: Store) {
    this.subs = [store.onChange(() => void this.render())];
  }

  async render(): Promise<void> {
    const byFile = new Map<string, { range: vscode.Range; address: string }[]>();
    for (const c of this.store.snapshot.status?.choices ?? []) {
      const rel = c.unit.split("::")[0];
      const uri = vscode.Uri.file(path.join(this.root, rel));
      let range = new vscode.Range(0, 0, 0, 0);
      try {
        const show = await this.store.show(rel);
        const open = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
        const text = open?.getText() ?? new TextDecoder().decode(await vscode.workspace.fs.readFile(uri));
        const unit = locate(show, text).find((u) => u.unit === c.unit);
        if (unit) {
          range = new vscode.Range(headLine(unit, text.split("\n")) - 1, 0, unit.last - 1, Number.MAX_SAFE_INTEGER);
        }
      } catch {
        // the warning still lands on the file
      }
      const list = byFile.get(uri.toString()) ?? [];
      list.push({ range, address: c.address });
      byFile.set(uri.toString(), list);
    }
    this.collection.clear();
    this.choices.clear();
    for (const [key, list] of byFile) {
      this.collection.set(
        vscode.Uri.parse(key),
        list.map(({ range }) => {
          const d = new vscode.Diagnostic(range, "Two versions: theirs waits", vscode.DiagnosticSeverity.Warning);
          d.source = "ugh";
          return d;
        }),
      );
      this.choices.set(key, list.map(({ range, address }) => ({ line: range.start.line, address })));
    }
  }

  provideCodeActions(doc: vscode.TextDocument, _range: vscode.Range, context: vscode.CodeActionContext): vscode.CodeAction[] {
    const here = this.choices.get(doc.uri.toString()) ?? [];
    return context.diagnostics
      .filter((d) => d.source === "ugh")
      .flatMap((d) => {
        // The marker moves with edits and the stored line does not: take the nearest.
        const hit = here.reduce<(typeof here)[number] | undefined>((best, c) =>
          !best || Math.abs(c.line - d.range.start.line) < Math.abs(best.line - d.range.start.line) ? c : best, undefined);
        if (!hit) return [];
        const action = new vscode.CodeAction("Resolve", vscode.CodeActionKind.QuickFix);
        action.command = { command: "ugh.resolve", title: "Resolve", arguments: [hit.address] };
        action.diagnostics = [d];
        action.isPreferred = true;
        return [action];
      });
  }

  dispose(): void {
    this.subs.forEach((s) => s.dispose());
    this.collection.dispose();
  }
}
