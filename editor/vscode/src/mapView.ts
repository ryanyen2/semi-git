// The Code Map: a Webview View (bottom panel) showing the deterministic code-entity graph
// (sgt.api entity_graph_view) — functions/classes/methods connected by containment + the
// transitive-reduced calls/imports, colored by owning feature. A checkpoint scrubber drives
// timeframe_view so you watch the codebase develop: regions are born, grow, and retire across
// frames. Entity colors are computed here via color.ts (the canonical TS OKLCH generator) so
// the webview script never re-implements the color contract.

import * as vscode from "vscode";
import { colorForNode } from "./color";
import { Store } from "./store";
import { EntityMapView } from "./types";

export class MapViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "sgtEntityMap";
  private view: vscode.WebviewView | undefined;
  private disposables: vscode.Disposable[] = [];

  constructor(
    private context: vscode.ExtensionContext,
    private store: Store
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

  private colorize(map: EntityMapView): EntityMapView {
    for (const e of map.entities) {
      e.color = e.node_id ? colorForNode(e.node_id) : null;
    }
    return map;
  }

  /** Frame count = distinct sgt commits (the scrubber's range; CLI `timeframe <n>` is the truth). */
  private async frameCount(): Promise<number> {
    try {
      const graph = await this.store.graph(true);
      const commits = new Set<string>();
      for (const n of graph.nodes) {
        for (const c of n.commits ?? []) {
          commits.add(c);
        }
      }
      return Math.max(1, commits.size);
    } catch {
      return 1;
    }
  }

  private async onMessage(msg: any): Promise<void> {
    if (!msg) {
      return;
    }
    if (msg.type === "ready") {
      void this.refresh();
    } else if (msg.type === "scrub" && typeof msg.frame === "number") {
      // Past frame — drive timeframe_view; the webview diffs against the prior frame.
      try {
        const map = this.colorize(await this.store.timeframe(msg.frame));
        this.view?.webview.postMessage({ type: "frame", map });
      } catch (e: any) {
        this.view?.webview.postMessage({ type: "error", message: e.message });
      }
    }
  }

  private async refresh(): Promise<void> {
    if (!this.view) {
      return;
    }
    try {
      const [map, frames] = await Promise.all([this.store.map(true), this.frameCount()]);
      this.view.webview.postMessage({ type: "map", map: this.colorize(map), frames });
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
    const js = this.uri(webview, "map.js");
    const css = this.uri(webview, "map.css");
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${cspSource} data:; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<link href="${css}" rel="stylesheet">
<title>Code Map</title>
</head>
<body>
<div id="app">
  <div id="header">
    <span class="brand">◆ Code Map</span>
    <span class="crumb-sep">›</span>
    <span id="count" class="crumb"></span>
    <span class="spacer"></span>
    <span id="frame-label" class="chip">now</span>
  </div>
  <div id="scrubber-row">
    <span class="scrub-icon" title="Scrub checkpoints">⏱</span>
    <input id="scrubber" type="range" min="1" max="1" value="1" step="1" aria-label="Checkpoint" />
    <button id="now" class="icon-btn" title="Jump to now">now</button>
  </div>
  <div id="scroll" tabindex="0">
    <div id="components"></div>
    <div id="empty" hidden>No entities yet — write some Python/TypeScript, then <code>sgt map</code>.</div>
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
