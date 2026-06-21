// @ts-check
// Feature Graph — a GitLens/GitKraken-style commit graph, but for the semantic DAG. Each feature
// is a ROW (newest/most-derived on top); a refs column carries status+kind, a swim-lane GRAPH
// column draws the dependency lanes (git-style lane allocation + colored bezier connectors), and
// an intent column carries the message. A canvas minimap rides on top. Pure SVG/canvas, no deps.
//
// Identity color is the same OKLCH hash used in blame and the TUI (hue = identity). Status is a
// glyph + node shape, never the hue.

const vscode = acquireVsCodeApi();
const NS = "http://www.w3.org/2000/svg";
const GOLDEN = 0.618033988749895;

const ROW = 26; // row height (px)
const LANE_W = 18; // swim-lane pitch (px)
const NODE_R = 5;

// ---- identity color: same math as src/color.ts (OKLCH -> sRGB hex), theme-aware ----------
function themeLC() {
  const c = document.body.className;
  if (c.includes("vscode-high-contrast-light")) return { L: 0.48, C: 0.15 };
  if (c.includes("vscode-high-contrast")) return { L: 0.8, C: 0.15 };
  if (c.includes("vscode-light")) return { L: 0.55, C: 0.14 };
  return { L: 0.72, C: 0.13 };
}
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
    const v = x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
    return Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0");
  };
  return `#${g(lr)}${g(lg)}${g(lb)}`;
}
function colorFor(id) {
  const { L, C } = themeLC();
  return oklchToHex(L, C, ((hashId(id) * GOLDEN) % 1) * 360);
}
function cssVar(name, fallback) {
  const v = getComputedStyle(document.body).getPropertyValue(name).trim();
  return v || fallback;
}

const STATUS_GLYPH = { active: "●", planned: "○", suspended: "◐", quarantined: "⚠" };

// ---- layout: order rows + assign swim lanes (git-graph lane allocation) -------------------
function computeLayout(graph) {
  const present = new Set(graph.nodes.map((n) => n.id));
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const deps = new Map(graph.nodes.map((n) => [n.id, (n.depends_on || []).filter((d) => present.has(d))]));

  // depth = longest dependency chain; a dependent sits ABOVE its dependencies (like a commit
  // above its parents). Order by depth desc, stable by original index.
  const depth = new Map();
  const seen = new Set();
  function d(id) {
    if (depth.has(id)) return depth.get(id);
    if (seen.has(id)) return 0;
    seen.add(id);
    const v = (deps.get(id) || []).reduce((m, x) => Math.max(m, 1 + d(x)), 0);
    depth.set(id, v);
    return v;
  }
  graph.nodes.forEach((n) => d(n.id));
  const order = graph.nodes
    .map((n, i) => ({ n, i }))
    .sort((a, b) => depth.get(b.n.id) - depth.get(a.n.id) || a.i - b.i)
    .map((x) => x.n);

  const rowOf = new Map(order.map((n, i) => [n.id, i]));

  // Lane sweep, top -> bottom. `lanes[k]` = the node a lane is currently flowing toward (its next
  // occupant), or null when free. A node takes the lane reserved for it (merging others), then
  // reserves its first dependency in that lane and any extra dependencies in fresh lanes.
  const lanes = [];
  const laneOf = new Map();
  const firstFree = () => {
    const k = lanes.indexOf(null);
    return k === -1 ? lanes.length : k;
  };
  for (const n of order) {
    let lane = lanes.indexOf(n.id);
    if (lane === -1) lane = firstFree();
    laneOf.set(n.id, lane);
    for (let k = 0; k < lanes.length; k++) if (lanes[k] === n.id) lanes[k] = null;
    let first = true;
    for (const dep of deps.get(n.id) || []) {
      if (lanes.indexOf(dep) !== -1) continue; // already reserved by another dependent
      if (first) { lanes[lane] = dep; first = false; }
      else lanes[firstFree()] = dep;
    }
  }
  const laneCount = Math.max(1, ...order.map((n) => laneOf.get(n.id) + 1));

  const edges = [];
  for (const n of order) {
    for (const dep of deps.get(n.id) || []) {
      if (rowOf.has(dep)) edges.push({ from: n.id, to: dep });
    }
  }
  return { order, byId, deps, depth, rowOf, laneOf, laneCount, edges };
}

const laneX = (lane) => lane * LANE_W + LANE_W / 2 + 4;
const rowY = (row) => row * ROW + ROW / 2;

// ---- state -------------------------------------------------------------------------------
let graphData = null;
let statusData = null;
let layout = null;
let filter = "";
let selectedId = null;

window.addEventListener("message", (e) => {
  const msg = e.data;
  if (msg.type === "graph") {
    graphData = msg.graph;
    statusData = msg.status || null;
    render();
  } else if (msg.type === "error") {
    document.getElementById("count").textContent = "error: " + msg.message;
  }
});

function render() {
  if (!graphData) return;
  const empty = !graphData.nodes.length;
  document.getElementById("empty").hidden = !empty;
  document.getElementById("rows").style.display = empty ? "none" : "";
  renderHeader();
  if (empty) return;
  layout = computeLayout(graphData);
  renderRows();
  drawLanes();
  drawMinimap();
  applyFilter();
}

function renderHeader() {
  document.getElementById("count").textContent = `${graphData.count} feature${graphData.count === 1 ? "" : "s"}`;
  const chip = document.getElementById("drift");
  const drift = statusData && statusData.drift;
  if (drift && drift.any) {
    chip.textContent = `⚠ ${drift.summary || "drifted"}`;
    chip.className = "chip warn";
  } else if (statusData) {
    chip.textContent = "✓ in sync";
    chip.className = "chip ok";
  } else {
    chip.textContent = "";
    chip.className = "chip";
  }
}

function trunc(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function renderRows() {
  const list = document.getElementById("rowlist");
  const graphWidth = layout.laneCount * LANE_W + LANE_W;
  document.documentElement.style.setProperty("--graph-w", `${graphWidth}px`);
  const frag = document.createDocumentFragment();
  for (const n of layout.order) {
    const ident = colorFor(n.id);
    const row = document.createElement("div");
    row.className = "row";
    row.dataset.id = n.id;
    row.tabIndex = -1;
    row.setAttribute("role", "row");
    row.setAttribute("aria-label", `${n.intent || n.id}. ${n.kind}, ${n.status}.` + (n.conflict ? ` Conflict.` : ""));

    const refs = document.createElement("div");
    refs.className = "refs";
    const pill = document.createElement("span");
    pill.className = "pill" + (n.status === "suspended" ? " dim" : "");
    // Tint the pill with the feature's identity color (a left accent bar + faint wash), so the
    // FEATURE column reads as a colored ref pill — the GitLens branch-pill analog.
    pill.style.background = ident + "1f";
    pill.style.boxShadow = `inset 2px 0 0 ${ident}`;
    const glyph = document.createElement("i");
    glyph.className = "g";
    glyph.textContent = STATUS_GLYPH[n.status] || "●";
    glyph.style.color = n.status === "quarantined" ? "var(--vscode-errorForeground)" : ident;
    pill.appendChild(glyph);
    pill.appendChild(document.createTextNode(n.kind));
    refs.appendChild(pill);
    if (n.conflict) {
      const c = document.createElement("span");
      c.className = "pill conflict";
      c.textContent = "⚠";
      c.title = String(n.conflict);
      refs.appendChild(c);
    }

    const gcell = document.createElement("div");
    gcell.className = "gcell";
    gcell.style.width = `${graphWidth}px`;

    const msg = document.createElement("div");
    msg.className = "msg";
    const t = document.createElement("span");
    t.className = "intent";
    t.textContent = n.intent || n.id;
    msg.appendChild(t);
    const meta = document.createElement("span");
    meta.className = "meta";
    const dn = (n.dependents || []).length;
    meta.textContent = dn ? `${dn} dependent${dn === 1 ? "" : "s"}` : "";
    msg.appendChild(meta);

    row.appendChild(refs);
    row.appendChild(gcell);
    row.appendChild(msg);
    row.addEventListener("click", () => select(n.id, true));
    frag.appendChild(row);
  }
  list.replaceChildren(frag);
}

function drawLanes() {
  const svg = document.getElementById("lanes");
  const graphWidth = layout.laneCount * LANE_W + LANE_W;
  svg.setAttribute("width", String(graphWidth));
  svg.setAttribute("height", String(layout.order.length * ROW));
  const bg = cssVar("--vscode-editor-background", "#1e1e1e");
  const frag = document.createDocumentFragment();

  // edges first (under nodes), colored by the source feature's identity hue.
  for (const e of layout.edges) {
    const x1 = laneX(layout.laneOf.get(e.from)), y1 = rowY(layout.rowOf.get(e.from));
    const x2 = laneX(layout.laneOf.get(e.to)), y2 = rowY(layout.rowOf.get(e.to));
    const path = document.createElementNS(NS, "path");
    const my = (y1 + y2) / 2;
    path.setAttribute("d", `M${x1} ${y1} C${x1} ${my} ${x2} ${my} ${x2} ${y2}`);
    path.setAttribute("class", "edge");
    path.setAttribute("stroke", colorFor(e.from));
    path.dataset.from = e.from;
    path.dataset.to = e.to;
    frag.appendChild(path);
  }
  // nodes
  for (const n of layout.order) {
    const cx = laneX(layout.laneOf.get(n.id)), cy = rowY(layout.rowOf.get(n.id));
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", String(cx));
    c.setAttribute("cy", String(cy));
    c.dataset.id = n.id;
    const ident = colorFor(n.id);
    if (n.status === "planned") {
      c.setAttribute("r", String(NODE_R - 0.5));
      c.setAttribute("fill", bg);
      c.setAttribute("stroke", ident);
      c.setAttribute("stroke-width", "2");
    } else if (n.status === "quarantined") {
      c.setAttribute("r", String(NODE_R));
      c.setAttribute("fill", ident);
      c.setAttribute("stroke", "var(--vscode-errorForeground)");
      c.setAttribute("stroke-width", "2");
    } else {
      c.setAttribute("r", String(NODE_R));
      c.setAttribute("fill", ident);
      c.setAttribute("stroke", bg);
      c.setAttribute("stroke-width", "2");
      if (n.status === "suspended") c.setAttribute("opacity", "0.5");
    }
    c.setAttribute("class", "gnode");
    frag.appendChild(c);
  }
  svg.replaceChildren(frag);
}

// ---- minimap: activity spline (effects per feature) + status markers ----------------------
function drawMinimap() {
  const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("minimap"));
  const w = canvas.clientWidth || canvas.parentElement.clientWidth;
  const h = 38;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(w * dpr));
  canvas.height = Math.floor(h * dpr);
  canvas.style.height = `${h}px`;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const order = layout.order;
  if (order.length < 2 || w < 8) return;

  const activityH = h - 8;
  const weights = order.map((n) => (n.effects ? n.effects.length : 1));
  const max = Math.max(1, ...weights);
  const xs = order.map((_, i) => (i / (order.length - 1)) * (w - 4) + 2);
  const ys = weights.map((v) => activityH - (v / max) * (activityH - 4) - 2);

  // monotone-ish spline (Catmull-Rom -> bezier) in the progressBar color, like GitLens's line.
  ctx.strokeStyle = cssVar("--vscode-progressBar-background", "#cf6edf");
  ctx.lineWidth = 1.2;
  ctx.globalAlpha = 0.9;
  ctx.beginPath();
  ctx.moveTo(xs[0], ys[0]);
  for (let i = 0; i < xs.length - 1; i++) {
    const x0 = xs[Math.max(0, i - 1)], y0 = ys[Math.max(0, i - 1)];
    const x1 = xs[i], y1 = ys[i];
    const x2 = xs[i + 1], y2 = ys[i + 1];
    const x3 = xs[Math.min(xs.length - 1, i + 2)], y3 = ys[Math.min(ys.length - 1, i + 2)];
    ctx.bezierCurveTo(x1 + (x2 - x0) / 6, y1 + (y2 - y0) / 6, x2 - (x3 - x1) / 6, y2 - (y3 - y1) / 6, x2, y2);
  }
  ctx.stroke();
  ctx.globalAlpha = 1;

  // status markers along the bottom rail
  const markerColor = {
    active: cssVar("--vscode-charts-green", "#89d185"),
    planned: cssVar("--vscode-charts-yellow", "#cca700"),
    suspended: cssVar("--vscode-descriptionForeground", "#888"),
    quarantined: cssVar("--vscode-errorForeground", "#f14c4c"),
  };
  for (let i = 0; i < order.length; i++) {
    ctx.fillStyle = markerColor[order[i].status] || markerColor.active;
    ctx.fillRect(Math.round(xs[i]) - 1, h - 4, 2, 3);
  }

  canvas.onclick = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    let best = 0, bd = Infinity;
    for (let i = 0; i < xs.length; i++) {
      const dd = Math.abs(xs[i] - x);
      if (dd < bd) { bd = dd; best = i; }
    }
    select(order[best].id, false);
    scrollToSelected();
  };
}

// ---- filter / selection / keyboard -------------------------------------------------------
function applyFilter() {
  const f = filter;
  for (const row of document.querySelectorAll(".row")) {
    const n = layout.byId.get(row.dataset.id);
    const match = !f || `${n.intent} ${n.id} ${n.kind}`.toLowerCase().includes(f);
    row.classList.toggle("dim", !match);
  }
  for (const c of document.querySelectorAll(".gnode")) {
    const n = layout.byId.get(c.dataset.id);
    const match = !f || `${n.intent} ${n.id} ${n.kind}`.toLowerCase().includes(f);
    c.classList.toggle("dim", !match);
  }
}

function select(id, open) {
  selectedId = id;
  for (const row of document.querySelectorAll(".row")) {
    const on = row.dataset.id === id;
    row.classList.toggle("selected", on);
    if (on) row.tabIndex = 0;
    else row.tabIndex = -1;
  }
  for (const c of document.querySelectorAll(".gnode")) {
    c.classList.toggle("sel", c.dataset.id === id);
  }
  for (const p of document.querySelectorAll(".edge")) {
    const inc = p.dataset.from === id || p.dataset.to === id;
    p.classList.toggle("hot", inc);
  }
  if (open) vscode.postMessage({ type: "open", id });
}

function scrollToSelected() {
  const el = document.querySelector(`.row[data-id="${CSS.escape(selectedId)}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
}

function moveSelection(delta) {
  if (!layout || !layout.order.length) return;
  let idx = layout.order.findIndex((n) => n.id === selectedId);
  idx = idx < 0 ? 0 : Math.max(0, Math.min(layout.order.length - 1, idx + delta));
  select(layout.order[idx].id, false);
  const el = document.querySelector(`.row[data-id="${CSS.escape(selectedId)}"]`);
  if (el) { el.focus(); el.scrollIntoView({ block: "nearest" }); }
}

const scroll = document.getElementById("scroll");
scroll.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); moveSelection(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveSelection(-1); }
  else if ((e.key === "Enter" || e.key === " ") && selectedId) { e.preventDefault(); select(selectedId, true); }
});

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
document.getElementById("filter").addEventListener(
  "input",
  debounce((e) => { filter = e.target.value.toLowerCase().trim(); applyFilter(); }, 120)
);
document.getElementById("refresh").addEventListener("click", () => vscode.postMessage({ type: "ready" }));

let rsz;
window.addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => { if (layout) drawMinimap(); }, 120); });

new MutationObserver(() => { if (graphData) render(); })
  .observe(document.body, { attributes: true, attributeFilter: ["class"] });

vscode.postMessage({ type: "ready" });
