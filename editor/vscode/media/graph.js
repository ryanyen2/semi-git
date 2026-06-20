// @ts-check
// Layered DAG renderer for the feature graph. Pure SVG, no deps. It lays nodes out by dependency
// depth (Sugiyama-style: layer by longest-path depth, then reduce edge crossings with a few
// median-ordering passes, routing long edges around intervening layers via dummy nodes), then
// pans/zooms a single viewport <g>. Nodes are keyed by id and reused across renders, so a graph
// mutation (plan/reconcile) *animates* nodes to their new positions instead of flashing — which
// is exactly how you see what moved. Color = feature identity (OKLCH, theme-aware, matching the
// editor's blame). Status = a glyph + opacity, never the hue (hue is reserved for identity).

const vscode = acquireVsCodeApi();
const NS = "http://www.w3.org/2000/svg";
const GOLDEN = 0.618033988749895;

// ---- color: identical math to src/color.ts (OKLCH -> sRGB hex), theme-aware ----------------
function themeLC() {
  const c = document.body.className;
  if (c.includes("vscode-high-contrast-light")) return { L: 0.48, C: 0.15 };
  if (c.includes("vscode-high-contrast")) return { L: 0.8, C: 0.15 };
  if (c.includes("vscode-light")) return { L: 0.55, C: 0.14 };
  return { L: 0.72, C: 0.13 }; // dark
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

// Status -> glyph (matches the TUI and the tree sidebar). The glyph carries status; identity
// stays on the hue. `suspended` also dims the whole node; `quarantined` flags in error red.
const STATUS_GLYPH = { active: "●", planned: "○", suspended: "◐", quarantined: "⚠" };

// ---- geometry ------------------------------------------------------------------------------
const W = 200, H = 24, COL = 264, ROW = 44, PADX = 28, PADY = 28;

/**
 * Sugiyama-ish layout. Returns node positions and, per edge, a routed point list.
 * @param {{nodes:any[],edges:any[]}} graph
 */
function layout(graph) {
  const ids = graph.nodes.map((n) => n.id);
  const idSet = new Set(ids);
  const deps = new Map(graph.nodes.map((n) => [n.id, (n.depends_on || []).filter((d) => idSet.has(d))]));

  // depth = longest dependency chain (a node sits right of its deepest dependency).
  const depth = new Map();
  const visiting = new Set();
  function d(id) {
    if (depth.has(id)) return depth.get(id);
    if (visiting.has(id)) return 0; // cycle guard
    visiting.add(id);
    const ds = deps.get(id) || [];
    const v = ds.length ? 1 + Math.max(...ds.map(d)) : 0;
    visiting.delete(id);
    depth.set(id, v);
    return v;
  }
  ids.forEach(d);

  // Build layers (arrays of node ids, real then dummy). Long edges get dummy nodes per spanned
  // layer so they route around boxes instead of through them.
  const maxLayer = Math.max(0, ...depth.values());
  const layers = [];
  for (let i = 0; i <= maxLayer; i++) layers.push([]);
  ids.forEach((id) => layers[depth.get(id)].push(id));

  // edge = src depends_on dst  => src deeper (right), dst shallower (left).
  const segments = []; // {edge, chain:[id...] left->right of real+dummy ids}
  const dummyAt = new Map(); // dummyId -> layer
  let dummySeq = 0;
  for (const e of graph.edges) {
    if (e.type !== "depends_on" || !idSet.has(e.src) || !idSet.has(e.dst)) continue;
    const hi = depth.get(e.src), lo = depth.get(e.dst);
    const chain = [e.dst];
    for (let lvl = lo + 1; lvl < hi; lvl++) {
      const did = `__d${dummySeq++}`;
      layers[lvl].push(did);
      dummyAt.set(did, lvl);
      chain.push(did);
    }
    chain.push(e.src);
    segments.push({ edge: e, chain });
  }

  // adjacency between consecutive layers, for median ordering.
  const below = new Map(); // id -> [ids in layer-1 it links to]
  const above = new Map(); // id -> [ids in layer+1 it links to]
  const push = (m, k, v) => { if (!m.has(k)) m.set(k, []); m.get(k).push(v); };
  for (const { chain } of segments) {
    for (let i = 0; i + 1 < chain.length; i++) {
      const lo = chain[i], hi = chain[i + 1];
      push(below, hi, lo);
      push(above, lo, hi);
    }
  }

  // order: index of each id within its layer. Initialize by insertion, then sweep.
  const order = layers.map((arr) => arr.slice());
  const indexOf = () => {
    const idx = new Map();
    order.forEach((arr) => arr.forEach((id, i) => idx.set(id, i)));
    return idx;
  };
  const median = (id, neigh, idx) => {
    const ps = (neigh.get(id) || []).map((n) => idx.get(n)).filter((v) => v != null).sort((a, b) => a - b);
    if (!ps.length) return -1;
    const m = Math.floor(ps.length / 2);
    return ps.length % 2 ? ps[m] : (ps[m - 1] + ps[m]) / 2;
  };
  for (let pass = 0; pass < 6; pass++) {
    const idx = indexOf();
    const downward = pass % 2 === 0;
    const range = downward ? [...order.keys()] : [...order.keys()].reverse();
    for (const lvl of range) {
      const neigh = downward ? below : above;
      if ((downward && lvl === 0) || (!downward && lvl === order.length - 1)) continue;
      const withMed = order[lvl].map((id) => ({ id, m: median(id, neigh, idx) }));
      // keep fixed (m<0) nodes in place; sort the rest by median (stable).
      const fixed = withMed.map((w, i) => (w.m < 0 ? i : -1)).filter((i) => i >= 0);
      const movable = withMed.filter((w) => w.m >= 0).sort((a, b) => a.m - b.m);
      const result = order[lvl].slice();
      let mi = 0;
      for (let i = 0; i < result.length; i++) {
        if (!fixed.includes(i)) result[i] = movable[mi++].id;
      }
      order[lvl] = result;
    }
  }

  // coordinates. Center each layer vertically around the tallest layer.
  const tallest = Math.max(1, ...order.map((a) => a.length));
  const pos = new Map();
  order.forEach((arr, lvl) => {
    const offset = ((tallest - arr.length) * ROW) / 2;
    arr.forEach((id, i) => {
      pos.set(id, { x: PADX + lvl * COL, y: PADY + offset + i * ROW, dummy: dummyAt.has(id) });
    });
  });

  // routed polylines per edge: dst.rightPort -> dummy mids -> src.leftPort
  const routes = segments.map(({ edge, chain }) => {
    const pts = chain.map((id, i) => {
      const p = pos.get(id);
      if (i === 0) return [p.x + W, p.y + H / 2];               // dst right port
      if (i === chain.length - 1) return [p.x, p.y + H / 2];     // src left port
      return [p.x + W / 2, p.y + H / 2];                         // dummy mid
    });
    return { edge, pts };
  });

  let maxX = 0, maxY = 0;
  for (const p of pos.values()) { maxX = Math.max(maxX, p.x + W); maxY = Math.max(maxY, p.y + H); }
  return { pos, routes, extent: { w: maxX + PADX, h: maxY + PADY } };
}

// ---- viewport (pan / zoom / fit) -----------------------------------------------------------
const view = { k: 1, tx: 0, ty: 0 };
let extent = { w: 1, h: 1 };

function applyView() {
  const vp = document.getElementById("viewport");
  vp.setAttribute("transform", `translate(${view.tx} ${view.ty}) scale(${view.k})`);
}
function fit() {
  const canvas = document.getElementById("canvas");
  const cw = canvas.clientWidth || 1, ch = canvas.clientHeight || 1;
  view.k = Math.min(cw / extent.w, ch / extent.h, 1.4);
  view.tx = (cw - extent.w * view.k) / 2;
  view.ty = Math.max(PADY, (ch - extent.h * view.k) / 2);
  applyView();
}

// ---- rendering (keyed reconcile so positions animate via CSS transitions) ------------------
const nodeEls = new Map(); // id -> <g>
let lastGraph = null;
let currentFilter = "";
let focusedId = null;

function makeNode(n) {
  const g = document.createElementNS(NS, "g");
  g.setAttribute("class", "node");
  g.setAttribute("tabindex", "0");
  g.setAttribute("role", "button");
  g.dataset.id = n.id;

  const rect = document.createElementNS(NS, "rect");
  rect.setAttribute("width", String(W));
  rect.setAttribute("height", String(H));
  rect.setAttribute("rx", "5");
  rect.setAttribute("class", "box");
  g.appendChild(rect);

  const glyph = document.createElementNS(NS, "text");
  glyph.setAttribute("x", "12");
  glyph.setAttribute("y", String(H / 2));
  glyph.setAttribute("dominant-baseline", "central");
  glyph.setAttribute("text-anchor", "middle");
  glyph.setAttribute("class", "glyph");
  g.appendChild(glyph);

  const text = document.createElementNS(NS, "text");
  text.setAttribute("x", "24");
  text.setAttribute("y", String(H / 2));
  text.setAttribute("dominant-baseline", "central");
  text.setAttribute("class", "label");
  g.appendChild(text);

  g.addEventListener("click", () => select(n.id, true));
  g.addEventListener("focus", () => select(n.id, false));
  g.addEventListener("keydown", (ev) => onNodeKey(ev, n.id));
  return g;
}

function updateNode(g, n) {
  const color = colorFor(n.id);
  const rect = g.querySelector("rect.box");
  rect.setAttribute("fill", color);
  rect.setAttribute("fill-opacity", "0.16");
  rect.setAttribute("stroke", color);

  const glyph = g.querySelector("text.glyph");
  glyph.textContent = STATUS_GLYPH[n.status] || "●";
  glyph.setAttribute("fill", n.status === "quarantined" ? "var(--vscode-editorError-foreground, #f14c4c)" : color);

  const reserve = n.conflict ? 20 : 6;
  const label = g.querySelector("text.label");
  label.textContent = trunc(n.intent || n.id, Math.max(8, Math.floor((W - 24 - reserve) / 7)));

  let warn = g.querySelector("text.warn");
  if (n.conflict) {
    if (!warn) {
      warn = document.createElementNS(NS, "text");
      warn.setAttribute("x", String(W - 12));
      warn.setAttribute("y", String(H / 2));
      warn.setAttribute("dominant-baseline", "central");
      warn.setAttribute("text-anchor", "middle");
      warn.setAttribute("class", "warn");
      warn.textContent = "⚠";
      g.appendChild(warn);
    }
  } else if (warn) {
    warn.remove();
  }

  g.classList.toggle("suspended", n.status === "suspended");
  const aria = `${n.intent || n.id}. ${n.kind}, ${n.status}.` + (n.conflict ? ` Conflict: ${n.conflict}.` : "");
  g.setAttribute("aria-label", aria);
  const title = g.querySelector("title") || g.appendChild(document.createElementNS(NS, "title"));
  title.textContent = `${n.intent}\n${n.kind} · ${n.status} · ${n.id}` + (n.conflict ? `\n⚠ ${n.conflict}` : "");
}

function render(graph) {
  const status = document.getElementById("status");
  status.textContent = graph.count ? `${graph.count} features` : "empty graph — run `sgt plan \"…\"`";
  const { pos, routes, extent: ex } = layout(graph);
  extent = ex;

  // edges (rebuilt each render — cheap relative to nodes; no identity to preserve).
  const edgeLayer = document.getElementById("edges");
  const frag = document.createDocumentFragment();
  for (const { pts } of routes) {
    const path = document.createElementNS(NS, "path");
    path.setAttribute("d", pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" "));
    path.setAttribute("class", "edge");
    frag.appendChild(path);
  }
  edgeLayer.replaceChildren(frag);

  // nodes: reconcile by id so existing <g> keep identity and animate transform to new pos.
  const nodeLayer = document.getElementById("nodes");
  const seen = new Set();
  for (const n of graph.nodes) {
    const p = pos.get(n.id);
    if (!p) continue;
    seen.add(n.id);
    let g = nodeEls.get(n.id);
    if (!g) {
      g = makeNode(n);
      g.setAttribute("transform", `translate(${p.x} ${p.y})`);
      nodeEls.set(n.id, g);
      nodeLayer.appendChild(g);
      g.classList.add("enter");
      requestAnimationFrame(() => g.classList.remove("enter"));
    } else {
      g.setAttribute("transform", `translate(${p.x} ${p.y})`);
    }
    updateNode(g, n);
  }
  for (const [id, g] of nodeEls) {
    if (!seen.has(id)) { g.remove(); nodeEls.delete(id); }
  }
  applyFilter();
  if (focusedId && !nodeEls.has(focusedId)) focusedId = null;
}

function trunc(s, n) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

// ---- filter (no relayout — just dim non-matches; preserves spatial memory) -----------------
function applyFilter() {
  if (!lastGraph) return;
  const f = currentFilter;
  for (const n of lastGraph.nodes) {
    const g = nodeEls.get(n.id);
    if (!g) continue;
    const match = !f || `${n.intent} ${n.id} ${n.kind}`.toLowerCase().includes(f);
    g.classList.toggle("dim", !match);
  }
}

// ---- selection & keyboard nav --------------------------------------------------------------
function select(id, open) {
  focusedId = id;
  for (const [nid, g] of nodeEls) g.classList.toggle("selected", nid === id);
  if (open) vscode.postMessage({ type: "open", id });
}
function onNodeKey(ev, id) {
  if (ev.key === "Enter" || ev.key === " ") {
    ev.preventDefault();
    vscode.postMessage({ type: "open", id });
    return;
  }
  const dir = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] }[ev.key];
  if (!dir) return;
  ev.preventDefault();
  const cur = currentPos(id);
  if (!cur) return;
  let best = null, bestScore = Infinity;
  for (const [nid, g] of nodeEls) {
    if (nid === id || g.classList.contains("dim")) continue;
    const p = currentPos(nid);
    const dx = p.x - cur.x, dy = p.y - cur.y;
    const along = dx * dir[0] + dy * dir[1];
    if (along <= 1) continue; // must be in the chosen direction
    const off = Math.abs(dx * dir[1] + dy * dir[0]); // perpendicular distance
    const score = along + off * 2;
    if (score < bestScore) { bestScore = score; best = g; }
  }
  if (best) best.focus();
}
function currentPos(id) {
  const g = nodeEls.get(id);
  if (!g) return null;
  const m = /translate\(([-\d.]+) ([-\d.]+)\)/.exec(g.getAttribute("transform") || "");
  return m ? { x: parseFloat(m[1]), y: parseFloat(m[2]) } : null;
}

// ---- input wiring --------------------------------------------------------------------------
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

window.addEventListener("message", (event) => {
  const msg = event.data;
  if (msg.type === "graph") {
    lastGraph = msg.graph;
    const first = nodeEls.size === 0;
    render(msg.graph);
    if (first) fit();
  } else if (msg.type === "error") {
    document.getElementById("status").textContent = "error: " + msg.message;
  }
});

const filterInput = document.getElementById("filter");
filterInput.addEventListener(
  "input",
  debounce((e) => { currentFilter = e.target.value.toLowerCase().trim(); applyFilter(); }, 120)
);

document.getElementById("fit").addEventListener("click", fit);

// pan + zoom on the canvas
const canvas = document.getElementById("canvas");
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const factor = Math.exp(-e.deltaY * 0.0015);
  const k2 = Math.max(0.15, Math.min(3, view.k * factor));
  // keep the point under the cursor fixed
  view.tx = mx - (mx - view.tx) * (k2 / view.k);
  view.ty = my - (my - view.ty) * (k2 / view.k);
  view.k = k2;
  applyView();
}, { passive: false });

let panning = null;
canvas.addEventListener("mousedown", (e) => {
  if (e.target.closest(".node")) return; // let nodes handle their own clicks
  panning = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty };
  canvas.classList.add("panning");
});
window.addEventListener("mousemove", (e) => {
  if (!panning) return;
  view.tx = panning.tx + (e.clientX - panning.x);
  view.ty = panning.ty + (e.clientY - panning.y);
  applyView();
});
window.addEventListener("mouseup", () => { panning = null; canvas.classList.remove("panning"); });

// Re-render on theme change so identity colors track light/dark.
new MutationObserver(() => { if (lastGraph) render(lastGraph); })
  .observe(document.body, { attributes: true, attributeFilter: ["class"] });

vscode.postMessage({ type: "ready" });
