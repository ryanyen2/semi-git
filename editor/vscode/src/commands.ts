// Command wiring. Read/preview commands are safe and immediate. The mutating revert command
// always confirms first, because `sgt revert` refuses on a fork and otherwise re-materializes +
// commits — so we surface the human report and then invalidate every surface.

import * as path from "node:path";
import * as vscode from "vscode";
import { ForkResolutionPanel } from "./forkResolution";
import { hasOwnSymbols } from "./mapFilter";
import { PlanDiffProvider, showPlanQuickPick } from "./plan";
import { PreviewProvider } from "./preview";
import { Store } from "./store";
import { BlameView, EmitView, NextAction, PlanView, ProposalChecklistEntry } from "./types";
import { WorkbenchProvider } from "./workbench";

/** What a tree item hands a command. A feature node carries `id`; a chapter node carries its
 * segment, and the selector for a chapter is `<feature-id>@<index>` -- the same string the
 * terminal takes. Without this the three verbs on a chapter silently acted on nothing, because
 * they were written when only features were selectable and read a bare string. */
function selectorOf(provided?: unknown): string | undefined {
  if (!provided) return undefined;
  if (typeof provided === "string") return provided;
  const node = provided as { kind?: string; id?: string; segment?: { feature_id: string; seg_index: number } };
  if (node.kind === "chapter" && node.segment) {
    return `${node.segment.feature_id}@${node.segment.seg_index}`;
  }
  return typeof node.id === "string" ? node.id : undefined;
}

async function pickFeature(store: Store, provided?: unknown): Promise<string | undefined> {
  const direct = selectorOf(provided);
  if (direct) {
    return direct;
  }
  let map;
  try {
    map = await store.map();
  } catch {
    return undefined;
  }
  // Husks excluded: a feature whose ops touch only sentinels reverts to nothing, so offering it here
  // is the silent-success shape (a named target that succeeds while doing nothing).
  const features = map.nodes.filter((n) => n.kind === "feature" && hasOwnSymbols(n));
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
function mutationHeadline(report: string): string {
  const lines = report.trim().split("\n").map((l) => l.trim()).filter(Boolean);
  return lines.filter((l) => l.startsWith("⚠")).pop() || lines[0] || "Done.";
}

/**
 * Where a flow's outcome is reported.
 *
 * Every flow below used to do both: hand the outcome to `onPhase` (which the workbench draws in
 * its confirm bar, where the decision was taken) AND raise a VS Code notification with the same
 * sentence. One click could produce three of them -- the caveat, the result, and whatever the
 * refresh said -- stacked in the corner, each covering the last, over the part of the timeline the
 * reader was watching to see what the action did.
 *
 * So: exactly one surface per flow. A caller with a phase sink owns its own reporting and gets no
 * notifications; the palette and the tree views have no surface of their own, so they still get
 * them. Errors follow the same rule -- the confirm bar draws a failure with its own Dismiss.
 */
function reportTo(opts: IdealEditFlowOpts) {
  const inSurface = Boolean(opts.onPhase);
  return {
    phase: opts.onPhase ?? (() => undefined),
    info: (m: string) => { if (!inSurface) { vscode.window.showInformationMessage(m); } },
    warn: (m: string) => { if (!inSurface) { vscode.window.showWarningMessage(m); } },
    error: (m: string) => { if (!inSurface) { vscode.window.showErrorMessage(m); } },
  };
}

/** First `n` of a list, comma-joined, with the remainder counted rather than printed -- a modal
 * that lists forty symbols is the same as a modal that lists none. */
function cap(xs: string[], n = 6): string {
  return xs.slice(0, n).join(", ") + (xs.length > n ? `, +${xs.length - n} more` : "");
}

/**
 * The consequence block a revert confirm carries as its modal `detail`.
 *
 * The confirm line alone answers "how many ops", and none of "of what", "which
 * files", "what still points at it", or "what is left alone" -- so a pilot
 * participant hit Rewind on a checkpoint and could not say afterwards what they
 * had rewound to. The emit view already carries every one of those (the CLI
 * prints them above its own y/N gate), so this reads them off the same
 * projection rather than deriving a second opinion the two surfaces could
 * disagree about.
 */
function consequenceDetail(view: EmitView): string {
  const files = Object.keys(view.files);
  // `::__anchor__`/`::__residue__` are the miner's own bookkeeping symbols; naming one at a user
  // reads as an internal leak, the same reason `sgt.api.so_what_for` skips them in its headline.
  const symbols = view.affected_symbols.filter((s) => !s.includes("::__"));
  const frontier = view.frontier ?? [];
  // `blast` is only the part of the frontier the target REFERENCES directly. Everything else
  // reached through chain or declared edges comes back as `carry`, and carry rows are toggleable
  // too -- so "no blast rows" does not mean "nothing is at risk". `toggleable` is the predicate
  // for that question; `blast` is the predicate for how many the next dialog will ask about.
  const blast = frontier.filter((r) => r.bucket === "blast" && r.toggleable);
  const atRisk = frontier.filter((r) => r.toggleable);
  const locked = frontier.filter((r) => !r.toggleable);
  const untouched = view.focus?.context_count ?? 0;
  return [
    view.focus?.so_what,
    `${view.removed.length} edit(s) come out` +
      (files.length ? `, across ${files.length} file(s): ${cap(files)}` : ", changing no files"),
    symbols.length ? `Symbols affected: ${cap(symbols)}` : undefined,
    // Absent is not the same as none. `sgt.api._frontier_rows` returns `[]` for any target it
    // cannot reduce to a single op -- which is every whole-feature and every checkpoint revert,
    // i.e. both ways into this dialog. Saying "nothing is left dangling" there would be asserting
    // safety from an absence of data, in the modal that gates a destructive rewrite.
    frontier.length
      ? atRisk.length
        ? `${atRisk.length} later edit(s) are built on this — you pick which to keep next.` +
          (blast.length && blast.length !== atRisk.length
            ? ` ${blast.length} of them reference it directly.`
            : "")
        : "Nothing built on it is left dangling."
      : "What is built on top was not computed for this target — check the diff before applying.",
    locked.length ? `${locked.length} prerequisite(s) it sits on stay put.` : undefined,
    untouched ? `${untouched} other feature(s) are untouched.` : undefined,
    files.length ? "Nothing is applied yet — the open PREVIEW tabs are the proposed before → after." : undefined,
  ]
    .filter(Boolean)
    .join("\n");
}

/** How a revert/restore flow was entered, and who is listening.
 *
 * The workbench's in-graph staged confirm already painted the consequence on the graph and took an
 * explicit Apply click, so `confirmed` skips the modal that would ask the same question a second
 * time -- the modal was the confirmation; it is not a second safety layer once a real one ran.
 * `openDiff: false` keeps the PREVIEW tabs closed (the workbench offers them on demand instead of
 * stealing focus on every apply). `onPhase` reports the flow's real stages -- checking the
 * consequence, rewriting+committing, rebuilding the graph -- so a surface can paint intermediate
 * progress rather than a dead gap between click and toast. Palette/tree entry points pass nothing
 * and keep the modal-and-diff behavior unchanged. */
export type IdealEditPhase = "checking" | "applying" | "refreshing" | "done" | "failed" | "cancelled";
export interface IdealEditFlowOpts {
  confirmed?: boolean;
  openDiff?: boolean;
  onPhase?: (phase: IdealEditPhase, detail?: string) => void;
}

/** The shared apply tail: mutate → invalidate → report, with phases. The caller has already taken
 * a real confirmation (the modal, or the workbench's staged Apply), so the CLI must not go looking
 * for another one it has no terminal to ask on -- `mutate` passes the `--yes` that says so (see
 * `mutationArgs` in cliSeam.ts).
 *
 * The headline goes through `phase("done", ...)` rather than only into a notification, so the
 * caveat line -- what did NOT happen -- reaches a workbench caller too. It used to be raised as a
 * warning toast and the phase carried the success line, so the two surfaces disagreed about what
 * the same apply had done. */
async function applyIdealEdit(
  store: Store,
  run: () => Promise<string>,
  report: ReturnType<typeof reportTo>
): Promise<void> {
  report.phase("applying");
  try {
    const text = await run();
    report.phase("refreshing");
    store.invalidate();
    const headline = mutationHeadline(text);
    report.info(headline);
    report.phase("done", headline);
  } catch (e: any) {
    report.phase("failed", e.message);
    report.error(e.message);
  }
}

// The interactive revert frontier (U3/R4): preview the selection, then let the user pick which
// toggleable dependents to KEEP (rescue) vs. drop with the target. `foundation` prerequisites
// can't be dropped, so they're surfaced as a count, not offered. Applying with kept dependents
// drafts continuation hollows (see `Sgt.revertKeep`); keeping none is a plain full revert.
// `sel` is anything the CLI's own selection ladder resolves -- a feature id/label, a `file::name`
// symbol, or a `<feature>@<n>` checkpoint. `--emit` and a plain `revert <sel>` share that ladder;
// `--keep` does NOT (cli/ideal_edit.py routes it to `_revert_keep_dependents`, which resolves via
// `verbs.resolve_target` and never reaches the checkpoint branch). That path is unreachable today
// only because the frontier above is empty for exactly the targets that would take it.
async function revertWithFrontier(
  store: Store,
  sel: string,
  preview: PreviewProvider,
  opts: IdealEditFlowOpts = {}
): Promise<void> {
  const report = reportTo(opts);
  const phase = report.phase;
  phase("checking");
  let view: EmitView;
  try {
    view = await store.sgt.emit(sel);
  } catch (e: any) {
    phase("failed", e.message);
    report.error(e.message);
    return;
  }
  // A checkpoint target comes back carrying its own chapter name; anything else is named by the
  // token the user picked. Computed before the refusal branch so even "cannot revert" names the
  // chapter, and used everywhere below so the question and the answer agree on the noun.
  const name = view.checkpoint ? `checkpoint "${view.checkpoint}"` : sel;

  if (!view.ok) {
    phase("failed", view.message || `Cannot revert ${name}.`);
    report.warn(view.message || `Cannot revert ${name}.`);
    return;
  }

  // An `ok` revert that removes nothing is not a change to confirm -- these ops are already out of
  // this composition. Asking anyway and then reporting success is precisely the "wait, is that
  // done?" the preview exists to prevent. Mirrors `restoreWithPreview`'s already-live guard below.
  if (view.removed.length === 0) {
    const nothing = `${name} removes nothing here — it is already out of this composition.`;
    phase("done", nothing);
    report.info(nothing);
    return;
  }

  // Show the resulting diff first -- "where this lands" -- so the confirm isn't a blind op-count.
  // A workbench-staged apply already held the consequence on the graph and offers the diff on
  // demand, so it opts out rather than having two tabs steal focus on every apply.
  if (opts.openDiff !== false) await preview.openDiff(view);
  const detail = consequenceDetail(view);

  const frontier = view.frontier ?? [];
  const toggleable = frontier.filter((r) => r.toggleable);
  const lockedCount = frontier.length - toggleable.length;

  // No dependents to choose among -> the plain confirm path (behavior unchanged from before U3).
  if (toggleable.length === 0) {
    if (!opts.confirmed) {
      const ok = await vscode.window.showWarningMessage(
        `Revert ${name}? Rewrites the working tree and commits.`,
        { modal: true, detail },
        "Apply"
      );
      if (ok !== "Apply") {
        phase("cancelled");
        return;
      }
    }
    await applyIdealEdit(store, () => store.sgt.mutate(["revert", sel]), report);
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
        `Revert ${name}: check dependents to KEEP; unchecked are removed with it` +
        (lockedCount ? ` · ${lockedCount} prerequisite(s) locked` : ""),
    }
  );
  if (!picks) {
    phase("cancelled");
    return; // cancelled
  }
  const keep = picks.map((p) => p.op_id);
  // The dependent QuickPick above is a *choice*, not a confirmation -- a staged workbench apply
  // still gets it, and only the redundant "are you sure" modal after it is skipped.
  if (!opts.confirmed) {
    const summary = keep.length
      ? `keep ${keep.length}/${toggleable.length} dependent(s) — drafts continuation hollows to fulfill`
      : `remove ${name} and all ${toggleable.length} dependent(s)`;
    const ok = await vscode.window.showWarningMessage(
      `Revert ${name} — ${summary}?`,
      { modal: true, detail },
      "Apply"
    );
    if (ok !== "Apply") {
      phase("cancelled");
      return;
    }
  }
  await applyIdealEdit(store, () => store.sgt.revertKeep(sel, keep), report);
}

/** `sgt restore` with the same feedforward `revert` gets: the resulting diff first, then a confirm
 * carrying real numbers. Restore is revert's inverse (`I ∪ ↓X`), so there is no dependent frontier
 * to choose among -- bringing an edit back also brings what it needs, and nothing is dropped -- but
 * "which files does this rewrite" is exactly as unanswerable from a prose modal here as it is for
 * revert. It also surfaces a refusal (the one-live-version rule) as the CLI's own explanation
 * rather than as a failed mutation after the user already committed to it. */
async function restoreWithPreview(
  store: Store,
  sel: string,
  preview: PreviewProvider,
  opts: IdealEditFlowOpts = {}
): Promise<void> {
  const report = reportTo(opts);
  const phase = report.phase;
  phase("checking");
  let view: EmitView;
  try {
    view = await store.sgt.emit(sel, "restore");
  } catch (e: any) {
    phase("failed", e.message);
    report.error(e.message);
    return;
  }
  if (!view.ok) {
    phase("failed", view.message || `Cannot restore ${sel}.`);
    report.warn(view.message || `Cannot restore ${sel}.`);
    return;
  }

  // An `ok` restore that adds nothing is the "already live" case, not a change to confirm. Asking
  // the user to approve a no-op teaches them the confirm means nothing.
  if (view.added.length === 0) {
    const nothing = `${sel} is already present — nothing to restore.`;
    phase("done", nothing);
    report.info(nothing);
    return;
  }

  if (!opts.confirmed) {
    const changedFiles = opts.openDiff !== false ? await preview.openDiff(view) : 0;
    const diffNote = changedFiles ? ` Changes ${changedFiles} file(s) — see the open diff.` : "";
    const brings = view.added.length > 1 ? ` (with ${view.added.length - 1} it depends on)` : "";
    const ok = await vscode.window.showWarningMessage(
      `Restore ${sel}? Brings back ${view.added.length} op(s)${brings}.${diffNote} ` +
        `Rewrites the working tree and commits.`,
      { modal: true },
      "Apply"
    );
    if (ok !== "Apply") {
      phase("cancelled");
      return;
    }
  } else if (opts.openDiff !== false) {
    await preview.openDiff(view);
  }
  await applyIdealEdit(store, () => store.sgt.mutate(["restore", sel]), report);
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

  reg("sgt.previewRevert", async (id?: unknown) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      void preview.preview(feature);
    }
  });
  // `opts` (IdealEditFlowOpts) is how the workbench's staged in-graph confirm drives the same
  // flow: it already showed the consequence and took an explicit Apply, so it passes
  // `confirmed: true` (skip the modal), `openDiff: false` (diff on demand instead of focus-steal),
  // and an `onPhase` that paints real progress in the confirm bar. Same-extension executeCommand
  // passes these through in-process, callbacks included.
  reg("sgt.revert", async (id?: unknown, opts?: IdealEditFlowOpts) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      await revertWithFrontier(store, feature, preview, opts ?? {});
    } else {
      opts?.onPhase?.("cancelled");
    }
  });
  reg("sgt.restore", async (id?: unknown, opts?: IdealEditFlowOpts) => {
    const feature = await pickFeature(store, id);
    if (feature) {
      await restoreWithPreview(store, feature, preview, opts ?? {});
    } else {
      opts?.onPhase?.("cancelled");
    }
  });

  // `sgt edit <sel>` (U4/KTD5): draft an in-place change. This drafts a continuation hollow and
  // mechanically repoints dependents; the user then edits the working tree and Saves to fulfill.
  // (No preview/frontier yet -- `sgt edit` has no `--emit`; that's a flagged CLI follow-up.)
  reg("sgt.edit", async (id?: unknown) => {
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
