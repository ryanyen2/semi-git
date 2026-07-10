// Command wiring. Read/preview commands are safe and immediate. The mutating revert command
// always confirms first, because `sgt revert` refuses on a fork and otherwise re-materializes +
// commits — so we surface the human report and then invalidate every surface.

import * as vscode from "vscode";
import { MapViewPanel } from "./mapView";
import { PlanDiffProvider, showPlanQuickPick } from "./plan";
import { PreviewProvider } from "./preview";
import { Store } from "./store";

async function pickFeature(store: Store, provided?: string): Promise<string | undefined> {
  if (provided) {
    return provided;
  }
  let map;
  try {
    map = await store.map();
  } catch {
    return undefined;
  }
  const features = map.nodes.filter((n) => n.kind === "feature");
  const pick = await vscode.window.showQuickPick(
    features.map((n) => ({ label: n.label || n.id, description: `${n.op_count} op(s) · ${n.id}`, id: n.id })),
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
  refreshBlame: () => void,
  planDiff: PlanDiffProvider
): void {
  const reg = (id: string, fn: (...a: any[]) => any) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  reg("sgt.refresh", () => {
    store.invalidate();
    refreshBlame();
  });
  reg("sgt.toggleBlame", () => toggle("blame.enabled"));
  reg("sgt.showFeatureMap", () => MapViewPanel.createOrShow(context, store));

  reg("sgt.previewRevert", async (id?: string) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      void preview.preview(feature);
    }
  });
  reg("sgt.revert", async (id?: string) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      await applyMutation(
        store,
        ["revert", feature],
        `Revert feature ${feature}? This rewrites the working tree and commits.`
      );
    }
  });

  reg("sgt.showPlanQuickPick", () => showPlanQuickPick(store, planDiff));
  reg("sgt.showPlanDiff", (target) => planDiff.showDiff(target));
}
