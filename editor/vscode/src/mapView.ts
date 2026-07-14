// The Feature Map webview panel: a rail visualization (redesigned from
// experiments/patch_clustering/out/rail2.html's language) of `map_view` + `history_view`,
// replacing the old sgtGraph sidebar TreeView. A singleton per workspace. The webview's own
// message protocol (`ready` / `previewVerb` / `applyVerb` / `renamePrompt`) drives media/map.js's
// hover-preview and action-bar interactions; every mutation still goes through the real, unmodified
// `sgt merge`/`split`/`rename`/`move`/`revert` commands -- a preview never writes anything.

import * as vscode from "vscode";
import { colorForNode } from "./color";
import { Store } from "./store";

export class MapViewPanel {
  private static current: MapViewPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly previewCache = new Map<string, unknown>();

  static createOrShow(context: vscode.ExtensionContext, store: Store): void {
    if (MapViewPanel.current) {
      MapViewPanel.current.panel.reveal(vscode.ViewColumn.Active);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "sgtFeatureMap",
      "SGT Feature Map",
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
      }
    );
    MapViewPanel.current = new MapViewPanel(panel, context, store);
  }

  private constructor(panel: vscode.WebviewPanel, context: vscode.ExtensionContext, private store: Store) {
    this.panel = panel;
    this.panel.webview.html = this.html(context);
    this.disposables.push(
      this.panel.onDidDispose(() => this.dispose()),
      this.panel.webview.onDidReceiveMessage((msg) => void this.onMessage(msg)),
      store.onDidChange(() => void this.pushState())
    );
  }

  private html(context: vscode.ExtensionContext): string {
    const webview = this.panel.webview;
    const jsUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "media", "map.js"));
    const cssUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "media", "map.css"));
    const nonce = String(context.extension.packageJSON.version || "0").length + "-" + Date.now();
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';" />
<link rel="stylesheet" href="${cssUri}" />
<title>SGT Feature Map</title>
</head>
<body>
<div id="root"></div>
<div id="detail"></div>
<script nonce="${nonce}" src="${jsUri}"></script>
</body>
</html>`;
  }

  private async pushState(): Promise<void> {
    let map;
    let history;
    try {
      [map, history] = await Promise.all([this.store.map(), this.store.history()]);
    } catch (e: any) {
      void this.panel.webview.postMessage({ type: "error", message: e.message });
      return;
    }
    const nodes = map.nodes.map((n) => ({ ...n, color: n.kind === "feature" ? colorForNode(n.id) : null }));
    this.previewCache.clear();
    void this.panel.webview.postMessage({ type: "state", map: { ...map, nodes }, history });
  }

  private async onMessage(msg: any): Promise<void> {
    switch (msg.type) {
      case "ready":
        void this.pushState();
        return;
      case "previewVerb":
        await this.preview(msg.verb, msg.args, msg.seq);
        return;
      case "applyVerb":
        await this.apply(msg.verb, msg.args);
        return;
      case "renamePrompt":
        await this.renamePrompt(msg.feature);
        return;
      default:
        return;
    }
  }

  private async preview(verb: string, args: string[], seq: number): Promise<void> {
    const key = `${verb}:${args.join("")}`;
    let result = this.previewCache.get(key);
    if (result === undefined) {
      try {
        result = await this.store.sgt.previewVerb(verb, args);
      } catch (e: any) {
        result = { ok: false, message: e.message, affected_features: [] };
      }
      this.previewCache.set(key, result);
    }
    void this.panel.webview.postMessage({ type: "previewResult", seq, result });
  }

  private async renamePrompt(feature: string): Promise<void> {
    const label = await vscode.window.showInputBox({ prompt: `New label for ${feature}` });
    if (label) {
      await this.apply("rename", [feature, label]);
    }
  }

  private async apply(verb: string, args: string[]): Promise<void> {
    if (verb === "revert") {
      // Reuse the existing `sgt.revert` command (commands.ts) rather than re-implementing its
      // confirm-dialog + mutate + invalidate + report sequence here.
      await vscode.commands.executeCommand("sgt.revert", args[0]);
      return;
    }
    try {
      const report = await this.store.sgt.mutate(this.cliArgsFor(verb, args));
      this.store.invalidate();
      vscode.window.showInformationMessage(report.trim().split("\n")[0] || "Done.");
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  private cliArgsFor(verb: string, args: string[]): string[] {
    switch (verb) {
      case "merge":
        return ["merge", args[0], args[1]];
      case "rename":
        return ["rename", args[0], args[1]];
      case "move": {
        const target = args[args.length - 1];
        const opIds = args.slice(0, -1);
        return ["move", ...opIds, "--to", target];
      }
      case "split":
        return ["split", args[0], "--apply"];
      default:
        throw new Error(`unknown feature verb ${verb}`);
    }
  }

  private dispose(): void {
    MapViewPanel.current = undefined;
    this.disposables.forEach((d) => d.dispose());
    this.panel.dispose();
  }
}
