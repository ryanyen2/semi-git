// In-situ diagnostics: drift -> Hint ("mined but not predicted by any active plan session", with
// a "Save to clear" quick-fix) and open forks -> Warning. `DriftEntry` carries its own spans
// (`sgt.core.lens.plan`'s footprint projection); `ForkRecord` doesn't (plan API addition #5,
// deferred -- see the module docstring in `sgt/api.py`'s fork projection), so a fork's range is
// cross-referenced from `blame_view`'s per-symbol spans for that file instead of a dedicated
// span field. Recomputed for every open document on `store.onDidChange`, gated by
// `sgt.diagnostics.drift`/`sgt.diagnostics.forks`.

import * as path from "node:path";
import * as vscode from "vscode";
import { Store } from "./store";
import { ForkRecord } from "./types";

const SOURCE_DRIFT = "sgt-drift";
const SOURCE_FORK = "sgt-fork";

export class DiagnosticsController implements vscode.Disposable {
  private collection: vscode.DiagnosticCollection;
  private disposables: vscode.Disposable[] = [];
  private debounce: NodeJS.Timeout | undefined;

  constructor(private store: Store, private root: string) {
    this.collection = vscode.languages.createDiagnosticCollection("sgt");
    this.disposables.push(
      this.collection,
      this.store.onDidChange(() => this.schedule()),
      vscode.workspace.onDidCloseTextDocument((doc) => this.collection.delete(doc.uri)),
      vscode.workspace.onDidChangeConfiguration((e) => {
        if (e.affectsConfiguration("sgt.diagnostics")) {
          this.schedule();
        }
      })
    );
  }

  private schedule(): void {
    clearTimeout(this.debounce);
    this.debounce = setTimeout(() => void this.render(), 200);
  }

  private config(key: string, def: boolean): boolean {
    return vscode.workspace.getConfiguration("sgt").get<boolean>(key, def);
  }

  async render(): Promise<void> {
    const driftOn = this.config("diagnostics.drift", true);
    const forksOn = this.config("diagnostics.forks", true);
    if (!driftOn && !forksOn) {
      this.collection.clear();
      return;
    }
    let compose;
    try {
      compose = await this.store.composeView();
    } catch {
      this.collection.clear();
      return;
    }
    const byFile = new Map<string, vscode.Diagnostic[]>();

    if (driftOn) {
      for (const entry of compose.drift.entries) {
        for (const file of entry.files) {
          for (const span of file.spans) {
            const range = new vscode.Range(Math.max(0, span.start_line - 1), 0, Math.max(0, span.end_line - 1), 0);
            const d = new vscode.Diagnostic(
              range,
              `sgt: drifted -- ${entry.kind} on ${span.symbol}, unpredicted by any active plan session`,
              vscode.DiagnosticSeverity.Hint
            );
            d.source = SOURCE_DRIFT;
            d.code = "drift";
            addTo(byFile, file.path, d);
          }
        }
      }
    }

    if (forksOn) {
      for (const record of compose.forks.forks) {
        const range = await this.forkRange(record);
        const d = new vscode.Diagnostic(
          range ?? new vscode.Range(0, 0, 0, 0),
          `sgt: fork on ${record.symbol} -- ${record.remedy}`,
          vscode.DiagnosticSeverity.Warning
        );
        d.source = SOURCE_FORK;
        d.code = "fork";
        addTo(byFile, record.file, d);
      }
    }

    this.collection.clear();
    for (const [rel, diags] of byFile) {
      const uri = vscode.Uri.file(path.isAbsolute(rel) ? rel : path.join(this.root, rel));
      this.collection.set(uri, diags);
    }
  }

  private async forkRange(record: ForkRecord): Promise<vscode.Range | undefined> {
    try {
      const blame = await this.store.blame(record.file);
      const span = blame.spans.find((s) => s.symbol === record.symbol);
      return span ? new vscode.Range(span.start_line - 1, 0, span.end_line - 1, 0) : undefined;
    } catch {
      return undefined;
    }
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
  }
}

// The drift diagnostic's quick-fix: `sgt save` mines + commits the current ideal, which is what
// clears drift (there's nothing to predict against once it's part of the recorded history).
export class DriftCodeActionProvider implements vscode.CodeActionProvider {
  provideCodeActions(
    _document: vscode.TextDocument,
    _range: vscode.Range,
    context: vscode.CodeActionContext
  ): vscode.CodeAction[] {
    const driftDiagnostics = context.diagnostics.filter((d) => d.source === SOURCE_DRIFT);
    if (!driftDiagnostics.length) {
      return [];
    }
    const action = new vscode.CodeAction("Save to clear drift (sgt save)", vscode.CodeActionKind.QuickFix);
    action.command = { command: "sgt.save", title: "Save" };
    action.diagnostics = driftDiagnostics;
    return [action];
  }
}

function addTo(map: Map<string, vscode.Diagnostic[]>, path_: string, d: vscode.Diagnostic): void {
  const arr = map.get(path_) ?? [];
  arr.push(d);
  map.set(path_, arr);
}
