// Shared state: one sgt client, a cached feature map, and a per-file blame cache. Everything
// reactive (decorations, the tree) listens to `onDidChange` and re-reads from here, so a single
// refresh after a checkpoint/feature-verb op updates every surface at once.

import * as vscode from "vscode";
import { Sgt } from "./sgt";
import { BlameView, DriftView, MapNode, MapView, PlanView, StatusView } from "./types";

export class Store {
  readonly sgt: Sgt;
  private mapCache: MapView | undefined;
  private statusCache: StatusView | undefined;
  private nodeById = new Map<string, MapNode>();
  private blameCache = new Map<string, BlameView>();
  private planCache: PlanView | undefined;
  private driftCache: DriftView | undefined;
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

  /** Drop all caches and notify every surface to re-read. Call after any mutation. */
  invalidate(): void {
    this.mapCache = undefined;
    this.statusCache = undefined;
    this.blameCache.clear();
    this.planCache = undefined;
    this.driftCache = undefined;
    this._onDidChange.fire();
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
