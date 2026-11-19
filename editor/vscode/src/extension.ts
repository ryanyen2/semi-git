// Activation: one client, one store, and every surface wired to it. The store refreshes on a file
// save, when HEAD or a ref moves, when names or the plan change (an agent's `ugh_name` or
// `ugh_plan` moves no ref), and after every verb. An uninitialized folder gets the same surfaces;
// they show Init until `.sgt/local/ideal.json` exists.

import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { Blame } from "./blame";
import { flows, registerCommands } from "./commands";
import { Diagnostics } from "./diagnostics";
import { Documents } from "./documents";
import { HoverProvider } from "./hover";
import { InlayHints } from "./inlayHints";
import { RenderPanel } from "./renderPanel";
import { StatusBar } from "./statusBar";
import { Store } from "./store";
import { Texts } from "./texts";
import { NamesTree } from "./tree/namesTree";
import { StatusTree } from "./tree/statusTree";
import { Ugh } from "./ugh";
import { WorkbenchView } from "./workbench";

const MARKER = path.join(".sgt", "local", "ideal.json");

export function activate(context: vscode.ExtensionContext): void {
  const folders = vscode.workspace.workspaceFolders ?? [];
  const root = (folders.find((f) => fs.existsSync(path.join(f.uri.fsPath, MARKER))) ?? folders[0])?.uri.fsPath;
  if (!root) {
    context.subscriptions.push(
      vscode.commands.registerCommand("ugh.init", () => vscode.window.showWarningMessage("Open a folder first.")),
    );
    return;
  }
  const keep = <T extends { dispose(): unknown }>(d: T): T => {
    context.subscriptions.push(d);
    return d;
  };

  const out = keep(vscode.window.createOutputChannel("ugh"));
  const ugh = keep(new Ugh(root, out));
  const store = new Store({
    initialized: () => fs.promises.stat(path.join(root, MARKER)).then(() => true, () => false),
    log: () => ugh.log(),
    status: () => ugh.status(),
    show: (rel) => ugh.show(rel),
  });
  const setRoot = (has: boolean) => void vscode.commands.executeCommand("setContext", "ugh.hasRoot", has);
  setRoot(fs.existsSync(path.join(root, MARKER)));
  keep(store.onChange((s) => setRoot(!s.uninitialized)));

  const docs = new Documents(root, store);
  const texts = keep(new Texts());
  const render = keep(new RenderPanel(context, root, ugh, store));
  let workbench: WorkbenchView;
  const deps = { root, ugh, store, docs, texts, render, workbench: () => workbench };
  const f = flows(deps);
  workbench = keep(new WorkbenchView(context.extensionUri, store, ugh, f.act));
  registerCommands(context, deps, f);

  const bar = keep(new StatusBar());
  keep(store.onChange((s) => bar.render(s)));
  const inlay = keep(new InlayHints(docs, store));
  const diagnostics = keep(new Diagnostics(root, store));
  keep(new Blame(docs, store));
  keep(vscode.workspace.registerTextDocumentContentProvider(Texts.scheme, texts));
  keep(vscode.window.registerWebviewViewProvider("ughWorkbench", workbench, {
    webviewOptions: { retainContextWhenHidden: true },
  }));
  keep(vscode.window.createTreeView("ughStatus", { treeDataProvider: keep(new StatusTree(root, store)) }));
  keep(vscode.window.createTreeView("ughNames", { treeDataProvider: keep(new NamesTree(store)) }));
  keep(vscode.languages.registerHoverProvider({ scheme: "file" }, new HoverProvider(docs)));
  keep(vscode.languages.registerInlayHintsProvider({ scheme: "file" }, inlay));
  keep(vscode.languages.registerCodeActionsProvider({ scheme: "file" }, diagnostics, {
    providedCodeActionKinds: [vscode.CodeActionKind.QuickFix],
  }));

  let timer: NodeJS.Timeout | undefined;
  const soon = () => {
    clearTimeout(timer);
    timer = setTimeout(() => void store.refresh(), 300);
  };
  keep({ dispose: () => clearTimeout(timer) });
  keep(vscode.workspace.onDidSaveTextDocument((doc) => {
    if (docs.relPath(doc.uri)) soon();
  }));
  for (const glob of [".git/HEAD", ".git/refs/**", ".sgt/names.json", ".sgt/local/plan.json"]) {
    const watcher = keep(vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(root, glob)));
    watcher.onDidChange(soon);
    watcher.onDidCreate(soon);
    watcher.onDidDelete(soon);
  }

  void store.refresh();
}

export function deactivate(): void {
  // the subscriptions dispose everything
}
