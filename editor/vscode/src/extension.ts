// Activation: find the .sgt workspace, build the shared store, and wire every surface to it.
// A file-watcher on .sgt/ invalidates the store after a checkpoint/feature-verb op (`sgt map`,
// `merge`/`split`/`rename`/`move`/`revert`, all of which touch tree.json and/or pins.json) so the
// tree and blame gutters refresh together.

import * as vscode from "vscode";
import { BlameController } from "./blame";
import { registerCommands } from "./commands";
import { PlanCodeLensProvider, PlanDiffProvider, PlanStatusBar } from "./plan";
import { MapTreeProvider } from "./tree";
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
  const tree = new MapTreeProvider(store);
  const planLens = new PlanCodeLensProvider(store);
  const planDiff = new PlanDiffProvider(root);
  const planStatusBar = new PlanStatusBar(store);
  context.subscriptions.push(blame, preview, planLens, planDiff, planStatusBar);
  context.subscriptions.push(vscode.window.createTreeView("sgtGraph", { treeDataProvider: tree }));
  context.subscriptions.push(vscode.languages.registerCodeLensProvider({ scheme: "file" }, planLens));

  registerCommands(context, store, preview, () => void blame.render(), planDiff);

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
}

export function deactivate(): void {
  // disposables handle teardown
}
