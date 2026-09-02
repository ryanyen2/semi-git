// Activation: find the .sgt workspace, build the shared store, and wire every surface to it.
// A file-watcher on .sgt/ invalidates the store after a checkpoint/feature-verb op (`sgt map`,
// `merge`/`split`/`rename`/`move`/`revert`, all of which touch tree.json and/or pins.json) so the
// feature-map webview and blame gutters refresh together.

import * as vscode from "vscode";
import { BlameController } from "./blame";
import { registerCommands } from "./commands";
import { DiagnosticsController, DriftCodeActionProvider } from "./diagnostics";
import { registerGitBridgeCommands } from "./gitBridge";
import { SymbolHoverProvider } from "./hover";
import { FeatureInlayHintsProvider } from "./inlayHints";
import { PlanCodeLensProvider, PlanDiffProvider, PlanStatusBar } from "./plan";
import { PreviewProvider } from "./preview";
import { RenderPanel } from "./renderPanel";
import { findSgtRoot, Sgt } from "./sgt";
import { GitStatusBar } from "./statusBar";
import { Store } from "./store";
import { ChangesTreeProvider } from "./tree/changesTree";
import { FeaturesTreeProvider } from "./tree/featuresTree";
import { NowTreeProvider } from "./tree/nowTree";
import { WorkbenchProvider } from "./workbench";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const out = vscode.window.createOutputChannel("semi-git");
  context.subscriptions.push(out);
  const root = await findSgtRoot();
  void vscode.commands.executeCommand("setContext", "sgt.hasRoot", !!root);

  // Always available, even without a `.sgt` store yet -- it's how the welcome-view CTA in
  // `sgtFeatures` (shown when `!sgt.hasRoot`) bootstraps a workspace.
  context.subscriptions.push(
    vscode.commands.registerCommand("sgt.init", async () => {
      const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
      if (!folder) {
        vscode.window.showErrorMessage("Open a folder first.");
        return;
      }
      try {
        await new Sgt(folder, out).mutate(["init"]);
      } catch (e: any) {
        vscode.window.showErrorMessage(`sgt init failed: ${e.message}`);
        return;
      }
      const choice = await vscode.window.showInformationMessage(
        "sgt initialized. Reload the window to activate semi-git.",
        "Reload Window"
      );
      if (choice === "Reload Window") {
        void vscode.commands.executeCommand("workbench.action.reloadWindow");
      }
    })
  );

  if (!root) {
    return; // no `.sgt` store; only sgt.init + the welcome view are live until one exists
  }

  const store = new Store(root, out);
  context.subscriptions.push(store);

  const blame = new BlameController(store);
  const preview = new PreviewProvider(store);
  const planLens = new PlanCodeLensProvider(store);
  const planDiff = new PlanDiffProvider(root);
  const planStatusBar = new PlanStatusBar(store);
  const gitStatusBar = new GitStatusBar(store);
  const diagnostics = new DiagnosticsController(store, root);
  const inlayHints = new FeatureInlayHintsProvider(store);
  context.subscriptions.push(blame, preview, planLens, planDiff, planStatusBar, gitStatusBar, diagnostics, inlayHints);
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider({ scheme: "file" }, planLens),
    vscode.languages.registerHoverProvider({ scheme: "file" }, new SymbolHoverProvider(store)),
    vscode.languages.registerCodeActionsProvider({ scheme: "file" }, new DriftCodeActionProvider(), {
      providedCodeActionKinds: [vscode.CodeActionKind.QuickFix],
    }),
    vscode.languages.registerInlayHintsProvider({ scheme: "file" }, inlayHints)
  );

  const renderPanel = new RenderPanel(context, store, () => root);
  context.subscriptions.push(
    renderPanel,
    vscode.commands.registerCommand("sgt.openRenderPanel", () => renderPanel.open()),
  );

  const workbenchProvider = new WorkbenchProvider(context, store, preview, root, renderPanel);
  registerCommands(context, store, preview, () => void blame.render(), planDiff, root, workbenchProvider);
  registerGitBridgeCommands(context, store);

  context.subscriptions.push(
    workbenchProvider,
    vscode.window.registerWebviewViewProvider("sgtWorkbench", workbenchProvider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  // Three views, and the sidebar used to carry five. `sgtForks` was an inbox for same-symbol chain
  // forks and `sgtCompositions` was the sessions-and-proposals switch/land/publish surface. Both
  // are permanently empty on a repository one person is reading -- so what they contributed was two
  // more names to work out, and, in Compositions' case, a Publish-to-GitHub row sitting one click
  // deep in a panel somebody opened to look at their own history. Neither verb was removed; they
  // are asked for by name on the CLI, which is where an outward-facing action belongs.
  const nowTree = new NowTreeProvider(store);
  const featuresTree = new FeaturesTreeProvider(store);
  const changesTree = new ChangesTreeProvider(store);
  context.subscriptions.push(nowTree, featuresTree, changesTree);

  const nowView = vscode.window.createTreeView("sgtNow", { treeDataProvider: nowTree });
  const featuresView = vscode.window.createTreeView("sgtFeatures", { treeDataProvider: featuresTree });
  const changesView = vscode.window.createTreeView("sgtChanges", { treeDataProvider: changesTree });
  context.subscriptions.push(nowView, featuresView, changesView);

  // Refresh only on .sgt changes -- a mined op, checkpoint, or feature verb rewrites the op store
  // and tree.json/pins.json under `.sgt/`. We deliberately do NOT watch `**/*.py`: every read
  // surface (blame/hover/inlay/tree) reaches the tree through `store.map()` -> `sgt map`, which
  // *rebuilds* (re-clusters + labels). Watching every keystroke-save invalidated that cache and
  // re-ran the rebuild on each edit -- a full Leiden re-cluster (and, before the label cache was
  // persisted, a fresh non-deterministic LLM relabel) on every save. Scoping invalidation to
  // `.sgt/` means the tree refreshes exactly when the op store actually changes; a symbol written
  // between checkpoints shows up in blame on the next mined op rather than mid-typing.
  const sgtWatcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(root, ".sgt/**/*.json")
  );
  let pending: NodeJS.Timeout | undefined;
  const refresh = () => {
    clearTimeout(pending);
    pending = setTimeout(() => {
      // A `sgt map`/`compose` READ rebuilds and rewrites tree.json/ideal.json/label_cache.json/...
      // under `.sgt/`, which trips this very watcher. Invalidating on our own writes re-issues the
      // read -> infinite rebuild loop (the sidebar spinning forever). Skip while our subprocess is
      // active; external mutations arrive while we're idle, and our own mutations invalidate
      // explicitly, so neither is missed.
      if (store.sgt.recentlyActive()) return;
      store.invalidate();
    }, 250); // debounce write storms
  };
  sgtWatcher.onDidChange(refresh);
  sgtWatcher.onDidCreate(refresh);
  sgtWatcher.onDidDelete(refresh);
  context.subscriptions.push(sgtWatcher);

  void blame.render();
  void planStatusBar.refresh();
  void gitStatusBar.refresh();
  void diagnostics.render();
}

export function deactivate(): void {
  // disposables handle teardown
}
