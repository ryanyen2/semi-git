// Revision navigation: preview what plugging a feature out (revert) or suspending it would do,
// without writing anything. Drives `sgt emit … --json` (a sandboxed dry-run), then opens a
// read-only diff per changed file. Refusals (e.g. a dependent still needs it) surface the
// witness instead of a diff. Content is served through a virtual `sgt-preview:` scheme.

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

  async preview(action: "revert" | "switch", ref: string, on?: boolean): Promise<void> {
    let res;
    try {
      res = await this.store.sgt.emit(action, ref, on);
    } catch (e: any) {
      vscode.window.showErrorMessage(`sgt emit failed: ${e.message}`);
      return;
    }
    if (res.error) {
      vscode.window.showErrorMessage(res.error);
      return;
    }
    if (!res.ok) {
      vscode.window.showWarningMessage(
        `Would be refused: ${res.message ?? "operation does not commute"}`
      );
      return;
    }
    const files = res.files ?? {};
    const paths = Object.keys(files);
    if (paths.length === 0) {
      vscode.window.showInformationMessage(`${verb(action, on)} ${ref}: no file changes.`);
      return;
    }
    const token = String(this.seq++);
    const label = `${verb(action, on)}: ${res.node_id ?? ref}`;
    for (const path of paths) {
      const left = this.uri(token, "current", path);
      const right = this.uri(token, "predicted", path);
      this.contents.set(left.toString(), files[path].before);
      this.contents.set(right.toString(), files[path].after);
      await vscode.commands.executeCommand(
        "vscode.diff",
        left,
        right,
        `${path} — ${label}`,
        { preview: true } as vscode.TextDocumentShowOptions
      );
    }
    const removed = res.removed && res.removed.length ? ` (removes ${res.removed.join(", ")})` : "";
    vscode.window.showInformationMessage(`Preview only — nothing written. ${label}${removed}`);
  }

  dispose(): void {
    this.registration.dispose();
  }
}

function verb(action: string, on?: boolean): string {
  if (action === "revert") {
    return "Revert";
  }
  return on ? "Restore" : "Suspend";
}
