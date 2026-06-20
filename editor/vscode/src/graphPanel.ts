// The feature-DAG webview: a real graph (multi-parent edges), laid out as dependency layers.
// We render with hand-written SVG in the webview (no external graph lib — keeps the bundle
// tiny and offline). The extension posts the `sgt export` payload; the webview posts back
// node clicks, which we route to `sgt.openNode`.

import * as vscode from "vscode";
import { Store } from "./store";

export class GraphPanel {
  private static current: GraphPanel | undefined;
  private panel: vscode.WebviewPanel;
  private disposables: vscode.Disposable[] = [];

  static show(context: vscode.ExtensionContext, store: Store): void {
    const column = vscode.ViewColumn.Beside;
    if (GraphPanel.current) {
      GraphPanel.current.panel.reveal(column);
      void GraphPanel.current.refresh();
      return;
    }
    const panel = vscode.window.createWebviewPanel("sgtGraph", "Feature DAG", column, {
      enableScripts: true,
      // retain so the user's pan/zoom viewport survives tab switches.
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
    });
    GraphPanel.current = new GraphPanel(panel, context, store);
  }

  private constructor(
    panel: vscode.WebviewPanel,
    private context: vscode.ExtensionContext,
    private store: Store
  ) {
    this.panel = panel;
    this.panel.webview.html = this.html();
    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage(
      (msg) => {
        if (msg?.type === "open" && msg.id) {
          vscode.commands.executeCommand("sgt.openNode", msg.id);
        } else if (msg?.type === "ready") {
          void this.refresh();
        }
      },
      null,
      this.disposables
    );
    this.disposables.push(this.store.onDidChange(() => void this.refresh()));
  }

  private async refresh(): Promise<void> {
    try {
      const graph = await this.store.graph(true);
      this.panel.webview.postMessage({ type: "graph", graph });
    } catch (e: any) {
      this.panel.webview.postMessage({ type: "error", message: e.message });
    }
  }

  private uri(...p: string[]): vscode.Uri {
    return this.panel.webview.asWebviewUri(
      vscode.Uri.joinPath(this.context.extensionUri, "media", ...p)
    );
  }

  private html(): string {
    const nonce = nonceStr();
    const cspSource = this.panel.webview.cspSource;
    const js = this.uri("graph.js");
    const css = this.uri("graph.css");
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${cspSource} data:; style-src ${cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
<link href="${css}" rel="stylesheet">
<title>Feature DAG</title>
</head>
<body>
<div id="toolbar">
  <input id="filter" type="text" placeholder="Filter features…" aria-label="Filter features" />
  <button id="fit" class="tool" title="Fit graph to window">Fit</button>
  <span id="status" aria-live="polite"></span>
  <span id="legend" aria-hidden="true">
    <span><span class="g">●</span> active</span>
    <span><span class="g">○</span> planned</span>
    <span><span class="g">◐</span> suspended</span>
    <span><span class="g">⚠</span> conflict</span>
  </span>
</div>
<div id="canvas"><svg id="graph"><g id="viewport"><g id="edges"></g><g id="nodes"></g></g></svg></div>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
  }

  private dispose(): void {
    GraphPanel.current = undefined;
    this.panel.dispose();
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
