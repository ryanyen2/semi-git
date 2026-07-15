// The Composition Workbench panel: a 3-pane view (rail: tree+timeline, inspector: detail/actions/
// code(I)) grounded in experiments/patch_clustering/out/rail3.html. A singleton per workspace.
// Sourced from one `compose_view` call (map+history+status+forks+drift+sessions+proposals+oracle)
// instead of the old feature-map's separate map()+history() fetches. The webview's message
// protocol (`ready`/`previewVerb`/`applyVerb`/`renamePrompt`, plus `pickComposition`/
// `requestFold` for this phase) drives media/workbench.js; every mutation still goes through the
// real, unmodified `sgt merge`/`split`/`rename`/`move`/`revert` commands -- a preview never writes
// anything, and a fold never materializes the working tree.
//
// Phase-3 scope: the static 3-pane skeleton, a QuickPick-driven composition selector (HEAD +
// sessions, by branch ref; proposals are view-only here), and a single-shot `foldAt`-backed code
// panel scoped to a feature by prefix-matching `MapNode.dir` over `fold_view.files` (there is no
// feature->file API field yet, and inventing one ahead of need would violate the additive-only,
// don't-speculate discipline).
//
// Phase 4 adds `scrubPlayhead`/`playheadResult`: the draggable playhead drags over
// `history_view.ops`' commit-index axis and folds `{commitIndex}` frontiers live (media/
// workbench.js debounces + snaps to op columns). It is a read-only exploration mode -- it never
// changes the composition ref previews/applies run against.

import * as vscode from "vscode";
import { colorForNode } from "./color";
import { FoldFrontier } from "./sgt";
import { Store } from "./store";

export class WorkbenchPanel {
  private static current: WorkbenchPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly previewCache = new Map<string, unknown>();

  static createOrShow(context: vscode.ExtensionContext, store: Store): void {
    if (WorkbenchPanel.current) {
      WorkbenchPanel.current.panel.reveal(vscode.ViewColumn.Active);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "sgtWorkbench",
      "SGT Composition Workbench",
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
      }
    );
    WorkbenchPanel.current = new WorkbenchPanel(panel, context, store);
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
    const jsUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "media", "workbench.js"));
    const cssUri = webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, "media", "workbench.css"));
    const nonce = String(context.extension.packageJSON.version || "0").length + "-" + Date.now();
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';" />
<link rel="stylesheet" href="${cssUri}" />
<title>SGT Composition Workbench</title>
</head>
<body>
<div id="app">
  <div id="titlebar">
    <button id="compositionBtn" class="composition-btn">HEAD</button>
    <span id="oracleChip" class="oracle-chip" data-state="pending">oracle: pending</span>
  </div>
  <div id="main">
    <div id="rail"></div>
    <button id="offscreenAbove" class="offscreen-pill offscreen-pill-top" hidden></button>
    <button id="offscreenBelow" class="offscreen-pill offscreen-pill-bottom" hidden></button>
    <div id="inspector"></div>
  </div>
</div>
<script nonce="${nonce}" src="${jsUri}"></script>
</body>
</html>`;
  }

  private async pushState(): Promise<void> {
    let compose;
    try {
      compose = await this.store.composeView();
    } catch (e: any) {
      void this.panel.webview.postMessage({ type: "error", message: e.message });
      return;
    }
    const nodes = compose.map.nodes.map((n) => ({
      ...n,
      color: n.kind === "feature" ? colorForNode(n.id) : null,
    }));
    this.previewCache.clear();
    void this.panel.webview.postMessage({ type: "state", compose: { ...compose, map: { ...compose.map, nodes } } });
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
      case "pickComposition":
        await this.pickComposition();
        return;
      case "requestFold":
        await this.requestFold(msg.featureId, msg.ref, msg.seq);
        return;
      case "scrubPlayhead":
        await this.scrubPlayhead(msg.commitIndex, msg.seq);
        return;
      default:
        return;
    }
  }

  private async preview(verb: string, args: string[], seq: number): Promise<void> {
    const key = `${verb}:${args.join("")}`;
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

  // The composition selector (titlebar button): HEAD, or a session by its branch ref. Proposals
  // aren't foldable refs (no checked-out branch), so picking one just opens the existing
  // read-only proposal summary instead of changing the fold target -- the full composition
  // selector (branches + proposal deltas as pseudo-frontiers) is Phase 4.
  private async pickComposition(): Promise<void> {
    let compose;
    try {
      compose = await this.store.composeView();
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    const items: (vscode.QuickPickItem & { ref?: string })[] = [
      { label: "HEAD", description: "current composition", ref: "HEAD" },
      ...compose.sessions.sessions.map((s) => ({
        label: s.name,
        description: `${s.branch} · ${s.new_op_count} op(s)`,
        ref: s.branch,
      })),
      ...compose.proposals.map((p) => ({ label: p.title || p.id, description: "proposal (view only)" })),
    ];
    const pick = await vscode.window.showQuickPick(items, { placeHolder: "Switch composition" });
    if (!pick) {
      return;
    }
    if (!pick.ref) {
      const proposal = compose.proposals.find((p) => (p.title || p.id) === pick.label);
      if (proposal) {
        await vscode.commands.executeCommand("sgt.viewProposal", proposal.id);
      }
      return;
    }
    void this.panel.webview.postMessage({ type: "compositionPicked", label: pick.label, ref: pick.ref });
  }

  // One non-interactive fold per feature selection, filtered to that feature's files by
  // prefix-matching `MapNode.dir` -- `fold_view.files` has no feature attribution field, so this
  // reuses the tree's existing `dir` rather than requesting a new API field ahead of need.
  private async requestFold(featureId: string, ref: string, seq: number): Promise<void> {
    let map;
    try {
      map = await this.store.map();
    } catch (e: any) {
      void this.panel.webview.postMessage({ type: "foldResult", seq, featureId, error: e.message });
      return;
    }
    const node = map.nodes.find((n) => n.id === featureId);
    const frontier: FoldFrontier = { ref: ref || "HEAD" };
    try {
      const fold = await this.store.foldAt(frontier);
      const files = node
        ? Object.fromEntries(Object.entries(fold.files || {}).filter(([path]) => path.startsWith(node.dir)))
        : fold.files;
      void this.panel.webview.postMessage({
        type: "foldResult",
        seq,
        featureId,
        files,
        oracle_verdict: fold.oracle_verdict,
        forked: fold.forked,
        error: fold.error,
      });
    } catch (e: any) {
      void this.panel.webview.postMessage({ type: "foldResult", seq, featureId, error: e.message });
    }
  }

  // Phase 4: the draggable playhead. `{commitIndex}` is its own `FoldFrontier` variant --
  // no new API needed, `foldAtSpec` already renders it as the plain digit string `sgt fold --at`
  // expects. Deliberately not `requestFold` (which is keyed to a featureId for cache/dir-prefix
  // filtering): the playhead has no feature selection and folds the whole frontier, letting the
  // webview filter by the selected feature's `dir` client-side so dragging never re-requests.
  private async scrubPlayhead(commitIndex: number, seq: number): Promise<void> {
    try {
      const fold = await this.store.foldAt({ commitIndex });
      void this.panel.webview.postMessage({
        type: "playheadResult",
        seq,
        commitIndex,
        op_count: fold.op_count,
        files: fold.files,
        oracle_verdict: fold.oracle_verdict,
        forked: fold.forked,
        error: fold.error,
      });
    } catch (e: any) {
      void this.panel.webview.postMessage({ type: "playheadResult", seq, commitIndex, error: e.message });
    }
  }

  private dispose(): void {
    WorkbenchPanel.current = undefined;
    this.disposables.forEach((d) => d.dispose());
    this.panel.dispose();
  }
}
