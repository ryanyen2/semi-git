// The state every surface reads: `ugh log` and `ugh status`, plus `ugh show <path>` per open file.
// Refreshes coalesce: a refresh asked for while one runs makes one more pass after it, never a
// queue of them, and every caller resolves once the newest state is in. The per-file reads change
// only with the history (saves, names, what is out, the choices), so they are kept until it does.
//
// No `vscode` import, so `store.test.ts` can drive it under `node --test`.

import type { LogView, ShowView, Snapshot, StatusView } from "./types.ts";

export interface StoreSource {
  initialized(): Promise<boolean>;
  log(): Promise<LogView>;
  status(): Promise<StatusView>;
  show(path: string): Promise<ShowView>;
}

export class Store {
  snapshot: Snapshot = { log: null, status: null };
  private source: StoreSource;
  private listeners = new Set<(s: Snapshot) => void>();
  private running: Promise<void> | undefined;
  private again = false;
  private shows = new Map<string, Promise<ShowView>>();
  private history = "";

  constructor(source: StoreSource) {
    this.source = source;
  }

  onChange(fn: (s: Snapshot) => void): { dispose(): void } {
    this.listeners.add(fn);
    return { dispose: () => this.listeners.delete(fn) };
  }

  refresh(): Promise<void> {
    if (this.running) {
      this.again = true;
      return this.running;
    }
    const run = this.loop().finally(() => {
      this.running = undefined;
    });
    this.running = run;
    return run;
  }

  /** `ugh show <path>`, cached until the history changes. A failed read is not cached. */
  show(path: string): Promise<ShowView> {
    let p = this.shows.get(path);
    if (!p) {
      p = this.source.show(path);
      this.shows.set(path, p);
      p.catch(() => {
        if (this.shows.get(path) === p) this.shows.delete(path);
      });
    }
    return p;
  }

  private async loop(): Promise<void> {
    do {
      this.again = false;
      await this.pass();
    } while (this.again);
  }

  private async pass(): Promise<void> {
    let next: Snapshot;
    try {
      if (!(await this.source.initialized())) {
        next = { log: null, status: null, uninitialized: true };
      } else {
        const [log, status] = await Promise.all([this.source.log(), this.source.status()]);
        next = { log, status };
      }
    } catch (e: any) {
      next = { log: null, status: null, error: String(e?.message ?? e).split("\n")[0] };
    }
    const history = JSON.stringify([next.log, next.status?.choices, next.uninitialized]);
    if (history !== this.history) {
      this.history = history;
      this.shows.clear();
    }
    this.snapshot = next;
    for (const fn of this.listeners) fn(next);
  }
}
