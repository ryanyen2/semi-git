// Command wiring. Read/preview commands are safe and immediate. The mutating revert command
// always confirms first, because `sgt revert` refuses on a fork and otherwise re-materializes +
// commits — so we surface the human report and then invalidate every surface.

import * as path from "node:path";
import * as vscode from "vscode";
import { ForkResolutionPanel } from "./forkResolution";
import { PlanDiffProvider, showPlanQuickPick } from "./plan";
import { PreviewProvider } from "./preview";
import { Store } from "./store";
import { EmitView, ProposalChecklistEntry } from "./types";

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

// The interactive revert frontier (U3/R4): preview the selection, then let the user pick which
// toggleable dependents to KEEP (rescue) vs. drop with the target. `foundation` prerequisites
// can't be dropped, so they're surfaced as a count, not offered. Applying with kept dependents
// drafts continuation hollows (see `Sgt.revertKeep`); keeping none is a plain full revert.
async function revertWithFrontier(store: Store, sel: string): Promise<void> {
  let view: EmitView;
  try {
    view = await store.sgt.emit(sel);
  } catch (e: any) {
    vscode.window.showErrorMessage(e.message);
    return;
  }
  if (!view.ok) {
    vscode.window.showWarningMessage(view.message || `Cannot revert ${sel}.`);
    return;
  }

  const frontier = view.frontier ?? [];
  const toggleable = frontier.filter((r) => r.toggleable);
  const lockedCount = frontier.length - toggleable.length;

  // No dependents to choose among -> the plain confirm path (behavior unchanged from before U3).
  if (toggleable.length === 0) {
    const note = lockedCount ? ` (built on ${lockedCount} kept prerequisite(s))` : "";
    await applyMutation(
      store,
      ["revert", sel],
      `Revert ${sel}? Removes ${view.removed.length} op(s)${note}. Rewrites the working tree and commits.`
    );
    return;
  }

  const keepEffect = (bucket: string) =>
    bucket === "blast" ? "keep drafts a continuation hollow" : "keep repoints for free";
  const picks = await vscode.window.showQuickPick(
    toggleable.map((r) => ({
      label: r.op_id.slice(0, 12),
      description: `${r.bucket} · ${keepEffect(r.bucket)}`,
      op_id: r.op_id,
    })),
    {
      canPickMany: true,
      placeHolder:
        `Revert ${sel}: check dependents to KEEP; unchecked are removed with it` +
        (lockedCount ? ` · ${lockedCount} prerequisite(s) locked` : ""),
    }
  );
  if (!picks) {
    return; // cancelled
  }
  const keep = picks.map((p) => p.op_id);
  const summary = keep.length
    ? `keep ${keep.length}/${toggleable.length} dependent(s) — drafts continuation hollows to fulfill`
    : `remove ${sel} and all ${toggleable.length} dependent(s)`;
  const ok = await vscode.window.showWarningMessage(`Revert ${sel} — ${summary}?`, { modal: true }, "Apply");
  if (ok !== "Apply") {
    return;
  }
  try {
    const report = await store.sgt.revertKeep(sel, keep);
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
  planDiff: PlanDiffProvider,
  root: string
): void {
  const reg = (id: string, fn: (...a: any[]) => any) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  reg("sgt.refresh", () => {
    store.invalidate();
    refreshBlame();
  });
  reg("sgt.toggleBlame", () => toggle("blame.enabled"));
  reg("sgt.openWorkbench", () => vscode.commands.executeCommand("sgtWorkbench.focus"));

  reg("sgt.previewRevert", async (id?: string) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      void preview.preview(feature);
    }
  });
  reg("sgt.revert", async (id?: string) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      await revertWithFrontier(store, feature);
    }
  });
  reg("sgt.restore", async (id?: string) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      await applyMutation(
        store,
        ["restore", feature],
        `Restore feature ${feature}? This rewrites the working tree and commits.`
      );
    }
  });

  // `sgt edit <sel>` (U4/KTD5): draft an in-place change. This drafts a continuation hollow and
  // mechanically repoints dependents; the user then edits the working tree and Saves to fulfill.
  // (No preview/frontier yet -- `sgt edit` has no `--emit`; that's a flagged CLI follow-up.)
  reg("sgt.edit", async (id?: string) => {
    const sel = await pickFeature(store, id);
    if (!sel) {
      return;
    }
    const intent = await vscode.window.showInputBox({
      prompt: `Edit ${sel} in place — describe the intent (optional)`,
      placeHolder: "e.g. accept an optional timeout argument",
    });
    if (intent === undefined) {
      return; // cancelled
    }
    try {
      const draft = await store.sgt.edit(sel, intent || undefined);
      store.invalidate();
      if (!draft.ok) {
        vscode.window.showWarningMessage(draft.message || `Cannot edit ${sel}.`);
        return;
      }
      const hollows = draft.hollow_ids?.length ?? 0;
      vscode.window.showInformationMessage(
        `Drafted edit of ${draft.target ?? sel} (${hollows} hollow${hollows === 1 ? "" : "s"}). ` +
          `Change the code in your working tree, then Save to fulfill.`
      );
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  });

  reg("sgt.showPlanQuickPick", () => showPlanQuickPick(store, planDiff));
  reg("sgt.showPlanDiff", (target) => planDiff.showDiff(target));

  // The N-column tip diff + merge-op/fulfill/land wizard (Phase 6).
  reg("sgt.resolveFork", (symbol?: string) => {
    if (!symbol) {
      return;
    }
    ForkResolutionPanel.createOrShow(context, store, root, symbol);
  });

  // Ack a trust-queue group or a single op (`sgtChanges`'s trustGroup/trustOp context menu),
  // dequeuing it from future `trust_view` calls.
  reg("sgt.reviewAck", async (node?: { kind?: string; group?: { op_ids: string[] }; op?: { op_id: string } }) => {
    const opIds = node?.kind === "trustGroup" ? node.group?.op_ids : node?.op ? [node.op.op_id] : undefined;
    if (!opIds?.length) {
      return;
    }
    const note = await vscode.window.showInputBox({ prompt: "Note (optional)" });
    try {
      const result = await store.sgt.reviewAck(opIds, note || undefined);
      store.invalidate();
      vscode.window.showInformationMessage(
        result.ok
          ? `✓ review ${result.id}: acked ${result.op_ids?.length ?? 0} op(s) (${result.scope})`
          : result.error || "ack failed"
      );
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  });

  // Partial-accept review + the U23 CAS advance (`sgtCompositions`'s proposal context menu).
  // Refuses a subset that omits a feature another chosen feature `requires`, naming it -- that
  // validation lives server-side (`propose.py::_resolve_subset`), surfaced here via `report.error`.
  reg("sgt.proposeLand", async (node?: { kind?: string; proposal?: { id: string; title?: string } }) => {
    const id = node?.proposal?.id;
    if (!id) {
      return;
    }
    let checklist: ProposalChecklistEntry[] = [];
    try {
      checklist = (await store.sgt.proposalReviewView(id)).feature_checklist || [];
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    let subset: string[] | undefined;
    if (checklist.length > 1) {
      const picks = await vscode.window.showQuickPick(
        checklist.map((f) => ({ label: f.label, description: `${f.op_count} op(s)`, picked: true, id: f.feature_id })),
        { canPickMany: true, placeHolder: "Land which features? (all selected by default)" }
      );
      if (!picks) {
        return;
      }
      if (picks.length < checklist.length) {
        subset = picks.map((p) => p.id);
      }
    }
    const label = node?.proposal?.title || id;
    const ok = await vscode.window.showWarningMessage(
      `Land proposal ${label}${subset ? ` (${subset.length}/${checklist.length} feature(s))` : ""}? Advances the base branch.`,
      { modal: true },
      "Land"
    );
    if (ok !== "Land") {
      return;
    }
    try {
      const report = await store.sgt.proposeLand(id, subset);
      store.invalidate();
      if (report.error) {
        vscode.window.showErrorMessage(`Land blocked: ${report.error}`);
      } else if (!report.landed) {
        vscode.window.showWarningMessage(`Land blocked: ${report.blocked_reason || "unknown reason"}`);
      } else {
        vscode.window.showInformationMessage(
          `✓ propose land ${report.branch}: ${(report.land_sha || "").slice(0, 12)} (${report.ops_added} op(s))`
        );
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  });

  // Pushes the proposal's rendered PR branch and creates/updates a GitHub PR via `gh`.
  reg("sgt.proposePublish", async (node?: { kind?: string; proposal?: { id: string; title?: string } }) => {
    const id = node?.proposal?.id;
    if (!id) {
      return;
    }
    const label = node?.proposal?.title || id;
    const ok = await vscode.window.showWarningMessage(`Publish proposal ${label} as a GitHub PR?`, { modal: true }, "Publish");
    if (ok !== "Publish") {
      return;
    }
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "sgt propose publish…" },
      async () => {
        try {
          const result = await store.sgt.proposePublish(id);
          vscode.window.showInformationMessage(
            result.ok ? `✓ PR ${result.action} on ${result.branch}` : result.error || "publish failed"
          );
        } catch (e: any) {
          vscode.window.showErrorMessage(e.message);
        }
      }
    );
  });

  reg("sgt.viewProposal", async (id?: string) => {
    if (!id) {
      return;
    }
    try {
      const p = await store.proposalView(id);
      const parts = [
        `Proposal ${p.id}`,
        p.title,
        p.base_ref && `base ${p.base_ref}`,
        p.status && `state: ${p.status.state}`,
        p.feature_delta?.length && `features: ${p.feature_delta.map((f) => f.label).join(", ")}`,
      ].filter(Boolean);
      vscode.window.showInformationMessage(parts.join(" — "));
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  });

  // Shared by the drift/unmanaged rows in `sgtChanges` -- jump to a file, optionally to a span.
  reg("sgt.jumpToLocation", async (loc: { path: string; startLine: number; endLine: number }) => {
    const uri = vscode.Uri.file(path.isAbsolute(loc.path) ? loc.path : path.join(root, loc.path));
    const doc = await vscode.workspace.openTextDocument(uri);
    const editor = await vscode.window.showTextDocument(doc);
    const line = Math.max(0, loc.startLine - 1);
    const endLine = Math.max(line, loc.endLine - 1);
    const range = new vscode.Range(line, 0, endLine, 0);
    editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
    editor.selection = new vscode.Selection(range.start, range.start);
  });
}
