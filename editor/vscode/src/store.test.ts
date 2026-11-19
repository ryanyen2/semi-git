// The store's refresh coalescing and its per-file cache. Run: npm test.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { Store } from "./store.ts";
import type { StoreSource } from "./store.ts";
import type { LogView, ShowView, StatusView } from "./types.ts";

const fixture = (name: string) => JSON.parse(fs.readFileSync(path.join("dev", "fixtures", name), "utf8"));
const LOG: LogView = fixture("log.json");
const STATUS: StatusView = fixture("status.json");
const SHOW: ShowView = fixture("show-file.json");

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

function source(over: Partial<StoreSource> = {}) {
  const calls = { log: 0, status: 0, show: 0 };
  const src: StoreSource = {
    initialized: async () => true,
    log: async () => (calls.log++, LOG),
    status: async () => (calls.status++, STATUS),
    show: async () => (calls.show++, SHOW),
    ...over,
  };
  return { src, calls };
}

test("refreshes asked for while one runs make one more pass, and all resolve on the newest state", async () => {
  const gate = deferred<LogView>();
  let n = 0;
  const { src, calls } = source({ log: () => (n++ === 0 ? gate.promise : Promise.resolve(LOG)) });
  const store = new Store(src);
  const seen: unknown[] = [];
  store.onChange((s) => seen.push(s.log));
  const first = store.refresh();
  const rest = [store.refresh(), store.refresh(), store.refresh()];
  gate.resolve({ saves: [] });
  await Promise.all([first, ...rest]);
  assert.equal(n, 2, "one pass, then exactly one more");
  assert.equal(calls.status, 2);
  assert.deepEqual(seen, [{ saves: [] }, LOG]);
  assert.equal(store.snapshot.log, LOG);
});

test("a refresh after the last one finished starts a new pass", async () => {
  const { src, calls } = source();
  const store = new Store(src);
  await store.refresh();
  await store.refresh();
  assert.equal(calls.log, 2);
});

test("a folder with no record is uninitialized, and nothing else is read", async () => {
  const { src, calls } = source({ initialized: async () => false });
  const store = new Store(src);
  await store.refresh();
  assert.deepEqual(store.snapshot, { log: null, status: null, uninitialized: true });
  assert.equal(calls.log, 0);
});

test("a failed read becomes one line of error, with no state", async () => {
  const { src } = source({ status: async () => { throw new Error("could not run 'ugh' (ENOENT)\nmore"); } });
  const store = new Store(src);
  await store.refresh();
  assert.deepEqual(store.snapshot, { log: null, status: null, error: "could not run 'ugh' (ENOENT)" });
});

test("a file's show is read once and kept while only unsaved edits change", async () => {
  let status = STATUS;
  const { src, calls } = source({ status: async () => status });
  const store = new Store(src);
  await store.refresh();
  await store.show("bikecount/metrics.py");
  await store.show("bikecount/metrics.py");
  assert.equal(calls.show, 1);
  status = { ...STATUS, drift: { any: false, paths: [] } };
  await store.refresh();
  await store.show("bikecount/metrics.py");
  assert.equal(calls.show, 1, "drift does not change what show says");
});

test("a file's show is read again once the history changes", async () => {
  let log = LOG;
  const { src, calls } = source({ log: async () => log });
  const store = new Store(src);
  await store.refresh();
  await store.show("bikecount/metrics.py");
  log = { saves: LOG.saves.slice(1) };
  await store.refresh();
  await store.show("bikecount/metrics.py");
  assert.equal(calls.show, 2);
});

test("a failed show is not cached", async () => {
  let fail = true;
  const { src } = source({ show: async () => { if (fail) throw new Error("busy"); return SHOW; } });
  const store = new Store(src);
  await assert.rejects(store.show("a.py"));
  fail = false;
  assert.equal(await store.show("a.py"), SHOW);
});
