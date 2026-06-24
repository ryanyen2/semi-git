// Shared state: one sgt client, a cached graph, and a per-file blame cache. Everything reactive
// (decorations, CodeLens, tree, webview) listens to `onDidChange` and re-reads from here, so a
// single refresh after a checkpoint/graph op updates every surface at once.

import * as vscode from "vscode";
import { Sgt } from "./sgt";
import { BlameView, DecisionGraphView, GraphView, NodeView, StatusView } from "./types";

export class Store {
  readonly sgt: Sgt;
  private graphCache: GraphView | undefined;
  private statusCache: StatusView | undefined;
  private decisionsCache: DecisionGraphView | undefined;
  private nodeById = new Map<string, NodeView>();
  private blameCache = new Map<string, BlameView>();
  private _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChange = this._onDidChange.event;

  constructor(repoRoot: string, out: vscode.OutputChannel) {
    this.sgt = new Sgt(repoRoot, out);
  }

  async graph(force = false): Promise<GraphView> {
    if (!this.graphCache || force) {
      this.graphCache = await this.sgt.export();
      this.nodeById = new Map(this.graphCache.nodes.map((n) => [n.id, n]));
    }
    return this.graphCache;
  }

  async decisions(force = false): Promise<DecisionGraphView> {
    if (!this.decisionsCache || force) {
      this.decisionsCache = await this.sgt.decisions();
    }
    return this.decisionsCache;
  }

  node(id: string): NodeView | undefined {
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

  /** Drop all caches and notify every surface to re-read. Call after any mutation. */
  invalidate(): void {
    this.graphCache = undefined;
    this.statusCache = undefined;
    this.decisionsCache = undefined;
    this.blameCache.clear();
    this._onDidChange.fire();
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}
