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
import { SaveFeature, UndoPreview } from "./types";
import { undoConfirmText } from "./undoConfirm";

// One feature's clause in the save attribution toast: its label (or "NEW unnamed feature" for a lane
// the save just minted), plus the first symbol it touched and a "+N" for the rest.
function summarizeSaveFeature(f: SaveFeature): string {
  const name = f.new ? "NEW unnamed feature" : f.label;
  const syms = f.symbols || [];
  const symPart = syms.length ? ` (${syms[0]}${syms.length > 1 ? ` +${syms.length - 1}` : ""})` : "";
  return `${name}${symPart}`;
}

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
    // Esc aborts, the way it does at every other "(optional)" prompt in this file. It used to save
    // anyway, which made this the one dialog where backing out of it still mutated the repo. Enter on
    // an empty box is still how you save without a message -- that returns "", not undefined.
    if (message === undefined) {
      return;
    }
    try {
      const result = await store.sgt.save(message || undefined);
      store.invalidate();
      if (!result.saved) {
        vscode.window.showInformationMessage(result.message || "nothing to save");
        return;
      }
      const sha = (result.commit || "").slice(0, 7);
      const features = result.features || [];
      // Feed the save's feature attribution forward: which feature(s) the new ops landed in, so a
      // just-minted (still unnamed) lane is both visible and nameable straight from the save toast.
      let text = features.length
        ? `Saved ${sha} — ${features.map(summarizeSaveFeature).join("; ")}`
        : `Saved ${sha} · ${result.ops ?? 0} edit(s)`;
      if (result.renamed?.ok && result.renamed.label) {
        text += ` · named "${result.renamed.label}"`;
      }
      const newFeat = features.find((f) => f.new);
      // Offer to name a just-minted lane (unless the save already named one via --as) -- one toast,
      // one button, resolved through the same `sgt feature rename` any rename uses.
      const actions = newFeat && !result.renamed?.ok ? ["Name it…"] : [];
      const choice = await vscode.window.showInformationMessage(text, ...actions);
      if (choice === "Name it…" && newFeat) {
        const label = await vscode.window.showInputBox({
          prompt: `Name the new feature (${newFeat.handle})`,
          placeHolder: "e.g. caching layer",
        });
        if (label) {
          try {
            await store.sgt.mutate(["feature", "rename", newFeat.handle, label]);
            store.invalidate();
            vscode.window.showInformationMessage(`Named "${label}".`);
          } catch (e: any) {
            vscode.window.showErrorMessage(e.message);
          }
        }
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  });

  reg("sgt.undo", async () => {
    // "Undo the last operation?" was the whole of this dialog: it asked for consent to reverse an
    // operation without saying which one, so the answer was a guess about what the user last did.
    // Undo is what you reach for when something has already gone wrong, which is the worst moment
    // to be asked blind -- and everything the question needs is known in advance. `sgt undo --emit`
    // reports it: the kind of operation being reversed, the edits coming back, the edits going away,
    // and the symbols by name. Same report `sgt undo` prints at a terminal before its own [y/N].
    let pv: UndoPreview;
    try {
      pv = await store.sgt.undoPreview();
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    const say = undoConfirmText(pv);
    if (say.kind === "nothing") {
      vscode.window.showInformationMessage(say.message);
      return;
    }
    if (say.kind === "refused") {
      vscode.window.showWarningMessage(say.message, { modal: true });
      return;
    }
    const ok = await vscode.window.showWarningMessage(
      say.message, { modal: true, detail: say.detail }, "Undo");
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
