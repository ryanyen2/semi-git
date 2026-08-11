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
let PANE_W = 900; // the simulated pane width; a test may narrow it to exercise responsive layout
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
  append(...cs) { cs.forEach((c) => (typeof c === "string" ? (this._text += c) : this.appendChild(c))); }
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
  // Overridable so a test can render the same composition at a narrow pane width and assert the
  // responsive behaviour (the forecast band shedding cards) rather than just the wide-pane happy path.
  get clientWidth() { return PANE_W; }
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
  querySelector(sel) {
    const hit = this.querySelectorAll(sel)[0];
    if (hit) return hit;
    // CHROME ONLY: the static titlebar/inspector markup lives in workbench.ts, not here, so a shim
    // that hand-copied it drifted out of date and crashed the harness on markup it had never heard
    // of. A chrome element instead mints a missing single-class child on demand -- enough for
    // renderTitlebar to write its labels into. Graph elements are NOT chrome, so a querySelector
    // over rendered SVG still returns null honestly and a genuine selector bug still fails.
    if (!this._chrome || !/^\.[\w-]+$/.test(sel)) return null;
    const child = new El("div");
    child.className = sel.slice(1);
    child._chrome = true;
    return this.appendChild(child);
  }
}

// Elements are minted ON DEMAND rather than from a fixed list. The list version silently rotted
// every time the real titlebar/inspector grew a node (the harness died on `viewSeg` being null long
// after that control shipped), which is the opposite of what a smoke test is for: it should fail on
// the render logic, never on its own scaffolding being out of date.
const byId = {};
function elById(id) {
  let el = byId[id];
  if (!el) {
    el = byId[id] = new El("div");
    el.attrs.id = id;
    el._chrome = true; // static markup from workbench.ts -- may vivify children (see querySelector)
  }
  return el;
}

const docListeners = {};
global.document = {
  getElementById: elById,
  createElement: (t) => new El(t),
  createElementNS: (_ns, t) => new El(t),
  // Document-level listeners (the popover's click-outside dismissal) are registered, not dropped, so
  // the harness can fire them if a future assertion needs to.
  addEventListener: (t, fn) => (docListeners[t] || (docListeners[t] = [])).push(fn),
  removeEventListener: () => {},
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

// The real host injects `grid_view`'s cell table (plan U3) into the state message alongside the
// compose_view aggregate; the fixture is a raw compose_view capture, so derive the equivalent
// cell join from its history here (the same op -> (feature, commit) grouping `grid_view` does).
compose.grid = (function gridFromHistory(history) {
  const commits = (history && history.commits) || [];
  const byCell = new Map();
  for (const op of (history && history.ops) || []) {
    if (op.feature_id == null) continue;
    const key = op.feature_id + "|" + op.commit_index;
    let c = byCell.get(key);
    if (!c) byCell.set(key, (c = { feature_id: op.feature_id, commit_index: op.commit_index, op_ids: [], kinds: {} }));
    c.op_ids.push(op.id);
    c.kinds[op.kind] = (c.kinds[op.kind] || 0) + 1;
  }
  const cells = [...byCell.values()].map((c) => ({
    feature_id: c.feature_id, commit_index: c.commit_index, op_ids: c.op_ids.slice().sort(),
    op_count: c.op_ids.length, kinds: c.kinds, fidelity: "full",
  }));
  return { commits, cells, commit_count: commits.length };
})(compose.history);

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
  // One lane, one "big event". Equal-sized chapters are the common case, and promoting every car that
  // ties the lane maximum drew a lane's worth of centered tags on top of each other -- an unreadable
  // smear above the strip -- and brightened the whole row as if every chapter were the notable one.
  const maxTags = Math.max(0, ...lanes.map((n) => n.querySelectorAll(".gcar-tag").length));
  const maxBig = Math.max(0, ...lanes.map((n) => n.querySelectorAll(".gcar-big-rect").length));
  check("at most one big-event tag per lane", maxTags <= 1, `${maxTags} on the busiest lane`);
  check("at most one big-event car per lane", maxBig <= 1, `${maxBig} on the busiest lane`);

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
  // ── The forecast band: anticipated work drawn as cars right of the `now` rule ────────────────
  // Both kinds of not-yet-real work (uncommitted edits, pending plan steps) must render in the CAR
  // grammar, in one band, NOT as the old dashed underline + bare `+N` badge. Feed a composition that
  // carries both and assert the band, the rule, and a named+clickable plan ghost all exist.
  const leafFeatures = (compose.map.nodes || [])
    .filter((n) => n.id.startsWith("f-") && !(n.children || []).length);
  const [firstFeature, secondFeature] = leafFeatures;
  if (firstFeature && secondFeature) {
    feed({ type: "state", compose: {
      ...compose,
      save_preview: { affected: [{ feature_id: firstFeature.id, op_count: 4 }], new_work_count: 4 },
      plan: { sessions: [{
        session_id: "s1", plan_text: "Add a forecast band",
        steps: [
          { hollow_id: "h1", title: "Reserve the band in geometry", status: "pending",
            predicted_feature: secondFeature.id, rationale: "the future needs room",
            predicted_footprint: ["workbench.js::ganttGeom"], files: [] },
          { hollow_id: "h2", title: "Draw ghost cars", status: "pending",
            predicted_feature: secondFeature.id, predicted_footprint: ["workbench.js::renderForecastCars"], files: [] },
        ],
      }] },
    } });
    console.log("\nforecast band:");
    const r2 = byId.rail;
    const band = r2.querySelectorAll(".forecast-band");
    const nowRule = r2.querySelectorAll(".now-rule");
    const planGhosts = r2.querySelectorAll(".gcar-plan-ghost");
    const saveGhosts = r2.querySelectorAll(".gcar-pending");
    const ghostLabels = r2.querySelectorAll(".gcar-ghost-label, .gcar-ghost-tag");
    check("forecast band + now rule drawn", band.length === 1 && nowRule.length === 1,
      `${band.length} band, ${nowRule.length} rule`);
    check("uncommitted work is a ghost car (not a badge)", saveGhosts.length === 1,
      `${saveGhosts.length} save ghost(s)`);
    check("each pending plan step is its own ghost car", planGhosts.length === 2,
      `${planGhosts.length} plan ghost(s)`);
    // The whole point of the redesign: a forecast car is NAMED. The old badge was a bare `+N`.
    check("ghost cars carry their step title", ghostLabels.length >= 2 &&
      ghostLabels.some((t) => /Reserve the band|Draw ghost/.test(t.textContent)),
      ghostLabels.map((t) => t.textContent).join(" | "));
    // A plan ghost is the same click target as its inspector card -- the old badge was inert.
    const ghostWrap = r2.querySelectorAll(".gcar-plan-wrap").find((n) => n._listeners && n._listeners.click);
    check("plan ghost is clickable (selects the step)", !!ghostWrap);
    // The retired encodings must be gone, or the view carries two languages for one idea again.
    check("old plan underline + count badge retired",
      r2.querySelectorAll(".gbar-plan").length === 0 && r2.querySelectorAll(".gbar-plan-count").length === 0);
    // Responsive: at a narrow pane the band must SHED cards into a stack (keeping the survivors
    // readable) rather than shrink all three into unreadable stubs. The stack's back outline is the
    // "more behind this" mark, so assert it appears and that fewer cards are drawn than at 900px.
    const wideCards = planGhosts.length + saveGhosts.length;
    PANE_W = 420;
    feed({ type: "state", compose: {
      ...compose,
      save_preview: { affected: [{ feature_id: firstFeature.id, op_count: 4 }], new_work_count: 4 },
      plan: { sessions: [{
        session_id: "s1", plan_text: "Add a forecast band",
        steps: [
          { hollow_id: "h1", title: "Reserve the band in geometry", status: "pending",
            predicted_feature: secondFeature.id, predicted_footprint: [], files: [] },
          { hollow_id: "h2", title: "Draw ghost cars", status: "pending",
            predicted_feature: secondFeature.id, predicted_footprint: [], files: [] },
        ],
      }] },
    } });
    const narrowCards = byId.rail.querySelectorAll(".gcar-ghost").length;
    check("narrow pane sheds cards into a stack (not illegible stubs)",
      narrowCards < wideCards && byId.rail.querySelectorAll(".gcar-ghost-stackback").length >= 1,
      `${wideCards} card(s) at 900px -> ${narrowCards} at 420px`);
    // Even collapsed, the reader still gets a NAME, never a bare count as their only information.
    const narrowLabels = byId.rail.querySelectorAll(".gcar-ghost-label, .gcar-ghost-tag")
      .map((t) => t.textContent);
    check("a collapsed stack is still named, never only a count",
      narrowLabels.some((t) => t && !/^＋/.test(t) && t.length > 1), narrowLabels.join(" | "));
    PANE_W = 900;

    // And with nothing pending, the band must not appear at all: an ordinary repo keeps its old axis.
    feed({ type: "state", compose: { ...compose, save_preview: null, plan: { sessions: [] } } });
    check("no forecast -> no band (layout unchanged for an idle repo)",
      byId.rail.querySelectorAll(".forecast-band").length === 0 &&
      byId.rail.querySelectorAll(".now-rule").length === 0);
  }

  // ── Pane measurement: never bake a layout for a pane that isn't there ────────────────────────
  // A hidden or collapsed webview measures 0x0. Drawing then locks the timeline into its 320px floor
  // in the corner of what is really a wide pane -- the "squeezed to the side" report -- and it stays
  // there for as long as nothing forces a reflow. The draw has to be skipped instead: the last good
  // SVG stays up, and the pane reflows to full width the moment it is measurable again.
  console.log("\npane measurement:");
  const idle = { type: "state", compose: { ...compose, save_preview: null, plan: { sessions: [] } } };
  const wideW = Number(byId.rail.querySelector("svg").getAttribute("width"));
  check("a wide pane draws at the pane width", wideW === 900, String(wideW));
  PANE_W = 0;
  feed(idle);
  const hidden = byId.rail.querySelector("svg");
  check("a 0-width (hidden) pane is not redrawn at the 320px floor",
    !!hidden && Number(hidden.getAttribute("width")) === 900,
    hidden ? hidden.getAttribute("width") : "no svg");
  PANE_W = 1200;
  feed(idle);
  const backW = Number(byId.rail.querySelector("svg").getAttribute("width"));
  check("a measurable pane reflows to its full width", backW === 1200, String(backW));
  PANE_W = 900;

  console.log(failures === 0 ? "\nSMOKE OK" : `\nSMOKE FAILED (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
} catch (e) {
  console.error("SMOKE THREW:", e && e.stack || e);
  process.exit(1);
}
