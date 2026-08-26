// Shared state: one sgt client, a cached feature map, and a per-file blame cache. Everything
// reactive (decorations, the tree) listens to `onDidChange` and re-reads from here, so a single
// refresh after a checkpoint/feature-verb op updates every surface at once.

import * as vscode from "vscode";
import { FoldFrontier, foldAtSpec, Sgt } from "./sgt";
import {
  BlameView,
  ComposeView,
  DriftView,
  FoldView,
  ForksView,
  GridView,
  HistoryView,
  IntentView,
  MapNode,
  MapView,
  NowView,
  PlanView,
  ProposalView,
  SavePreviewView,
  SessionsView,
  StatusView,
} from "./types";

export class Store {
  readonly sgt: Sgt;
  private mapCache: MapView | undefined;
  private mapInFlight: Promise<MapView> | undefined;
  private historyCache: HistoryView | undefined;
  private historyInFlight: Promise<HistoryView> | undefined;
  private gridCache: GridView | undefined;
  private gridInFlight: Promise<GridView> | undefined;
  private statusCache: StatusView | undefined;
  private statusInFlight: Promise<StatusView> | undefined;
  private nodeById = new Map<string, MapNode>();
  private blameCache = new Map<string, BlameView>();
  private planCache: PlanView | undefined;
  private driftCache: DriftView | undefined;
  private composeCache: ComposeView | undefined;
  private composeInFlight: Promise<ComposeView> | undefined;
  private forksCache: ForksView | undefined;
  private forksInFlight: Promise<ForksView> | undefined;
  private sessionsCache: SessionsView | undefined;
  private intentCache: IntentView | undefined;
  private intentInFlight: Promise<IntentView> | undefined;
  private nowCache: NowView | undefined;
  private nowInFlight: Promise<NowView> | undefined;
  private proposalCache = new Map<string, ProposalView>();
  private foldCache = new Map<string, FoldView>(); // bounded LRU of recent frontier folds (see foldAt)
  // Bumped once per invalidate(). Every coalesced fetch captures it before shelling out and
  // commits to its cache only if it still matches on resolution -- so a read already in flight
  // when an external `.sgt/**/*.json` mutation fires the watcher (another terminal, an agent)
  // can't settle *after* invalidate() cleared the caches and overwrite them with pre-mutation
  // data. Without this the surface would show stale state until an unrelated event triggered
  // another read (the "webview doesn't match the CLI until reload" failure).
  private generation = 0;
  private _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChange = this._onDidChange.event;

  constructor(repoRoot: string, out: vscode.OutputChannel) {
    this.sgt = new Sgt(repoRoot, out);
  }

  // Shares the in-flight promise like `composeView` -- activation fires several map consumers at
  // once, so without this each misses the (still-unset) cache and spawns its own `sgt log --tree`.
  // A forced call starts a fresh fetch rather than joining a possibly-stale in-flight one.
  async map(force = false): Promise<MapView> {
    if (this.mapCache && !force) return this.mapCache;
    if (!this.mapInFlight || force) {
      const gen = this.generation;
      const p: Promise<MapView> = this.sgt
        .map()
        .then((v) => {
          if (gen === this.generation) {
            this.mapCache = v;
            this.nodeById = new Map(v.nodes.map((n) => [n.id, n]));
          }
          return v;
        })
        .finally(() => {
          if (this.mapInFlight === p) this.mapInFlight = undefined; // never clear a newer slot
        });
      this.mapInFlight = p;
    }
    return this.mapInFlight;
  }

  // Cached alongside `map()` -- both are invalidated together off the same `.sgt/**/*.json`
  // watcher (a feature verb or a new mined commit changes both the tree and the op DAG). Shares
  // the in-flight promise like `map`/`composeView`.
  async history(force = false): Promise<HistoryView> {
    if (this.historyCache && !force) return this.historyCache;
    if (!this.historyInFlight || force) {
      const gen = this.generation;
      const p: Promise<HistoryView> = this.sgt
        .history()
        .then((v) => {
          if (gen === this.generation) this.historyCache = v;
          return v;
        })
        .finally(() => {
          if (this.historyInFlight === p) this.historyInFlight = undefined;
        });
      this.historyInFlight = p;
    }
    return this.historyInFlight;
  }

  // The canonical lane×commit cell join (`grid_view`, plan U3), cached alongside `map`/`history`
  // and invalidated with them -- the workbench's timeline/rail layouts render from this rather than
  // re-deriving the (op -> cell) join client-side. Shares the in-flight promise like `map`.
  async gridView(force = false): Promise<GridView> {
    if (this.gridCache && !force) return this.gridCache;
    if (!this.gridInFlight || force) {
      const gen = this.generation;
      const p: Promise<GridView> = this.sgt
        .grid()
        .then((v) => {
          if (gen === this.generation) this.gridCache = v;
          return v;
        })
        .finally(() => {
          if (this.gridInFlight === p) this.gridInFlight = undefined;
        });
      this.gridInFlight = p;
    }
    return this.gridInFlight;
  }

  node(id: string): MapNode | undefined {
    return this.nodeById.get(id);
  }

  // Shares the in-flight promise like `map` -- the status bar and the tree both call this at
  // activation.
  async status(force = false): Promise<StatusView> {
    if (this.statusCache && !force) return this.statusCache;
    if (!this.statusInFlight || force) {
      const gen = this.generation;
      const p: Promise<StatusView> = this.sgt
        .status()
        .then((v) => {
          if (gen === this.generation) this.statusCache = v;
          return v;
        })
        .finally(() => {
          if (this.statusInFlight === p) this.statusInFlight = undefined;
        });
      this.statusInFlight = p;
    }
    return this.statusInFlight;
  }

  async blame(file: string, force = false): Promise<BlameView> {
    const cached = this.blameCache.get(file);
    if (cached && !force) {
      return cached;
    }
    const view = await this.sgt.blame(file);
    this.blameCache.set(file, view);
    return view;
  }

  async planView(force = false): Promise<PlanView> {
    if (!this.planCache || force) {
      this.planCache = await this.sgt.planStatus();
    }
    return this.planCache;
  }

  // Drift is no longer a standalone CLI verb (folded into `save`, plan U12); its per-op span
  // projection is one child of the `compose_view` aggregate this store already fetches, so source
  // it from there rather than a dead `sgt drift` shell-out.
  async driftView(force = false): Promise<DriftView> {
    if (!this.driftCache || force) {
      this.driftCache = (await this.composeView(force)).drift;
    }
    return this.driftCache;
  }

  // The in-situ save preview -- which features would gain ops on the next `sgt save`. Like
  // `driftView`, it's one child of the `compose_view` aggregate the store already fetches, so
  // source it from there rather than a separate shell-out.
  async savePreview(force = false): Promise<SavePreviewView> {
    return (await this.composeView(force)).save_preview;
  }

  // The workbench's primary poll -- one aggregate refresh instead of ~9 separate shell-outs.
  // Activation fires this from 3 tree/webview consumers at once; share the in-flight promise so
  // they collapse into a single `sgt advanced compose` invocation instead of each missing the
  // (still-unset) cache and firing its own.
  async composeView(force = false): Promise<ComposeView> {
    if (this.composeCache && !force) return this.composeCache;
    if (!this.composeInFlight || force) {
      const gen = this.generation;
      const p: Promise<ComposeView> = this.sgt
        .compose()
        .then((v) => {
          if (gen === this.generation) this.composeCache = v;
          return v;
        })
        .finally(() => {
          if (this.composeInFlight === p) this.composeInFlight = undefined;
        });
      this.composeInFlight = p;
    }
    return this.composeInFlight;
  }

  // Same in-flight race as `composeView` -- the badge and the tree view both call this at
  // activation.
  async forksView(force = false): Promise<ForksView> {
    if (this.forksCache && !force) return this.forksCache;
    if (!this.forksInFlight || force) {
      const gen = this.generation;
      const p: Promise<ForksView> = this.sgt
        .forksView()
        .then((v) => {
          if (gen === this.generation) this.forksCache = v;
          return v;
        })
        .finally(() => {
          if (this.forksInFlight === p) this.forksInFlight = undefined;
        });
      this.forksInFlight = p;
    }
    return this.forksInFlight;
  }

  // The intent overlay (`sgt.api.intent_view`), cached like `map`/`history` and invalidated with
  // them off the same `.sgt/**/*.json` watcher. The hover reads it to surface a feature's live
  // intent-ledger rationale; sharing the in-flight promise keeps repeated hovers from each firing
  // their own `sgt intent list`.
  async intentView(force = false): Promise<IntentView> {
    if (this.intentCache && !force) return this.intentCache;
    if (!this.intentInFlight || force) {
      const gen = this.generation;
      const p: Promise<IntentView> = this.sgt
        .intentView()
        .then((v) => {
          if (gen === this.generation) this.intentCache = v;
          return v;
        })
        .finally(() => {
          if (this.intentInFlight === p) this.intentInFlight = undefined;
        });
      this.intentInFlight = p;
    }
    return this.intentInFlight;
  }

  // The state-of-actions view (`sgt.api.now_view`), cached and invalidated off the same
  // `.sgt/**/*.json` watcher as the rest -- the activity feed and save preview both write under
  // `.sgt/`, so an agent edit (via the PostToolUse hook) or a save refreshes the Now tree live.
  // Shares the in-flight promise so the several tree sections don't each fire their own `sgt now`.
  async nowView(force = false): Promise<NowView> {
    if (this.nowCache && !force) return this.nowCache;
    if (!this.nowInFlight || force) {
      const gen = this.generation;
      const p: Promise<NowView> = this.sgt
        .nowView()
        .then((v) => {
          if (gen === this.generation) this.nowCache = v;
          return v;
        })
        .finally(() => {
          if (this.nowInFlight === p) this.nowInFlight = undefined;
        });
      this.nowInFlight = p;
    }
    return this.nowInFlight;
  }

  async sessionsView(force = false): Promise<SessionsView> {
    if (!this.sessionsCache || force) {
      this.sessionsCache = await this.sgt.sessionsView();
    }
    return this.sessionsCache;
  }

  async proposalView(id: string, force = false): Promise<ProposalView> {
    const cached = this.proposalCache.get(id);
    if (cached && !force) {
      return cached;
    }
    const view = await this.sgt.proposalView(id);
    this.proposalCache.set(id, view);
    return view;
  }

  // A small bounded LRU keyed by the `sgt fold --at` spec: the scrubber replays the same handful
  // of frontiers as the playhead moves back and forth, so a cache spares a fresh `sgt advanced
  // fold` per revisited frame. Capped so a long scrub can't grow it without bound (an eviction
  // per insert past the cap), and cleared in `invalidate()` with the other caches.
  // `stillWanted` (optional) lets an interactive caller -- the scrub playhead, a selection fold --
  // drop a queued fold that a newer one superseded before it ever spawns (see Sgt.run). A cache
  // hit ignores it: already-computed data costs nothing to hand back.
  async foldAt(frontier: FoldFrontier, stillWanted?: () => boolean): Promise<FoldView> {
    // A `ref` frontier ("HEAD", a branch) is a *moving* target: its spec string is stable but the
    // content it resolves to changes as the ref advances, so caching it by spec would serve the
    // pre-advance fold after a checkpoint. Only `commitIndex`/`opIds` frontiers name fixed content
    // and are safe to memoize. Ref folds always shell out.
    if ("ref" in frontier) return this.sgt.foldAt(frontier, stillWanted);
    const key = foldAtSpec(frontier);
    const cached = this.foldCache.get(key);
    if (cached) {
      this.foldCache.delete(key); // re-insert to mark most-recently-used (Map keeps insertion order)
      this.foldCache.set(key, cached);
      return cached;
    }
    const gen = this.generation;
    const view = await this.sgt.foldAt(frontier, stillWanted);
    if (gen !== this.generation) return view; // an invalidate() raced this fetch -- don't repopulate
    this.foldCache.set(key, view);
    if (this.foldCache.size > 32) {
      const oldest = this.foldCache.keys().next().value; // least-recently-used
      if (oldest !== undefined) this.foldCache.delete(oldest);
    }
    return view;
  }

  /** Drop all caches and notify every surface to re-read. Call after any mutation. */
  invalidate(): void {
    this.generation++; // any fetch begun before now must not repopulate a cache it's about to clear
    this.mapCache = undefined;
    this.mapInFlight = undefined;
    this.historyCache = undefined;
    this.historyInFlight = undefined;
    this.gridCache = undefined;
    this.gridInFlight = undefined;
    this.statusCache = undefined;
    this.statusInFlight = undefined;
    this.blameCache.clear();
    this.planCache = undefined;
    this.driftCache = undefined;
    this.composeCache = undefined;
    this.composeInFlight = undefined;
    this.forksCache = undefined;
    this.forksInFlight = undefined;
    this.sessionsCache = undefined;
    this.intentCache = undefined;
    this.intentInFlight = undefined;
    this.nowCache = undefined;
    this.nowInFlight = undefined;
    this.proposalCache.clear();
    this.foldCache.clear();
    this._onDidChange.fire();
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
