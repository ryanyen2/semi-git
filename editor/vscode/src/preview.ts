// Revision navigation: preview what reverting a feature would do, without writing anything.
// Drives `sgt revert <feature> --emit --json` (a sandboxed dry-run), then opens a read-only diff
// per changed file. Refusals (e.g. a fork) surface the message instead of a diff. Content is
// served through a virtual `sgt-preview:` scheme.

import * as vscode from "vscode";
import { Store } from "./store";
import { EmitView } from "./types";

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
    let res: EmitView;
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
    await this.openDiff(res);
  }

  /** Open a read-only before→after diff per changed file for an already-fetched emit view -- the
   * "where this lands" preview. Shared by `sgt.previewRevert` and the revert/restore confirm flows
   * (so an exact ideal edit always shows its resulting diff before it commits) without re-running
   * `emit`. Titles come from the view's own `verb`, so nothing here assumes revert. Returns the
   * count of files shown. */
  async openDiff(res: EmitView): Promise<number> {
    const verb = res.verb ? res.verb[0].toUpperCase() + res.verb.slice(1) : "Edit";
    const paths = Object.keys(res.files);
    if (paths.length === 0) {
      vscode.window.showInformationMessage(`${verb} ${res.target}: would change no files.`);
      return 0;
    }
    const token = String(this.seq++);
    // The tab title has to say which of the two things this is. It used to read
    // `test_waitlist.py — Revert: f-10462e17…`, which a pilot participant read as a receipt for a
    // revert that had already happened -- it is in fact the proposal, opened *before* the confirm.
    // "PREVIEW" leads the label because a tab strip truncates the tail, and it stays true after
    // the user applies (it is still the preview of that edit) where a "not applied yet" would not.
    const label = `PREVIEW ${verb}: ${res.target}`;
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
    return paths.length;
  }

  dispose(): void {
    this.registration.dispose();
  }
}
