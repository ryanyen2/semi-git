// Revision navigation: preview what reverting a feature would do, without writing anything.
// Drives `sgt revert <feature> --emit --json` (a sandboxed dry-run), then opens a read-only diff
// per changed file. Refusals (e.g. a fork) surface the message instead of a diff. Content is
// served through a virtual `sgt-preview:` scheme.

import * as vscode from "vscode";
import { Store } from "./store";

const SCHEME = "sgt-preview";

export class PreviewProvider implements vscode.TextDocumentContentProvider, vscode.Disposable {
  private contents = new Map<string, string>();
  private seq = 0;
  private registration: vscode.Disposable;

  constructor(private store: Store) {
    this.registration = vscode.workspace.registerTextDocumentContentProvider(SCHEME, this);
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.contents.get(uri.toString()) ?? "";
  }

  private uri(token: string, side: string, path: string): vscode.Uri {
    // path carried in the fragment so the diff title/lang resolve from the real filename.
    return vscode.Uri.parse(`${SCHEME}:${side}/${token}/${path}`).with({ fragment: path });
  }

  async preview(feature: string): Promise<void> {
    let res;
    try {
      res = await this.store.sgt.emit(feature);
    } catch (e: any) {
      vscode.window.showErrorMessage(`sgt revert --emit failed: ${e.message}`);
      return;
    }
    if (!res.ok) {
      vscode.window.showWarningMessage(
        `Would be refused: ${res.message || "operation does not commute"}`
      );
      return;
    }
    const paths = Object.keys(res.files);
    if (paths.length === 0) {
      vscode.window.showInformationMessage(`Revert ${feature}: no file changes.`);
      return;
    }
    const token = String(this.seq++);
    const label = `Revert: ${res.target}`;
    for (const path of paths) {
      const left = this.uri(token, "current", path);
      const right = this.uri(token, "predicted", path);
      this.contents.set(left.toString(), res.files[path].before);
      this.contents.set(right.toString(), res.files[path].after);
      await vscode.commands.executeCommand(
        "vscode.diff",
        left,
        right,
        `${path} — ${label}`,
        { preview: true } as vscode.TextDocumentShowOptions
      );
    }
    const removed = res.removed.length ? ` (removes ${res.removed.join(", ")})` : "";
    vscode.window.showInformationMessage(`Preview only — nothing written. ${label}${removed}`);
  }

  dispose(): void {
    this.registration.dispose();
  }
}
