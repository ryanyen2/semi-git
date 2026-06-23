// The Feature Graph as a Webview *View* docked in the bottom Panel (like GitLens's Commit Graph),
// not a floating editor tab. Dense, row-based: a KIND ref-pill column, a git-style swim-lane GRAPH
// column, and a short-label MESSAGE column, with an in-situ detail pane (no modal popups). It also
// shows live agent presence — which features are being edited right now (uncommitted drift) and
// which just landed — so you can watch the coding agent work the graph in real time.

import * as vscode from "vscode";
import { Store } from "./store";
import { ActivityEvent, PendingWork } from "./types";

export class GraphViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "sgtFeatureGraph";
  private view: vscode.WebviewView | undefined;
  private disposables: vscode.Disposable[] = [];
  private pendingSelect: string | undefined;

  constructor(
    private context: vscode.ExtensionContext,
    private store: Store,
    private root: string,
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

  /** Forward live Claude Code activity (ephemeral presence telemetry) to the Activity feed. */
  postActivity(events: ActivityEvent[]): void {
    this.view?.webview.postMessage({ type: "activity", events });
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
    } else if (msg.type === "reveal" && msg.file) {
      await this.reveal(msg.file, msg.target);
    }
  }

  /** Open the file an effect touched and flash the symbol's range (click-through from the effects
   *  list). Best-effort: precise via the document-symbol provider, else a def/class text search. */
  private async reveal(file: string, target?: string): Promise<void> {
    try {
      const uri = vscode.Uri.file(this.root.endsWith("/") ? this.root + file : `${this.root}/${file}`);
      const doc = await vscode.workspace.openTextDocument(uri);
      const editor = await vscode.window.showTextDocument(doc, { preview: true });
      const range = (await this.symbolRange(uri, doc, target)) || new vscode.Range(0, 0, 0, 0);
      editor.selection = new vscode.Selection(range.start, range.start);
      editor.revealRange(range, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
      this.flashRange(editor, new vscode.Range(range.start.line, 0, range.end.line, 0));
    } catch (e: any) {
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
    const leaf = target.split(/[.:]/).pop() || target; // "Class.method" -> "method"
    try {
      const syms = (await vscode.commands.executeCommand(
        "vscode.executeDocumentSymbolProvider",
        uri
      )) as vscode.DocumentSymbol[] | undefined;
      const hit = syms && findSymbol(syms, leaf);
      if (hit) {
        return hit.selectionRange;
      }
    } catch {
      // provider not ready (e.g. Python extension still loading) — fall back to text search
    }
    const re = new RegExp(`^\\s*(?:async\\s+)?(?:def|class)\\s+${escapeRe(leaf)}\\b`);
    for (let i = 0; i < doc.lineCount; i++) {
      if (re.test(doc.lineAt(i).text)) {
        const col = doc.lineAt(i).firstNonWhitespaceCharacterIndex;
        return new vscode.Range(i, col, i, doc.lineAt(i).text.length);
      }
    }
    return undefined;
  }

  /** A transient whole-line highlight + overview-ruler mark that clears itself after ~1.6s. */
  private flashRange(editor: vscode.TextEditor, range: vscode.Range): void {
    const deco = vscode.window.createTextEditorDecorationType({
      isWholeLine: true,
      backgroundColor: new vscode.ThemeColor("editor.findMatchHighlightBackground"),
      borderWidth: "0 0 0 2px",
      borderStyle: "solid",
      borderColor: new vscode.ThemeColor("focusBorder"),
      overviewRulerColor: new vscode.ThemeColor("focusBorder"),
      overviewRulerLane: vscode.OverviewRulerLane.Full,
    });
    editor.setDecorations(deco, [range]);
    setTimeout(() => deco.dispose(), 1600);
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
      const { editing, pending } = await this.presence(status);
      this.view.webview.postMessage({
        type: "graph",
        graph,
        status,
        editing,
        pending,
        select: this.pendingSelect,
      });
      this.pendingSelect = undefined;
    } catch (e: any) {
      this.view.webview.postMessage({ type: "error", message: e.message });
    }
  }

  /** Live presence from drift, split two ways:
   *  - `editing`: features that own a drifted line (the agent is editing an *existing* node).
   *  - `pending`: drifted/added files that blame can't attribute to any node — brand-new work the
   *    agent is building before its checkpoint. Surfaced as ghost rows so the graph isn't stale. */
  private async presence(status: any): Promise<{ editing: string[]; pending: PendingWork[] }> {
    const modified: string[] = (status?.drift?.modified ?? []).concat(status?.drift?.deleted ?? []);
    const added: string[] = status?.drift?.added ?? [];
    const editing = new Set<string>();
    const pending: PendingWork[] = [];
    for (const file of modified) {
      try {
        const b = await this.store.blame(file, true);
        const owners = (b.spans ?? []).filter((s: any) => s.node_id);
        for (const s of owners) {
          editing.add(s.node_id as string);
        }
        if (!owners.length) {
          pending.push({ label: baseName(file), file });
        }
      } catch {
        // file may be unparseable mid-edit — treat as pending work in flight
        pending.push({ label: baseName(file), file });
      }
    }
    for (const file of added) {
      pending.push({ label: baseName(file), file }); // newly-created files have no node yet
    }
    return { editing: [...editing], pending };
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
      <span><i class="g">◆</i>active</span>
      <span><i class="g">◇</i>planned</span>
      <span><i class="g dim">◆</i>suspended</span>
      <span><i class="g">⚠</i>conflict</span>
    </span>
  </div>
  <canvas id="minimap" aria-hidden="true"></canvas>
  <div id="main">
    <div id="left">
      <div id="colhead"><span class="c-refs">KIND</span><span class="c-graph">GRAPH</span><span class="c-msg">FEATURE</span></div>
      <div id="scroll" tabindex="0">
        <div id="ghosts"></div>
        <div id="rows"><svg id="lanes" aria-hidden="true"></svg><div id="rowlist"></div></div>
        <div id="empty" hidden>No features yet — run <code>sgt plan "…"</code>.</div>
      </div>
    </div>
    <div id="divider" role="separator" aria-orientation="vertical" tabindex="0" title="Drag to resize"></div>
    <aside id="detail">
      <div id="pane-tabs" role="tablist">
        <button class="tab" data-tab="activity" role="tab">Activity</button>
        <button class="tab" data-tab="inspect" role="tab">Inspect</button>
      </div>
      <div id="pane-body"></div>
    </aside>
  </div>
</div>
<div id="ctxmenu" hidden role="menu"></div>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
  }
}

function baseName(p: string): string {
  return p.split("/").pop() || p;
}

function findSymbol(syms: vscode.DocumentSymbol[], name: string): vscode.DocumentSymbol | undefined {
  for (const s of syms) {
    if (s.name === name) {
      return s;
    }
    const inner = findSymbol(s.children || [], name);
    if (inner) {
      return inner;
    }
  }
  return undefined;
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
