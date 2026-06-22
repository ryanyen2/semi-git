// Activation: find the .sgt workspace, build the shared store, and wire every surface to it.
// A file-watcher on .sgt/* invalidates the store after a checkpoint/graph op so blame, lenses,
// the tree, and the webview all refresh together.

import * as vscode from "vscode";
import { BlameController } from "./blame";
import { SgtCodeLensProvider } from "./codelens";
import { registerCommands } from "./commands";
import { GraphTreeProvider } from "./tree";
import { GraphViewProvider } from "./graphView";
import { MapViewProvider } from "./mapView";
import { SgtHoverProvider } from "./hover";
import { PreviewProvider } from "./preview";
import { findSgtRoot } from "./sgt";
import { Store } from "./store";

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const root = await findSgtRoot();
  if (!root) {
    return; // not an sgt workspace; stay dormant
  }
  const out = vscode.window.createOutputChannel("semi-git");
  const store = new Store(root, out);
  context.subscriptions.push(out, store);

  const blame = new BlameController(store);
  const preview = new PreviewProvider(store);
  const tree = new GraphTreeProvider(store);
  context.subscriptions.push(blame, preview);

  const graphView = new GraphViewProvider(context, store, () => void blame.render());
  context.subscriptions.push(
    graphView,
    vscode.window.createTreeView("sgtGraph", { treeDataProvider: tree }),
    vscode.window.registerWebviewViewProvider(GraphViewProvider.viewId, graphView, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );
  const mapView = new MapViewProvider(context, store);
  context.subscriptions.push(
    mapView,
    vscode.window.registerWebviewViewProvider(MapViewProvider.viewId, mapView, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );
  const py: vscode.DocumentSelector = { language: "python" };
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider(py, new SgtCodeLensProvider(store)),
    vscode.languages.registerHoverProvider(py, new SgtHoverProvider(store))
  );

  registerCommands(context, store, preview, graphView, () => void blame.render());

  // Refresh on .sgt changes (a checkpoint/graph op rewrites graph.json / effects.json) and on
  // any *.py change — the latter is how we surface live agent presence: when the agent writes
  // files (drift appears), the graph marks the features it's editing in near-real-time.
  const sgtWatcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(root, ".sgt/*.json")
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
}

export function deactivate(): void {
  // disposables handle teardown
}
