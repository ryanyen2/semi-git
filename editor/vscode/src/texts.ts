// Read-only documents for the diff editors: a file at HEAD, a step's unit before and after its
// save, and the file with theirs in place of ours. The path is kept so the language is the file's.

import * as vscode from "vscode";

export class Texts implements vscode.TextDocumentContentProvider, vscode.Disposable {
  static readonly scheme = "ugh-text";
  private texts = new Map<string, string>();
  private changed = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.changed.event;

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.texts.get(uri.toString()) ?? "";
  }

  /** A document holding `text`, named by `side` and the repo-relative `path`. */
  put(side: string, path: string, text: string): vscode.Uri {
    const uri = vscode.Uri.from({ scheme: Texts.scheme, path: "/" + path, query: side });
    const key = uri.toString();
    const known = this.texts.has(key);
    this.texts.set(key, text);
    if (known) this.changed.fire(uri);
    return uri;
  }

  dispose(): void {
    this.changed.dispose();
  }
}
