// Fork resolution: an N-column view of a same-symbol chain fork's tip images (`ForkDetailView`
// already carries full file content per tip -- no extra fold call needed) plus the compose-and-
// fulfill wizard that used to be a copy-tip-ids-to-clipboard placeholder in commands.ts:
// `sgt merge-op <a> <b>` drafts a reconciling hollow -> the user hand-edits the working tree to
// match -> `sgt fulfill <draft-id> --from-tree` stages it -> bare `sgt land` commits it. Every
// step but the hand-edit is a real, unmodified kernel verb; the wizard just keeps the draft id in
// view state between steps. One panel per fork symbol.

import * as path from "node:path";
import * as vscode from "vscode";
import { Store } from "./store";
import { ForkDetailView } from "./types";

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
    const inst = new ForkResolutionPanel(panel, store, root, symbol);
    ForkResolutionPanel.panels.set(symbol, inst);
  }

  private constructor(
    panel: vscode.WebviewPanel,
    private store: Store,
    private root: string,
    private symbol: string
  ) {
    this.panel = panel;
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
    this.panel.webview.html = this.html(detail);
  }

  private html(detail: ForkDetailView): string {
    const paths = new Set<string>();
    for (const tip of detail.tips) {
      Object.keys(tip.files).forEach((p) => paths.add(p));
    }
    const columns = detail.tips
      .map((tip) => {
        const files = [...paths]
          .map((p) => {
            const content = tip.files[p];
            return content === undefined
              ? `<div class="file"><div class="path">${escapeHtml(p)}</div><pre class="missing">(not touched by this tip)</pre></div>`
              : `<div class="file"><div class="path">${escapeHtml(p)}</div><pre>${escapeHtml(content)}</pre></div>`;
          })
          .join("");
        return `<div class="col"><h3>${escapeHtml(tip.op_id.slice(0, 12))}</h3>${files}</div>`;
      })
      .join("");

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
  #columns { display: flex; gap: 1px; overflow: auto; background: var(--vscode-panel-border); }
  .col { flex: 1; min-width: 320px; background: var(--vscode-editor-background); }
  .col h3 { font-size: 12px; margin: 0; padding: 6px 10px; background: var(--vscode-titleBar-inactiveBackground); }
  .file .path { font-size: 11px; opacity: 0.75; padding: 4px 10px; }
  .file pre { margin: 0; padding: 0 10px 10px; font-size: 12px; white-space: pre-wrap; }
  .file pre.missing { opacity: 0.5; font-style: italic; }
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
<div id="columns">${columns}</div>
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
