// Fork resolution: a native side-by-side diff of a same-symbol chain fork's two tip images
// (`ForkDetailView` already carries full file content per tip -- no extra fold call needed) opened
// through VS Code's built-in `vscode.diff` editor, plus a small compose-and-fulfill action strip
// that used to be a copy-tip-ids-to-clipboard placeholder in commands.ts: `sgt merge-op <a> <b>`
// drafts a reconciling hollow -> the user hand-edits the working tree to match -> `sgt fulfill
// <draft-id> --from-tree` stages it -> `sgt commit` commits it. Every step but the hand-edit is a
// real, unmodified kernel verb; the panel just keeps the draft id in view state between steps and
// serves the two tips' content through a virtual `sgt-fork:` scheme. One panel per fork symbol.

import * as path from "node:path";
import * as vscode from "vscode";
import { Store } from "./store";
import { ForkDetailView } from "./types";

const TIP_SCHEME = "sgt-fork";

// A read-only content provider for the two fork tips' images, mirroring PreviewProvider (preview.ts):
// contents are keyed by a virtual `sgt-fork:` uri so `vscode.diff` can render them side by side.
// Registered once, lazily, and disposed with the extension.
class ForkTipProvider implements vscode.TextDocumentContentProvider {
  private static instance: ForkTipProvider | undefined;
  private contents = new Map<string, string>();
  private seq = 0;

  static ensure(context: vscode.ExtensionContext): ForkTipProvider {
    if (!ForkTipProvider.instance) {
      ForkTipProvider.instance = new ForkTipProvider();
      context.subscriptions.push(
        vscode.workspace.registerTextDocumentContentProvider(TIP_SCHEME, ForkTipProvider.instance)
      );
    }
    return ForkTipProvider.instance;
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    return this.contents.get(uri.toString()) ?? "";
  }

  private uri(token: string, side: string, rel: string): vscode.Uri {
    // path in the fragment so the diff title/lang resolve from the real filename (as PreviewProvider).
    return vscode.Uri.parse(`${TIP_SCHEME}:${side}/${token}/${rel}`).with({ fragment: rel });
  }

  /** Open one native `vscode.diff` per path in the tip union: left = tip A, right = tip B. */
  async showDiff(detail: ForkDetailView): Promise<void> {
    const [a, b] = detail.tips;
    if (!a || !b) return;
    const paths = new Set<string>([...Object.keys(a.files), ...Object.keys(b.files)]);
    const token = String(this.seq++);
    for (const rel of paths) {
      const left = this.uri(token, "tipA", rel);
      const right = this.uri(token, "tipB", rel);
      this.contents.set(left.toString(), a.files[rel] ?? "");
      this.contents.set(right.toString(), b.files[rel] ?? "");
      await vscode.commands.executeCommand(
        "vscode.diff",
        left,
        right,
        `${rel} — Fork: ${detail.symbol} (tip A ◀▶ tip B)`,
        { preview: true } as vscode.TextDocumentShowOptions
      );
    }
  }
}

type Stage = "tips" | "drafted" | "fulfilled" | "landed";

export class ForkResolutionPanel {
  private static panels = new Map<string, ForkResolutionPanel>();

  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private stage: Stage = "tips";
  private draft: { draftId: string; hollowIds: string[] } | undefined;
  private statusLine: string | undefined;

  static createOrShow(context: vscode.ExtensionContext, store: Store, root: string, symbol: string): void {
    const existing = ForkResolutionPanel.panels.get(symbol);
    if (existing) {
      existing.panel.reveal(vscode.ViewColumn.Beside);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "sgtForkResolution",
      `Fork: ${symbol}`,
      vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true }
    );
    const inst = new ForkResolutionPanel(panel, store, root, symbol, context);
    ForkResolutionPanel.panels.set(symbol, inst);
  }

  private readonly tipProvider: ForkTipProvider;
  private diffsOpened = false;

  private constructor(
    panel: vscode.WebviewPanel,
    private store: Store,
    private root: string,
    private symbol: string,
    context: vscode.ExtensionContext
  ) {
    this.panel = panel;
    this.tipProvider = ForkTipProvider.ensure(context);
    this.disposables.push(
      panel.onDidDispose(() => this.dispose()),
      panel.webview.onDidReceiveMessage((msg) => void this.onMessage(msg))
    );
    void this.render();
  }

  private dispose(): void {
    ForkResolutionPanel.panels.delete(this.symbol);
    this.disposables.forEach((d) => d.dispose());
  }

  private async onMessage(msg: { type: string; intent?: string; message?: string }): Promise<void> {
    try {
      if (msg.type === "mergeOp") {
        const detail = await this.store.sgt.forkDetail(this.symbol);
        if (detail.tips.length < 2) {
          this.statusLine = "✗ this fork no longer has two open tips";
        } else {
          const draft = await this.store.sgt.mergeOp(detail.tips[0].op_id, detail.tips[1].op_id, msg.intent);
          if (!draft.ok || !draft.draft_id) {
            this.statusLine = `✗ ${draft.message || "merge-op failed"}`;
          } else {
            this.draft = { draftId: draft.draft_id, hollowIds: draft.hollow_ids || [] };
            this.stage = "drafted";
            this.statusLine = `✓ drafted ${draft.draft_id.slice(0, 12)}`;
            this.store.invalidate();
          }
        }
      } else if (msg.type === "openFiles") {
        await this.openAffectedFiles();
        return; // no re-render needed
      } else if (msg.type === "fulfill" && this.draft) {
        const result = await this.store.sgt.fulfillDraft(this.draft.draftId);
        this.stage = "fulfilled";
        this.statusLine = `✓ staged ${result.op_ids?.length ?? 0} op(s) — run the oracle, then Land`;
        this.store.invalidate();
      } else if (msg.type === "land") {
        const result = await this.store.sgt.landCandidate(msg.message);
        this.stage = "landed";
        this.statusLine = `✓ landed ${(result.sha || "").slice(0, 12)}`;
        this.store.invalidate();
      }
    } catch (e: any) {
      this.statusLine = `✗ ${e.message}`;
    }
    await this.render();
  }

  private async openAffectedFiles(): Promise<void> {
    let detail: ForkDetailView;
    try {
      detail = await this.store.sgt.forkDetail(this.symbol);
    } catch {
      return;
    }
    const paths = new Set<string>();
    for (const tip of detail.tips) {
      Object.keys(tip.files).forEach((p) => paths.add(p));
    }
    for (const rel of paths) {
      const uri = vscode.Uri.file(path.isAbsolute(rel) ? rel : path.join(this.root, rel));
      try {
        const doc = await vscode.workspace.openTextDocument(uri);
        await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.One, preview: false });
      } catch {
        // file may not exist on this tip; skip
      }
    }
  }

  private async render(): Promise<void> {
    let detail: ForkDetailView;
    try {
      detail = await this.store.sgt.forkDetail(this.symbol);
    } catch (e: any) {
      this.panel.webview.html = errorHtml(e.message);
      return;
    }
    if (detail.error) {
      this.panel.webview.html = errorHtml(detail.error);
      return;
    }
    // Open the two tips in a native side-by-side diff editor once (re-renders after each action must
    // not re-spawn duplicate diff editors); the webview panel is the action strip beside it.
    if (!this.diffsOpened && detail.tips.length >= 2) {
      this.diffsOpened = true;
      void this.tipProvider.showDiff(detail);
    }
    this.panel.webview.html = this.html(detail);
  }

  private html(detail: ForkDetailView): string {
    const actions = this.actionsHtml();
    const nonce = String(Math.random()).slice(2) + this.symbol.length;

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 0; margin: 0; }
  header { padding: 10px 14px; border-bottom: 1px solid var(--vscode-panel-border); }
  header h1 { font-size: 14px; margin: 0 0 4px; }
  header p { margin: 0; opacity: 0.8; font-size: 12px; }
  #hint { padding: 8px 14px; opacity: 0.75; font-size: 12px; }
  footer { padding: 10px 14px; border-top: 1px solid var(--vscode-panel-border); display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  button { cursor: pointer; }
  #status { font-size: 12px; opacity: 0.85; }
  input[type=text] { background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border); padding: 3px 6px; }
</style>
<title>Fork: ${escapeHtml(detail.symbol)}</title>
</head>
<body>
<header>
  <h1>Fork on <code>${escapeHtml(detail.symbol)}</code></h1>
  <p>${escapeHtml(detail.remedy)}</p>
</header>
<div id="hint">Tip A ◀▶ Tip B opened in a diff editor. Reconcile the working tree, then use the actions below.</div>
<footer>${actions}</footer>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  document.querySelectorAll("[data-action]").forEach((el) => {
    el.addEventListener("click", () => {
      const type = el.getAttribute("data-action");
      const msg = { type };
      if (type === "mergeOp") {
        msg.intent = document.getElementById("intent")?.value || undefined;
      }
      if (type === "land") {
        msg.message = document.getElementById("landMessage")?.value || undefined;
      }
      vscode.postMessage(msg);
    });
  });
</script>
</body>
</html>`;
  }

  private actionsHtml(): string {
    const status = this.statusLine ? `<span id="status">${escapeHtml(this.statusLine)}</span>` : "";
    if (this.stage === "tips") {
      return `
        <input type="text" id="intent" placeholder="intent (optional)" />
        <button data-action="mergeOp">Draft merge (sgt merge-op)</button>
        ${status}`;
    }
    if (this.stage === "drafted") {
      return `
        <button data-action="openFiles">Open affected files</button>
        <span>Hand-edit the working tree to reconcile, then:</span>
        <button data-action="fulfill">Fulfill from working tree</button>
        ${status}`;
    }
    if (this.stage === "fulfilled") {
      return `
        <input type="text" id="landMessage" placeholder="commit message (optional)" />
        <button data-action="land">Land</button>
        ${status}`;
    }
    return `<span>Resolved.</span> ${status}`;
  }
}

function errorHtml(message: string): string {
  return `<!DOCTYPE html><html><body style="font-family:var(--vscode-font-family);padding:14px;">
  <p>✗ ${escapeHtml(message)}</p></body></html>`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
