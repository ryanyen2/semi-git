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
    // Inputs answer `.value`. The find box reads its own value on every render (the lens is a
    // function of what is typed), so without this the harness died in `lensState` before drawing a
    // single node -- and had been dying there, so nothing below was being exercised at all.
    this.value = "";
    // A real `style` answers `setProperty`/`getPropertyValue` as well as named properties, and the
    // chip's growth origin is a CSS custom property -- which can ONLY be set that way. A plain
    // object silently lacked the method and the harness died on the first hover chip.
    this.style = {
      _custom: {},
      setProperty(k, v) { this._custom[k] = String(v); },
      getPropertyValue(k) { return this._custom[k] || ""; },
      removeProperty(k) { delete this._custom[k]; },
    };
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
  // The preview morph stashes each lane's resting op-count under `data-orig` so backing out can put
  // it back, and asks first whether one is already there. Without these the harness died the moment
  // a preview was staged -- on scaffolding, not on the render logic it is here to check.
  hasAttribute(k) { return this.attrs[k] != null; }
  removeAttribute(k) { delete this.attrs[k]; }
  set textContent(v) { this._text = String(v); this.children = []; }
  // Aggregates descendants, like the real thing. A `<text>` that carries its content in `<tspan>`
  // children (the checkpoint chip) read as empty under a getter that returned only its own `_text`,
  // so a chip with a perfectly good name in it looked nameless to the harness.
  get textContent() { return this._text || this.children.map((c) => c.textContent).join(""); }
  set innerHTML(v) { this.children = []; this._text = ""; }
  get innerHTML() { return ""; }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach((c) => (typeof c === "string" ? (this._text += c) : this.appendChild(c))); }
  // The titlebar chips rebuild themselves with replaceChildren; without it the harness died in
  // renderTitlebar before ever reaching the graph it exists to exercise.
  replaceChildren(...cs) { this.children = []; this._text = ""; cs.forEach((c) => this.appendChild(c)); }
  insertBefore(c, ref) {
    c.parent = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i === -1) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
  }
  insertAdjacentElement(_pos, el) { if (this.parent) this.parent.children.push(el); return el; }
  // Real elements detach themselves; the retracting chip removes itself when its animation ends.
  remove() {
    if (!this.parent) return;
    const i = this.parent.children.indexOf(this);
    if (i !== -1) this.parent.children.splice(i, 1);
    this.parent = null;
  }
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
  // NOTE: this returns a real Array, while a browser returns a NodeList -- which has `forEach` but
  // NOT `find`/`filter`/`map`. Array methods called on a `querySelectorAll` result therefore work
  // here and silently do nothing in Chrome, and this harness cannot see the difference. The chip's
  // retraction shipped broken exactly that way. Spread before using array methods in `workbench.js`.
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

// Canvas 2D, enough of it to measure text. The gutter labels are cut to a PIXEL budget measured in
// the font they are actually drawn in (see `textWidth` in workbench.js), so the shim has to answer
// `measureText` or the render path dies at import. A fixed 6.5px per glyph is a stand-in for real
// glyph metrics -- what this harness checks is that the fitting logic runs and produces sane
// widths, not that a headless Node agrees with Chrome about a font it doesn't have.
const CANVAS_PX_PER_CHAR = 6.5;
class Ctx2D {
  constructor() { this.font = ""; }
  measureText(s) { return { width: String(s).length * CANVAS_PX_PER_CHAR }; }
}

const docListeners = {};
global.document = {
  getElementById: elById,
  createElement: (t) => {
    const el = new El(t);
    if (t === "canvas") el.getContext = () => new Ctx2D();
    return el;
  },
  createElementNS: (_ns, t) => new El(t),
  // The measurement probes are appended to <body>; without one they would throw on append.
  body: new El("body"),
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
  // The chip hover-intent waits for the cursor to rest before naming a chapter; 0 makes it show
  // synchronously so this harness's mouseenter -> assert sequences stay synchronous.
  __CHIP_INTENT_MS__: 0,
};
// The label-measurement probes read their font off a real styled node. There is no stylesheet here,
// so answer with the shape `fontFor` builds its shorthand from; the widths come from Ctx2D anyway.
global.getComputedStyle = () => ({
  fontStyle: "normal", fontWeight: "400", fontSize: "11px", fontFamily: "monospace",
});
global.window.getComputedStyle = global.getComputedStyle;
global.MessageEvent = class { constructor(type, init) { this.type = type; this.data = (init || {}).data; } };
global.ResizeObserver = class { observe() {} disconnect() {} };
let posted = [];
global.acquireVsCodeApi = () => ({ postMessage: (m) => posted.push(m), getState: () => null, setState: () => {} });

// ── Run the real script ──────────────────────────────────────────────────────────────────────
const jsPath = path.join(__dirname, "..", "media", "workbench.js");
const src = fs.readFileSync(jsPath, "utf8");
// The file defines top-level functions then an IIFE; eval in this global scope.
eval(src);

// ---- end-domshim (slice boundary: `render-bundle.js` reuses everything above this line, so the
// real study bundle can be rendered through the real webview code without a second DOM shim)

const compose = require("./fixture.js")(
  JSON.parse(fs.readFileSync(path.join(__dirname, "fixture-compose.json"), "utf8")));

function feed(msg) {
  ALL.length = 0; // reset registry so counts reflect this render only
  // re-collect existing fixed nodes
  Object.values(byId).forEach((e) => ALL.push(e));
  dispatch(msg);
}

// A host message that does NOT re-render the graph (a preview answer, an apply phase): it paints
// over the rail that is already there, so the registry has to survive it.
function dispatch(msg) {
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
  // The DEFAULT view is the folded tree: non-root subsystems arrive collapsed to meta-lanes, so
  // the first open shows the hierarchy's top level rather than every leaf as a flat sibling list.
  const metaLanes = lanes.filter((n) => {
    const id = n.getAttribute("data-id") || "";
    return id && !id.startsWith("f-");
  });
  check("default view is the folded tree (collapsed subsystems present as meta-lanes)",
        metaLanes.length > 0, `${metaLanes.length} meta-lanes of ${lanes.length} lanes`);
  const swatches = rail.querySelectorAll(".glane-swatch");
  const grey = [...swatches].filter((el) => el.getAttribute("fill") === "#8a8a8a");
  check("every lane has an identity hue (subsystem meta-lanes included)",
        swatches.length > 0 && grey.length === 0,
        `${grey.length}/${swatches.length} swatches fell back to neutral grey`);
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

  // Fold stability: expanding a collapsed subsystem must not reshuffle the rows around it. The
  // meta-lane's slot becomes the header's slot; everything above stays exactly where it was.
  // (This is the ordering-by-kind rule in orderedChildren -- laneness flips on fold, kind doesn't.)
  console.log("fold stability:");
  const rowOrder = () => byId.rail.querySelectorAll(".glane, .swimlane")
    .map((n) => ({ id: n.getAttribute("data-id"),
                   y: parseFloat(n.querySelectorAll("rect")[0].getAttribute("y")) }))
    .sort((a, b) => a.y - b.y).map((r) => r.id);
  const beforeFold = rowOrder();
  const metaLane = byId.rail.querySelectorAll(".glane").find((n) => {
    const id = n.getAttribute("data-id") || "";
    return id && !id.startsWith("f-");
  });
  if (metaLane && metaLane._listeners.click) {
    const subId = metaLane.getAttribute("data-id");
    const slot = beforeFold.indexOf(subId);
    metaLane._listeners.click.forEach((fn) => fn({}));
    const afterFold = rowOrder();
    check("expanding a fold keeps every row above it in place",
          JSON.stringify(afterFold.slice(0, slot)) === JSON.stringify(beforeFold.slice(0, slot)),
          `before=${beforeFold.join(",")} after=${afterFold.join(",")}`);
    check("the expanded block opens at the fold's own slot", afterFold[slot] === subId,
          `expected header ${subId} at ${slot}, got ${afterFold[slot]}`);
    // fold it back so later assertions meet the default view again
    const header = byId.rail.querySelectorAll(".swimlane").find((n) => n.getAttribute("data-id") === subId);
    if (header && header._listeners.click) header._listeners.click.forEach((fn) => fn({}));
  } else {
    check("a collapsed subsystem exists to test fold stability", false, "no meta lane found");
  }

  // Theme focus (TableLens): clicking a group on the idle panel compresses every non-member lane
  // to a thin density row, keeps the members at full height, and captions the lens in a banner.
  console.log("theme focus:");
  const themeRow = byId.inspector.querySelectorAll(".theme-row")[0];
  check("idle panel lists cross-feature groups", !!themeRow);
  if (themeRow && themeRow._listeners.click) {
    themeRow._listeners.click.forEach((fn) => fn({}));
    const quiet = byId.rail.querySelectorAll(".glane-quiet");
    const loud = byId.rail.querySelectorAll(".glane").filter((n) => !String(n.attrs.class).includes("glane-quiet"));
    check("context lanes compress under the focus", quiet.length > 0, `${quiet.length} quiet lanes`);
    check("member lanes keep their full rows", loud.length > 0, `${loud.length} full lanes`);
    check("the banner names the lens", byId.themeBanner && byId.themeBanner.hidden === false);
    const clearBtns = byId.themeBanner.querySelectorAll("button");
    const clearBtn = clearBtns[clearBtns.length - 1];
    if (clearBtn && clearBtn._listeners.click) clearBtn._listeners.click.forEach((fn) => fn({}));
    check("clearing the focus restores every lane",
          byId.rail.querySelectorAll(".glane-quiet").length === 0);
  }

  // Cross-feature work: by default each spanning item is ONE ◆ marker on the time axis (no
  // always-on link lines -- several at once drew spaghetti); hovering rings its member chapters
  // and clicking enters the focus, where exactly one spine draws.
  console.log("cross-feature markers:");
  let unfoldGuard = 0;
  for (;;) {
    const metaFold = byId.rail.querySelectorAll(".glane").find((n) => {
      const id = n.getAttribute("data-id") || "";
      return id && !id.startsWith("f-");
    });
    if (!metaFold || !metaFold._listeners.click || unfoldGuard++ > 10) break;
    metaFold._listeners.click.forEach((fn) => fn({}));
  }
  const marks = byId.rail.querySelectorAll(".theme-mark");
  check("spanning work sits on the axis as a ◆ marker", marks.length > 0, `${marks.length} markers`);
  check("no spine draws before anyone asks", byId.rail.querySelectorAll(".theme-spine").length === 0);
  const mark = marks[0];
  if (mark && mark._listeners.mouseenter) {
    mark._listeners.mouseenter.forEach((fn) => fn({}));
    check("hovering the marker draws its one spine",
          byId.rail.querySelectorAll(".theme-spine").length === 1);
    check("hovering rings the member chapters",
          byId.rail.querySelectorAll(".gcar-theme-member").length >= 2,
          `${byId.rail.querySelectorAll(".gcar-theme-member").length} ringed`);
    mark._listeners.mouseleave.forEach((fn) => fn({}));
    check("leaving retracts the spine",
          byId.rail.querySelectorAll(".theme-spine").length === 0);
  }
  if (mark && mark._listeners.click) {
    mark._listeners.click.forEach((fn) => fn({ stopPropagation() {} }));
    check("clicking the marker enters the focus",
          byId.themeBanner && byId.themeBanner.hidden === false);
    const focusedSpines = byId.rail.querySelectorAll(".theme-spine-focused");
    check("the focused work draws exactly one spine", focusedSpines.length === 1,
          `${focusedSpines.length} spines`);
    check("its dots mark at least two lanes",
          focusedSpines.length > 0 && focusedSpines[0].querySelectorAll(".theme-spine-dot").length >= 2);
    const markClear = byId.themeBanner.querySelectorAll("button");
    const mcb = markClear[markClear.length - 1];
    if (mcb && mcb._listeners.click) mcb._listeners.click.forEach((fn) => fn({}));
    check("clearing the focus removes the spine",
          byId.rail.querySelectorAll(".theme-spine-focused").length === 0);
  }

  // Selecting a feature: simulate a click on the first feature lane -> inspector populates.
  const featureLane = lanes.find((n) => n.getAttribute("data-id") && n.getAttribute("data-id").startsWith("f-"));
  if (featureLane && featureLane._listeners.click) {
    posted = [];
    featureLane._listeners.click.forEach((fn) => fn({}));
    // `requestChange`, not `requestFold`: selecting a lane asks "what did this DO", and the change
    // panel is what answers it. The code-at-this-frontier fold is the fallback the panel falls to
    // when there is no projection to read, so it is not what a plain select posts any more.
    check("select asks the host what this selection changed",
      posted.some((m) => m.type === "requestChange"), JSON.stringify(posted.map((m) => m.type)));
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

  // A feature's NAME is not a second click target inside its row: it used to pin a lens (dim every
  // other lane) on click, which is a large answer to a click that in this view means "show me this
  // feature". The whole row is one target now, and nothing in the gutter claims otherwise.
  check("feature label is not its own click target",
    byId.rail.querySelectorAll(".glane-label-btn").length === 0
    && byId.rail.querySelectorAll(".glane-lens-mark").length === 0);
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

  // ── The name column: fitted, resizable, and never silently truncating ────────────────────────
  // The gutter used to be a hard `clamp(130, paneW*0.4, 220)` -- about 34 glyphs -- cut with a
  // guessed 6.5px per character and carrying no tooltip. On a repo whose feature names are
  // sentences that made most rows unidentifiable, with no way to widen the column and no way to
  // read the rest. Three things have to hold now: the column fits its content, a cut name still
  // hands over its full text, and the reader can drag the divider.
  console.log("\nname column:");
  PANE_W = 900;
  feed(idle);
  const divider = byId.rail.querySelector(".col-divider-grip");
  check("the name column has a drag grip", !!divider);

  const laneLabels = byId.rail.querySelectorAll(".glane-label");
  check("lane labels are drawn", laneLabels.length > 0, `${laneLabels.length} label(s)`);
  // Anything ellipsized must carry its full text as a <title> child (an SVG <text> ignores the
  // attribute), so a name too long for the column is still recoverable by hovering it.
  const clipped = laneLabels.filter((t) => /…$/.test(t.textContent));
  check("every clipped label carries its full text on hover",
    clipped.every((t) => t.children.some((c) => c.tagName === "title" && c.textContent.length > 0)),
    `${clipped.length} clipped of ${laneLabels.length}`);

  // Dragging the grip right widens the column: the labels get more room, not the plot.
  const widthOfCol = () => Number(byId.rail.querySelector(".col-divider-rule").getAttribute("x1"));
  const before = widthOfCol();
  divider._listeners.pointerdown[0]({
    clientX: 100, pointerId: 1, preventDefault() {}, stopPropagation() {},
  });
  (docListeners.pointermove || []).forEach((fn) => fn({ clientX: 220 }));
  check("dragging the divider widens the name column", widthOfCol() > before,
    `${before} -> ${widthOfCol()}`);
  (docListeners.pointerup || []).forEach((fn) => fn({}));

  // ...and a double-click drops back to the measured fit rather than leaving the reader stuck with
  // whatever they last dragged.
  const dragged = widthOfCol();
  byId.rail.querySelector(".col-divider-grip")
    ._listeners.dblclick[0]({ preventDefault() {}, stopPropagation() {} });
  check("double-click resets the column to its fit", widthOfCol() !== dragged,
    `${dragged} -> ${widthOfCol()}`);

  // ── Nothing draws past the axis ───────────────────────────────────────────────────────────────
  // x means WHEN, so a car past the plot's right edge is claiming a time that does not exist. A
  // dense lane used to walk right off the end: every car is nudged clear of the previous one and
  // floored at `minCarW`, and the clamp that was supposed to pull it back floored at `minCarW` too.
  console.log("\nplot bounds:");
  const plotEdge = Number(byId.rail.querySelector(".axis-track").getAttribute("x2"));
  const carRights = byId.rail.querySelectorAll(".gcar, .gcar-cell")
    .map((r) => Number(r.getAttribute("x")) + Number(r.getAttribute("width")));
  const worst = Math.max(0, ...carRights);
  check("no chapter car is drawn past the axis", worst <= plotEdge + 0.5,
    `rightmost car ${worst.toFixed(1)} vs plot edge ${plotEdge}`);
  // ...and the op-count column sits clear of them, in the reserved strip to their right.
  const countXs = new Set(byId.rail.querySelectorAll(".gbar-count").map((t) => t.getAttribute("x")));
  check("op counts share one column, clear of the plot",
    countXs.size === 1 && Number([...countXs][0]) > plotEdge, [...countXs].join(","));

  // A chapter's out-of-car tag may use the empty time AFTER its own chapter and none of anyone
  // else's. Measured to the plot's right edge instead of to the next car, a tag ran up to 140px
  // across every chapter that followed it, and a dense lane rendered as overlapping text.
  const tagOverlaps = [];
  for (const lane of byId.rail.querySelectorAll(".glane")) {
    const cars = lane.querySelectorAll(".gcar")
      .map((r) => ({ x: Number(r.getAttribute("x")), w: Number(r.getAttribute("width")) }))
      .sort((a, b) => a.x - b.x);
    for (const tag of lane.querySelectorAll(".gcar-tag-inrow")) {
      const tx = Number(tag.getAttribute("x"));
      const tw = String(tag.textContent).length * CANVAS_PX_PER_CHAR;
      for (const c of cars) {
        if (c.x >= tx + tw || c.x + c.w <= tx) continue;    // no horizontal overlap
        if (Math.abs(c.x + c.w - tx) <= 6) continue;        // its own car, immediately left of it
        tagOverlaps.push(`"${tag.textContent}" over a car at x=${c.x}`);
      }
    }
  }
  check("no chapter label is drawn over another chapter's car", tagOverlaps.length === 0,
    tagOverlaps.slice(0, 3).join("; "));

  // Density texture is bounded by PIXELS, not by commits. One rect per commit meant a chapter
  // spanning 400 commits drew 400 rects into a 30px car -- 0.075px each, none of them visible --
  // which measured 588,499 nodes and a 5.2-second render on a 4,000-commit history.
  const subPixel = [...byId.rail.querySelectorAll(".gcar-cell")]
    .map((c) => Number(c.getAttribute("width")))
    .filter((w) => w < 0.5);
  check("no density cell is drawn narrower than it can be seen", subPixel.length === 0,
    `${subPixel.length} sub-pixel cell(s)`);

  // ── Every checkpoint can say its own name ─────────────────────────────────────────────────────
  // Most cars are too narrow to hold their label inline, so before the chip the only way to read a
  // chapter's name was an OS tooltip on a one-second delay. Hovering must put the name on screen,
  // and it must come back off.
  console.log("\ncheckpoint names:");
  const chipLayer = byId.rail.querySelector(".gchip-layer");
  check("the chip layer paints above the lanes", !!chipLayer
    && chipLayer.parent.children.indexOf(chipLayer)
       > chipLayer.parent.children.indexOf(chipLayer.parent.querySelector(".glanes")));
  const narrow = byId.rail.querySelectorAll(".gcar-wrap")
    .find((c) => c._listeners && c._listeners.mouseenter
      && c.querySelectorAll(".gcar-label").length === 0);
  if (!narrow) {
    check("a car with no inline label exists to hover", false, "none found in the fixture");
  } else {
    narrow._listeners.mouseenter.forEach((fn) => fn({}));
    const chip = chipLayer.querySelector(".gchip");
    const name = chip && chip.querySelector(".gchip-name");
    check("hovering an unlabelled chapter names it", !!name && String(name.textContent).trim().length > 0,
      name ? name.textContent : "no chip");
    // The chip must stay inside the plot: clamped left of the op-count column, right of the gutter.
    const bg = chip && chip.querySelector(".gchip-bg");
    const right = bg ? Number(bg.getAttribute("x")) + Number(bg.getAttribute("width")) : 0;
    check("the chip stays inside the plot", bg && right <= plotEdge + 0.5,
      `chip right ${right.toFixed(1)} vs plot edge ${plotEdge}`);
    // Leaving retracts the hover chip. What may remain is the SELECTED chapter's pinned chip --
    // an earlier test in this run clicks a car, and the name of the thing you picked is supposed to
    // stay on screen.
    // The chip must not carry a geometry transform of its own. It used to unfold out of the bar's
    // rectangle, which read at pointer speed as the row rearranging itself under the question; it
    // now fades at its final size. Asserted as an ABSENCE because the regression this guards is a
    // re-introduced morph, and a morph is exactly what a transform on this node would be.
    const hoverChip = chipLayer.querySelectorAll(".gchip")
      .find((c) => !c.classList.contains("gchip-pinned"));
    const moved = hoverChip
      && (hoverChip.style.transform || hoverChip.style.getPropertyValue("--flip-from"));
    check("the name arrives without moving the bar", !!hoverChip && !moved,
      hoverChip ? `transform: ${moved}` : "no hover chip");

    narrow._listeners.mouseleave.forEach((fn) => fn({}));
    // Leaving RETRACTS the hover chip along the way it came -- it is not deleted where it stands.
    // Deleting it is what made the name read as thrown at the reader and then taken away: the only
    // motion in the interaction pointed one way. What may also remain is the SELECTED chapter's
    // pinned chip; an earlier test in this run clicks a car, and the name of the thing you picked is
    // supposed to stay on screen.
    const hoverChips = chipLayer.querySelectorAll(".gchip")
      .filter((c) => !c.classList.contains("gchip-pinned"));
    check("it retracts on leave rather than vanishing",
      hoverChips.length === 1 && hoverChips[0].classList.contains("gchip-out"),
      `${hoverChips.length} hover chip(s), classes ${hoverChips.map((c) => c.className).join("|")}`);
    // ...and a hover that arrives mid-retreat cancels it, instead of leaving a trail of retreating
    // chips behind a cursor swept along a dense lane.
    narrow._listeners.mouseenter.forEach((fn) => fn({}));
    const trailing = chipLayer.querySelectorAll(".gchip-out");
    check("a new hover cancels the retreat in progress", trailing.length === 0,
      `${trailing.length} retreating chip(s) left behind`);
  }

  // ── A held confirm does not make the timeline unreadable ──────────────────────────────────────
  // Staging a revert/restore holds a consequence on the field and asks a question about it. Reading
  // is not previewing, so pointing at a neighbouring chapter while deciding has to keep working:
  // the hover used to return early on `stagedAction`, which left the OS `<title>` tooltip -- a
  // second late, drawn over the timeline it describes -- as the only way to identify anything on
  // screen at the exact moment the reader most needs to. Nothing here previews or cancels: the
  // staged paint, and the bar carrying the question, both have to survive the hover.
  console.log("\nreading under a held confirm:");
  // Not the lane an earlier test already selected: a click on that one is a DESELECT (single-select
  // toggles), which empties the detail panel and leaves nothing to stage from.
  const laneWithCars = byId.rail.querySelectorAll(".glane")
    .find((g) => /^f-/.test(g.getAttribute("data-id") || "") && g.querySelector(".gcar-wrap")
      && !g.classList.contains("selected"));
  if (!laneWithCars || !laneWithCars._listeners.click) {
    check("a feature lane with chapters exists to stage from", false, "none found in the fixture");
  } else {
    laneWithCars._listeners.click.forEach((fn) => fn(noopEv)); // select it: the inspector lists its checkpoints
    const rewind = byId.inspector.querySelectorAll(".checkpoint-rewind")
      .find((b) => b._listeners && b._listeners.click);
    check("the detail panel offers a checkpoint action", !!rewind);
    if (rewind) {
      rewind._listeners.click.forEach((fn) => fn(noopEv)); // stage the confirm
      check("the checkpoint action stages a confirm", byId.confirmBar.hidden === false);
      const car = byId.rail.querySelectorAll(".gcar-wrap")
        .find((c) => c._listeners && c._listeners.mouseenter);
      car._listeners.mouseenter.forEach((fn) => fn({}));
      check("a chapter still highlights under a held confirm", car.classList.contains("gcar-hovered"));
      const chip = byId.rail.querySelector(".gchip-layer").querySelector(".gchip-name");
      check("a chapter still names itself under a held confirm",
        !!chip && String(chip.textContent).trim().length > 0, chip ? chip.textContent : "no chip");
      check("the question survives the hover", byId.confirmBar.hidden === false);
      car._listeners.mouseleave.forEach((fn) => fn({}));
      check("the question survives the leave", byId.confirmBar.hidden === false);
    }
  }

  // A revert and a restore of the same work used to draw the same picture: the same dashed cars,
  // the same dimmed field, the same banner, and an unsigned `N → M` at the right edge of the pane
  // as the only difference. Every assertion here is one of the channels that now carries the
  // direction -- the paint on the target lane, the sign on the delta, the state each chapter is
  // drawn in, and the sentence on the field. If a future change collapses any of them back onto a
  // shared appearance, this is where it shows up.
  console.log("\nrevert vs restore read differently:");
  // Staged through the theme banner's whole-group verbs, not a lane's action bar: the bar offers
  // Restore only when something in that lane is actually retired (nothing is, in this fixture),
  // and the banner is the surface the reported flow used anyway -- click the cross-feature work,
  // then Revert this work / Restore.
  for (const lane of byId.rail.querySelectorAll(".glane")) {
    if (lane.classList.contains("selected") && lane._listeners.click) {
      lane._listeners.click.forEach((fn) => fn(noopEv)); // deselect: the idle panel lists the groups
    }
  }
  const dirThemeRow = byId.inspector.querySelectorAll(".theme-row")[0];
  if (dirThemeRow && dirThemeRow._listeners.click) dirThemeRow._listeners.click.forEach((fn) => fn({}));
  // A member lane, not a compressed context one: the focus draws the quiet rows as density strips
  // with no chapter cars, and the chapter half of the preview is exactly what has to be checked.
  const dirLane = byId.rail.querySelectorAll(".glane")
    .find((g) => /^f-/.test(g.getAttribute("data-id") || "")
      && !g.classList.contains("glane-quiet") && g.querySelector(".gcar-wrap"));
  const dirBtn = (verb) => byId.themeBanner.querySelectorAll("button")
    .find((b) => String(b.textContent) === (verb === "restore" ? "Restore" : "Revert this work"));
  if (!dirLane || !dirBtn("revert") || !dirBtn("restore")) {
    check("a focused cross-feature group offers both directions", false,
          `lane=${!!dirLane} banner=[${byId.themeBanner.querySelectorAll("button").map((b) => b.textContent)}]`);
  } else {
    const fid = dirLane.getAttribute("data-id");
    const segs = (compose.intent.segments || []).filter((s) => s.feature_id === fid);
    const ops = segs.flatMap((s) => s.op_ids || []);
    // One staged preview per direction, answered with the same op set moving the other way, so the
    // only thing that differs between the two readings below is the verb.
    const stage = (verb) => {
      posted = [];
      dirBtn(verb)._listeners.click.forEach((fn) => fn(noopEv));
      const ask = posted.filter((m) => m.type === "previewVerb").pop();
      if (!ask) return null;
      const back = verb === "restore";
      // Dispatched directly, NOT through `feed`: `feed` clears the element registry so a render's
      // node counts are its own, and a `previewResult` does not re-render the graph -- routing it
      // through `feed` would empty the very rail this then reads the preview paint off.
      dispatch({
        type: "previewResult", seq: ask.seq,
        result: {
          ok: true, verb, target: fid, forked: false, message: "", files: {}, affected: [],
          affected_symbols: [], target_ops: ops,
          removed: back ? [] : ops, added: back ? ops : [],
          focus: {
            so_what: "", edges: [], context_count: 7,
            nodes: [{ feature_id: fid, label: "t", role: "target",
                      ops_before: back ? 3 : 26, ops_after: back ? 26 : 3 }],
          },
        },
      });
      // `findRow` in the webview takes the LAST match for a data-id, so read the same way rather
      // than the first: a stale row from a previous render must not be what this asserts against.
      const rows = byId.rail.querySelectorAll(".glane").filter((g) => g.getAttribute("data-id") === fid);
      const row = rows[rows.length - 1];
      const count = row && row.querySelector(".gbar-count");
      return {
        cls: row ? String(row.className || "") : "",
        count: count ? String(count.textContent) : "",
        cars: byId.rail.querySelectorAll(".gcar-wrap")
          .map((w) => String(w.className || "")).filter((c) => /gcar-preview-(in|out)/.test(c)),
        say: String(byId.previewContext.textContent || ""),
      };
    };
    const out = stage("revert");
    // Cancel the held stage: a second stageAction would work, but leaving one held would make the
    // restore reading depend on the revert reading's teardown.
    const cancel = byId.confirmBar.querySelectorAll("button").find((b) => String(b.textContent) === "Cancel");
    if (cancel && cancel._listeners.click) cancel._listeners.click.forEach((fn) => fn(noopEv));
    const back = out ? stage("restore") : null;
    if (!out || !back) {
      check("both directions can be staged from the action bar", false,
            `revert=${!!out} restore=${!!back} (the bar offers Restore only when something is retired)`);
    } else {
      check("the target lane is painted losing one way and gaining the other",
            /preview-losing/.test(out.cls) && /preview-gaining/.test(back.cls),
            `revert=${out.cls} | restore=${back.cls}`);
      check("the op delta is signed, so the pair needs no arithmetic",
            /−\d/.test(out.count) && /\+\d/.test(back.count), `revert="${out.count}" restore="${back.count}"`);
      check("an affected chapter is drawn leaving one way and arriving the other",
            out.cars.length > 0 && back.cars.length > 0
              && out.cars.every((c) => /gcar-preview-out/.test(c))
              && back.cars.every((c) => /gcar-preview-in/.test(c)),
            `revert=[${out.cars}] restore=[${back.cars}]`);
      check("the sentence on the field names the verb",
            /^Revert · −/.test(out.say) && /^Restore · \+/.test(back.say),
            `revert="${out.say}" restore="${back.say}"`);

      // The seam between two changes that landed together: chapters are hoverable under a held
      // confirm now, and the direction sentence lives in the pill that hover used to own. The
      // hover's own retract is scoped to the sentence IT wrote (`identity`), so a swept chapter
      // must not take the held preview's caption with it -- if that scoping is ever dropped, the
      // reader loses the one statement of direction on the field by moving the mouse.
      const hoverCar = byId.rail.querySelectorAll(".gcar-wrap")
        .find((c) => c._listeners && c._listeners.mouseenter && c._listeners.mouseleave);
      if (hoverCar) {
        hoverCar._listeners.mouseenter.forEach((fn) => fn({}));
        hoverCar._listeners.mouseleave.forEach((fn) => fn({}));
        check("sweeping a chapter does not wipe the held preview's direction caption",
              String(byId.previewContext.textContent || "") === back.say
                && byId.previewContext.hidden === false,
              `after the sweep: "${byId.previewContext.textContent}" (was "${back.say}")`);
      }

      // The third and fourth moments. Applying used to leave the bar saying "Done", which is the
      // one word that cannot tell a reader which of the two directions they just took -- and the
      // lane's settle flash, which is the only mark left in the graph itself, named no direction
      // either (and, as it turned out, could not play at all: its CSS rule and the keyframes it
      // names had been merged into one invalid selector and dropped by the parser).
      const apply = byId.confirmBar.querySelectorAll("button").find((b) => String(b.textContent) === "Restore");
      if (!apply || !apply._listeners.click) {
        check("a staged restore offers Apply", false, "no enabled Restore in the bar");
      } else {
        apply._listeners.click.forEach((fn) => fn(noopEv));
        for (const phase of ["checking", "applying", "refreshing"]) {
          dispatch({ type: "applyProgress", verb: "restore", ref: fid, phase });
        }
        dispatch({ type: "applyProgress", verb: "restore", ref: fid, phase: "done",
                   detail: "restored 23 edit(s)" });
        check("the receipt names the direction, not just that something finished",
              /Restored/.test(String(byId.confirmBar.textContent)),
              String(byId.confirmBar.textContent));
        // The settle flash lands on the next state push, which is where the rewritten lane exists.
        feed({ type: "state", compose });
        const settled = byId.rail.querySelectorAll(".glane").filter((g) => g.classList.contains("settle-flash"));
        check("the lane that changed settles, in the direction it moved",
              settled.length > 0 && settled.every((g) => g.classList.contains("settle-in")),
              settled.map((g) => g.className).join(" ; ") || "nothing settled");
      }
    }
  }

  // A refusal is the other thing this bar has to be able to say. `renderConfirmBar` disabled Apply
  // on `ok:false` and left it there greyed out, which reads as "loading" or "not allowed" rather
  // than "sgt answered no" -- and the two ways out of the one-live-version rule were shown only on
  // the hover path, i.e. never at the moment somebody is actually blocked.
  console.log("\na refused stage says why, and what to do:");
  // The focus from the section above survives the state push, and clicking the group again would
  // TOGGLE it off -- so only enter one if the banner is not already offering its verbs.
  const banded = () => byId.themeBanner.querySelectorAll("button").find((b) => String(b.textContent) === "Restore");
  if (!banded()) {
    const refuseRow = byId.inspector.querySelectorAll(".theme-row")[0];
    if (refuseRow && refuseRow._listeners.click) refuseRow._listeners.click.forEach((fn) => fn({}));
  }
  const rBtn = banded();
  if (!rBtn || !rBtn._listeners.click) {
    check("a focused cross-feature group offers Restore to refuse", false, "no banner Restore");
  } else {
    posted = [];
    rBtn._listeners.click.forEach((fn) => fn(noopEv));
    const ask = posted.filter((m) => m.type === "previewVerb").pop();
    dispatch({
      type: "previewResult", seq: ask.seq,
      result: {
        ok: false, forked: true, verb: "restore", target: "pkg/metrics.py::__residue__",
        message: "would leave two live versions of pkg/metrics.py::__residue__",
        removed: [], added: [], affected_symbols: [], files: {},
      },
    });
    const text = String(byId.confirmBar.textContent);
    const btns = byId.confirmBar.querySelectorAll("button").map((b) => String(b.textContent));
    check("the reason is on the bar, where the click was", /two live versions/.test(text), text);
    check("no dead Apply is offered", !btns.includes("Restore"), btns.join(" | "));
    check("the way out is named", /way out: revert the live version first/.test(text), text);
    check("the bar reads as a refusal", byId.confirmBar.classList.contains("refused"));
    // ...and NOT in the top-centered refusal card, which sits exactly where the theme banner does.
    check("the refusal does not also draw over the banner", byId.previewRefusal.hidden === true);
  }

  // ── The stylesheet, audited as text ───────────────────────────────────────────────────────────
  // Three ways this file has silently lost a rule, all of them shipped, none of them visible to
  // `tsc`, to this harness's render path, or to a reader skimming the diff. A browser recovers from
  // bad CSS by dropping things quietly, so the only way to catch these is to read the source.
  console.log("\nstylesheet:");
  const cssText = fs.readFileSync(path.join(__dirname, "..", "media", "workbench.css"), "utf8");

  // 1. A comment that ends early. `.ghost-*/.preview-*` inside prose closes the comment at the
  // `*/`, and the words after it become a selector that swallows the NEXT rule's whole block. That
  // is what killed `.preview-context-pill { position: absolute }` for a month: the pill became a
  // grid item, claimed the inspector's column, and the pane relaid out around it every time
  // somebody hovered a checkpoint.
  const stripped = [];
  for (let i = 0, line = 1; i < cssText.length; ) {
    if (cssText.startsWith("/*", i)) {
      const j = cssText.indexOf("*/", i + 2);
      if (j < 0) { stripped.push(["\u0000unterminated", line]); break; }
      line += cssText.slice(i, j + 2).split("\n").length - 1;
      i = j + 2;
      continue;
    }
    if (cssText[i] === "\n") line++;
    stripped.push([cssText[i], line]);
    i++;
  }
  const strays = stripped
    .map(([c, line], k) => (c === "*" && (stripped[k + 1] || [])[0] === "/" ? line : 0))
    .filter(Boolean);
  check("no comment ends early (a stray */ eats the next rule)", strays.length === 0,
    `line(s) ${strays.join(", ")}`);

  // 2. A rule that declares a property AND composes a keyframe that animates the same property.
  // The animation sits in a higher cascade origin than a normal author declaration, so the
  // declaration is decoration -- which is how an ARRIVING chapter came to be drawn at the fill of a
  // leaving one, borrowing a pulse written for a much fainter state. Not `fill-opacity` only: three
  // of the rules this catches are one property rename away from the same accident in `opacity`,
  // `stroke-width` or `stroke-dashoffset`, so every plain-numeric property is compared.
  //
  // Overlapping IS the idiom, and the band is what expresses that: reduced motion strips the
  // animation and the declared value is what remains on screen, so it has to be the value the
  // motion was breathing around. A declaration INSIDE its keyframe's range is therefore correct and
  // a declaration outside it is the bug. A keyframe that names a property once (a one-way drift
  // with an implicit `from`) is skipped: there the declaration legitimately IS the starting value.
  const declText = stripped.map(([c]) => c).join("");
  const num = /(?:^|[;{\s])([a-z-]+):\s*(-?[\d.]+)\s*(?=[;}]|$)/g;
  const frames = new Map();
  for (const m of declText.matchAll(/@keyframes\s+([\w-]+)\s*\{([\s\S]*?)\n\}/g)) {
    const bands = new Map();
    for (const d of m[2].matchAll(num)) {
      const seen = bands.get(d[1]) || [];
      seen.push(Number(d[2]));
      bands.set(d[1], seen);
    }
    frames.set(m[1], bands);
  }
  const overridden = [];
  for (const m of declText.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const sel = m[1].trim(), body = m[2];
    if (sel.startsWith("@") || sel.includes("%")) continue;
    const named = (body.match(/animation:\s*([\s\S]*?);/) || ["", ""])[1]
      .split(",").map((a) => a.trim().split(/\s+/)[0]).filter(Boolean);
    if (!named.length) continue;
    for (const d of body.matchAll(num)) {
      const prop = d[1], v = Number(d[2]);
      for (const name of named) {
        const vals = (frames.get(name) || new Map()).get(prop);
        if (!vals || new Set(vals).size < 2) continue; // no oscillation to be inside of
        const lo = Math.min(...vals), hi = Math.max(...vals);
        if (v < lo || v > hi) {
          overridden.push(`${sel} declares ${prop}: ${v} but ${name} animates it ${lo}-${hi}`);
        }
      }
    }
  }
  check("no rule's declared value is thrown away by the keyframe it composes",
    overridden.length === 0, overridden.join("; "));

  // 3. The same selector written twice at the TOP level, both times setting the same property.
  // The later copy wins on order regardless of which one the author was looking at -- a duplicate
  // `.preview-context-pill[hidden]` set `display: none` under one that set `display: block` for a
  // fade, so the fade had never once played. Top level only, and by design: redefining a property
  // for the same selector inside a `@media`/`@container` block is how this file does responsive
  // layout and reduced motion, and that is the opposite of a mistake.
  const props = new Map();
  {
    let depth = 0, prelude = "", atDepth = 0, sel = null;
    for (let i = 0; i < declText.length; i++) {
      const ch = declText[i];
      if (ch === "{") {
        depth++;
        const head = prelude.trim().replace(/\s+/g, " ");
        prelude = "";
        if (depth === 1 && head.startsWith("@")) { atDepth = 1; sel = null; continue; }
        // A rule is top-level when nothing encloses it, or when only an at-rule does.
        sel = depth === 1 || (depth === 2 && atDepth === 1) ? head : null;
        if (depth === 2 && atDepth === 1) sel = null; // inside a media/container query: skip
        continue;
      }
      if (ch === "}") {
        if (depth === 1) atDepth = 0;
        depth--;
        prelude = "";
        sel = null;
        continue;
      }
      if (depth === 1 && sel) {
        // Collect the declaration block one char at a time, splitting on ";".
        const semi = declText.indexOf(";", i);
        const close = declText.indexOf("}", i);
        if (semi === -1 || (close !== -1 && close < semi)) { i = close - 1; continue; }
        const prop = declText.slice(i, semi).split(":")[0].trim();
        if (prop && !prop.startsWith("/")) {
          const key = `${sel} :: ${prop}`;
          props.set(key, (props.get(key) || 0) + 1);
        }
        i = semi;
        continue;
      }
      if (depth === 0) prelude += ch;
    }
  }
  const clashes = [...props].filter(([, n]) => n > 1).map(([k]) => k);
  check("no top-level selector sets the same property in two rules", clashes.length === 0,
    clashes.join("; "));

  console.log(failures === 0 ? "\nSMOKE OK" : `\nSMOKE FAILED (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
} catch (e) {
  console.error("SMOKE THREW:", e && e.stack || e);
  process.exit(1);
}
