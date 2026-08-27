// The Composition Workbench view: a 3-pane view (rail: tree+timeline, inspector: detail/actions/
// code(I)) grounded in experiments/patch_clustering/out/rail3.html. Docked as a `WebviewView` in
// the bottom panel (beside Terminal/Output), matching GitLens's Commit Graph, rather than an
// editor tab. Sourced from one `compose_view` call (map+history+status+forks+drift+sessions+
// proposals+oracle) instead of the old feature-map's separate map()+history() fetches. The
// webview's message protocol (`ready`/`previewVerb`/`applyVerb`/`renamePrompt`, plus
// `pickComposition`/`requestFold`, and the staged-confirm trio `applyStaged`/`revertSequence`/
// `openStagedDiff` whose progress flows back as `applyProgress`) drives media/workbench.js; every
// mutation still goes through the real, unmodified `sgt merge`/`split`/`rename`/`move`/`revert`
// commands -- a preview never writes anything, and a fold never materializes the working tree.
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
import type { IdealEditPhase } from "./commands"; // type-only: keeps the commands↔workbench import cycle out of the emitted JS
import { PreviewProvider } from "./preview";
import { FoldFrontier, StaleRequestError } from "./sgt";
import { Store } from "./store";

/** The composition picker's ref, as a fold frontier.
 *
 * Its first entry is the literal "HEAD" (labelled "current composition"), and `workbench.js` sends
 * that same string as its no-selection default. In this UI "HEAD" means *the present* — which is
 * the current ideal, not the git ref of that name. The two diverge the moment an ideal edit is
 * applied locally: `apply` mints a revert's compensating ops with empty provenance, so
 * `lens.ideal_for_ref` can never select them and a HEAD fold returns the pre-revert tree (measured
 * on bikecount: 111 ops against the current ideal's 113, disagreeing with the working tree on 7 of
 * 16 files). That is why the code(I) panel kept showing the un-reverted code after a revert.
 *
 * A real branch or session ref still folds as a ref, which is what those mean. Shared by
 * `previewComposition` and `requestFold` so the rule cannot drift between the two. */
function compositionFrontier(ref: string | undefined): FoldFrontier {
  return ref && ref !== "HEAD" ? { ref } : { current: true };
}

export class WorkbenchProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  private view: vscode.WebviewView | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  private readonly previewCache = new Map<string, unknown>();
  private pendingReveal: string | null = null;
  // The newest seq per interactive read class. Every `sgt` call is serialized behind the store
  // flock (see Sgt.run), so a cursor sweep used to queue one subprocess per row crossed and run
  // them all; each read now checks this when its queue turn arrives and a superseded one is
  // dropped before it spawns. Sequence numbers already existed for result-ordering -- this reuses
  // them for cancellation.
  private readonly latest = { preview: 0, fold: 0, playhead: 0, find: 0 };

  constructor(
    private readonly context: vscode.ExtensionContext,
    private store: Store,
    private readonly previewTabs: PreviewProvider,
    private readonly root: string,
    // Optional so the workbench keeps working with no panel open -- the render panel is a second
    // consumer of a fold the scrub already performs, never a dependency of it.
    private readonly render?: { show(frontier: FoldFrontier): unknown; isOpen(): boolean }
  ) {
    this.disposables.push(
      store.onDidChange(() => void this.pushState()),
      // Identity colors are theme-aware (color.ts's OKLCH lightness shifts light<->dark), so a
      // theme switch needs the rail's node colors recomputed -- same trigger blame.ts uses.
      vscode.window.onDidChangeActiveColorTheme(() => void this.pushState()),
      this.watchAgentActions()
    );
  }

  /**
   * Paint what an agent is doing, while it does it.
   *
   * The rail already knows how to show the consequence of a revert or a restore
   * before it happens -- that is the hover preview. It just had no way to hear
   * about one it did not start. The MCP server writes the verb and target to
   * `.sgt/local/pending_action.json` around every ideal-edit tool call, so an
   * agent asked to "take the waitlist out" produces the same ghost paint the
   * participant would have got by hovering it themselves.
   *
   * Watching a file rather than holding a socket, because the agent and the
   * editor are separate processes that start and stop independently, and a
   * missed hint is worth nothing while a wedged connection would cost the rail.
   */
  private watchAgentActions(): vscode.Disposable {
    const watcher = vscode.workspace.createFileSystemWatcher("**/.sgt/local/pending_action.json");
    const read = async (uri: vscode.Uri) => {
      try {
        const raw = Buffer.from(await vscode.workspace.fs.readFile(uri)).toString("utf8");
        const action = JSON.parse(raw) as { verb?: string; ref?: string; state?: string; ts?: number };
        if (!action.verb || !action.ref) return;
        // A note left by a previous session is not news. The rail should never
        // open showing a revert somebody's agent ran yesterday.
        if (typeof action.ts === "number" && Date.now() - action.ts > 60_000) return;
        void this.view?.webview.postMessage({ type: "agentAction", ...action });
      } catch {
        // A half-written file: the next event carries the whole thing.
      }
    };
    watcher.onDidCreate(read);
    watcher.onDidChange(read);
    return watcher;
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
    <div class="tb-zone tb-nav">
      <div id="viewSeg" class="view-seg" role="group" aria-label="view">
        <button class="seg-btn" data-view="gantt" title="Feature timeline (Gantt)">▤ Timeline</button>
        <button class="seg-btn" data-view="rail" title="Episode rail (what I did, in order)">◫ Rail</button>
      </div>
      <button id="compositionBtn" class="composition-btn">HEAD</button>
    </div>
    <div class="tb-divider"></div>
    <div class="tb-zone tb-status">
      <button id="oracleChip" class="oracle-chip" data-state="pending" title="oracle — click to run, alt-click to override">
        <span class="oracle-dot"></span><span class="oracle-label">oracle</span>
      </button>
      <button id="plansChip" class="plans-chip" title="plan sessions — click for details">
        <svg class="plans-ring" width="14" height="14" viewBox="-7 -7 14 14"></svg>
        <span class="plans-label">Plans 0/0</span>
      </button>
      <span id="driftChip" class="drift-chip" hidden></span>
    </div>
    <div class="tb-zone tb-find">
      <input id="findBox" class="find-box" type="search" spellcheck="false"
             placeholder="find… e.g. the thing that formats dates"
             title="describe what you are looking for; Enter to search, Esc to clear" />
      <div id="findResults" class="find-results" hidden></div>
    </div>
    <div id="titlebarActions" class="titlebar-actions">
      <button id="inspectorToggle" title="Hide detail panel">◧</button>
      <button id="saveBtn" class="btn-primary" title="sgt save — record and commit">Save</button>
      <button id="undoBtn" title="sgt undo">Undo</button>
    </div>
    <div id="plansPopover" class="plans-popover" hidden></div>
  </div>
  <div id="main">
    <div id="rail"></div>
    <button id="offscreenAbove" class="offscreen-pill offscreen-pill-top" hidden></button>
    <button id="offscreenBelow" class="offscreen-pill offscreen-pill-bottom" hidden></button>
    <div id="previewContext" class="preview-context-pill" hidden></div>
    <div id="previewRefusal" class="preview-refusal-pill" hidden></div>
    <div id="armedBanner" class="armed-banner" hidden></div>
    <div id="confirmBar" class="confirm-bar" hidden></div>
    <div id="inspector"></div>
  </div>
  <div id="presence" title="where you are: composition · view · selection closure · uncommitted work"></div>
</div>
<script nonce="${nonce}" src="${jsUri}"></script>
</body>
</html>`;
  }

  // Reveal a feature in the workbench from the editor (the hover "Open Workbench" link, or a cursor):
  // focus the panel and post a message the webview handles by selecting + spotlighting + scrolling to
  // that feature's lane. The webview stashes a reveal that arrives before its lane exists, so we
  // deliver eagerly here and again on the next "ready" (covers a cold first open of the view).
  async revealFeature(featureId: string): Promise<void> {
    this.pendingReveal = featureId;
    await vscode.commands.executeCommand("sgtWorkbench.focus");
    this.deliverReveal();
  }

  private deliverReveal(): void {
    if (this.pendingReveal != null && this.view) {
      void this.view.webview.postMessage({ type: "revealFeature", featureId: this.pendingReveal });
      this.pendingReveal = null;
    }
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
        await this.pushState();
        this.deliverReveal(); // deliver a reveal requested before the webview first came up
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
      case "applyVerb":
        await this.apply(msg.verb, msg.args);
        return;
      case "applyStaged":
        await this.applyStaged(msg.verb, msg.ref);
        return;
      case "revertSequence":
        await this.revertSequence(msg.refs, msg.label, msg.noun);
        return;
      case "openStagedDiff":
        await this.openStagedDiff(msg.verb, msg.ref);
        return;
      case "openFoldFiles":
        await this.openFoldFiles(msg.commitIndex);
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
      case "find":
        await this.find(msg.query, msg.seq);
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
      case "landCandidate":
        await this.landCandidate();
        return;
      case "abandonCandidate":
        await this.abandonCandidate();
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
      case "resumePlan":
        await vscode.commands.executeCommand("sgt.resumePlan", msg.sessionId);
        return;
      default:
        return;
    }
  }

  private async preview(verb: string, args: string[], seq: number): Promise<void> {
    this.latest.preview = seq;
    // Joined on U+001F (unit separator), not "": a bare join makes ["ab","c"] and ["a","bc"] the same key.
    const key = `${verb}:${args.join("")}`;
    let result = this.previewCache.get(key);
    if (result === undefined) {
      try {
        // Revert/restore of a single selector previews through the emit dry-run (`sgt <verb>
        // <sel> --emit`), NOT `advanced preview`. Two reasons. Emit's selection ladder resolves
        // everything the real verb resolves -- feature, label, symbol, `<f>@<n>` checkpoint --
        // on every deployed CLI (advanced preview's checkpoint branch is newer than some pinned
        // study builds, which answer "feature ... not found" and left the graph paintless). And
        // emit's projection carries the full consequence -- `files`, the `focus` subgraph with
        // per-feature N→M, `so_what` -- where advanced preview's revert branch has none of them,
        // so the confirm bar would honestly-but-wrongly say "no files change". Same projection
        // the apply flow itself runs from, so preview and apply can never disagree.
        result = (verb === "revert" || verb === "restore") && args.length === 1
          ? await this.store.sgt.emit(args[0], verb, () => this.latest.preview === seq)
          : await this.store.sgt.previewVerb(verb, args, () => this.latest.preview === seq);
      } catch (e: any) {
        if (e instanceof StaleRequestError) return; // superseded before it spawned; the newer one answers
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
    this.latest.preview = seq; // shares the preview lane: a newer hover of ANY verb supersedes it
    let result;
    try {
      result = await this.store.sgt.splitPreview(featureId, () => this.latest.preview === seq);
    } catch (e: any) {
      if (e instanceof StaleRequestError) return;
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
      await this.store.sgt.mutate(["advanced", "oracle", "run"]);
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
      await this.store.sgt.mutate(["advanced", "oracle", "override", "--status", status, "--reason", reason]);
      this.store.invalidate();
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  // The plan-mark inspector card's "Confirm match" action: promotes a footprint-overlap candidate
  // to an actual confirmed match, which is what flips the step to "matched" and lands its rail mark
  // on the next refresh. The former `sgt checkpoint --confirm-*` verb folded into `sgt save
  // --resolve-plan` (U12); the confirm-hollow/confirm-op pairs are unchanged.
  private async confirmCheckpoint(hollowIds: string[], opIds: string[]): Promise<void> {
    const args = ["save", "--resolve-plan"];
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

  // Landing is one of a staged candidate's two exits, and it used to be the titlebar's "Commit" --
  // a button that was wrong in both directions at once. `sgt save` mines *and* commits, so a Commit
  // beside Save promised a step Save had already taken; and what it actually ran was `advanced
  // commit`, which lands a staged rewrite candidate and otherwise refuses with "nothing staged". So
  // the daily loop's third button was either redundant or an error toast, never a useful action --
  // drawn permanently for a state that is rare and gated. It now lives in the Working-changes card,
  // which draws it only when a candidate exists and enables it only once the oracle has passed:
  // the gate the refusal used to deliver *after* the click.
  private async landCandidate(): Promise<void> {
    try {
      const result = await this.store.sgt.landCandidate();
      this.store.invalidate();
      vscode.window.showInformationMessage(
        result.ok ? `✓ landed ${(result.sha || "").slice(0, 12)}` : "Landing failed."
      );
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  // The other exit. Abandoning discards a candidate the user built by hand-editing the tree and
  // nothing in sgt can bring it back (`unstage` drops `staged.json` and restores the committed ideal
  // over the top), so it asks first. The detail names what survives rather than what is lost: the
  // ops the rewrite was *of* are still recorded, which is the fact that makes the choice safe.
  private async abandonCandidate(): Promise<void> {
    const ok = await vscode.window.showWarningMessage(
      "Abandon the staged rewrite candidate?",
      {
        modal: true,
        detail: "The hand-edited bytes are discarded and the recorded ideal is restored to the "
          + "working tree. The ops the rewrite was of are untouched.",
      },
      "Abandon"
    );
    if (ok !== "Abandon") {
      return;
    }
    try {
      const result = await this.store.sgt.abandonCandidate();
      this.store.invalidate();
      vscode.window.showInformationMessage(
        result.ok
          ? `✓ abandoned; restored ${(result.op_ids || []).length} recorded op(s) to the tree`
          : "Abandon failed."
      );
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  // The compact titlebar action group (Save/Undo): reuse the existing palette commands rather than
  // re-implementing their confirm/mutate/invalidate/report sequence here, same as
  // `apply("revert", ...)` reuses `sgt.revert` above.
  // The webview goes inert while one of these runs (loopButtonState in workbench.js). It sets that
  // state on the click and clears it on the `state` push the mutation triggers; this end signal is
  // what clears it on the paths that never reach a mutation -- a cancelled dialog, "nothing to
  // save" -- where no fresh composition is ever pushed.
  private async dailyLoop(verb: "save" | "undo"): Promise<void> {
    try {
      await vscode.commands.executeCommand(verb === "save" ? "sgt.save" : "sgt.undo");
    } finally {
      void this.view?.webview.postMessage({ type: "loopBusy", verb: null });
    }
  }

  // A staged apply from the webview's in-graph confirm bar: the consequence was already painted
  // on the graph and the user clicked Apply there, so this drives the same `sgt.revert`/
  // `sgt.restore` flow with `confirmed` (skip the modal that would ask the same question twice),
  // `openDiff: false` (the bar offers the diff on demand instead of two tabs stealing focus), and
  // a phase callback the confirm bar renders as live progress -- checking → rewriting →
  // rebuilding, not a dead gap between click and toast.
  private async applyStaged(verb: string, ref: string): Promise<void> {
    if (!ref) return;
    if (verb === "split") {
      // Split is metadata-only (no working-tree rewrite), so its staged apply is the direct
      // `--apply` call with the same phase reporting -- there is no emit/frontier flow to reuse.
      this.postPhase(verb, ref, "applying");
      try {
        const r = await this.store.sgt.splitApply(ref);
        if (!r.ok) throw new Error(r.message || "split failed");
        this.postPhase(verb, ref, "refreshing");
        this.store.invalidate();
        vscode.window.showInformationMessage(
          `Split ${r.feature ?? ref} into a new feature ${r.new_feature ?? ""}.`.trim());
        this.postPhase(verb, ref, "done");
      } catch (e: any) {
        this.postPhase(verb, ref, "failed", e.message);
        vscode.window.showErrorMessage(e.message);
      }
      return;
    }
    if (verb !== "revert" && verb !== "restore") return;
    await vscode.commands.executeCommand(verb === "revert" ? "sgt.revert" : "sgt.restore", ref, {
      confirmed: true,
      openDiff: false,
      onPhase: (phase: IdealEditPhase, detail?: string) => this.postPhase(verb, ref, phase, detail),
    });
  }

  private postPhase(verb: string, ref: string, phase: IdealEditPhase, detail?: string): void {
    void this.view?.webview.postMessage({ type: "applyProgress", verb, ref, phase, detail });
  }

  // "Back to here" (staged in the webview): rewind a feature to one of its checkpoints by
  // reverting every LATER chapter, newest-first (`refs` arrive in apply order). Sequential like
  // `revertSelection` -- each `sgt revert` re-resolves against current state -- and STOPS on the
  // first refusal rather than pressing on into an inconsistent partial. The staged confirm bar
  // already carried the consequence; the per-ref phase reports are the intermediate feedback a
  // multi-commit rewind owes the person watching it.
  private async revertSequence(refs: string[], label?: string, noun = "chapter"): Promise<void> {
    if (!refs?.length) return;
    const post = (phase: IdealEditPhase, detail?: string) =>
      this.postPhase("revert", refs[0], phase, detail);
    let done = 0;
    post("applying", refs.length > 1 ? `${noun} 1 of ${refs.length}…` : undefined);
    for (const ref of refs) {
      try {
        await this.store.sgt.mutate(["revert", ref]);
        done++;
        if (done < refs.length) post("applying", `${noun} ${done + 1} of ${refs.length}…`);
      } catch (e: any) {
        this.store.invalidate();
        const msg = `Reverted ${done}/${refs.length} ${noun}(s); stopped at ${ref}: ${e.message}`;
        post("failed", msg);
        vscode.window.showWarningMessage(msg);
        return;
      }
    }
    post("refreshing");
    this.store.invalidate();
    const doneMsg = label
      ? `reverted to "${label}" — ${done} later ${noun}(s) removed`
      : `reverted ${done} ${noun}(s)`;
    post("done", doneMsg);
    vscode.window.showInformationMessage(`✓ ${doneMsg}`);
  }

  // The staged confirm bar's "Open diff": the PREVIEW tabs on demand rather than automatically on
  // every apply. Same emit projection the apply itself runs from, so the tabs can never disagree
  // with the consequence the bar states.
  private async openStagedDiff(verb: string, ref: string): Promise<void> {
    try {
      const view = await this.store.sgt.emit(ref, verb === "restore" ? "restore" : "revert");
      if (!view.ok) {
        vscode.window.showWarningMessage(view.message || `Cannot preview ${verb} of ${ref}.`);
        return;
      }
      await this.previewTabs.openDiff(view);
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  // The playhead's "visit this version": pick files from the scrubbed frontier's fold and open
  // each as a read-only now ⇄ then diff in a real editor -- the codebase at that point, not a
  // snippet in the inspector. The fold is served from store.foldAt's LRU (the scrub already
  // fetched it), so the picker opens instantly. Never materializes the working tree.
  private async openFoldFiles(commitIndex: number): Promise<void> {
    let fold;
    try {
      fold = await this.store.foldAt({ commitIndex });
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
      return;
    }
    const files = fold.files || {};
    const paths = Object.keys(files).sort();
    if (!paths.length) {
      vscode.window.showInformationMessage(`No files at commit ${commitIndex}.`);
      return;
    }
    const picks = await vscode.window.showQuickPick(
      paths.map((p) => ({ label: p })),
      {
        canPickMany: true,
        placeHolder: `Files as of c${commitIndex} — opens read-only; your working tree is untouched`,
      }
    );
    if (!picks?.length) return;
    for (const pick of picks) {
      await this.previewTabs.openFrontierFile(this.root, pick.label, files[pick.label], `c${commitIndex}`);
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
      const summary = await this.runFeatureVerb(verb, args);
      this.store.invalidate();
      vscode.window.showInformationMessage(summary);
    } catch (e: any) {
      vscode.window.showErrorMessage(e.message);
    }
  }

  // Dispatches to the typed `Sgt` methods, which spell the verbs at their real CLI paths
  // (`feature regroup merge`, `feature rename`, ...). This used to build bare `sgt merge` /
  // `sgt rename` / `sgt move` / `sgt split` argv inline. Those spellings were re-homed under
  // `feature` (KTD2) and are not top-level verbs any more, so the CLI answered them by printing
  // its help text and exiting 0 -- the action bar reported success and changed nothing. Going
  // through the typed methods means there is exactly one place each verb's path is written.
  private async runFeatureVerb(verb: string, args: string[]): Promise<string> {
    switch (verb) {
      case "merge": {
        const r = await this.store.sgt.merge(args[0], args[1]);
        if (!r.ok) throw new Error(r.message || "merge failed");
        return `Merged ${r.op_count ?? 0} op(s) into ${r.survivor ?? args[0]}.`;
      }
      case "rename": {
        const r = await this.store.sgt.rename(args[0], args[1]);
        if (!r.ok) throw new Error(r.message || "rename failed");
        return `Renamed to ${r.new_label ?? args[1]}.`;
      }
      case "move": {
        const target = args[args.length - 1];
        const opIds = args.slice(0, -1);
        const r = await this.store.sgt.move(opIds, target);
        if (!r.ok) throw new Error(r.message || "move failed");
        return `Moved ${r.op_ids?.length ?? opIds.length} op(s) to ${r.target ?? target}.`;
      }
      case "split": {
        const r = await this.store.sgt.splitApply(args[0]);
        if (!r.ok) throw new Error(r.message || "split failed");
        return `Split ${r.feature ?? args[0]} into a new feature ${r.new_feature ?? ""}.`.trim();
      }
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
      const fold = await this.store.foldAt(compositionFrontier(ref));
      void this.view?.webview.postMessage({
        type: "compositionPreviewResult", seq, ref,
        files: fold.files, oracle_verdict: fold.oracle_verdict, forked: fold.forked, error: fold.error,
      });
    } catch (e: any) {
      void this.view?.webview.postMessage({ type: "compositionPreviewResult", seq, ref, error: e.message });
    }
  }

  // One non-interactive fold per feature selection, filtered to that feature's files. Filtering by
  // the majority-prefix `dir` alone drops the feature's own production files when its members span
  // dirs (e.g. a leaf labeled by its test dir but owning `livehub/conflict.py`), leaving an empty
  // panel; so union the member file-set (`MapNode.members`, `file::qualname`) with the dir prefix.
  private async requestFold(featureId: string, ref: string, seq: number): Promise<void> {
    this.latest.fold = seq;
    let map;
    try {
      map = await this.store.map();
    } catch (e: any) {
      void this.view?.webview.postMessage({ type: "foldResult", seq, featureId, error: e.message });
      return;
    }
    const node = map.nodes.find((n) => n.id === featureId);
    try {
      const fold = await this.store.foldAt(compositionFrontier(ref), () => this.latest.fold === seq);
      const memberFiles = new Set((node?.members || []).map((m) => m.split("::")[0]));
      const files = node
        ? Object.fromEntries(Object.entries(fold.files || {}).filter(
            ([path]) => memberFiles.has(path) || path.startsWith(node.dir)))
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
      if (e instanceof StaleRequestError) return; // a newer selection's fold answers instead
      void this.view?.webview.postMessage({ type: "foldResult", seq, featureId, error: e.message });
    }
  }

  /**
   * `sgt find`, from the box in the title bar.
   *
   * Report-only, and slow enough to want a sequence number: the semantic rung
   * embeds the query, so a fast typist can have three of these in flight and
   * the second one must not paint over the third.
   */
  private async find(query: string, seq: number): Promise<void> {
    this.latest.find = seq;
    try {
      const view = await this.store.sgt.find(String(query || ""), () => this.latest.find === seq);
      void this.view?.webview.postMessage({ type: "findResult", seq, ...view });
    } catch (e: any) {
      if (e instanceof StaleRequestError) return; // retyped before this ran; the newer query answers
      void this.view?.webview.postMessage({ type: "findResult", seq, ok: false, hits: [], message: e.message });
    }
  }

  // Phase 4: the draggable playhead. `{commitIndex}` is its own `FoldFrontier` variant --
  // no new API needed, `foldAtSpec` already renders it as the plain digit string `sgt fold --at`
  // expects. Deliberately not `requestFold` (which is keyed to a featureId for cache/dir-prefix
  // filtering): the playhead has no feature selection and folds the whole frontier, letting the
  // webview filter by the selected feature's `dir` client-side so dragging never re-requests.
  private async scrubPlayhead(commitIndex: number, seq: number): Promise<void> {
    this.latest.playhead = seq;
    // Phase 5: if the render panel is open, the same frontier drives the running app. Fired
    // before the read so the panel starts its own (debounced) fold in parallel rather than after
    // this one returns -- the panel's fold writes to disk and is the slower of the two.
    if (this.render?.isOpen()) void this.render.show({ commitIndex });
    try {
      const fold = await this.store.foldAt({ commitIndex }, () => this.latest.playhead === seq);
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
      if (e instanceof StaleRequestError) return; // the scrub moved on; the newer frontier answers
      void this.view?.webview.postMessage({ type: "playheadResult", seq, commitIndex, error: e.message });
    }
  }

  dispose(): void {
    this.disposables.forEach((d) => d.dispose());
  }
}
