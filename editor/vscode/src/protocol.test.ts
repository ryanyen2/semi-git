// The workbench protocol, host side, fed the fixtures. Run: npm test.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import { StaleRequestError } from "./cliSeam.ts";
import { Protocol, workbenchHtml } from "./protocol.ts";
import type { FromWebview, Host, ToWebview } from "./protocol.ts";
import type { FindView, Plan, ShowView, Snapshot } from "./types.ts";

const fixture = (name: string) => JSON.parse(fs.readFileSync(path.join("dev", "fixtures", name), "utf8"));
const STATE: Snapshot = { log: fixture("log.json"), status: fixture("status.json") };
const REVERT: Plan = fixture("revert-name.json");
const WHOLE: Plan = fixture("revert-whole.json");
const SHOW: ShowView = fixture("show-name.json");
const FOUND: FindView = fixture("find.json");

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

function harness(over: Partial<Host> = {}) {
  const posted: ToWebview[] = [];
  const acted: FromWebview[] = [];
  let refreshed = 0;
  const host: Host = {
    snapshot: () => STATE,
    preview: async (_verb, _addresses, whole) => (whole ? WHOLE : REVERT),
    apply: async () => ({ ...REVERT, applied: true }),
    show: async () => SHOW,
    find: async () => FOUND,
    refresh: async () => void refreshed++,
    post: (msg) => void posted.push(msg),
    act: (msg) => void acted.push(msg),
    ...over,
  };
  return { protocol: new Protocol(host), posted, acted, refreshed: () => refreshed };
}

test("ready is answered with the state", async () => {
  const h = harness();
  await h.protocol.receive({ type: "ready" });
  assert.deepEqual(h.posted, [{ type: "state", ...STATE }]);
});

test("a preview is answered with the planner's answer under its id", async () => {
  const h = harness();
  await h.protocol.receive({ type: "preview", id: 4, verb: "revert", addresses: ["hourly profile"], whole: false });
  await h.protocol.receive({ type: "preview", id: 5, verb: "revert", addresses: ["hourly profile"], whole: true });
  assert.deepEqual(h.posted, [{ type: "plan", id: 4, plan: REVERT }, { type: "plan", id: 5, plan: WHOLE }]);
});

test("a preview the CLI refused is answered with its one line", async () => {
  const h = harness({ preview: async () => { throw new Error("no such address: 'nope'\nnearest hits"); } });
  await h.protocol.receive({ type: "preview", id: 1, verb: "revert", addresses: ["nope"] });
  assert.deepEqual(h.posted, [{ type: "plan", id: 1, error: "no such address: 'nope'" }]);
});

test("only the newest preview is answered: an older one finishing late is dropped", async () => {
  const slow = deferred<Plan>();
  const h = harness({ preview: (_v, a) => (a[0] === "slow" ? slow.promise : Promise.resolve(REVERT)) });
  const first = h.protocol.receive({ type: "preview", id: 1, verb: "revert", addresses: ["slow"] });
  await h.protocol.receive({ type: "preview", id: 2, verb: "revert", addresses: ["fast"] });
  slow.resolve(WHOLE);
  await first;
  assert.deepEqual(h.posted, [{ type: "plan", id: 2, plan: REVERT }]);
});

test("an older request still queued is told it is unwanted, and never answered", async () => {
  const wanted: boolean[] = [];
  const turn = deferred<void>();
  const h = harness({
    // The client checks `stillWanted` when the request's turn on its queue comes.
    show: async (_a, stillWanted) => {
      await turn.promise;
      wanted.push(stillWanted());
      if (!stillWanted()) throw new StaleRequestError();
      return SHOW;
    },
  });
  const first = h.protocol.receive({ type: "show", id: 1, address: "event days" });
  const second = h.protocol.receive({ type: "show", id: 2, address: "event days" });
  turn.resolve();
  await Promise.all([first, second]);
  assert.deepEqual(wanted, [false, true]);
  assert.deepEqual(h.posted, [{ type: "detail", id: 2, show: SHOW }]);
});

test("kinds do not supersede each other", async () => {
  const h = harness();
  await Promise.all([
    h.protocol.receive({ type: "show", id: 7, address: "event days" }),
    h.protocol.receive({ type: "find", id: 8, query: "event" }),
  ]);
  assert.deepEqual(h.posted, [{ type: "detail", id: 7, show: SHOW }, { type: "found", id: 8, hits: FOUND.hits }]);
});

test("an apply is answered, then the state is refreshed, and a preview pending before it is dropped", async () => {
  const slow = deferred<Plan>();
  const order: string[] = [];
  const h = harness({
    preview: () => slow.promise,
    apply: async (verb, addresses, whole) => {
      order.push(`apply ${verb} ${addresses.join(",")} ${whole}`);
      return { ...WHOLE, applied: true };
    },
    refresh: async () => void order.push("refresh"),
    post: (msg) => void order.push(`post ${msg.type}`),
  });
  const pending = h.protocol.receive({ type: "preview", id: 1, verb: "revert", addresses: ["hourly profile"] });
  await h.protocol.receive({ type: "apply", id: 2, verb: "revert", addresses: ["hourly profile"], whole: true });
  slow.resolve(REVERT);
  await pending;
  assert.deepEqual(order, ["apply revert hourly profile true", "post plan", "refresh"]);
});

test("an apply refused before any plan (a dirty tree) answers with the error, and still refreshes", async () => {
  const h = harness({ apply: async () => { throw new Error("revert would overwrite uncommitted changes"); } });
  await h.protocol.receive({ type: "apply", id: 3, verb: "revert", addresses: ["x"] });
  assert.deepEqual(h.posted, [{ type: "plan", id: 3, error: "revert would overwrite uncommitted changes" }]);
  assert.equal(h.refreshed(), 1);
});

test("a show that failed to run comes back as a refused show, a find as no hits", async () => {
  const h = harness({
    show: async () => { throw new Error("ugh did not answer within 120s"); },
    find: async () => { throw new Error("boom"); },
  });
  await h.protocol.receive({ type: "show", id: 1, address: "a.py::f" });
  await h.protocol.receive({ type: "find", id: 2, query: "f" });
  assert.deepEqual(h.posted, [
    { type: "detail", id: 1, show: { ok: false, address: "a.py::f", message: "ugh did not answer within 120s" } },
    { type: "found", id: 2, hits: [] },
  ]);
});

test("the messages that open or ask go to VS Code untouched", async () => {
  const h = harness();
  const msgs: FromWebview[] = [
    { type: "name", addresses: ["a.py::f", "3f2a1c0"] },
    { type: "rename", label: "event days" },
    { type: "resolve", address: "bikecount/metrics.py::yearly_summary" },
    { type: "openStep", address: "bikecount/metrics.py::side_totals@22496d8" },
    { type: "openFile", path: "bikecount/metrics.py", line: 17 },
    { type: "render", address: null },
    { type: "copy", text: "event days" },
    { type: "init" },
  ];
  for (const m of msgs) await h.protocol.receive(m);
  assert.deepEqual(h.acted, msgs);
  assert.deepEqual(h.posted, []);
});

test("the page loads only its script and stylesheet, the script by nonce", () => {
  const html = workbenchHtml("vscode-webview://x", "https://x/workbench.js", "https://x/workbench.css", "n0nce");
  assert.match(html, /default-src 'none'/);
  assert.match(html, /script-src 'nonce-n0nce'/);
  assert.doesNotMatch(html, /unsafe-inline|unsafe-eval/);
  assert.match(html, /<script nonce="n0nce" src="https:\/\/x\/workbench.js"><\/script>/);
  assert.match(html, /<link rel="stylesheet" href="https:\/\/x\/workbench.css">/);
});
