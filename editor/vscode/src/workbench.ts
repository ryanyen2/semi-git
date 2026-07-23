// The Composition Workbench view: a 3-pane view (rail: tree+timeline, inspector: detail/actions/
// code(I)) grounded in experiments/patch_clustering/out/rail3.html. Docked as a `WebviewView` in
// the bottom panel (beside Terminal/Output), matching GitLens's Commit Graph, rather than an
// editor tab. Sourced from one `compose_view` call (map+history+status+forks+drift+sessions+
// proposals+oracle) instead of the old feature-map's separate map()+history() fetches. The
// webview's message protocol (`ready`/`previewVerb`/`applyVerb`/`renamePrompt`, plus
// `pickComposition`/`requestFold` for this phase) drives media/workbench.js; every mutation still
// goes through the real, unmodified `sgt merge`/`split`/`rename`/`move`/`revert` commands -- a
// preview never writes anything, and a fold never materializes the working tree.
//
// Phase-3 scope: the static 3-pane skeleton, a QuickPick-driven composition selector (HEAD +
// sessions, by branch ref; proposals are view-only here), and a single-shot `foldAt`-backed code
// panel scoped to a feature by prefix-matching `MapNode.dir` over `fold_view.files` (there is no
// feature->file API field yet, and inventing one ahead of need would violate the additive-only,
// don't-speculate discipline).
//
// Phase 4 adds `scrubPlayhead`/`playheadResult`: the draggable playhead drags over
// `history_view.ops`' commit-index axis and folds `{commitIndex}` frontiers live (media/
// workbench.js debounces + snaps to op columns). It is a read-only exploration mode -- it never
// changes the composition ref previews/applies run against.
//
// Phase 6 renders pending plan predictions in-situ on the rail (media/workbench.js's
// `collectPlanMarks`/`renderPlanMarksForRow`: an open mark in the future band of the predicted
// feature's own row, not a separate ghost subtree): purely a client-side overlay over the same
// `compose.plan`/`compose.history` this class already pushes -- no new message types, no
// host-side change here.

import * as vscode from "vscode";
import { colorForNode } from "./color";
import { FoldFrontier } from "./sgt";
import { Store } from "./store";

export class WorkbenchProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly previewCache = new Map<string, unknown>();

  constructor(private readonly context: vscode.ExtensionContext, private store: Store) {
    this.disposables.push(
      store.onDidChange(() => void this.pushState()),
      // Identity colors are theme-aware (color.ts's OKLCH lightness shifts light<->dark), so a
      // theme switch needs the rail's node colors recomputed -- same trigger blame.ts uses.
      vscode.window.onDidChangeActiveColorTheme(() => void this.pushState())
    );
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "media")],
    };
    view.webview.html = this.html(view.webview);
    this.disposables.push(
      view.webview.onDidReceiveMessage((msg) => void this.onMessage(msg)),
      view.onDidDispose(() => {
        this.view = undefined;
      })
    );
  }

  private html(webview: vscode.Webview): string {
    const jsUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "workbench.js"));
    const cssUri = webview.asWebviewUri(vscode.Uri.joinPath(this.context.extensionUri, "media", "workbench.css"));
    const nonce = String(this.context.extension.packageJSON.version || "0").length + "-" + Date.now();
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';" />
<link rel="stylesheet" href="${cssUri}" />
<title>SGT Composition Workbench</title>
</head>
<body>
<div id="app">
  <div id="titlebar">
    <button id="compositionBtn" class="composition-btn">HEAD</button>
    <span id="oracleChip" class="oracle-chip" data-state="pending">oracle: pending</span>
    <div id="titlebarActions" class="titlebar-actions">
      <button id="saveBtn" title="sgt save">Save</button>
      <button id="commitBtn" title="sgt commit">Commit</button>
      <button id="undoBtn" title="sgt undo">Undo</button>
    </div>
  </div>
  <div id="main">
    <div id="rail"></div>
    <button id="offscreenAbove" class="offscreen-pill offscreen-pill-top" hidden></button>
    <button id="offscreenBelow" class="offscreen-pill offscreen-pill-bottom" hidden></button>
    <div id="inspector"></div>
  </div>
  <div id="presence" title="where you are: composition · view · selection closure · uncommitted work"></div>
</div>
<script nonce="${nonce}" src="${jsUri}"></script>
</body>
</html>`;
  }

  private async pushState(): Promise<void> {
    let compose;
    try {
      compose = await this.store.composeView();
    } catch (e: any) {
      void this.view?.webview.postMessage({ type: "error", message: e.message });
      return;
    }
    // `compose_view`'s `map` is a *pure read* of the last-built `tree.json`; `history` is projected
    // straight from git ops. Right after `sgt init` (which writes the ideal.json marker but never
    // builds the tree) that leaves `map.nodes` empty while `history.ops` is full -- the timeline
    // renders no lanes even though the ops exist. The feature tree heals itself because it calls
    // `sgt map` (a rebuild), but that rebuild's `.sgt/` write is swallowed by the watcher's own
    // loop-guard, so the compose cache is never refreshed and the timeline stays blank until a
    // window reload. Build the tree once here (`store.map(true)` == `sgt map`, which saves
    // tree.json) and re-read compose so the lanes appear without a reload.
    if (!compose.map.nodes.length && compose.history.ops.length) {
      try {
        await this.store.map(true);
        compose = await this.store.composeView(true);
      } catch {
        // Fall through with the empty map; the rail just shows no lanes rather than erroring.
      }
    }
    // The canonical lane×commit cell join (`grid_view`, plan U3): the timeline/rail layouts render
    // from this instead of re-deriving the (op -> cell) join client-side. Fetched after any heal
    // above so it reflects the just-built tree; an empty fallback just yields no lanes.
    let grid;
    try {
      grid = await this.store.gridView();
    } catch {
      grid = { commits: [], cells: [] };
    }
    const nodes = compose.map.nodes.map((n) => ({
      ...n,
      color: n.kind === "feature" ? colorForNode(n.id) : null,
    }));
    this.previewCache.clear();
    void this.view?.webview.postMessage({
      type: "state",
      compose: { ...compose, grid, map: { ...compose.map, nodes } },
    });
  }

  private async onMessage(msg: any): Promise<void> {
    switch (msg.type) {
      case "ready":
        void this.pushState();
        return;
      case "previewVerb":
        await this.preview(msg.verb, msg.args, msg.seq);
        return;
      case "previewSplit":
        await this.previewSplit(msg.featureId, msg.seq);
        return;
      case "selectClosure":
        await this.selectClosure(msg.refs, msg.seq);
        return;
      case "revertSelection":
        await this.revertSelection(msg.refs);
        return;
      case "revertCheckpoint":
        await this.revertCheckpoint(msg.ref, msg.label);
        return;
      case "applyVerb":
        await this.apply(msg.verb, msg.args);
        return;
      case "renamePrompt":
        await this.renamePrompt(msg.feature);
        return;
      case "pickComposition":
        await this.pickComposition();
        return;
      case "requestFold":
        await this.requestFold(msg.featureId, msg.ref, msg.seq);
        return;
      case "scrubPlayhead":
        await this.scrubPlayhead(msg.commitIndex, msg.seq);
        return;
      case "runOracle":
        await this.runOracle();
        return;
      case "overrideOracle":
        await this.overrideOracle();
        return;
      case "dailyLoop":
        await this.dailyLoop(msg.verb);
        return;
      case "resolveFork":
        await vscode.commands.executeCommand("sgt.resolveFork", msg.symbol);
        return;
      case "confirmCheckpoint":
        await this.confirmCheckpoint(msg.hollowIds, msg.opIds);
        return;
      case "openPlanDiff":
        await vscode.commands.executeCommand("sgt.showPlanDiff", msg.target);
        return;
      default:
        return;
    }
  }

  private async preview(verb: string, args: string[], seq: number): Promise<void> {
    const key = `${verb}:${args.join("")}`;
    let result = this.previewCache.get(key);
    if (result === undefined) {
      try {
        result = await this.store.sgt.previewVerb(verb, args);
      } catch (e: any) {
        result = { ok: false, message: e.message, affected_features: [] };
      }
      this.previewCache.set(key, result);
    }
    void this.view?.webview.postMessage({ type: "previewResult", seq, result });
  }

  // Split has no `sgt preview split` entry (`sgt split <feature>` with no `--apply` already *is*
  // that preview, by design -- see `sgt/cli/inspect.py`'s `_preview_verb`), so its hover-preview
  // goes through `splitPreview` rather than the generic `previewVerb` round-trip every other
  // action-bar verb shares. Not cached like `preview()` -- a split's grouping is cheap to
  // recompute and, unlike merge/move/revert, has no meaningful memoization key beyond the
  // feature id itself changing, which already busts `previewCache`'s per-composition clear.
  private async previewSplit(featureId: string, seq: number): Promise<void> {
    let result;
    try {
      result = await this.store.sgt.splitPreview(featureId);
    } catch (e: any) {
      result = { ok: false, message: e.message };
    }
    void this.view?.webview.postMessage({ type: "previewResult", seq, result });
  }

  // The union closure a multi-select induces (Stage C): `sgt select` reports the feature ids +
  // closure op count + pulled features, so the workbench can show "N features → M ops in closure"
  // and paint the union. Report-only (never materializes), same seq-drop pattern as preview().
  private async selectClosure(refs: string[], seq: number): Promise<void> {
    let result;
    try {
      result = await this.store.sgt.select(refs);
    } catch (e: any) {
      result = { ok: false, message: e.message };
    }
    void this.view?.webview.postMessage({ type: "selectionResult", seq, result });
  }

  // Revert a multi-select in turn (Stage C). Mirrors the TUI, whose multi-select applies per
  // feature rather than as one atomic set-revert: each `sgt revert <feature>` re-resolves against
  // current state (mine-on-contact), so reverting them sequentially is correct, and we STOP on the
  // first refusal (e.g. a fork) rather than pressing on into an inconsistent partial. One confirm
  // up front covers the batch.
  private async revertSelection(refs: string[]): Promise<void> {
    if (!refs?.length) {
      return;
    }
    const ok = await vscode.window.showWarningMessage(
      `Revert ${refs.length} selected feature(s)? Each is reverted and committed in turn; stops if one refuses.`,
      { modal: true },
      "Revert all"
    );
    if (ok !== "Revert all") {
      return;
    }
    let done = 0;
    for (const ref of refs) {
      try {
        await this.store.sgt.mutate(["revert", ref]);
        done++;
      } catch (e: any) {
        this.store.invalidate();
        vscode.window.showWarningMessage(`Reverted ${done}/${refs.length}; stopped at ${ref}: ${e.message}`);
        return;
      }
    }
    this.store.invalidate();
    vscode.window.showInformationMessage(`Reverted ${done} feature(s).`);
  }

  // Rewind a single feature-scoped checkpoint (an intent segment). `ref` is `<feature>@<n>`, which
  // `sgt revert` resolves to that segment's deterministic op-set -- the same exact ideal-edit path
  // as any other revert. A checkpoint-specific confirm (not the multi-feature one) so the user
  // sees exactly which chapter they're undoing.
  private async revertCheckpoint(ref: string, label: string): Promise<void> {
    if (!ref) {
      return;
    }
    const ok = await vscode.window.showWarningMessage(
      `Rewind checkpoint "${label}"? This removes its ops (and anything built on them) and commits.`,
      { modal: true },
      "Rewind"
    );
    if (ok !== "Rewind") {
      return;
    }
    try {
      await this.store.sgt.mutate(["revert", ref]);
      vscode.window.showInformationMessage(`Rewound checkpoint "${label}".`);
    } catch (e: any) {
      vscode.window.showWarningMessage(`Could not rewind "${label}": ${e.message}`);
    }
    this.store.invalidate();
  }

  private async renamePrompt(feature: string): Promise<void> {
    const label = await vscode.window.showInputBox({ prompt: `New label for ${feature}` });
    if (label) {
      await this.apply("rename", [feature, label]);
    }
  }

  // The oracle chip is the workbench's one interactive verification affordance: a click runs the
  // configured tiers and the chip's next render carries the fresh `compose.status.oracle`
  // transition (unconfigured/pending -> pass/fail) -- no separate report dialog needed for the
  // common case, since the chip itself is that report.
  private async runOracle(): Promise<void> {
    try {
      await this.store.sgt.mutate(["oracle", "run"]);
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    } finally {
      this.store.invalidate();
    }
  }

  // Alt-click on the chip: a human verdict that supersedes the tiers (`sgt oracle override`),
  // for cases the configured tiers can't decide on their own.
  private async overrideOracle(): Promise<void> {
    const status = await vscode.window.showQuickPick(["pass", "fail"], { placeHolder: "Override verdict" });
    if (!status) {
      return;
    }
    const reason = await vscode.window.showInputBox({ prompt: "Reason for the override" });
    if (!reason) {
      return;
    }
    try {
      await this.store.sgt.mutate(["oracle", "override", "--status", status, "--reason", reason]);
      this.store.invalidate();
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  // The plan-mark inspector card's "Confirm match" action: promotes a `sgt checkpoint`
  // footprint-overlap candidate to an actual confirmed match, which is what flips the step to
  // "matched" and lands its rail mark on the next refresh.
  private async confirmCheckpoint(hollowIds: string[], opIds: string[]): Promise<void> {
    const args = ["checkpoint"];
    for (const h of hollowIds) args.push("--confirm-hollow", h);
    for (const o of opIds) args.push("--confirm-op", o);
    try {
      await this.store.sgt.mutate(args);
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    } finally {
      this.store.invalidate();
    }
  }

  // The compact titlebar action group (Save/Commit/Undo): reuse the existing palette commands
  // rather than re-implementing their confirm/mutate/invalidate/report sequence here, same as
  // `apply("revert", ...)` reuses `sgt.revert` above.
  private async dailyLoop(verb: "save" | "commit" | "undo"): Promise<void> {
    if (verb === "save") {
      await vscode.commands.executeCommand("sgt.save");
      return;
    }
    if (verb === "undo") {
      await vscode.commands.executeCommand("sgt.undo");
      return;
    }
    try {
      const result = await this.store.sgt.landCandidate();
      this.store.invalidate();
      vscode.window.showInformationMessage(result.ok ? `✓ committed ${(result.sha || "").slice(0, 12)}` : "Commit failed.");
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  private async apply(verb: string, args: string[]): Promise<void> {
    if (verb === "revert" || verb === "restore") {
      // Reuse the existing `sgt.revert`/`sgt.restore` command (commands.ts) rather than
      // re-implementing its confirm-dialog + mutate + invalidate + report sequence here.
      await vscode.commands.executeCommand(verb === "revert" ? "sgt.revert" : "sgt.restore", args[0]);
      return;
    }
    try {
      const report = await this.store.sgt.mutate(this.cliArgsFor(verb, args));
      this.store.invalidate();
      vscode.window.showInformationMessage(report.trim().split("\n")[0] || "Done.");
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  private cliArgsFor(verb: string, args: string[]): string[] {
    switch (verb) {
      case "merge":
        return ["merge", args[0], args[1]];
      case "rename":
        return ["rename", args[0], args[1]];
      case "move": {
        const target = args[args.length - 1];
        const opIds = args.slice(0, -1);
        return ["move", ...opIds, "--to", target];
      }
      case "split":
        return ["split", args[0], "--apply"];
      default:
        throw new Error(`unknown feature verb ${verb}`);
    }
  }

  // The composition selector (titlebar button): HEAD, or a session by its branch ref. Proposals
  // aren't foldable refs (no checked-out branch), so picking one just opens the existing
  // read-only proposal summary instead of changing the fold target. Uses `createQuickPick` rather
  // than `showQuickPick` so arrowing over an item (`onDidChangeActive`) can live-preview it in the
  // workbench's own code(I) panel before commit -- accepting a real branch actually runs
  // `sgt switch` (the same confirm + mutate sequence as the palette's `sgt.switch`, reused here
  // rather than reopening its own picker); HEAD needs no switch since it's already checked out.
  private async pickComposition(): Promise<void> {
    let compose;
    try {
      compose = await this.store.composeView();
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    const items: (vscode.QuickPickItem & { ref?: string })[] = [
      { label: "HEAD", description: "current composition", ref: "HEAD" },
      ...compose.sessions.sessions.map((s) => ({
        label: s.name,
        description: `${s.branch} · ${s.new_op_count} op(s)`,
        ref: s.branch,
      })),
      ...compose.proposals.map((p) => ({ label: p.title || p.id, description: "proposal (view only)" })),
    ];

    const qp = vscode.window.createQuickPick<vscode.QuickPickItem & { ref?: string }>();
    qp.items = items;
    qp.placeholder = "Switch composition -- hover to preview, select to switch";
    let previewSeq = 0;
    const previewSub = qp.onDidChangeActive((active) => {
      void this.previewComposition(active[0]?.ref, ++previewSeq);
    });
    qp.onDidAccept(() => void this.acceptComposition(qp, compose!.proposals));
    qp.onDidHide(() => {
      previewSub.dispose();
      qp.dispose();
      void this.view?.webview.postMessage({ type: "compositionPreviewEnd" });
    });
    qp.show();
  }

  private async acceptComposition(
    qp: vscode.QuickPick<vscode.QuickPickItem & { ref?: string }>,
    proposals: { id: string; title: string }[]
  ): Promise<void> {
    const pick = qp.selectedItems[0];
    qp.hide();
    if (!pick) {
      return;
    }
    if (!pick.ref) {
      const proposal = proposals.find((p) => (p.title || p.id) === pick.label);
      if (proposal) {
        await vscode.commands.executeCommand("sgt.viewProposal", proposal.id);
      }
      return;
    }
    if (pick.ref === "HEAD") {
      // Already checked out -- nothing to switch, just point the fold target back at it.
      void this.view?.webview.postMessage({ type: "compositionPicked", label: pick.label, ref: pick.ref });
      return;
    }
    const ok = await vscode.window.showWarningMessage(
      `Switch to ${pick.ref}? Mines the current ref first (nothing is lost), then checks out ${pick.ref}.`,
      { modal: true },
      "Switch"
    );
    if (ok !== "Switch") {
      return;
    }
    try {
      const result = await this.store.sgt.switchBranch(pick.ref);
      this.store.invalidate();
      vscode.window.showInformationMessage(
        result.ok ? `✓ switch ${result.branch}: ${result.ops} op(s) in the ideal` : result.error || "switch failed"
      );
      if (result.ok) {
        void this.view?.webview.postMessage({ type: "compositionPicked", label: pick.label, ref: pick.ref });
      }
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  // Hover-preview while the composition QuickPick is open: fold the highlighted composition
  // (unfiltered, same as `scrubPlayhead` -- the webview filters to the selected feature's `dir`
  // client-side) and hand it to the workbench's own code(I) panel, so arrowing through
  // compositions shows what each one would display before committing to a real switch.
  // Sequence-guarded against a stale reply landing after a newer hover, same pattern as
  // `requestFold`/`scrubPlayhead`.
  private async previewComposition(ref: string | undefined, seq: number): Promise<void> {
    if (!ref) {
      void this.view?.webview.postMessage({ type: "compositionPreviewEnd" });
      return;
    }
    void this.view?.webview.postMessage({ type: "compositionPreviewStart", ref, seq });
    try {
      const fold = await this.store.foldAt({ ref });
      void this.view?.webview.postMessage({
        type: "compositionPreviewResult", seq, ref,
        files: fold.files, oracle_verdict: fold.oracle_verdict, forked: fold.forked, error: fold.error,
      });
    } catch (e: any) {
      void this.view?.webview.postMessage({ type: "compositionPreviewResult", seq, ref, error: e.message });
    }
  }

  // One non-interactive fold per feature selection, filtered to that feature's files by
  // prefix-matching `MapNode.dir` -- `fold_view.files` has no feature attribution field, so this
  // reuses the tree's existing `dir` rather than requesting a new API field ahead of need.
  private async requestFold(featureId: string, ref: string, seq: number): Promise<void> {
    let map;
    try {
      map = await this.store.map();
    } catch (e: any) {
      void this.view?.webview.postMessage({ type: "foldResult", seq, featureId, error: e.message });
      return;
    }
    const node = map.nodes.find((n) => n.id === featureId);
    const frontier: FoldFrontier = { ref: ref || "HEAD" };
    try {
      const fold = await this.store.foldAt(frontier);
      const files = node
        ? Object.fromEntries(Object.entries(fold.files || {}).filter(([path]) => path.startsWith(node.dir)))
        : fold.files;
      void this.view?.webview.postMessage({
        type: "foldResult",
        seq,
        featureId,
        files,
        oracle_verdict: fold.oracle_verdict,
        forked: fold.forked,
        error: fold.error,
      });
    } catch (e: any) {
      void this.view?.webview.postMessage({ type: "foldResult", seq, featureId, error: e.message });
    }
  }

  // Phase 4: the draggable playhead. `{commitIndex}` is its own `FoldFrontier` variant --
  // no new API needed, `foldAtSpec` already renders it as the plain digit string `sgt fold --at`
  // expects. Deliberately not `requestFold` (which is keyed to a featureId for cache/dir-prefix
  // filtering): the playhead has no feature selection and folds the whole frontier, letting the
  // webview filter by the selected feature's `dir` client-side so dragging never re-requests.
  private async scrubPlayhead(commitIndex: number, seq: number): Promise<void> {
    try {
      const fold = await this.store.foldAt({ commitIndex });
      void this.view?.webview.postMessage({
        type: "playheadResult",
        seq,
        commitIndex,
        op_count: fold.op_count,
        files: fold.files,
        oracle_verdict: fold.oracle_verdict,
        forked: fold.forked,
        error: fold.error,
      });
    } catch (e: any) {
      void this.view?.webview.postMessage({ type: "playheadResult", seq, commitIndex, error: e.message });
    }
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
  }
}
