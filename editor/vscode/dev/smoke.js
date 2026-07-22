// Headless smoke test for the workbench webview render path (dev-only, .vscodeignore'd).
// The webview can't be screenshotted from a terminal, so instead of a visual check we run the
// REAL media/workbench.js under a minimal DOM shim, feed it the real compose fixture, and assert
// the graph render path executes end-to-end without throwing and actually emits nodes/edges/axis.
//
//   node dev/smoke.js
//
// This is not a substitute for a visual pass in VS Code, but it catches the class of bug a static
// syntax check can't: undefined references, bad attribute access, selector logic, on the real data.

const fs = require("fs");
const path = require("path");

// ── Minimal DOM ────────────────────────────────────────────────────────────────────────────────
let ALL = [];
class El {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attrs = {};
    this._classes = new Set();
    this._text = "";
    this.style = {};
    this.dataset = {};
    this.parent = null;
    this._listeners = {};
    this.hidden = false;
    this.classList = {
      add: (...c) => c.forEach((x) => this._classes.add(x)),
      remove: (...c) => c.forEach((x) => this._classes.delete(x)),
      toggle: (c, on) => { if (on === undefined) on = !this._classes.has(c); on ? this._classes.add(c) : this._classes.delete(c); return on; },
      contains: (c) => this._classes.has(c),
    };
    ALL.push(this);
  }
  get className() { return [...this._classes].join(" "); }
  set className(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.className = v; }
  getAttribute(k) { return this.attrs[k] != null ? this.attrs[k] : null; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text; }
  set innerHTML(v) { this.children = []; this._text = ""; }
  get innerHTML() { return ""; }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  insertBefore(c, ref) {
    c.parent = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i === -1) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
  }
  insertAdjacentElement(_pos, el) { if (this.parent) this.parent.children.push(el); return el; }
  addEventListener(t, fn) { (this._listeners[t] || (this._listeners[t] = [])).push(fn); }
  removeEventListener() {}
  setPointerCapture() {}
  getBoundingClientRect() { return { left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 }; }
  scrollIntoView() {}
  get clientWidth() { return 900; }
  get clientHeight() { return 600; }
  get scrollTop() { return 0; }
  set scrollTop(_v) {}
  _descendants() {
    const out = [];
    const walk = (n) => n.children.forEach((c) => { out.push(c); walk(c); });
    walk(this);
    return out;
  }
  _matches(sel) {
    // supports "tag", ".cls", ".a.b"
    if (sel.startsWith(".")) return sel.slice(1).split(".").every((c) => this._classes.has(c));
    return this.tagName === sel;
  }
  querySelectorAll(sel) {
    const parts = sel.split(",").map((s) => s.trim());
    return this._descendants().filter((el) => parts.some((p) => el._matches(p)));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

const byId = {};
for (const id of ["app", "titlebar", "rail", "inspector", "compositionBtn", "oracleChip",
  "offscreenAbove", "offscreenBelow", "saveBtn", "commitBtn", "undoBtn", "titlebarActions"]) {
  byId[id] = new El("div");
  byId[id].attrs.id = id;
}

global.document = {
  getElementById: (id) => byId[id] || null,
  createElement: (t) => new El(t),
  createElementNS: (_ns, t) => new El(t),
};
const winListeners = {};
global.window = {
  addEventListener: (t, fn) => (winListeners[t] || (winListeners[t] = [])).push(fn),
  removeEventListener: () => {},
  matchMedia: () => ({ matches: false }),
  dispatchEvent: (ev) => (winListeners[ev.type] || []).forEach((fn) => fn(ev)),
};
global.MessageEvent = class { constructor(type, init) { this.type = type; this.data = (init || {}).data; } };
global.ResizeObserver = class { observe() {} disconnect() {} };
let posted = [];
global.acquireVsCodeApi = () => ({ postMessage: (m) => posted.push(m), getState: () => null, setState: () => {} });

// ── Run the real script ──────────────────────────────────────────────────────────────────────
const jsPath = path.join(__dirname, "..", "media", "workbench.js");
const src = fs.readFileSync(jsPath, "utf8");
// The file defines top-level functions then an IIFE; eval in this global scope.
eval(src);

const compose = JSON.parse(fs.readFileSync(path.join(__dirname, "fixture-compose.json"), "utf8"));

function feed(msg) {
  ALL.length = 0; // reset registry so counts reflect this render only
  // re-collect existing fixed nodes
  Object.values(byId).forEach((e) => ALL.push(e));
  global.window.dispatchEvent(new global.MessageEvent("message", { data: msg }));
}

let failures = 0;
function check(name, cond, detail) {
  if (cond) { console.log("  ✓", name); }
  else { console.log("  ✗", name, detail != null ? `(${detail})` : ""); failures++; }
}

try {
  feed({ type: "state", compose });
  const rail = byId.rail;
  const lanes = rail.querySelectorAll(".glane");
  const swimlanes = rail.querySelectorAll(".swimlane");
  const svg = rail.querySelector("svg");
  const axisTick = rail.querySelectorAll(".axis-tick");
  const handle = rail.querySelectorAll(".frontier-handle");
  const veil = rail.querySelectorAll(".future-veil");
  const cars = rail.querySelectorAll(".gcar-wrap");
  const cells = rail.querySelectorAll(".gcar-cell");
  console.log("state render:");
  check("svg emitted", !!svg);
  check("lanes emitted", lanes.length > 0, `${lanes.length} lanes`);
  check("swimlane header(s) emitted", swimlanes.length > 0, `${swimlanes.length} swimlanes`);
  check("chunk-cars emitted", cars.length > 0, `${cars.length} cars`);
  check("within-car density cells emitted", cells.length > 0, `${cells.length} cells`);
  check("time axis ticks", axisTick.length > 0, `${axisTick.length} ticks`);
  check("frontier handle + veil", handle.length === 1 && veil.length === 1);
  check("lane has swatch+cars+count",
    lanes[0] && lanes[0].querySelectorAll(".glane-swatch").length === 1
      && lanes[0].querySelectorAll(".gcar-wrap").length > 0
      && lanes[0].querySelectorAll(".gbar-count").length === 1);

  // Selecting a feature: simulate a click on the first feature lane -> inspector populates.
  const featureLane = lanes.find((n) => n.getAttribute("data-id") && n.getAttribute("data-id").startsWith("f-"));
  if (featureLane && featureLane._listeners.click) {
    posted = [];
    featureLane._listeners.click.forEach((fn) => fn({}));
    check("select posts requestFold", posted.some((m) => m.type === "requestFold"), JSON.stringify(posted.map((m) => m.type)));
  }

  // Clicking a chunk-car selects its CHECKPOINT (distinct target from the whole-feature row click).
  // Re-query the live tree: the feature-select above re-rendered, detaching the earlier handles.
  const noopEv = { stopPropagation() {}, preventDefault() {} };
  const freshFeatureLane = byId.rail.querySelectorAll(".glane")
    .find((n) => (n.getAttribute("data-id") || "").startsWith("f-"));
  const car = freshFeatureLane &&
    freshFeatureLane.querySelectorAll(".gcar-wrap").find((c) => c._listeners && c._listeners.click);
  check("chunk-car is its own click target", !!car);
  if (car) {
    car._listeners.click.forEach((fn) => fn(noopEv));
    const selectedCar = byId.rail.querySelectorAll(".gcar-wrap.gcar-selected");
    const selectedRow = byId.inspector.querySelectorAll(".checkpoint.selected");
    check("car click focuses a checkpoint (gantt + inspector in sync)",
      selectedCar.length >= 1 && selectedRow.length >= 1,
      `${selectedCar.length} car(s), ${selectedRow.length} row(s)`);
  }

  // Clicking a feature LABEL spotlights it (a viewing toggle, not a feature-select): the svg goes
  // into focus mode, without throwing.
  const labelBtn = byId.rail.querySelectorAll(".glane-label-btn").find((n) => n._listeners && n._listeners.click);
  check("feature label is its own click target", !!labelBtn);
  if (labelBtn) {
    labelBtn._listeners.click.forEach((fn) => fn(noopEv));
    check("label click spotlights (svg enters focus mode)",
      byId.rail.querySelector("svg").classList.contains("focus"));
  }
  console.log(failures === 0 ? "\nSMOKE OK" : `\nSMOKE FAILED (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
} catch (e) {
  console.error("SMOKE THREW:", e && e.stack || e);
  process.exit(1);
}
