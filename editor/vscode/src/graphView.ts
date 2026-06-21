// The Feature Graph as a Webview *View* docked in the bottom Panel (like GitLens's Commit Graph),
// not a floating editor tab. It renders a dense, row-based DAG — a refs/status column, a
// git-style swim-lane GRAPH column, and an intent column — from the `sgt export` + `sgt status`
// projections. The webview posts back node clicks, which route to `sgt.openNode`.

import * as vscode from "vscode";
import { Store } from "./store";

export class GraphViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "sgtFeatureGraph";
  private view: vscode.WebviewView | undefined;
  private disposables: vscode.Disposable[] = [];

  constructor(private context: vscode.ExtensionContext, private store: Store) {
    this.disposables.push(this.store.onDidChange(() => void this.refresh()));
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "media")],
    };
    webviewView.webview.html = this.html(webviewView.webview);
    webviewView.webview.onDidReceiveMessage((msg) => {
      if (msg?.type === "open" && msg.id) {
        vscode.commands.executeCommand("sgt.openNode", msg.id);
      } else if (msg?.type === "ready") {
        void this.refresh();
      }
    });
    webviewView.onDidChangeVisibility(() => {
      if (webviewView.visible) {
        void this.refresh();
      }
    });
  }

  private async refresh(): Promise<void> {
    if (!this.view) {
      return;
    }
    try {
      const [graph, status] = await Promise.all([this.store.graph(true), this.store.status(true)]);
      this.view.webview.postMessage({ type: "graph", graph, status });
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
    const js = this.uri(webview, "graph.js");
    const css = this.uri(webview, "graph.css");
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${cspSource} data:; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<link href="${css}" rel="stylesheet">
<title>Feature Graph</title>
</head>
<body>
<div id="app">
  <div id="header">
    <span class="brand">◆ Feature Graph</span>
    <span class="crumb-sep">›</span>
    <span id="count" class="crumb"></span>
    <span id="drift" class="chip"></span>
    <span class="spacer"></span>
    <button id="refresh" class="icon-btn" title="Refresh">↻</button>
  </div>
  <div id="toolbar">
    <span class="search-icon">⌕</span>
    <input id="filter" type="text" placeholder="Search features…" aria-label="Search features" />
    <span id="legend" aria-hidden="true">
      <span><i class="g">●</i>active</span>
      <span><i class="g">○</i>planned</span>
      <span><i class="g">◐</i>suspended</span>
      <span><i class="g">⚠</i>conflict</span>
    </span>
  </div>
  <canvas id="minimap" aria-hidden="true"></canvas>
  <div id="colhead"><span class="c-refs">KIND</span><span class="c-graph">GRAPH</span><span class="c-msg">INTENT</span></div>
  <div id="scroll" tabindex="0">
    <div id="rows"><svg id="lanes" aria-hidden="true"></svg><div id="rowlist"></div></div>
    <div id="empty" hidden>No features yet — run <code>sgt plan "…"</code>.</div>
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

function nonceStr(): string {
  let t = "";
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) {
    t += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return t;
}
