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
    } else if (msg.type === "compose" && msg.decision) {
      // Pinning a lane to a decision is `restore <decision-id>` (the decision id carries the lane).
      await this.mutate(["restore", msg.decision], `pinned ${msg.feature ?? msg.decision}`);
    } else if (msg.type === "distill" && msg.id) {
      // LLM rationale fill for one decision; offline this is a no-op (no key) and just reports so.
      await this.mutate(["decisions", "distill", msg.id], `distilled ${msg.id}`);
    } else if (msg.type === "reveal" && msg.file) {
      await this.reveal(msg.file, msg.target);
    } else if (msg.type === "command" && typeof msg.id === "string") {
      // The webview drives the shipped commands (preview diffs, revert/suspend/restore with their
      // confirmation modals) rather than re-implementing them — one mutation path, every surface.
      if (DecisionViewProvider.allowed.has(msg.id)) {
        await vscode.commands.executeCommand(msg.id, msg.arg);
      }
    }
  }

  /** Commands the webview may invoke. Keeps the message bridge from being a generic command exec. */
  private static readonly allowed = new Set([
    "sgt.previewRevert",
    "sgt.previewSwitchOff",
    "sgt.previewSwitchOn",
    "sgt.revert",
    "sgt.switchOff",
    "sgt.switchOn",
  ]);

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
    const anime = this.uri(webview, "anime.min.js");
    const css = this.uri(webview, "decision.css");
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${cspSource} data:; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<link href="${css}" rel="stylesheet">
<title>Decision Graph</title>
</head>
<body class="show-feed">
<div id="app">
  <header id="header">
    <span class="brand">Decision Graph</span>
    <span id="count" class="crumb"></span>
    <span id="head" class="chip" title="the in-force composition that materializes the working tree"></span>
    <span class="spacer"></span>
    <div id="density" role="group" aria-label="Graph density">
      <button class="seg" data-d="airy" title="Airy" aria-label="Airy">
        <svg viewBox="0 0 13 13"><rect class="bar" x="1" y="1.5" width="11" height="1.5"/><rect class="bar" x="1" y="5.75" width="11" height="1.5"/><rect class="bar" x="1" y="10" width="11" height="1.5"/></svg>
      </button>
      <button class="seg" data-d="default" title="Default" aria-label="Default">
        <svg viewBox="0 0 13 13"><rect class="bar" x="1" y="1.5" width="11" height="1.5"/><rect class="bar" x="1" y="4.5" width="11" height="1.5"/><rect class="bar" x="1" y="7.5" width="11" height="1.5"/><rect class="bar" x="1" y="10.5" width="11" height="1.5"/></svg>
      </button>
      <button class="seg" data-d="compact" title="Compact" aria-label="Compact">
        <svg viewBox="0 0 13 13"><rect class="bar" x="1" y="1.5" width="11" height="1.2"/><rect class="bar" x="1" y="3.8" width="11" height="1.2"/><rect class="bar" x="1" y="6.1" width="11" height="1.2"/><rect class="bar" x="1" y="8.4" width="11" height="1.2"/><rect class="bar" x="1" y="10.7" width="11" height="1.2"/></svg>
      </button>
    </div>
    <button id="toggle-spread" class="icon-btn" title="Avoid edge crossings (spread lanes by dependency)">⪧</button>
    <button id="toggle-feed" class="icon-btn" title="Toggle agent activity">◴</button>
    <button id="toggle-detail" class="icon-btn" title="Toggle detail pane">▤</button>
    <button id="refresh" class="icon-btn" title="Refresh">↻</button>
  </header>
  <div id="main">
    <div id="stage">
      <div id="list">
        <svg id="rail" aria-hidden="true"></svg>
        <div id="rows"></div>
        <div id="empty-state">
          <svg viewBox="0 0 64 64" aria-hidden="true">
            <path class="es-line" d="M16 14 C30 14 30 32 44 32"/>
            <path class="es-line" d="M16 14 C30 14 30 50 44 50"/>
            <circle class="es-dot-fill" cx="16" cy="14" r="4"/>
            <circle class="es-dot-planned" cx="44" cy="32" r="3.5"/>
            <circle class="es-dot-planned" cx="44" cy="50" r="3.5"/>
          </svg>
          <h2>No decisions yet</h2>
          <p>Plan a feature and checkpoint it, and each decision lands here as a node in the lineage. Run <code>sgt plan</code> to begin.</p>
        </div>
      </div>
    </div>
    <div id="handle" title="Drag to resize"></div>
    <aside id="detail"><div class="placeholder">Select a decision to read its rationale.</div></aside>
  </div>
  <div id="feedbar">
    <div id="feedhead"><span id="feed-dot"></span>Agent activity</div>
    <div id="feed"></div>
  </div>
</div>
<div id="menu"></div>
<script nonce="${nonce}" src="${anime}"></script>
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
