// Shared state: one sgt client, a cached feature map, and a per-file blame cache. Everything
// reactive (decorations, the tree) listens to `onDidChange` and re-reads from here, so a single
// refresh after a checkpoint/feature-verb op updates every surface at once.

import * as vscode from "vscode";
import { FoldFrontier, Sgt } from "./sgt";
import {
  BlameView,
  ComposeView,
  DriftView,
  ForksView,
  HistoryView,
  MapNode,
  MapView,
  PlanView,
  ProposalView,
  SessionsView,
  StatusView,
} from "./types";

export class Store {
  readonly sgt: Sgt;
  private mapCache: MapView | undefined;
  private historyCache: HistoryView | undefined;
  private statusCache: StatusView | undefined;
  private nodeById = new Map<string, MapNode>();
  private blameCache = new Map<string, BlameView>();
  private planCache: PlanView | undefined;
  private driftCache: DriftView | undefined;
  private composeCache: ComposeView | undefined;
  private composeInFlight: Promise<ComposeView> | undefined;
  private forksCache: ForksView | undefined;
  private forksInFlight: Promise<ForksView> | undefined;
  private sessionsCache: SessionsView | undefined;
  private proposalCache = new Map<string, ProposalView>();
  private _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChange = this._onDidChange.event;

  constructor(repoRoot: string, out: vscode.OutputChannel) {
    this.sgt = new Sgt(repoRoot, out);
  }

  async map(force = false): Promise<MapView> {
    if (!this.mapCache || force) {
      this.mapCache = await this.sgt.map();
      this.nodeById = new Map(this.mapCache.nodes.map((n) => [n.id, n]));
    }
    return this.mapCache;
  }

  // Cached alongside `map()` -- both are invalidated together off the same `.sgt/**/*.json`
  // watcher (a feature verb or a new mined commit changes both the tree and the op DAG).
  async history(force = false): Promise<HistoryView> {
    if (!this.historyCache || force) {
      this.historyCache = await this.sgt.history();
    }
    return this.historyCache;
  }

  node(id: string): MapNode | undefined {
    return this.nodeById.get(id);
  }

  async status(force = false): Promise<StatusView> {
    if (!this.statusCache || force) {
      this.statusCache = await this.sgt.status();
    }
    return this.statusCache;
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

  async driftView(force = false): Promise<DriftView> {
    if (!this.driftCache || force) {
      this.driftCache = await this.sgt.drift();
    }
    return this.driftCache;
  }

  // The workbench's primary poll -- one aggregate refresh instead of ~9 separate shell-outs.
  // Activation fires this from 3 tree/webview consumers at once; share the in-flight promise so
  // they collapse into a single `sgt advanced compose` invocation instead of each missing the
  // (still-unset) cache and firing its own.
  async composeView(force = false): Promise<ComposeView> {
    if (this.composeCache && !force) return this.composeCache;
    if (!this.composeInFlight || force) {
      this.composeInFlight = this.sgt
        .compose()
        .then((v) => {
          this.composeCache = v;
          return v;
        })
        .finally(() => {
          this.composeInFlight = undefined;
        });
    }
    return this.composeInFlight;
  }

  // Same in-flight race as `composeView` -- the badge and the tree view both call this at
  // activation.
  async forksView(force = false): Promise<ForksView> {
    if (this.forksCache && !force) return this.forksCache;
    if (!this.forksInFlight || force) {
      this.forksInFlight = this.sgt
        .forksView()
        .then((v) => {
          this.forksCache = v;
          return v;
        })
        .finally(() => {
          this.forksInFlight = undefined;
        });
    }
    return this.forksInFlight;
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

  // Never cached: each call is a live playhead position, and the host debounces drag events
  // (250ms) before issuing one -- caching by frontier would just grow unboundedly.
  foldAt(frontier: FoldFrontier) {
    return this.sgt.foldAt(frontier);
  }

  /** Drop all caches and notify every surface to re-read. Call after any mutation. */
  invalidate(): void {
    this.mapCache = undefined;
    this.historyCache = undefined;
    this.statusCache = undefined;
    this.blameCache.clear();
    this.planCache = undefined;
    this.driftCache = undefined;
    this.composeCache = undefined;
    this.composeInFlight = undefined;
    this.forksCache = undefined;
    this.forksInFlight = undefined;
    this.sessionsCache = undefined;
    this.proposalCache.clear();
    this._onDidChange.fire();
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
