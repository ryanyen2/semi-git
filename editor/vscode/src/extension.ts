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
import { findSgtRoot, Sgt } from "./sgt";
import { GitStatusBar } from "./statusBar";
import { Store } from "./store";
import { ChangesTreeProvider } from "./tree/changesTree";
import { CompositionsTreeProvider } from "./tree/compositionsTree";
import { FeaturesTreeProvider } from "./tree/featuresTree";
import { ForksTreeProvider } from "./tree/forksTree";

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

  registerCommands(context, store, preview, () => void blame.render(), planDiff, root);
  registerGitBridgeCommands(context, store);

  const featuresTree = new FeaturesTreeProvider(store);
  const forksTree = new ForksTreeProvider(store);
  const changesTree = new ChangesTreeProvider(store);
  const compositionsTree = new CompositionsTreeProvider(store);
  context.subscriptions.push(featuresTree, forksTree, changesTree, compositionsTree);

  const featuresView = vscode.window.createTreeView("sgtFeatures", { treeDataProvider: featuresTree });
  const forksView = vscode.window.createTreeView("sgtForks", { treeDataProvider: forksTree });
  const changesView = vscode.window.createTreeView("sgtChanges", { treeDataProvider: changesTree });
  const compositionsView = vscode.window.createTreeView("sgtCompositions", { treeDataProvider: compositionsTree });
  context.subscriptions.push(featuresView, forksView, changesView, compositionsView);

  const refreshForksBadge = async () => {
    try {
      const forks = await store.forksView();
      forksView.badge = forks.open
        ? { value: forks.open, tooltip: `${forks.open} open fork(s)` }
        : undefined;
    } catch {
      forksView.badge = undefined;
    }
  };
  void refreshForksBadge();
  context.subscriptions.push(store.onDidChange(() => void refreshForksBadge()));

  // Refresh on .sgt changes (a feature verb rewrites tree.json/pins.json under it) and on any
  // *.py change — the latter is how newly-written symbols show up in blame before the next
  // `sgt map` re-clusters them.
  const sgtWatcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(root, ".sgt/**/*.json")
  );
  const pyWatcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(root, "**/*.py")
  );
  let pending: NodeJS.Timeout | undefined;
  const refresh = () => {
    clearTimeout(pending);
    pending = setTimeout(() => store.invalidate(), 250); // debounce write storms
  };
  for (const w of [sgtWatcher, pyWatcher]) {
    w.onDidChange(refresh);
    w.onDidCreate(refresh);
    w.onDidDelete(refresh);
  }
  context.subscriptions.push(
    sgtWatcher,
    pyWatcher,
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (doc.languageId === "python") {
        refresh();
      }
    })
  );

  void blame.render();
  void planStatusBar.refresh();
  void gitStatusBar.refresh();
  void diagnostics.render();
}

export function deactivate(): void {
  // disposables handle teardown
}
