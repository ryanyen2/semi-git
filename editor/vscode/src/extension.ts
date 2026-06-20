// Activation: find the .sgt workspace, build the shared store, and wire every surface to it.
// A file-watcher on .sgt/* invalidates the store after a checkpoint/graph op so blame, lenses,
// the tree, and the webview all refresh together.

import * as vscode from "vscode";
import { BlameController } from "./blame";
import { SgtCodeLensProvider } from "./codelens";
import { registerCommands } from "./commands";
import { GraphTreeProvider } from "./tree";
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

  context.subscriptions.push(
    vscode.window.createTreeView("sgtGraph", { treeDataProvider: tree })
  );
  const py: vscode.DocumentSelector = { language: "python" };
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider(py, new SgtCodeLensProvider(store)),
    vscode.languages.registerHoverProvider(py, new SgtHoverProvider(store))
  );

  registerCommands(context, store, preview, () => void blame.render());

  // Refresh on .sgt changes (a checkpoint/graph op rewrites graph.json / effects.json) and
  // when the user saves a Python file (drift may have changed).
  const watcher = vscode.workspace.createFileSystemWatcher(
    new vscode.RelativePattern(root, ".sgt/*.json")
  );
  const refresh = () => store.invalidate();
  watcher.onDidChange(refresh);
  watcher.onDidCreate(refresh);
  watcher.onDidDelete(refresh);
  context.subscriptions.push(
    watcher,
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (doc.languageId === "python") {
        store.invalidate();
      }
    })
  );

  void blame.render();
}

export function deactivate(): void {
  // disposables handle teardown
}
