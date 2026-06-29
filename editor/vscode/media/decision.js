// Decision Graph webview — a commit-graph rail. Each decision is one row (newest/HEAD on top); the
// left rail draws the node as a dot at its (lane, row), and dependency connectors are STRAIGHT
// segments — a vertical run down the child's lane plus one straight diagonal into the parent, like
// git log — over a transitively-reduced edge set so only the most direct dependency is ever drawn.
// The row body to the right carries the decision title + meta, lineage-view style. The CSP forbids
// external scripts, so the graph is hand-rolled SVG; anime.js is vendored locally and loaded with a nonce.
//
// Status is NEVER hue — hue is the feature's identity (the color contract). Status comes from the
// explicit `status` field and is encoded by the dot glyph: planned = soft hollow ring, landed =
// filled dim, in_force = filled + halo, selected = accent ring + incident edges lit (accent dashed),
// clash = red marker, in-process (live activity touching the footprint) = rotating dashed ring.

const vscode = acquireVsCodeApi();
const NS = "http://www.w3.org/2000/svg";

let state = null; // the decision_graph_view payload
let headId = null; // the anchored top node (named head, or the derived visual head) — set per render
let selected = null; // selected decision id
let sig = ""; // signature of the last-rendered graph (skip rebuild on selection-only changes)
let preview = null; // { kind, drop:Set, force:Set } — client-side in-graph action preview
const inProcess = new Map(); // decision id -> expiry timestamp (driven by live agent activity)
const spinners = new Map(); // decision id -> anime instance for the rotating ring
let activity = []; // recent agent activity events (ephemeral presence)
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ─── OKLCH identity color ─────────────────────────────────────────────────────────────────────
// Mirrored byte-for-byte from editor/vscode/src/color.ts and sgt/tui/color.py (the webview can't
// import across the bundle boundary). tests/test_color_parity.py slices the block below.
const GOLDEN = 0.618033988749895;
function hashId(id) {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
function oklchToHex(L, C, hDeg) {
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
  const lr = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const lb = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;
  const g = (x) => {
    const c = x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
    return Math.round(Math.max(0, Math.min(1, c)) * 255);
  };
  return "#" + [g(lr), g(lg), g(lb)].map((c) => c.toString(16).padStart(2, "0")).join("");
}
function colorFor(id) {
  if (!id) return "#888888";
  return oklchToHex(0.72, 0.13, ((hashId(id) * GOLDEN) % 1) * 360);
}

// ─── Layout engine ────────────────────────────────────────────────────────────────────────────
// Pure: takes the graph payload, returns row/lane assignments. No DOM, no color, no anime — so it
// can be sliced out and exercised under node (tests/test_decision_layout.py). The contract:
//   • one row per decision, newest landing on top; ties broken by a stable topological order over
//     the dependency edges (a node sits above the decisions it builds on), then feature, then id;
//   • each feature occupies exactly one lane (column) across the full row-span it touches, so its
//     decisions are a straight vertical line — and lanes are reused by features whose spans don't
//     overlap (greedy interval-graph coloring), giving the minimum column count with no overlap.
// `opts.avoidCrossings` switches lane assignment from minimal-column interval-coloring to the
// crossing-reducing variant below: features are packed in dependency order (a feature and the
// feature it builds on land in adjacent lanes, so the connector is short with nothing between), and
// a forbidden-lane test rejects any lane that would route an edge over an intervening dot, opening
// a fresh column instead. This trades a tidy column count for spear-free routing — the GitKraken /
// "forbidden set" tradeoff from pvigier's commit-graph notes, ported to our feature-spine model.
function computeLayout(graph, opts) {
  const avoidCrossings = !!(opts && opts.avoidCrossings);
  const decisions = (graph.decisions || []).slice();
  const idSet = new Set(decisions.map((d) => d.id));
  // Transitive reduction over the builds-on DAG: drop A→C whenever a longer A→…→C builds-on path
  // already exists, so only the most direct dependency is ever drawn or routed. Those redundant
  // "skip" edges are exactly the long ones that spear intervening lanes, so dropping them is what
  // keeps the rail git-log clean. Computed once here so the layout (lane packing + spear test) and
  // the renderer share one edge set. revises/fork lineage is never reduced — it is the stored spine.
  const edges = reduceBuildsOn(graph.edges || [], idSet);

  // Topological rank within an equal-landing group: an edge src→dst means src depends on dst, so
  // dst should sit lower (older) than src. We give every node a depth = longest dependency chain
  // beneath it; deeper (more depended-upon) nodes get a higher rank so they sort toward the bottom.
  // This only ever discriminates between decisions that share a landing — landing dominates first.
  const deps = {}; // id -> [ids it depends on]
  for (const d of decisions) deps[d.id] = [];
  for (const e of edges) {
    if (idSet.has(e.src) && idSet.has(e.dst) && e.src !== e.dst) deps[e.src].push(e.dst);
  }
  const depthMemo = {};
  function depth(id, seen) {
    if (depthMemo[id] !== undefined) return depthMemo[id];
    if (seen.has(id)) return 0; // cycle guard (derived edges should be acyclic, but be safe)
    seen.add(id);
    let d = 0;
    for (const dn of deps[id]) d = Math.max(d, 1 + depth(dn, seen));
    seen.delete(id);
    return (depthMemo[id] = d);
  }
  for (const d of decisions) depth(d.id, new Set());

  // Row order. Default: newest landing on top, with dependency depth breaking ties so that within a
  // single landing the integrator (the most-depended-upon node — what a human reads as HEAD) floats
  // to the TOP, never sinking under the leaves it builds on. (Real planned decisions already carry
  // distinct dependency-topological landings, so the integrator is the newest; this tiebreak is what
  // keeps an equal-landing cohort right-side-up instead of upside-down.) Then feature, then id.
  // When the projection names a primary `head` (the in-force integrator) the order is instead rooted
  // there: HEAD first, then a DFS over the things it builds on (nearest/newest dependency just
  // beneath), then anything unreachable by landing — a fan becomes a rooted tree with HEAD on top,
  // while a spine graph is unchanged. See docs/design/2026-06-25-decision-graph-layout.md.
  const byLanding = (a, b) =>
    b.landing - a.landing ||
    depthMemo[b.id] - depthMemo[a.id] ||
    (a.feature < b.feature ? -1 : a.feature > b.feature ? 1 : 0) ||
    (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const head = graph.head;
  let ordered;
  if (head && idSet.has(head)) {
    const landingOf = {};
    for (const d of decisions) landingOf[d.id] = d.landing;
    const seen = new Set();
    ordered = [];
    const dfs = (id) => {
      if (seen.has(id)) return;
      seen.add(id);
      ordered.push(id);
      for (const dn of deps[id].slice().sort((a, b) => landingOf[b] - landingOf[a])) dfs(dn);
    };
    dfs(head);
    for (const d of decisions.slice().sort(byLanding)) if (!seen.has(d.id)) ordered.push(d.id);
  } else {
    ordered = decisions.slice().sort(byLanding).map((d) => d.id);
  }
  const rowOf = {};
  ordered.forEach((id, i) => (rowOf[id] = i));
  decisions.sort((a, b) => rowOf[a.id] - rowOf[b.id]);

  // Each feature's inclusive row-span (top = newest decision's row, bot = oldest's row). A feature
  // reserves its whole span even across rows owned by other features (the spine passes behind them).
  const span = {};
  for (const d of decisions) {
    const r = rowOf[d.id];
    const s = span[d.feature];
    if (!s) span[d.feature] = { top: r, bot: r };
    else { s.top = Math.min(s.top, r); s.bot = Math.max(s.bot, r); }
  }

  // Visitation order for lane packing. Baseline: features by top row (gives minimal columns).
  // avoidCrossings: a dependency-first DFS — emit each feature immediately before the features it
  // builds on, so a dependent and its dependency are packed into neighbouring lanes and their
  // connector spans no intervening lane. Roots and ties fall back to top-row order for determinism.
  const byTop = (a, b) => span[a].top - span[b].top || (a < b ? -1 : 1);
  let order = Object.keys(span).sort(byTop);
  const featureOf = {};
  for (const d of decisions) featureOf[d.id] = d.feature;
  if (avoidCrossings) {
    const featDeps = {}; // feature -> features it builds on
    for (const f in span) featDeps[f] = new Set();
    for (const e of edges) {
      const af = featureOf[e.src], bf = featureOf[e.dst];
      if (af !== undefined && bf !== undefined && af !== bf) featDeps[af].add(bf);
    }
    const seen = new Set(), dfs = [];
    const visit = (f) => {
      if (seen.has(f)) return;
      seen.add(f); dfs.push(f);
      for (const d of [...featDeps[f]].sort(byTop)) visit(d);
    };
    for (const r of order) visit(r);
    order = dfs;
  }

  // Per-feature rows, and a growing record of placed dots/edges so the forbidden-lane test can ask:
  // would putting feature f at lane L route an existing edge over one of f's dots, or one of f's
  // edges over an already-placed dot? A "spear" is a dot strictly inside an edge's (lane, row) box.
  const rowsByFeature = {};
  for (const d of decisions) (rowsByFeature[d.feature] ||= []).push(rowOf[d.id]);
  const placedDots = [], placedEdges = [];
  function wouldSpear(lane, f) {
    for (const r of rowsByFeature[f])
      for (const E of placedEdges)
        if (E.lo < lane && lane < E.hi && E.r0 < r && r < E.r1) return true;
    for (const e of edges) {
      const af = featureOf[e.src], bf = featureOf[e.dst];
      if (af === undefined || bf === undefined || af === bf) continue;
      let oLane, myRow, oRow;
      if (af === f && laneOf[bf] !== undefined) { oLane = laneOf[bf]; myRow = rowOf[e.src]; oRow = rowOf[e.dst]; }
      else if (bf === f && laneOf[af] !== undefined) { oLane = laneOf[af]; myRow = rowOf[e.dst]; oRow = rowOf[e.src]; }
      else continue;
      const lo = Math.min(lane, oLane), hi = Math.max(lane, oLane);
      const r0 = Math.min(myRow, oRow), r1 = Math.max(myRow, oRow);
      for (const D of placedDots)
        if (lo < D.lane && D.lane < hi && r0 < D.row && D.row < r1) return true;
    }
    return false;
  }

  const laneBot = []; // laneBot[i] = bottom row currently occupying lane i
  const laneOf = {};

  // Fan-bus collapse. A fan/star (many leaf capabilities feeding one integrator) otherwise gets
  // packed by interval-coloring into ONE column, where every feeder→HEAD edge hides as a vertical
  // behind the intervening dots (the "straight line, can't see edges" failure). When the projection
  // names a HEAD, pin its feature to lane 0 and gather its pure-leaf feeders — single-decision
  // features HEAD builds on, that nothing else builds on and that build on nothing — into ONE shared
  // adjacent "bus" lane, so each feeder→HEAD connector becomes a visible short curve and width stays
  // at O(spines)+1 regardless of how many feeders there are.
  if (head && idSet.has(head)) {
    const headFeat = featureOf[head];
    const out = {}, inc = {};
    for (const e of edges) {
      if (e.type !== "builds-on") continue;
      const af = featureOf[e.src], bf = featureOf[e.dst];
      if (af === undefined || bf === undefined || af === bf) continue;
      (out[af] ||= new Set()).add(bf);
      (inc[bf] ||= new Set()).add(af);
    }
    const feeders = [];
    for (const f of out[headFeat] || []) {
      if (f === headFeat || (rowsByFeature[f] || []).length !== 1) continue;
      if ((out[f] || new Set()).size !== 0) continue; // builds on nothing itself (a pure leaf)
      const fi = inc[f] || new Set();
      if (fi.size === 1 && fi.has(headFeat)) feeders.push(f); // only HEAD builds on it
    }
    if (feeders.length >= 2) {
      laneOf[headFeat] = 0;
      laneBot[0] = span[headFeat].bot;
      let busBot = -Infinity;
      for (const f of feeders) { laneOf[f] = 1; busBot = Math.max(busBot, span[f].bot); }
      laneBot[1] = busBot;
      if (avoidCrossings)
        for (const f of [headFeat, ...feeders])
          for (const r of rowsByFeature[f]) placedDots.push({ row: r, lane: laneOf[f] });
    }
  }

  // Lane-adjacency: the mean lane of f's already-placed builds-on neighbours (either direction). When
  // several lanes are equally valid we pick the one closest to this target so a dependent sits next to
  // its dependency and the connector is a short curve — never opening a new column to do it (column
  // count is unchanged; only the choice *among* existing valid lanes changes).
  function neighborLane(f) {
    let sum = 0, n = 0;
    for (const e of edges) {
      if (e.type !== "builds-on" && e.type !== "revises") continue;
      const af = featureOf[e.src], bf = featureOf[e.dst];
      if (af === undefined || bf === undefined || af === bf) continue;
      const other = af === f ? bf : bf === f ? af : null;
      if (other !== null && laneOf[other] !== undefined) { sum += laneOf[other]; n++; }
    }
    return n ? sum / n : null;
  }

  for (const f of order) {
    if (laneOf[f] !== undefined) continue; // pinned by the fan-bus pass above
    const s = span[f];
    const valid = [];
    for (let L = 0; L < laneBot.length; L++) {
      if (laneBot[L] >= s.top) continue;            // occupied
      if (avoidCrossings && wouldSpear(L, f)) continue;
      valid.push(L);
    }
    const target = avoidCrossings ? neighborLane(f) : null;
    let lane;
    if (!valid.length) {
      lane = laneBot.length;                        // every existing lane is taken/spears — new column
    } else if (target !== null) {
      lane = valid.reduce((best, L) =>
        Math.abs(L - target) < Math.abs(best - target) ||
        (Math.abs(L - target) === Math.abs(best - target) && L < best) ? L : best, valid[0]);
    } else {
      lane = valid[0];                              // baseline: lowest free lane (minimal columns)
    }
    laneBot[lane] = s.bot;
    laneOf[f] = lane;
    if (avoidCrossings) {
      for (const r of rowsByFeature[f]) placedDots.push({ row: r, lane });
      for (const e of edges) {
        const af = featureOf[e.src], bf = featureOf[e.dst];
        if (af === undefined || bf === undefined || af === bf) continue;
        const other = af === f ? bf : bf === f ? af : null;
        if (other === null || laneOf[other] === undefined) continue; // record only once both ends land
        placedEdges.push({
          lo: Math.min(laneOf[af], laneOf[bf]), hi: Math.max(laneOf[af], laneOf[bf]),
          r0: Math.min(rowOf[e.src], rowOf[e.dst]), r1: Math.max(rowOf[e.src], rowOf[e.dst]),
        });
      }
    }
  }

  const pos = {};
  for (const d of decisions) pos[d.id] = { row: rowOf[d.id], lane: laneOf[d.feature] };
  // `head` is the anchored top node; `edges` is the transitively-reduced set the renderer draws.
  return { decisions, rowOf, laneOf, span, pos, head, edges, laneCount: Math.max(1, laneBot.length) };
}

// Transitive reduction of the builds-on DAG: keep an edge A→B only when B is NOT reachable from A
// through some other child of A (i.e. A→B is the direct link, not a shortcut implied by A→…→B).
// Only builds-on edges are reduced; revises/fork lineage passes through untouched. Returns a new
// array preserving the input edges minus the redundant builds-on shortcuts.
function reduceBuildsOn(all, idSet) {
  const adj = {}; // builds-on adjacency among present decisions
  for (const e of all)
    if (e.type === "builds-on" && idSet.has(e.src) && idSet.has(e.dst) && e.src !== e.dst)
      (adj[e.src] ||= []).push(e.dst);
  const memo = {};
  function reach(n) {
    if (memo[n]) return memo[n];
    memo[n] = new Set(); // placeholder breaks cycles (a back-edge contributes no extra reach)
    const r = new Set();
    for (const m of adj[n] || []) { r.add(m); for (const x of reach(m)) r.add(x); }
    return (memo[n] = r);
  }
  const redundant = new Set();
  for (const a in adj) {
    const kids = adj[a];
    for (const b of kids)
      for (const c of kids)
        if (c !== b && reach(c).has(b)) { redundant.add(a + "	" + b); break; }
  }
  return all.filter((e) => !(e.type === "builds-on" && redundant.has(e.src + "	" + e.dst)));
}
// ---- end-layout (test slice boundary) ----

// ─── Geometry (driven by the compactness control) ──────────────────────────────────────────────
// Three density steps the header segmented control cycles through; persisted in vscode state so the
// graph reopens at the chosen compaction. row + lane spacing scale together so the lineage stays
// readable at every step.
const DENSITY = {
  airy:    { row: 38, lane: 26 },
  default: { row: 30, lane: 20 },
  compact: { row: 22, lane: 15 },
};
let density = (vscode.getState() || {}).density || "default";
// avoid-crossings lane packing: spear-free routing is the better default now that planned nodes
// carry real builds-on edges; honour a persisted off-toggle, but default ON when unset.
let spread = (() => { const s = (vscode.getState() || {}).spread; return s === undefined ? true : s; })();
const RAIL_PAD = 16, NODE_R = 5;
const rowH = () => DENSITY[density].row;
const laneW = () => DENSITY[density].lane;
const railWidth = (laneCount) => RAIL_PAD + (laneCount - 1) * laneW() + RAIL_PAD;
const laneX = (lane) => RAIL_PAD + lane * laneW();
const rowY = (row) => row * rowH() + rowH() / 2;

// A dependency connector between a child dot (x1,y1) and the parent it builds on (x2,y2). Routed
// with STRAIGHT segments only — no Béziers — exactly like a commit graph (git log / Git Graph):
//   • same lane → one dead-straight vertical line through the dots (a mainline);
//   • different lanes → a straight run down the child's own lane, then a single straight diagonal
//     into the parent. The lane change happens adjacent to the parent (one row away), so the long
//     part of the edge is a clean vertical and only the short joint is diagonal. When the two dots
//     are already one row apart the run collapses and it is just one straight diagonal.
function edgePath(x1, y1, x2, y2) {
  if (Math.abs(x2 - x1) < 1) return `M${x1} ${y1} L${x2} ${y2}`;
  const down = y2 > y1;
  const bendY = down ? y2 - rowH() / 2 : y2 + rowH() / 2; // turn one row short of the parent
  if ((down && bendY <= y1) || (!down && bendY >= y1)) return `M${x1} ${y1} L${x2} ${y2}`;
  return `M${x1} ${y1} L${x1} ${bendY} L${x2} ${y2}`;
}

// ─── DOM handles ──────────────────────────────────────────────────────────────────────────────
const list = document.getElementById("list");
const rail = document.getElementById("rail");
const rowsEl = document.getElementById("rows");
const detail = document.getElementById("detail");
const main = document.getElementById("main");
const handle = document.getElementById("handle");
const feed = document.getElementById("feed");
const menu = document.getElementById("menu");
const densityBtns = Array.from(document.querySelectorAll("#density .seg"));

document.getElementById("refresh").onclick = () => vscode.postMessage({ type: "ready" });
document.getElementById("toggle-detail").onclick = () => {
  document.body.classList.toggle("no-detail");
  saveUi();
};
document.getElementById("toggle-feed").onclick = () => {
  document.body.classList.toggle("show-feed");
  saveUi();
};
document.getElementById("toggle-spread").onclick = (e) => {
  spread = !spread;
  e.currentTarget.classList.toggle("on", spread);
  saveUi();
  if (state) { renderGraph(); applySelection(); }
};
document.addEventListener("click", () => hideMenu());

for (const b of densityBtns) b.onclick = () => setDensity(b.dataset.d);
function setDensity(d) {
  density = d;
  for (const b of densityBtns) b.classList.toggle("on", b.dataset.d === d);
  saveUi();
  if (state) { renderGraph(); applySelection(); }
}

// ─── Persisted UI state ─────────────────────────────────────────────────────────────────────────
function restoreUi() {
  const s = vscode.getState() || {};
  for (const b of densityBtns) b.classList.toggle("on", b.dataset.d === density);
  document.getElementById("toggle-spread").classList.toggle("on", spread);
  if (s.detailW) detail.style.width = s.detailW + "px";
  if (s.noDetail) document.body.classList.add("no-detail");
  if (s.showFeed === false) document.body.classList.remove("show-feed");
}
function saveUi() {
  const s = vscode.getState() || {};
  vscode.setState({
    ...s,
    density,
    spread,
    detailW: parseInt(detail.style.width, 10) || s.detailW,
    noDetail: document.body.classList.contains("no-detail"),
    showFeed: document.body.classList.contains("show-feed"),
  });
}

// ─── Resizable detail divider ───────────────────────────────────────────────────────────────────
// A 1px hairline grip between stage and detail; drag adjusts the detail width and persists it.
(function wireHandle() {
  let dragging = false;
  handle.addEventListener("mousedown", (e) => {
    dragging = true;
    document.body.classList.add("resizing");
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const right = main.getBoundingClientRect().right;
    const w = Math.max(220, Math.min(560, right - e.clientX));
    detail.style.width = w + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("resizing");
    saveUi();
  });
})();

window.addEventListener("message", (e) => {
  const m = e.data;
  if (m.type === "decisions") {
    state = m.graph;
    if (m.select) selected = m.select;
    update();
  } else if (m.type === "select") {
    selected = m.id;
    applySelection();
    scrollToSelected();
  } else if (m.type === "activity") {
    ingestActivity(m.events || []);
  } else if (m.type === "error") {
    detail.innerHTML = `<div class="err">${esc(m.message)}</div>`;
  }
});

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// ─── Status helpers (drive glyphs from the explicit status field, never commits.length) ─────────
function statusOf(d) {
  if (d.status === "in_force" || d.status === "landed" || d.status === "planned") return d.status;
  // Defensive fallback if a payload omits status: infer from commits + frontier.
  if (state && Object.values(state.frontier || {}).includes(d.id)) return "in_force";
  return (d.commits || []).length ? "landed" : "planned";
}

// ─── Render orchestration ─────────────────────────────────────────────────────────────────────
// Rebuild the rail+rows only when the graph itself changes; selection / preview / activity are
// cheap class toggles so anime entry animations don't re-fire on every cursor move.
function update() {
  if (!state) return;
  const decisions = state.decisions || [];
  document.getElementById("count").textContent =
    `${decisions.length} decision${decisions.length === 1 ? "" : "s"}`;
  const head = Object.values(state.frontier || {}).length;
  const chip = document.getElementById("head");
  chip.textContent = head ? `${head} in force` : "";
  chip.style.display = head ? "" : "none";

  const newSig = JSON.stringify(decisions.map((d) => [d.id, d.feature, d.landing, statusOf(d)]))
    + "|" + JSON.stringify(state.frontier || {}) + "|" + density + "|" + spread
    + "|" + (state.head || "") + "|" + (state.edges || []).map((e) => e.src + ">" + e.dst + e.type).join(",");
  if (newSig !== sig) { sig = newSig; renderGraph(); }
  applySelection();
}

function renderGraph() {
  const L = computeLayout(state, { avoidCrossings: spread });
  // The anchored top node: the named in-force HEAD, or the layout's derived visual head for a graph
  // with nothing in force (a fresh plan). Every HEAD marker reads from this, so HEAD is emphasized on
  // top in both cases.
  headId = state.head || L.head;
  const empty = document.getElementById("empty-state");
  empty.style.display = L.decisions.length ? "none" : "flex";

  // Connectors stay inside the lanes they join (straight verticals + a single straight diagonal joint),
  // so the rail needs no extra right-margin bulge room.
  const byId = new Map(L.decisions.map((d) => [d.id, d]));
  const rw = railWidth(L.laneCount);
  const totalH = Math.max(rowH(), L.decisions.length * rowH());
  list.style.setProperty("--rail-w", rw + "px");
  list.style.setProperty("--row-h", rowH() + "px");
  rail.setAttribute("viewBox", `0 0 ${rw} ${totalH}`);
  rail.setAttribute("width", rw);
  rail.setAttribute("height", totalH);
  while (rail.firstChild) rail.removeChild(rail.firstChild);
  rowsEl.innerHTML = "";

  const inForce = new Set(Object.values(state.frontier || {}));
  const decsByFeature = {};
  for (const d of L.decisions) (decsByFeature[d.feature] ||= []).push(d);

  // 1) Feature spines — one straight vertical line per feature across its full row-span (a feature's
  //    own revise lineage reads as a single column, lineage-view style).
  for (const f in decsByFeature) {
    const ds = decsByFeature[f].sort((a, b) => L.pos[a.id].row - L.pos[b.id].row);
    const x = laneX(L.pos[ds[0].id].lane);
    const y1 = rowY(L.pos[ds[0].id].row), y2 = rowY(L.pos[ds[ds.length - 1].id].row);
    if (y2 > y1) rail.appendChild(path(`M${x} ${y1} L${x} ${y2}`, "spine", { stroke: colorFor(f) }));
  }

  // 2) Dependency connectors: fork lineage across lanes and faint derived builds-on links, drawn from
  //    the transitively-reduced edge set (L.edges) so only the most direct dependency shows. Each is
  //    routed by edgePath() as straight segments — a vertical run in the child's lane plus one
  //    straight diagonal into the parent — so it never balloons sideways into the rows it spans.
  for (const e of L.edges || []) {
    const a = byId.get(e.src), b = byId.get(e.dst);
    if (!a || !b) continue;
    if (a.feature === b.feature) continue; // intra-feature revise lineage is the spine already
    const pa = L.pos[a.id], pb = L.pos[b.id];
    const x1 = laneX(pa.lane), y1 = rowY(pa.row), x2 = laneX(pb.lane), y2 = rowY(pb.row);
    const d = edgePath(x1, y1, x2, y2);
    const cls = e.type === "builds-on" ? "link builds-on" : "link fork";
    const el = path(d, cls, { stroke: colorFor(a.feature) });
    el.dataset.src = a.id; el.dataset.dst = b.id;
    rail.appendChild(el);
  }

  // 3) Clash markers — two in-force decisions that touch the same entity.
  for (const c of state.clash || []) {
    const a = byId.get(c.a), b = byId.get(c.b);
    if (!a || !b) continue;
    const t = text(laneX(L.pos[a.id].lane), (rowY(L.pos[a.id].row) + rowY(L.pos[b.id].row)) / 2, "⚠", "clash");
    rail.appendChild(t);
  }

  // 4) Nodes + 5) row bodies.
  for (const d of L.decisions) {
    const p = L.pos[d.id];
    const col = colorFor(d.feature);
    const node = svgGroup("node", { transform: `translate(${laneX(p.lane)},${rowY(p.row)})` });
    node.dataset.id = d.id;
    paintGlyph(node, d, col);
    node.appendChild(circle(0, 0, NODE_R + 8, "hit", {}));
    node.onclick = (ev) => { ev.stopPropagation(); select(d.id); };
    node.oncontextmenu = (ev) => { ev.preventDefault(); ev.stopPropagation(); select(d.id); showMenu(ev, d); };
    node.onmouseenter = () => highlightLinks(d.id, true);
    node.onmouseleave = () => highlightLinks(d.id, false);
    rail.appendChild(node);

    rowsEl.appendChild(renderRow(d, col));
  }

  staggerIn();
  applyInProcess();
}

// Paint the status glyph for a node group. Hue = feature; the glyph shape = status. The primary
// HEAD (the integrator the projection names in `head`) gets an extra accent ring so the eye lands on
// "what the codebase currently is" — distinct from the per-lane in-force tips.
function paintGlyph(node, d, col) {
  const st = statusOf(d);
  if (headId === d.id) node.appendChild(circle(0, 0, NODE_R + 7, "headring", {}));
  if (st === "in_force") {
    node.appendChild(circle(0, 0, NODE_R + 4, "halo", { stroke: col }));
    node.appendChild(circle(0, 0, NODE_R, "disc", { stroke: col, fill: col }));
  } else if (st === "landed") {
    node.appendChild(circle(0, 0, NODE_R, "disc landed", { stroke: col, fill: col }));
  } else {
    node.appendChild(circle(0, 0, NODE_R, "disc planned", { stroke: col, fill: "var(--panel)" }));
  }
}

function renderRow(d, col) {
  const row = document.createElement("div");
  row.className = "row" + (selected === d.id ? " sel" : "");
  row.dataset.id = d.id;
  const intent = d.intent || {};
  // Title is the short human slug when present (the "5-word handle" for scanning the log); the full
  // decision sentence drops to the sub so it's still visible inline, else context/consequence.
  const title = intent.slug || intent.decision;
  const sub = (intent.slug && intent.decision) ? intent.decision : (intent.context || intent.consequence || "");
  const hash = (d.commits && d.commits[0] ? d.commits[0] : "").slice(0, 7);
  const lc = d.lifecycle || {};
  const tag = lc.kind && lc.kind !== "introduce" ? `<span class="lk">${esc(lc.kind)}</span>` : "";
  const headBadge = headId === d.id ? `<span class="headbadge" title="primary HEAD — the integrator">HEAD</span>` : "";
  row.innerHTML = `
    <span class="feat" style="--hue:${col}" title="${esc(d.feature)}"></span>
    <span class="title ${title ? "" : "muted"}">${esc(title || "Not distilled")}</span>
    ${headBadge}
    ${sub ? `<span class="sub">${esc(sub)}</span>` : ""}
    ${tag}
    <span class="spacer"></span>
    <span class="land" title="checkpoint ${d.landing}">@${d.landing}</span>
    ${hash ? `<span class="hash">${esc(hash)}</span>` : ""}`;
  row.onclick = () => select(d.id);
  row.oncontextmenu = (ev) => { ev.preventDefault(); select(d.id); showMenu(ev, d); };
  row.onmouseenter = () => { highlightLinks(d.id, true); highlightNode(d.id, true); };
  row.onmouseleave = () => { highlightLinks(d.id, false); highlightNode(d.id, false); };
  return row;
}

// ─── Selection & detail ───────────────────────────────────────────────────────────────────────
function select(id) {
  selected = id;
  applySelection();
}

function applySelection() {
  if (!state) return;
  for (const r of rowsEl.children) r.classList.toggle("sel", r.dataset.id === selected);
  for (const n of rail.querySelectorAll(".node")) n.classList.toggle("sel", n.dataset.id === selected);
  // Light the selected node's incident edges as accent dashed curves.
  for (const l of rail.querySelectorAll(".link"))
    l.classList.toggle("sel-link", selected != null && (l.dataset.src === selected || l.dataset.dst === selected));
  const d = (state.decisions || []).find((x) => x.id === selected);
  if (d) {
    showDetail(d);
    pulse(rail.querySelector(`.node.sel .disc`));
  } else {
    detail.innerHTML = `<div class="placeholder">Select a decision to read its rationale.</div>`;
  }
}

function scrollToSelected() {
  const r = rowsEl.querySelector(".row.sel");
  if (r) r.scrollIntoView({ block: "nearest" });
}

// The deterministic, faithful description: defines / uses / used-by, read straight from the entity
// call graph (sgt.api computes it offline). Distinct from the ADR prose below — this can't vibe.
function structureHtml(s) {
  if (!s) return "";
  const row = (label, arr) =>
    arr && arr.length
      ? `<div class="st-row"><span class="st-k">${label}</span><span class="st-v">${arr
          .map((x) => `<code class="ent">${esc(x)}</code>`)
          .join("")}</span></div>`
      : "";
  const body = row("Defines", s.defines) + row("Uses", s.uses) + row("Used&nbsp;by", s.used_by);
  return body ? `<section class="structure"><h4>Structure</h4>${body}</section>` : "";
}

function showDetail(d) {
  const st = statusOf(d);
  const it = d.intent || {};
  const alts = (d.alternatives || [])
    .map((a) =>
      `<div class="alt"><div class="alt-h"><span class="opt">${esc(a.option)}</span>
       ${a.source ? `<span class="src" title="rationale source">${esc(a.source)}</span>` : ""}</div>
       <span class="lose">${esc(a.why_rejected)}</span></div>`)
    .join("");
  const tx = (d.commits || []).map((c) => `<code class="c">${esc(c.slice(0, 7))}</code>`).join('<span class="arr">→</span>');
  const fp = (d.footprint || [])
    .map((k) => {
      const [file, target] = k.split("::");
      return `<button class="fp" data-file="${esc(file)}" data-target="${esc(target || "")}">
        <span class="fp-t">${esc(target || file)}</span><span class="fp-f">${esc(file)}</span></button>`;
    })
    .join("");
  const lc = d.lifecycle || {};
  const statusLine = st === "planned"
    ? `<span class="st planned"><span class="g">○</span> Planned</span>`
    : st === "in_force"
      ? `<span class="st force"><span class="g">●</span> In force</span><span class="st-note">materializes the working tree</span>`
      : `<span class="st landed"><span class="g">●</span> Landed</span><span class="st-note">recorded, not in force</span>`;
  const notDistilled = !it.context && !it.consequence;

  detail.innerHTML = `
    <header class="dh">
      <span class="dot" style="background:${colorFor(d.feature)}"></span>
      <div class="dh-t"><b>${esc(it.slug || it.decision || d.id)}</b>
        <span class="meta">${esc(d.feature)} · @${d.landing}${lc.of ? ` · ${esc(lc.kind)} of ${esc(lc.of)}` : ""}</span>
      </div>
    </header>
    <div class="status">${statusLine}</div>
    ${structureHtml(d.structure)}
    <section class="adr">
      <div class="adr-row"><span class="k">Context</span><span class="v ${it.context ? "" : "muted"}">${esc(it.context) || "Not distilled"}</span></div>
      <div class="adr-row"><span class="k">Decision</span><span class="v">${esc(it.decision) || "Not distilled"}</span></div>
      <div class="adr-row"><span class="k">Consequence</span><span class="v ${it.consequence ? "" : "muted"}">${esc(it.consequence) || "Not distilled"}</span></div>
      ${notDistilled ? `<button class="distill" data-act="distill"><span class="spark">✦</span> Distill rationale</button>` : ""}
    </section>
    ${alts ? `<section><h4>Alternatives weighed</h4><div class="alts">${alts}</div></section>` : ""}
    <section><h4>Git transaction</h4><div class="txn">${tx || '<span class="muted">No commits yet.</span>'}</div></section>
    <section><h4>Footprint</h4><div class="fps">${fp || '<span class="muted">No footprint.</span>'}</div></section>
    <footer class="actions">
      ${st === "in_force" ? "" : `<button class="act primary" data-act="pin">Pin to HEAD</button>`}
      <button class="act ${st === "in_force" ? "" : "primary"}" data-act="${st === "in_force" ? "suspend" : "restore"}">${st === "in_force" ? "Suspend" : "Restore"}</button>
      <button class="act danger" data-act="revert">Revert</button>
    </footer>
    <div class="difflink"><a data-act="preview-revert" tabindex="0">See file diff</a></div>`;

  for (const b of detail.querySelectorAll(".fp"))
    b.onclick = () => vscode.postMessage({ type: "reveal", file: b.dataset.file, target: b.dataset.target });
  // Hovering an action animates the in-graph preview; clicking performs it. The animated graph state
  // IS the preview — no card pops above the graph.
  for (const b of detail.querySelectorAll(".act")) {
    b.onclick = () => runAction(b.dataset.act, d);
    b.onmouseenter = () => previewAction(b.dataset.act, d);
    b.onmouseleave = () => clearPreview();
  }
  const diff = detail.querySelector(".difflink a");
  if (diff) {
    diff.onclick = () => runAction("preview-revert", d);
    diff.onkeydown = (e) => { if (e.key === "Enter") runAction("preview-revert", d); };
  }
  const dist = detail.querySelector(".distill");
  if (dist) dist.onclick = () => { dist.classList.add("busy"); dist.innerHTML = '<span class="spark">✦</span> Distilling'; runAction("distill", d); };
}

// ─── Context menu ─────────────────────────────────────────────────────────────────────────────
function showMenu(ev, d) {
  const force = statusOf(d) === "in_force";
  const items = [
    { act: "inspect", label: "Inspect" },
    { sep: true },
    !force && { act: "pin", label: "Pin to HEAD" },
    force ? { act: "suspend", label: "Suspend" } : { act: "restore", label: "Restore" },
    { sep: true },
    { act: "revert", label: "Revert (plug out)", danger: true },
    { act: "preview-revert", label: "See file diff" },
  ].filter(Boolean);
  menu.innerHTML = items
    .map((i) => (i.sep ? `<div class="msep"></div>` : `<div class="mi ${i.danger ? "danger" : ""}" data-act="${i.act}">${i.label}</div>`))
    .join("");
  for (const el of menu.querySelectorAll(".mi")) {
    el.onclick = (e) => { e.stopPropagation(); hideMenu(); runAction(el.dataset.act, d); };
    el.onmouseenter = () => previewAction(el.dataset.act, d);
    el.onmouseleave = () => clearPreview();
  }
  const pad = 6, mw = 210;
  menu.style.display = "block";
  menu.style.left = Math.min(ev.clientX, window.innerWidth - mw - pad) + "px";
  menu.style.top = Math.min(ev.clientY, window.innerHeight - menu.offsetHeight - pad) + "px";
}
function hideMenu() { menu.style.display = "none"; clearPreview(); }

// ─── Actions + in-graph hover preview ───────────────────────────────────────────────────────────
// Preview is computed client-side so it is instant and lives entirely in the graph: pinning recomposes
// the frontier (the picked decision becomes in force, the feature's old tip drops), revert/suspend
// ghost the whole feature lane. The file-level dry-run ("See file diff") is delegated to the host.
function runAction(act, d) {
  clearPreview();
  switch (act) {
    case "inspect": select(d.id); break;
    case "distill": vscode.postMessage({ type: "distill", id: d.id }); break;
    case "pin": vscode.postMessage({ type: "compose", feature: d.feature, decision: d.id }); break;
    case "preview-revert": vscode.postMessage({ type: "command", id: "sgt.previewRevert", arg: d.feature }); break;
    case "revert": vscode.postMessage({ type: "command", id: "sgt.revert", arg: d.feature }); break;
    case "suspend": vscode.postMessage({ type: "command", id: "sgt.switchOff", arg: d.feature }); break;
    case "restore": vscode.postMessage({ type: "command", id: "sgt.switchOn", arg: d.feature }); break;
  }
}

function previewAction(act, d) {
  if (!state) return;
  const featDs = (state.decisions || []).filter((x) => x.feature === d.feature).map((x) => x.id);
  if (act === "pin" || act === "restore") {
    const tip = (state.frontier || {})[d.feature];
    preview = { kind: "pin", force: new Set([d.id]), drop: new Set(tip && tip !== d.id ? [tip] : []) };
  } else if (act === "revert" || act === "preview-revert" || act === "suspend") {
    preview = { kind: "drop", drop: new Set(featDs), force: new Set() };
  } else { clearPreview(); return; }
  paintPreview();
}
function clearPreview() {
  if (!preview) return;
  preview = null;
  for (const n of rail.querySelectorAll(".node")) n.classList.remove("pv-drop", "pv-force");
  for (const r of rowsEl.children) r.classList.remove("pv-drop", "pv-force");
}
function paintPreview() {
  if (!preview) return;
  const forced = [];
  for (const n of rail.querySelectorAll(".node")) {
    const drop = preview.drop.has(n.dataset.id);
    const force = preview.force.has(n.dataset.id);
    n.classList.toggle("pv-drop", drop);
    n.classList.toggle("pv-force", force);
    if (force) forced.push(n.querySelector(".disc"));
  }
  for (const r of rowsEl.children) {
    r.classList.toggle("pv-drop", preview.drop.has(r.dataset.id));
    r.classList.toggle("pv-force", preview.force.has(r.dataset.id));
  }
  // A small motivated pop on the node(s) that would become in force, so the eye lands on the change.
  if (!reduceMotion && forced.length)
    anime({ targets: forced, r: [NODE_R, NODE_R * 1.5, NODE_R], duration: 420, easing: "easeOutQuad" });
}

// Brighten a node's incident connectors on hover (the lineage-view "trace dependencies" gesture).
function highlightLinks(id, on) {
  for (const l of rail.querySelectorAll(".link"))
    l.classList.toggle("lit", on && (l.dataset.src === id || l.dataset.dst === id));
}
function highlightNode(id, on) {
  const n = rail.querySelector(`.node[data-id="${cssEsc(id)}"]`);
  if (n) n.classList.toggle("hover", on);
}
function cssEsc(s) {
  return window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&");
}

// ─── Live agent activity → feed + in-process node state ───────────────────────────────────────
function ingestActivity(events) {
  if (!events.length) return;
  for (const ev of events) {
    activity.push(ev);
    if (ev.kind === "tool" && ev.target) markInProcess(ev.target);
  }
  activity = activity.slice(-60);
  renderFeed(events);
}

function markInProcess(target) {
  if (!state) return;
  const base = String(target).split("/").pop();
  const until = Date.now() + 5000;
  for (const d of state.decisions || []) {
    if ((d.footprint || []).some((k) => k.split("::")[0].split("/").pop() === base)) {
      inProcess.set(d.id, until);
    }
  }
  applyInProcess();
  setTimeout(() => { sweepInProcess(); }, 5200);
}
function sweepInProcess() {
  const now = Date.now();
  for (const [id, t] of inProcess) if (t <= now) inProcess.delete(id);
  applyInProcess();
}
function applyInProcess() {
  const now = Date.now();
  for (const n of rail.querySelectorAll(".node")) {
    const id = n.dataset.id;
    const active = inProcess.has(id) && inProcess.get(id) > now;
    let ring = n.querySelector(".proc");
    if (active && !ring) {
      ring = circle(0, 0, NODE_R + 4, "proc", { stroke: colorFor(decFeature(id)) });
      n.appendChild(ring);
      if (!reduceMotion)
        spinners.set(id, anime({ targets: ring, rotate: 360, loop: true, duration: 1500, easing: "linear" }));
    } else if (!active && ring) {
      spinners.get(id)?.pause();
      spinners.delete(id);
      ring.remove();
    }
  }
}
function decFeature(id) {
  const d = (state.decisions || []).find((x) => x.id === id);
  return d ? d.feature : "";
}

function renderFeed(fresh) {
  for (const ev of fresh) {
    const el = document.createElement("div");
    el.className = "fe " + ev.kind;
    el.innerHTML = ev.kind === "tool"
      ? `<span class="ft">${esc(ev.name || "tool")}</span> <span class="fg">${esc(ev.target || "")}</span>`
      : `<span class="fth">${esc(ev.text || "")}</span>`;
    feed.appendChild(el);
    if (!reduceMotion) anime({ targets: el, opacity: [0, 1], translateX: [-6, 0], duration: 220, easing: "easeOutQuad" });
  }
  while (feed.children.length > 60) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
  document.getElementById("feed-dot").classList.add("live");
  clearTimeout(renderFeed._t);
  renderFeed._t = setTimeout(() => document.getElementById("feed-dot").classList.remove("live"), 2500);
}

// ─── anime.js transitions ─────────────────────────────────────────────────────────────────────
function staggerIn() {
  // Node groups are positioned by a `transform="translate(...)"` ATTRIBUTE; anime writes
  // `style.transform`, which overrides it — so we only ever animate opacity on the group, never a
  // transform (a scale/translate would collapse every node onto the origin). The "pop" lives on the
  // disc's radius instead, which is safe because the disc sits at the group's local 0,0.
  if (reduceMotion) return;
  anime({ targets: rail.querySelectorAll(".node"), opacity: [0, 1],
    delay: anime.stagger(10), duration: 240, easing: "easeOutQuad" });
  anime({ targets: rail.querySelectorAll(".node .disc"), r: [0, NODE_R],
    delay: anime.stagger(10), duration: 300, easing: "easeOutBack" });
  anime({ targets: rowsEl.querySelectorAll(".row"), opacity: [0, 1], translateX: [-8, 0],
    delay: anime.stagger(7), duration: 240, easing: "easeOutQuad" });
  for (const p of rail.querySelectorAll(".spine, .link")) {
    const len = p.getTotalLength ? p.getTotalLength() : 0;
    if (!len) continue;
    p.style.strokeDasharray = len;
    anime({ targets: p, strokeDashoffset: [len, 0], duration: 440, easing: "easeInOutSine",
      complete: () => { p.style.strokeDasharray = ""; } });
  }
}
function pulse(el) {
  // Animate the radius, not a CSS scale: a transform on an SVG <circle> has surprising origin
  // behaviour across transform-box defaults, whereas r is unambiguous.
  if (el && !reduceMotion) anime({ targets: el, r: [NODE_R, NODE_R * 1.6, NODE_R], duration: 360, easing: "easeOutQuad" });
}

// ─── SVG helpers ──────────────────────────────────────────────────────────────────────────────
function svgGroup(cls, attrs) {
  const g = document.createElementNS(NS, "g");
  g.setAttribute("class", cls);
  for (const k in attrs) g.setAttribute(k, attrs[k]);
  return g;
}
function path(d, cls, attrs) {
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", d); p.setAttribute("class", cls);
  for (const k in attrs) p.setAttribute(k, attrs[k]);
  return p;
}
function circle(cx, cy, r, cls, attrs) {
  const c = document.createElementNS(NS, "circle");
  c.setAttribute("cx", cx); c.setAttribute("cy", cy); c.setAttribute("r", r); c.setAttribute("class", cls);
  for (const k in attrs) c.setAttribute(k, attrs[k]);
  return c;
}
function text(x, y, s, cls) {
  const t = document.createElementNS(NS, "text");
  t.setAttribute("x", x); t.setAttribute("y", y); t.setAttribute("class", cls);
  t.textContent = s;
  return t;
}

restoreUi();
vscode.postMessage({ type: "ready" });
