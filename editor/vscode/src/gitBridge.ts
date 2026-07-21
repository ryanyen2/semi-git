// Git-bridge commands: the palette/status-bar surface for the daily-loop verbs (`switch`/`save`/
// `undo`, D3) and the network verbs (`sync`/`push`/`land`). Each calls the typed `sgt.ts` method
// directly (not the generic `mutate()`) because the structured JSON result drives the
// confirmation/result message, then `store.invalidate()` -- same convention as every other
// mutating command in commands.ts. Confirm before anything outward-facing or working-tree-moving
// (switch, push, land); `save` never destroys anything so it's immediate; `sync` is fetch-and-
// integrate (surfaces forks rather than applying a textual merge) so it doesn't need one either.
// `land <branch>` additionally refuses while any fork is open, per the plan's land-gating rule.

import * as vscode from "vscode";
import { Store } from "./store";

async function pickBranch(store: Store, prompt: string): Promise<string | undefined> {
  let branches: string[] = [];
  try {
    const compose = await store.composeView();
    branches = compose.sessions.sessions.map((s) => s.branch);
  } catch {
    // fall through to manual entry
  }
  if (branches.length === 0) {
    return vscode.window.showInputBox({ prompt, placeHolder: "branch name" });
  }
  const OTHER = "Type a branch name…";
  const pick = await vscode.window.showQuickPick([...branches, OTHER], { placeHolder: prompt });
  if (pick === undefined) {
    return undefined;
  }
  return pick === OTHER ? vscode.window.showInputBox({ prompt, placeHolder: "branch name" }) : pick;
}

export function registerGitBridgeCommands(context: vscode.ExtensionContext, store: Store): void {
  const reg = (id: string, fn: (...a: any[]) => any) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  reg("sgt.switch", async () => {
    const branch = await pickBranch(store, "Switch to branch");
    if (!branch) {
      return;
    }
    const ok = await vscode.window.showWarningMessage(
      `Switch to ${branch}? Mines the current ref first (nothing is lost), then checks out ${branch}.`,
      { modal: true },
      "Switch"
    );
    if (ok !== "Switch") {
      return;
    }
    try {
      const result = await store.sgt.switchBranch(branch);
      store.invalidate();
      vscode.window.showInformationMessage(
        result.ok ? `✓ switch ${result.branch}: ${result.ops} op(s) in the ideal` : result.error || "switch failed"
      );
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  });

  reg("sgt.save", async () => {
    const message = await vscode.window.showInputBox({ prompt: "Commit message (optional)" });
    try {
      const result = await store.sgt.save(message || undefined);
      store.invalidate();
      vscode.window.showInformationMessage(
        result.saved
          ? `✓ save ${(result.commit || "").slice(0, 12)}: ${result.ops} op(s)`
          : result.message || "nothing to save"
      );
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  });

  reg("sgt.undo", async () => {
    const ok = await vscode.window.showWarningMessage("Undo the last operation?", { modal: true }, "Undo");
    if (ok !== "Undo") {
      return;
    }
    try {
      const result = await store.sgt.undo();
      store.invalidate();
      if (!result.undone) {
        vscode.window.showInformationMessage(result.message || "nothing to undo");
        return;
      }
      const parts = [`✓ undo ${(result.commit || "").slice(0, 12)}: restored ${result.restored_ops} op(s)`];
      if (result.removed?.length) {
        parts.push(`dropped ${result.removed.length}`);
      }
      if (result.added?.length) {
        parts.push(`re-added ${result.added.length}`);
      }
      vscode.window.showInformationMessage(parts.join(" · "));
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  });

  reg("sgt.sync", async () => {
    const remote = await vscode.window.showInputBox({ prompt: "Remote (optional, default: origin)" });
    if (remote === undefined) {
      return;
    }
    const branch = await vscode.window.showInputBox({ prompt: "Branch (optional, default: current)" });
    if (branch === undefined) {
      return;
    }
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "sgt sync…" }, async () => {
      try {
        const report = await store.sgt.sync(remote || undefined, branch || undefined);
        store.invalidate();
        if (report.open_fork_count > 0) {
          const choice = await vscode.window.showWarningMessage(
            `sgt sync: ${report.message} — ${report.open_fork_count} open fork(s).`,
            "Open Forks"
          );
          if (choice === "Open Forks") {
            void vscode.commands.executeCommand("sgtForks.focus");
          }
        } else {
          vscode.window.showInformationMessage(`✓ sync: ${report.message} (${report.ops_added} op(s) added)`);
        }
      } catch (e: any) {
        vscode.window.showErrorMessage(e.message);
      }
    });
  });

  reg("sgt.push", async () => {
    const remote = await vscode.window.showInputBox({ prompt: "Remote (optional, default: origin)" });
    if (remote === undefined) {
      return;
    }
    const branch = await vscode.window.showInputBox({ prompt: "Branch (optional, default: current)" });
    if (branch === undefined) {
      return;
    }
    const ok = await vscode.window.showWarningMessage(`Push to ${remote || "the default remote"}?`, { modal: true }, "Push");
    if (ok !== "Push") {
      return;
    }
    await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "sgt push…" }, async () => {
      try {
        const result = await store.sgt.push(remote || undefined, branch || undefined);
        vscode.window.showInformationMessage(result.ok ? "✓ push succeeded" : "push failed");
      } catch (e: any) {
        vscode.window.showErrorMessage(e.message);
      }
    });
  });

  reg("sgt.land", async () => {
    let status;
    try {
      status = await store.status(true);
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    if (status.forks.open > 0) {
      const choice = await vscode.window.showErrorMessage(
        `Land blocked: ${status.forks.open} open fork(s). Resolve them first.`,
        "Open Forks"
      );
      if (choice === "Open Forks") {
        void vscode.commands.executeCommand("sgtForks.focus");
      }
      return;
    }
    const branch = await pickBranch(store, "Land onto branch");
    if (!branch) {
      return;
    }
    const ok = await vscode.window.showWarningMessage(
      `Land onto ${branch}? Oracle-gated; advances that shared branch.`,
      { modal: true },
      "Land"
    );
    if (ok !== "Land") {
      return;
    }
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `sgt land ${branch}…` },
      async () => {
        try {
          const report = await store.sgt.land(branch);
          store.invalidate();
          if (!report.landed) {
            vscode.window.showWarningMessage(`Land blocked: ${report.blocked_reason || "unknown reason"}`);
            return;
          }
          const msg = `✓ land ${branch}: ${(report.land_sha || "").slice(0, 12)} (${report.ops_added} op(s))`;
          if (report.open_fork_count > 0) {
            const choice = await vscode.window.showWarningMessage(`${msg} — ${report.open_fork_count} new open fork(s).`, "Open Forks");
            if (choice === "Open Forks") {
              void vscode.commands.executeCommand("sgtForks.focus");
            }
          } else {
            vscode.window.showInformationMessage(msg);
          }
        } catch (e: any) {
          vscode.window.showErrorMessage(e.message);
        }
      }
    );
  });
}
