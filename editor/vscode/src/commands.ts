// Command wiring. Read/preview commands are safe and immediate. Mutating commands
// (revert/suspend/restore) always confirm first, because sgt refuses on drift and re-materializes
// + commits — so we surface the human report and then invalidate every surface.

import * as vscode from "vscode";
import { GraphPanel } from "./graphPanel";
import { PreviewProvider } from "./preview";
import { Store } from "./store";

async function pickNode(store: Store, provided?: string): Promise<string | undefined> {
  if (provided) {
    return provided;
  }
  let graph;
  try {
    graph = await store.graph();
  } catch {
    return undefined;
  }
  const pick = await vscode.window.showQuickPick(
    graph.nodes.map((n) => ({ label: n.intent || n.id, description: `${n.kind} · ${n.id}`, id: n.id })),
    { placeHolder: "Pick a feature" }
  );
  return pick?.id;
}

function toggle(key: string): void {
  const c = vscode.workspace.getConfiguration("sgt");
  const cur = c.get<boolean>(key, true);
  c.update(key, !cur, vscode.ConfigurationTarget.Workspace);
}

async function applyMutation(store: Store, args: string[], confirmMsg: string): Promise<void> {
  const ok = await vscode.window.showWarningMessage(confirmMsg, { modal: true }, "Apply");
  if (ok !== "Apply") {
    return;
  }
  try {
    const report = await store.sgt.mutate(args);
    store.invalidate();
    vscode.window.showInformationMessage(report.trim().split("\n")[0] || "Done.");
  } catch (e: any) {
    vscode.window.showErrorMessage(e.message);
  }
}

export function registerCommands(
  context: vscode.ExtensionContext,
  store: Store,
  preview: PreviewProvider,
  refreshBlame: () => void
): void {
  const reg = (id: string, fn: (...a: any[]) => any) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  reg("sgt.refresh", () => {
    store.invalidate();
    refreshBlame();
  });
  reg("sgt.showGraph", () => GraphPanel.show(context, store));
  reg("sgt.toggleBlame", () => toggle("blame.enabled"));
  reg("sgt.toggleHeatmap", () => toggle("heatmap.enabled"));
  reg("sgt.toggleCodeLens", () => toggle("codeLens.enabled"));

  reg("sgt.openNode", async (id?: string) => {
    const node = id ?? (await pickNode(store));
    if (!node) {
      return;
    }
    const n = store.node(node);
    if (!n) {
      return;
    }
    const detail =
      `${n.intent}\n\n${n.kind} · ${n.status} · ${n.id}\n` +
      `depends on: ${n.depends_on.join(", ") || "—"}\n` +
      `dependents: ${n.dependents.join(", ") || "—"}` +
      (n.conflict ? `\n⚠ ${n.conflict}` : "");
    const action = await vscode.window.showInformationMessage(
      detail,
      { modal: true },
      "Preview revert",
      "Preview suspend"
    );
    if (action === "Preview revert") {
      void preview.preview("revert", node);
    } else if (action === "Preview suspend") {
      void preview.preview("switch", node, false);
    }
  });

  reg("sgt.previewRevert", async (id?: string) => {
    const node = await pickNode(store, id);
    if (node) {
      void preview.preview("revert", node);
    }
  });
  reg("sgt.previewSwitchOff", async (id?: string) => {
    const node = await pickNode(store, id);
    if (node) {
      void preview.preview("switch", node, false);
    }
  });
  reg("sgt.previewSwitchOn", async (id?: string) => {
    const node = await pickNode(store, id);
    if (node) {
      void preview.preview("switch", node, true);
    }
  });

  reg("sgt.revert", async (id?: string) => {
    const node = await pickNode(store, id);
    if (node) {
      await applyMutation(store, ["revert", node], `Revert (plug out) feature ${node}? This rewrites the working tree and commits.`);
    }
  });
  reg("sgt.switchOff", async (id?: string) => {
    const node = await pickNode(store, id);
    if (node) {
      await applyMutation(store, ["switch", node, "off"], `Suspend feature ${node}?`);
    }
  });
  reg("sgt.switchOn", async (id?: string) => {
    const node = await pickNode(store, id);
    if (node) {
      await applyMutation(store, ["switch", node, "on"], `Restore feature ${node}?`);
    }
  });
}
