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
  private proposalCache = new Map<string, ProposalView>();
  private foldCache = new Map<string, FoldView>(); // bounded LRU of recent frontier folds (see foldAt)
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
      this.mapInFlight = this.sgt
        .map()
        .then((v) => {
          this.mapCache = v;
          this.nodeById = new Map(v.nodes.map((n) => [n.id, n]));
          return v;
        })
        .finally(() => {
          this.mapInFlight = undefined;
        });
    }
    return this.mapInFlight;
  }

  // Cached alongside `map()` -- both are invalidated together off the same `.sgt/**/*.json`
  // watcher (a feature verb or a new mined commit changes both the tree and the op DAG). Shares
  // the in-flight promise like `map`/`composeView`.
  async history(force = false): Promise<HistoryView> {
    if (this.historyCache && !force) return this.historyCache;
    if (!this.historyInFlight || force) {
      this.historyInFlight = this.sgt
        .history()
        .then((v) => {
          this.historyCache = v;
          return v;
        })
        .finally(() => {
          this.historyInFlight = undefined;
        });
    }
    return this.historyInFlight;
  }

  // The canonical lane×commit cell join (`grid_view`, plan U3), cached alongside `map`/`history`
  // and invalidated with them -- the workbench's timeline/rail layouts render from this rather than
  // re-deriving the (op -> cell) join client-side. Shares the in-flight promise like `map`.
  async gridView(force = false): Promise<GridView> {
    if (this.gridCache && !force) return this.gridCache;
    if (!this.gridInFlight || force) {
      this.gridInFlight = this.sgt
        .grid()
        .then((v) => {
          this.gridCache = v;
          return v;
        })
        .finally(() => {
          this.gridInFlight = undefined;
        });
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
      this.statusInFlight = this.sgt
        .status()
        .then((v) => {
          this.statusCache = v;
          return v;
        })
        .finally(() => {
          this.statusInFlight = undefined;
        });
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

  // A small bounded LRU keyed by the `sgt fold --at` spec: the scrubber replays the same handful
  // of frontiers as the playhead moves back and forth, so a cache spares a fresh `sgt advanced
  // fold` per revisited frame. Capped so a long scrub can't grow it without bound (an eviction
  // per insert past the cap), and cleared in `invalidate()` with the other caches. Signature and
  // promise semantics are unchanged.
  async foldAt(frontier: FoldFrontier): Promise<FoldView> {
    const key = foldAtSpec(frontier);
    const cached = this.foldCache.get(key);
    if (cached) {
      this.foldCache.delete(key); // re-insert to mark most-recently-used (Map keeps insertion order)
      this.foldCache.set(key, cached);
      return cached;
    }
    const view = await this.sgt.foldAt(frontier);
    this.foldCache.set(key, view);
    if (this.foldCache.size > 32) {
      const oldest = this.foldCache.keys().next().value; // least-recently-used
      if (oldest !== undefined) this.foldCache.delete(oldest);
    }
    return view;
  }

  /** Drop all caches and notify every surface to re-read. Call after any mutation. */
  invalidate(): void {
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
    this.proposalCache.clear();
    this.foldCache.clear();
    this._onDidChange.fire();
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
