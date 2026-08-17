// Command wiring. Read/preview commands are safe and immediate. The mutating revert command
// always confirms first, because `sgt revert` refuses on a fork and otherwise re-materializes +
// commits — so we surface the human report and then invalidate every surface.

import * as path from "node:path";
import * as vscode from "vscode";
import { ForkResolutionPanel } from "./forkResolution";
import { PlanDiffProvider, showPlanQuickPick } from "./plan";
import { PreviewProvider } from "./preview";
import { Store } from "./store";
import { BlameView, EmitView, NextAction, PlanView, ProposalChecklistEntry } from "./types";
import { WorkbenchProvider } from "./workbench";

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

/**
 * Surface a mutation's report without losing its warnings.
 *
 * The report's first line says what happened; any ⚠ lines after it say what did
 * NOT happen -- a restore that leaves the earlier revert's collateral removed
 * prints one, and truncating to line one silently dropped it. A caveat the CLI
 * thought worth a ⚠ outranks the success line here, because a toast is the only
 * part of this a person reliably reads.
 */
function showMutationReport(report: string): void {
  const lines = report.trim().split("\n").map((l) => l.trim()).filter(Boolean);
  const caveat = lines.filter((l) => l.startsWith("⚠")).pop();
  if (caveat) {
    vscode.window.showWarningMessage(caveat);
    return;
  }
  vscode.window.showInformationMessage(lines[0] || "Done.");
}

async function applyMutation(store: Store, args: string[], confirmMsg: string): Promise<void> {
  const ok = await vscode.window.showWarningMessage(confirmMsg, { modal: true }, "Apply");
  if (ok !== "Apply") {
    return;
  }
  try {
    // The modal above is the confirmation, so the CLI must not go looking for
    // another one it has no terminal to ask on.
    const report = await store.sgt.confirmedMutate(args);
    store.invalidate();
    showMutationReport(report);
  } catch (e: any) {
    vscode.window.showErrorMessage(e.message);
  }
}

// The interactive revert frontier (U3/R4): preview the selection, then let the user pick which
// toggleable dependents to KEEP (rescue) vs. drop with the target. `foundation` prerequisites
// can't be dropped, so they're surfaced as a count, not offered. Applying with kept dependents
// drafts continuation hollows (see `Sgt.revertKeep`); keeping none is a plain full revert.
async function revertWithFrontier(store: Store, sel: string, preview: PreviewProvider): Promise<void> {
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

  // Show the resulting diff first -- "where this lands" -- so the confirm isn't a blind op-count.
  const changedFiles = await preview.openDiff(view);

  const frontier = view.frontier ?? [];
  const toggleable = frontier.filter((r) => r.toggleable);
  const lockedCount = frontier.length - toggleable.length;

  // No dependents to choose among -> the plain confirm path (behavior unchanged from before U3).
  if (toggleable.length === 0) {
    const note = lockedCount ? ` (built on ${lockedCount} kept prerequisite(s))` : "";
    const diffNote = changedFiles ? ` Changes ${changedFiles} file(s) — see the open diff.` : "";
    await applyMutation(
      store,
      ["revert", sel],
      `Revert ${sel}? Removes ${view.removed.length} op(s)${note}.${diffNote} Rewrites the working tree and commits.`
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
    showMutationReport(report);
  } catch (e: any) {
    vscode.window.showErrorMessage(e.message);
  }
}

/** `sgt restore` with the same feedforward `revert` gets: the resulting diff first, then a confirm
 * carrying real numbers. Restore is revert's inverse (`I ∪ ↓X`), so there is no dependent frontier
 * to choose among -- bringing an edit back also brings what it needs, and nothing is dropped -- but
 * "which files does this rewrite" is exactly as unanswerable from a prose modal here as it is for
 * revert. It also surfaces a refusal (the one-live-version rule) as the CLI's own explanation
 * rather than as a failed mutation after the user already committed to it. */
async function restoreWithPreview(store: Store, sel: string, preview: PreviewProvider): Promise<void> {
  let view: EmitView;
  try {
    view = await store.sgt.emit(sel, "restore");
  } catch (e: any) {
    vscode.window.showErrorMessage(e.message);
    return;
  }
  if (!view.ok) {
    vscode.window.showWarningMessage(view.message || `Cannot restore ${sel}.`);
    return;
  }

  // An `ok` restore that adds nothing is the "already live" case, not a change to confirm. Asking
  // the user to approve a no-op teaches them the confirm means nothing.
  if (view.added.length === 0) {
    vscode.window.showInformationMessage(`${sel} is already present — nothing to restore.`);
    return;
  }

  const changedFiles = await preview.openDiff(view);
  const diffNote = changedFiles ? ` Changes ${changedFiles} file(s) — see the open diff.` : "";
  const brings = view.added.length > 1 ? ` (with ${view.added.length - 1} it depends on)` : "";
  await applyMutation(
    store,
    ["restore", sel],
    `Restore ${sel}? Brings back ${view.added.length} op(s)${brings}.${diffNote} ` +
      `Rewrites the working tree and commits.`
  );
}

export function registerCommands(
  context: vscode.ExtensionContext,
  store: Store,
  preview: PreviewProvider,
  refreshBlame: () => void,
  planDiff: PlanDiffProvider,
  root: string,
  workbench: WorkbenchProvider
): void {
  const reg = (id: string, fn: (...a: any[]) => any) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  reg("sgt.refresh", () => {
    store.invalidate();
    refreshBlame();
  });
  reg("sgt.toggleBlame", () => toggle("blame.enabled"));
  reg("sgt.openWorkbench", () => vscode.commands.executeCommand("sgtWorkbench.focus"));

  // Reveal a symbol's owning feature in the workbench graph. Called with a feature id from the
  // symbol hover's "Open Workbench" link, or with no argument from the editor context menu / palette
  // -- in which case the feature is resolved from the blame span under the cursor (same path as
  // sgt.revertSymbol). The workbench selects + spotlights + scrolls to that feature's lane.
  reg("sgt.revealInWorkbench", async (featureId?: string) => {
    let feature = featureId;
    if (!feature) {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage("Open a file and put the cursor on a symbol to reveal its feature.");
        return;
      }
      const rel = vscode.workspace.asRelativePath(editor.document.uri, false);
      let blame: BlameView;
      try {
        blame = await store.blame(rel);
      } catch (e: any) {
        vscode.window.showErrorMessage(e.message);
        return;
      }
      const line = editor.selection.active.line + 1; // blame spans are 1-based inclusive
      const span = blame.spans.find((s) => s.start_line <= line && line <= s.end_line);
      if (!span) {
        vscode.window.showWarningMessage("No mined symbol under the cursor.");
        return;
      }
      feature = span.feature_id;
    }
    await workbench.revealFeature(feature);
  });

  reg("sgt.previewRevert", async (id?: string) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      void preview.preview(feature);
    }
  });
  reg("sgt.revert", async (id?: string) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      await revertWithFrontier(store, feature, preview);
    }
  });
  reg("sgt.restore", async (id?: string) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      await restoreWithPreview(store, feature, preview);
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

  // `sgt.revertSymbol`: revert the symbol under the cursor. This is the op/symbol-level entry
  // point that actually surfaces the interactive frontier -- a feature-level revert removes a
  // whole op-set and has no single-op dependent frontier (sgt.api._frontier_rows resolves the
  // target to one op). The symbol ref (`file::name`) comes from the blame span over the cursor.
  reg("sgt.revertSymbol", async () => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage("Open a file and put the cursor on a symbol to revert.");
      return;
    }
    const rel = vscode.workspace.asRelativePath(editor.document.uri, false);
    let blame: BlameView;
    try {
      blame = await store.blame(rel);
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    const line = editor.selection.active.line + 1; // blame spans are 1-based inclusive
    const span = blame.spans.find((s) => s.start_line <= line && line <= s.end_line);
    if (!span) {
      vscode.window.showWarningMessage("No mined symbol under the cursor.");
      return;
    }
    await revertWithFrontier(store, span.symbol, preview);
  });

  reg("sgt.showPlanQuickPick", () => showPlanQuickPick(store, planDiff));
  reg("sgt.showPlanDiff", (target) => planDiff.showDiff(target));

  // Hand a stalled plan back to Claude Code: relaunch the interrupted session in a terminal.
  // sgt never authors code itself -- resuming the real agent conversation is the correct on-ramp.
  // Tradeoff: `claude` must be on PATH; if not, the terminal shows command-not-found -- which is
  // exactly the "relaunch that session in their terminal" behavior the user asked for.
  reg("sgt.resumePlan", async (sessionId?: string) => {
    if (!sessionId) {
      return;
    }
    const view = await store.planView();
    const session = view.sessions.find((s) => s.session_id === sessionId);
    // With a captured Claude session id we resume that exact conversation (its plan context is
    // already restored); otherwise the bare picker lets the user pick -- sgt has already shown
    // which plan stalled and its remaining steps.
    const cmd = session?.claude_session_id
      ? `claude --resume ${session.claude_session_id}`
      : "claude --resume";
    // Run the resume (it launches the interactive session and restores context); we deliberately
    // do NOT append a `-p` prompt -- the user continues the restored conversation by typing.
    const term = vscode.window.createTerminal({ name: "sgt resume" });
    term.sendText(cmd, true);
    term.show();
  });

  // The "Now" tree's next-action row. A fork routes to its resolution wizard; anything else with a
  // recommended command runs it in a terminal (a `claude --resume`, `sgt save`, `sgt intent review`
  // -- all interactive or worth watching, so a terminal, not a silent shell-out). `clean` (no
  // command) is a no-op; the row is informational.
  reg("sgt.runNextAction", (action?: NextAction) => {
    if (!action) {
      return;
    }
    if (action.kind === "resolve_fork" && action.target) {
      void vscode.commands.executeCommand("sgt.resolveFork", action.target);
      return;
    }
    // A stalled plan isn't a command to fire blindly -- the whole confusion is "what plan, and why
    // is it stuck?" So route it to the resolution panel, which explains the stall (which steps did
    // land, under what name, whether a conversation is even linked) before offering resume/close.
    if (action.kind === "resume_plan" && action.target) {
      void vscode.commands.executeCommand("sgt.resolveStalledPlan", action.target);
      return;
    }
    if (!action.command) {
      return;
    }
    const term = vscode.window.createTerminal({ name: "sgt next" });
    term.sendText(action.command, true);
    term.show();
  });

  // A stalled plan: work went quiet with steps still unmatched. Rather than a dead click, explain
  // WHY -- per pending step, whether its predicted files already saw edits (built under a different
  // name than predicted, which the name-exact matcher will never confirm) or truly got no edits,
  // plus whether a Claude conversation is even linked for resume -- then offer the three real exits:
  // resume the thread, mark it done (work landed, keep the record), or abandon it.
  reg("sgt.resolveStalledPlan", async (sessionId?: string) => {
    if (!sessionId) {
      return;
    }
    let view: PlanView;
    try {
      view = await store.sgt.planStatus();
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    const session = view.sessions.find((s) => s.session_id === sessionId);
    if (!session) {
      vscode.window.showInformationMessage("That plan is no longer active -- it may already be closed.");
      store.invalidate();
      return;
    }

    const pending = session.steps.filter((s) => s.status === "pending");
    const built = pending.filter((s) => s.covered);
    const csid = session.claude_session_id;
    const stepLines = pending.map((s) => {
      const mark = s.covered ? "✓ built" : "✗ no edits";
      return `${mark}  ${s.title}\n        ${s.coverage_reason ?? "no coverage signal recorded"}`;
    });
    const linkNote = csid
      ? `Linked conversation: claude --resume ${csid}`
      : "No conversation is linked to this plan -- Resume falls back to Claude Code's session picker.";
    const detail = [
      `${pending.length} step(s) still open; ${built.length} already landed under a different name ` +
        `than the plan predicted, so sgt's name-exact matcher never linked them.`,
      "",
      ...stepLines,
      "",
      linkNote,
    ].join("\n");

    // Fully built -> the honest exit is "mark done" (keeps the record); nothing built -> lead with
    // Abandon. Resume only when there's a way to relaunch the thread.
    const markDone = built.length === pending.length ? "Mark done (recommended)" : "Mark done";
    const buttons = csid ? ["Resume in Claude", markDone, "Abandon"] : [markDone, "Abandon"];
    const choice = await vscode.window.showInformationMessage(
      `Stalled plan: ${pending.length} step(s) left, ${built.length} already built`,
      { modal: true, detail },
      ...buttons
    );
    if (!choice) {
      return;
    }
    if (choice === "Resume in Claude") {
      const term = vscode.window.createTerminal({ name: "sgt resume plan" });
      term.sendText(csid ? `claude --resume ${csid}` : "claude --resume", true);
      term.show();
      return;
    }
    try {
      if (choice.startsWith("Mark done")) {
        await store.sgt.planDone(sessionId);
        vscode.window.showInformationMessage("Plan marked done -- kept as history for revert/attribution.");
      } else {
        await store.sgt.planAbandon(sessionId);
        vscode.window.showInformationMessage("Plan abandoned -- its unfinished steps recorded as open intents.");
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    store.invalidate();
  });

  // The N-column tip diff + merge-op/fulfill/land wizard (Phase 6).
  reg("sgt.resolveFork", (symbol?: string) => {
    if (!symbol) {
      return;
    }
    ForkResolutionPanel.createOrShow(context, store, root, symbol);
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
