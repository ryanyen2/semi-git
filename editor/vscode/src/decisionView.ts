// The Decision Graph: the single graph surface (replaces the Feature Graph forest and the Code Map
// scrubber). A swim-lane DAG where x = time (a decision's checkpoint landing), each lane is a
// feature, and the tip of each lane is its latest decision. "In force" (the frontier that
// materializes the working tree) is a separate channel — a glyph/halo, never hue — so you can hold
// feature-A@v3 alongside feature-B@latest. builds-on edges are derived; only revises/fork are stored.

import * as vscode from "vscode";
import { Store } from "./store";
import { ActivityEvent } from "./types";

export class DecisionViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "sgtDecisionGraph";
  private view: vscode.WebviewView | undefined;
  private disposables: vscode.Disposable[] = [];
  private pendingSelect: string | undefined;

  constructor(
    private context: vscode.ExtensionContext,
    private store: Store,
    private root: string
  ) {
    this.disposables.push(this.store.onDidChange(() => void this.refresh()));
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "media")],
    };
    webviewView.webview.html = this.html(webviewView.webview);
    webviewView.webview.onDidReceiveMessage((msg) => this.onMessage(msg));
    webviewView.onDidChangeVisibility(() => {
      if (webviewView.visible) {
        void this.refresh();
      }
    });
  }

  /** Reveal the panel and select a decision/feature (the inspect entrypoint from the tree). */
  selectNode(id: string): void {
    this.pendingSelect = id;
    if (this.view) {
      this.view.show?.(false);
      this.view.webview.postMessage({ type: "select", id });
    }
    void vscode.commands.executeCommand(`${DecisionViewProvider.viewId}.focus`);
  }

  /** Forward live Claude Code activity (ephemeral presence telemetry); the view may show it. */
  postActivity(events: ActivityEvent[]): void {
    this.view?.webview.postMessage({ type: "activity", events });
  }

  private async onMessage(msg: any): Promise<void> {
    if (!msg) {
      return;
    }
    if (msg.type === "ready") {
      void this.refresh();
    } else if (msg.type === "compose" && msg.feature && msg.decision) {
      await this.mutate(["compose", msg.feature, msg.decision], `composed ${msg.feature}`);
    } else if (msg.type === "revert" && msg.id) {
      await this.mutate(["revert", msg.id], `reverted ${msg.id}`);
    } else if (msg.type === "reveal" && msg.file) {
      await this.reveal(msg.file, msg.target);
    }
  }

  private async mutate(args: string[], label: string): Promise<void> {
    try {
      const report = await this.store.sgt.mutate(args);
      this.store.invalidate();
      vscode.window.setStatusBarMessage(`sgt: ${report.trim().split("\n")[0] || label}`, 4000);
    } catch (e: any) {
      this.view?.webview.postMessage({ type: "error", message: e.message });
    }
  }

  /** Open the file a decision's footprint touches and flash the symbol. */
  private async reveal(file: string, target?: string): Promise<void> {
    try {
      const uri = vscode.Uri.file(this.root.endsWith("/") ? this.root + file : `${this.root}/${file}`);
      const doc = await vscode.workspace.openTextDocument(uri);
      const editor = await vscode.window.showTextDocument(doc, { preview: true });
      const range = (await this.symbolRange(uri, doc, target)) || new vscode.Range(0, 0, 0, 0);
      editor.selection = new vscode.Selection(range.start, range.start);
      editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
    } catch {
      vscode.window.setStatusBarMessage(`sgt: couldn't open ${file}`, 3000);
    }
  }

  private async symbolRange(
    uri: vscode.Uri,
    doc: vscode.TextDocument,
    target?: string
  ): Promise<vscode.Range | undefined> {
    if (!target) {
      return undefined;
    }
    const leaf = target.split(/[.:]/).pop() || target;
    const re = new RegExp(`^\\s*(?:async\\s+)?(?:def|class)\\s+${escapeRe(leaf)}\\b`);
    for (let i = 0; i < doc.lineCount; i++) {
      if (re.test(doc.lineAt(i).text)) {
        const col = doc.lineAt(i).firstNonWhitespaceCharacterIndex;
        return new vscode.Range(i, col, i, doc.lineAt(i).text.length);
      }
    }
    return undefined;
  }

  private async refresh(): Promise<void> {
    if (!this.view) {
      return;
    }
    try {
      const graph = await this.store.decisions(true);
      // The webview self-colors from the feature/lane id (the OKLCH math is mirrored in
      // media/decision.js, since it can't import color.ts across the bundle boundary).
      this.view.webview.postMessage({ type: "decisions", graph, select: this.pendingSelect });
      this.pendingSelect = undefined;
    } catch (e: any) {
      this.view.webview.postMessage({ type: "error", message: e.message });
    }
  }

  private uri(webview: vscode.Webview, ...p: string[]): vscode.Uri {
    return webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", ...p));
  }

  private html(webview: vscode.Webview): string {
    const nonce = nonceStr();
    const cspSource = webview.cspSource;
    const js = this.uri(webview, "decision.js");
    const css = this.uri(webview, "decision.css");
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${cspSource} data:; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<link href="${css}" rel="stylesheet">
<title>Decision Graph</title>
</head>
<body>
<div id="app">
  <div id="header">
    <span class="brand">◆ Decision Graph</span>
    <span class="crumb-sep">›</span>
    <span id="count" class="crumb"></span>
    <span id="head" class="chip" title="the in-force composition that materializes the working tree"></span>
    <span class="spacer"></span>
    <button id="refresh" class="icon-btn" title="Refresh">↻</button>
  </div>
  <div id="main">
    <div id="stage"><svg id="svg" aria-hidden="true"></svg></div>
    <div id="divider" role="separator" aria-orientation="vertical" tabindex="0" title="Drag to resize"></div>
    <aside id="detail"><div id="empty">Select a decision.</div></aside>
  </div>
</div>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
  }
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function nonceStr(): string {
  let t = "";
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) {
    t += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return t;
}
