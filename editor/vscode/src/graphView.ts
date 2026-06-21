// The Feature Graph as a Webview *View* docked in the bottom Panel (like GitLens's Commit Graph),
// not a floating editor tab. Dense, row-based: a KIND ref-pill column, a git-style swim-lane GRAPH
// column, and a short-label MESSAGE column, with an in-situ detail pane (no modal popups). It also
// shows live agent presence — which features are being edited right now (uncommitted drift) and
// which just landed — so you can watch the coding agent work the graph in real time.

import * as vscode from "vscode";
import { Store } from "./store";

export class GraphViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "sgtFeatureGraph";
  private view: vscode.WebviewView | undefined;
  private disposables: vscode.Disposable[] = [];
  private pendingSelect: string | undefined;

  constructor(
    private context: vscode.ExtensionContext,
    private store: Store,
    private refreshBlame: () => void
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

  /** Reveal the panel and open the in-situ detail for a node (the inspect entrypoint — no modal). */
  selectNode(id: string): void {
    this.pendingSelect = id;
    if (this.view) {
      this.view.show?.(false);
      this.view.webview.postMessage({ type: "select", id });
    }
    void vscode.commands.executeCommand(`${GraphViewProvider.viewId}.focus`);
  }

  private async onMessage(msg: any): Promise<void> {
    if (!msg) {
      return;
    }
    if (msg.type === "ready") {
      void this.refresh();
    } else if (msg.type === "preview" && msg.id) {
      // Non-mutating dry-run -> opens a read-only diff. No popup.
      const cmd =
        msg.action === "switch"
          ? msg.on
            ? "sgt.previewSwitchOn"
            : "sgt.previewSwitchOff"
          : "sgt.previewRevert";
      void vscode.commands.executeCommand(cmd, msg.id);
    } else if (msg.type === "apply" && msg.id) {
      // The webview already inline-confirmed (two-click arm), so apply directly — no modal.
      await this.apply(msg.action, msg.id, msg.on);
    }
  }

  private async apply(action: string, id: string, on?: boolean): Promise<void> {
    const args =
      action === "switch" ? ["switch", id, on ? "on" : "off"] : ["revert", id];
    try {
      const report = await this.store.sgt.mutate(args);
      this.store.invalidate(); // triggers refresh on every surface
      // Non-modal toast (a status message, not a blocking popup).
      vscode.window.setStatusBarMessage(`sgt: ${report.trim().split("\n")[0] || "done"}`, 4000);
    } catch (e: any) {
      this.view?.webview.postMessage({ type: "applyError", id, message: e.message });
    }
  }

  private async refresh(): Promise<void> {
    if (!this.view) {
      return;
    }
    try {
      const [graph, status] = await Promise.all([this.store.graph(true), this.store.status(true)]);
      const editing = await this.editingNodes(status);
      this.view.webview.postMessage({
        type: "graph",
        graph,
        status,
        editing,
        select: this.pendingSelect,
      });
      this.pendingSelect = undefined;
    } catch (e: any) {
      this.view.webview.postMessage({ type: "error", message: e.message });
    }
  }

  /** Features with uncommitted drift in a file they own — i.e. the agent is editing them now. */
  private async editingNodes(status: any): Promise<string[]> {
    const modified: string[] = (status?.drift?.modified ?? []).concat(status?.drift?.deleted ?? []);
    const out = new Set<string>();
    for (const file of modified) {
      try {
        const b = await this.store.blame(file, true);
        for (const s of b.spans ?? []) {
          if (s.node_id) {
            out.add(s.node_id);
          }
        }
      } catch {
        // file may be unparseable mid-edit — skip
      }
    }
    return [...out];
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
    <span id="presence" class="presence" hidden></span>
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
  <div id="main">
    <div id="left">
      <div id="colhead"><span class="c-refs">KIND</span><span class="c-graph">GRAPH</span><span class="c-msg">FEATURE</span></div>
      <div id="scroll" tabindex="0">
        <div id="rows"><svg id="lanes" aria-hidden="true"></svg><div id="rowlist"></div></div>
        <div id="empty" hidden>No features yet — run <code>sgt plan "…"</code>.</div>
      </div>
    </div>
    <aside id="detail" hidden></aside>
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
