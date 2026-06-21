// @ts-check
// Feature Graph — a GitLens/GitKraken-style commit graph for the semantic DAG. Each feature is a
// ROW (most-derived on top): a KIND ref-pill column, a swim-lane GRAPH column (git-style lane
// allocation + colored bezier edges), and a short kebab LABEL in the FEATURE column. Selecting a
// row opens an in-situ detail pane (no modal popups) with the full intent rendered as rich text,
// clickable cross-references, and modal-free actions. Live agent presence: features with
// uncommitted drift pulse as "editing", and a just-landed feature flashes.
//
// Identity color is the same OKLCH hash used in blame and the TUI (hue = identity; status = glyph).

const vscode = acquireVsCodeApi();
const NS = "http://www.w3.org/2000/svg";
const GOLDEN = 0.618033988749895;
const ROW = 26, LANE_W = 18, NODE_R = 5;

// ---- identity color: same math as src/color.ts -------------------------------------------
function themeLC() {
  const c = document.body.className;
  if (c.includes("vscode-high-contrast-light")) return { L: 0.48, C: 0.15 };
  if (c.includes("vscode-high-contrast")) return { L: 0.8, C: 0.15 };
  if (c.includes("vscode-light")) return { L: 0.55, C: 0.14 };
  return { L: 0.72, C: 0.13 };
}
function hashId(id) {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function oklchToHex(L, C, hDeg) {
  const h = (hDeg * Math.PI) / 180, a = C * Math.cos(h), b = C * Math.sin(h);
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

// ---- short structured label (XXX-YYY-ZZZ, <=5 words) -------------------------------------
const VERBS = new Set(["add", "implement", "remove", "delete", "fix", "refactor", "update", "create",
  "support", "handle", "make", "allow", "introduce", "expose", "wire", "render", "cache", "stream",
  "validate", "normalize", "parse", "store", "load", "compose", "rerank", "rank", "embed", "search",
  "query", "build", "extract", "split", "merge", "track", "compute", "resolve", "guard"]);
const STOP = new Set(["a", "an", "the", "to", "of", "that", "for", "by", "with", "and", "or", "in",
  "on", "into", "from", "its", "it", "this", "class", "method", "function", "each", "all", "as",
  "back", "caller", "specific", "relevant", "using", "via"]);
function shortLabel(intent) {
  if (!intent) return "feature";
  const ticks = [...intent.matchAll(/`([^`]+)`/g)].map((m) => m[1].toLowerCase().replace(/[^a-z0-9]+/g, ""));
  const words = intent.toLowerCase().replace(/`/g, " ").split(/[^a-z0-9]+/).filter(Boolean);
  const segs = [];
  if (words[0] && VERBS.has(words[0])) segs.push(words[0]);
  for (const t of ticks) { if (t && !segs.includes(t)) segs.push(t); if (segs.length >= 4) break; }
  if (segs.length < 2) {
    for (const w of words) {
      if (STOP.has(w) || segs.includes(w)) continue;
      segs.push(w);
      if (segs.length >= 4) break;
    }
  }
  const out = segs.filter(Boolean).slice(0, 5).join("-");
  return out || words.slice(0, 3).join("-") || "feature";
}

// ---- layout: order rows + assign swim lanes ----------------------------------------------
function computeLayout(graph) {
  const present = new Set(graph.nodes.map((n) => n.id));
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const deps = new Map(graph.nodes.map((n) => [n.id, (n.depends_on || []).filter((d) => present.has(d))]));

  // Compact, git-graph-like ordering: a depth-first topological sort (LIFO Kahn) where a node is
  // placed only after all its dependents, and we dive into a dependency chain before backtracking.
  // This keeps a node directly above its dependency, so edges stay short and vertical (the reason
  // GitLens looks compact) instead of long diagonals scattered by a depth bucket sort.
  const depCount = new Map(graph.nodes.map((n) => [n.id, 0]));
  for (const n of graph.nodes) for (const dep of deps.get(n.id)) depCount.set(dep, depCount.get(dep) + 1);
  const ready = [];
  for (let i = graph.nodes.length - 1; i >= 0; i--) {
    if (depCount.get(graph.nodes[i].id) === 0) ready.push(graph.nodes[i].id); // roots: nothing depends on them
  }
  const order = [];
  const placed = new Set();
  while (ready.length) {
    const id = ready.pop();
    if (placed.has(id)) continue;
    placed.add(id);
    order.push(byId.get(id));
    const ds = deps.get(id) || [];
    for (let j = ds.length - 1; j >= 0; j--) {
      const dep = ds[j];
      depCount.set(dep, depCount.get(dep) - 1);
      if (depCount.get(dep) === 0) ready.push(dep); // ready once all its dependents are placed
    }
  }
  for (const n of graph.nodes) if (!placed.has(n.id)) order.push(n); // cycle safety (shouldn't happen)

  const rowOf = new Map(order.map((n, i) => [n.id, i]));
  const lanes = [];
  const laneOf = new Map();
  // Nearest free lane to a hint, so a branch takes the lane closest to its parent — fewer crossings.
  const ff = (hint = 0) => {
    let best = -1, bd = Infinity;
    for (let k = 0; k < lanes.length; k++) {
      if (lanes[k] == null) { const dd = Math.abs(k - hint); if (dd < bd) { bd = dd; best = k; } }
    }
    return best === -1 ? lanes.length : best;
  };
  for (const n of order) {
    let lane = lanes.indexOf(n.id);
    if (lane === -1) lane = ff(0);
    laneOf.set(n.id, lane);
    for (let k = 0; k < lanes.length; k++) if (lanes[k] === n.id) lanes[k] = null;
    let first = true;
    for (const dep of deps.get(n.id) || []) {
      if (lanes.indexOf(dep) !== -1) continue;
      if (first) { lanes[lane] = dep; first = false; } else lanes[ff(lane)] = dep;
    }
  }
  const laneCount = Math.max(1, ...order.map((n) => laneOf.get(n.id) + 1));
  const edges = [];
  for (const n of order) for (const dep of deps.get(n.id) || []) if (rowOf.has(dep)) edges.push({ from: n.id, to: dep });
  return { order, byId, deps, rowOf, laneOf, laneCount, edges };
}
const laneX = (lane) => lane * LANE_W + LANE_W / 2 + 4;
const rowY = (row) => row * ROW + ROW / 2;

// ---- state -------------------------------------------------------------------------------
let graphData = null, statusData = null, layout = null;
let filter = "", selectedId = null;
let editing = new Set();
let prevIds = new Set();
let refIndex = new Map(); // ref key -> node id, for clickable cross-references

window.addEventListener("message", (e) => {
  const msg = e.data;
  if (msg.type === "graph") {
    graphData = msg.graph;
    statusData = msg.status || null;
    editing = new Set(msg.editing || []);
    render();
    if (msg.select) select(msg.select, true);
  } else if (msg.type === "select" && graphData) {
    select(msg.id, true);
  } else if (msg.type === "applyError") {
    flashDetailError(msg.message);
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
  if (empty) { document.getElementById("detail").hidden = true; return; }
  layout = computeLayout(graphData);
  buildRefIndex();
  renderRows();
  drawLanes();
  drawMinimap();
  applyFilter();
  if (selectedId && layout.byId.has(selectedId)) openDetail(selectedId); // refresh open pane
  prevIds = new Set(graphData.nodes.map((n) => n.id));
}

function buildRefIndex() {
  refIndex = new Map();
  for (const n of graphData.nodes) {
    refIndex.set(n.id.toLowerCase(), n.id);
    refIndex.set(n.id.toLowerCase().slice(0, 8), n.id);
    refIndex.set(shortLabel(n.intent), n.id);
  }
}

function renderHeader() {
  const cnt = graphData.count;
  document.getElementById("count").textContent = `${cnt} feature${cnt === 1 ? "" : "s"}`;
  const chip = document.getElementById("drift");
  const drift = statusData && statusData.drift;
  if (drift && drift.any) { chip.textContent = `⚠ ${drift.summary || "drifted"}`; chip.className = "chip warn"; }
  else if (statusData) { chip.textContent = "✓ in sync"; chip.className = "chip ok"; }
  else { chip.textContent = ""; chip.className = "chip"; }

  // live agent presence
  const p = document.getElementById("presence");
  if (editing.size) {
    const labels = [...editing].map((id) => (layout && layout.byId.get(id) ? shortLabel(layout.byId.get(id).intent) : id.slice(0, 8)));
    p.textContent = `✎ agent editing ${editing.size}: ${labels.slice(0, 3).join(", ")}${labels.length > 3 ? "…" : ""}`;
    p.hidden = false;
  } else {
    p.hidden = true;
  }
}

function trunc(s, n) { return s.length > n ? s.slice(0, n - 1) + "…" : s; }

function renderRows() {
  const list = document.getElementById("rowlist");
  const graphWidth = layout.laneCount * LANE_W + LANE_W;
  document.documentElement.style.setProperty("--graph-w", `${graphWidth}px`);
  const frag = document.createDocumentFragment();
  for (const n of layout.order) {
    const ident = colorFor(n.id);
    const row = document.createElement("div");
    row.className = "row";
    if (editing.has(n.id)) row.classList.add("editing");
    if (!prevIds.has(n.id) && prevIds.size) row.classList.add("landed");
    row.dataset.id = n.id;
    row.tabIndex = -1;
    row.setAttribute("role", "row");
    row.title = n.intent || n.id;
    row.setAttribute("aria-label", `${shortLabel(n.intent)}. ${n.kind}, ${n.status}.`);

    const refs = document.createElement("div");
    refs.className = "refs";
    const pill = document.createElement("span");
    pill.className = "pill" + (n.status === "suspended" ? " dim" : "");
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
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = shortLabel(n.intent);
    msg.appendChild(label);
    if (editing.has(n.id)) {
      const e = document.createElement("span");
      e.className = "editing-badge";
      e.textContent = "✎ editing";
      msg.appendChild(e);
    }

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
  for (const n of layout.order) {
    const cx = laneX(layout.laneOf.get(n.id)), cy = rowY(layout.rowOf.get(n.id));
    const ident = colorFor(n.id);
    if (editing.has(n.id)) {
      const halo = document.createElementNS(NS, "circle");
      halo.setAttribute("cx", String(cx));
      halo.setAttribute("cy", String(cy));
      halo.setAttribute("r", String(NODE_R + 3));
      halo.setAttribute("class", "halo");
      halo.setAttribute("fill", "none");
      halo.setAttribute("stroke", ident);
      frag.appendChild(halo);
    }
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", String(cx));
    c.setAttribute("cy", String(cy));
    c.dataset.id = n.id;
    if (n.status === "planned") {
      c.setAttribute("r", String(NODE_R - 0.5)); c.setAttribute("fill", bg); c.setAttribute("stroke", ident); c.setAttribute("stroke-width", "2");
    } else if (n.status === "quarantined") {
      c.setAttribute("r", String(NODE_R)); c.setAttribute("fill", ident); c.setAttribute("stroke", "var(--vscode-errorForeground)"); c.setAttribute("stroke-width", "2");
    } else {
      c.setAttribute("r", String(NODE_R)); c.setAttribute("fill", ident); c.setAttribute("stroke", bg); c.setAttribute("stroke-width", "2");
      if (n.status === "suspended") c.setAttribute("opacity", "0.5");
    }
    c.setAttribute("class", "gnode");
    frag.appendChild(c);
  }
  svg.replaceChildren(frag);
}

// ---- minimap ------------------------------------------------------------------------------
function drawMinimap() {
  const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("minimap"));
  const w = canvas.clientWidth || canvas.parentElement.clientWidth;
  const h = 34;
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
  ctx.strokeStyle = cssVar("--vscode-progressBar-background", "#cf6edf");
  ctx.lineWidth = 1.2;
  ctx.globalAlpha = 0.9;
  ctx.beginPath();
  ctx.moveTo(xs[0], ys[0]);
  for (let i = 0; i < xs.length - 1; i++) {
    const x0 = xs[Math.max(0, i - 1)], y0 = ys[Math.max(0, i - 1)];
    const x1 = xs[i], y1 = ys[i], x2 = xs[i + 1], y2 = ys[i + 1];
    const x3 = xs[Math.min(xs.length - 1, i + 2)], y3 = ys[Math.min(ys.length - 1, i + 2)];
    ctx.bezierCurveTo(x1 + (x2 - x0) / 6, y1 + (y2 - y0) / 6, x2 - (x3 - x1) / 6, y2 - (y3 - y1) / 6, x2, y2);
  }
  ctx.stroke();
  ctx.globalAlpha = 1;
  const mc = {
    active: cssVar("--vscode-charts-green", "#89d185"),
    planned: cssVar("--vscode-charts-yellow", "#cca700"),
    suspended: cssVar("--vscode-descriptionForeground", "#888"),
    quarantined: cssVar("--vscode-errorForeground", "#f14c4c"),
  };
  for (let i = 0; i < order.length; i++) {
    ctx.fillStyle = editing.has(order[i].id) ? mc.quarantined : (mc[order[i].status] || mc.active);
    ctx.fillRect(Math.round(xs[i]) - 1, h - 4, 2, 3);
  }
  canvas.onclick = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    let best = 0, bd = Infinity;
    for (let i = 0; i < xs.length; i++) { const dd = Math.abs(xs[i] - x); if (dd < bd) { bd = dd; best = i; } }
    select(order[best].id, true);
  };
}

// ---- rich text + detail pane -------------------------------------------------------------
function esc(s) {
  return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function resolveRef(token) {
  const k = token.toLowerCase().replace(/[^a-z0-9-]/g, "");
  return refIndex.get(k) || refIndex.get(k.slice(0, 8)) || null;
}
/** Render intent as rich text: `code` spans, and @ref/#ref/backtick tokens that resolve to a
 *  feature become clickable cross-references. */
function renderRich(text) {
  let html = esc(text);
  // backtick code spans -> <code>, linked if the token resolves to a feature
  html = html.replace(/`([^`]+)`/g, (_m, inner) => {
    const id = resolveRef(inner);
    const body = `<code>${inner}</code>`;
    return id ? `<a class="xref" data-ref="${esc(id)}">${body}</a>` : body;
  });
  // @ref / #ref explicit references
  html = html.replace(/(^|[\s(])([@#])([A-Za-z0-9_-]{2,})/g, (m, pre, sig, tok) => {
    const id = resolveRef(tok);
    return id ? `${pre}<a class="xref" data-ref="${esc(id)}">${sig}${esc(tok)}</a>` : m;
  });
  return html;
}

function chip(id) {
  const n = layout.byId.get(id);
  const ident = colorFor(id);
  const label = n ? shortLabel(n.intent) : id.slice(0, 8);
  return `<a class="dep-chip xref" data-ref="${esc(id)}" style="box-shadow: inset 2px 0 0 ${ident}; background:${ident}1f">${esc(label)}</a>`;
}

function openDetail(id) {
  const n = layout.byId.get(id);
  const detail = document.getElementById("detail");
  if (!n) { detail.hidden = true; return; }
  const ident = colorFor(id);
  const glyph = STATUS_GLYPH[n.status] || "●";
  const isOn = n.status !== "suspended";
  const effects = (n.effects || []).map((e) => `<li><span class="op">${esc(e.op)}</span> <code>${esc(e.target)}</code> <span class="file">${esc(e.file)}</span></li>`).join("");
  const deps = (n.depends_on || []).map(chip).join(" ") || "<span class='none'>—</span>";
  const dependents = (n.dependents || []).map(chip).join(" ") || "<span class='none'>—</span>";
  const conflict = n.conflict ? `<div class="conflict-box">⚠ ${esc(typeof n.conflict === "string" ? n.conflict : (n.conflict.reason || "conflict"))}</div>` : "";
  const editingBadge = editing.has(id) ? `<span class="editing-badge big">✎ agent editing now</span>` : "";

  detail.hidden = false;
  detail.innerHTML = `
    <div class="d-head">
      <span class="d-glyph" style="color:${n.status === "quarantined" ? "var(--vscode-errorForeground)" : ident}">${glyph}</span>
      <span class="d-label">${esc(shortLabel(n.intent))}</span>
      <button class="icon-btn d-close" title="Close">✕</button>
    </div>
    <div class="d-sub">${esc(n.kind)} · ${esc(n.status)} · <code>${esc(id)}</code> ${editingBadge}</div>
    <div class="d-intent">${renderRich(n.intent || "")}</div>
    ${conflict}
    <div class="d-section"><div class="d-k">depends on</div><div class="d-chips">${deps}</div></div>
    <div class="d-section"><div class="d-k">dependents</div><div class="d-chips">${dependents}</div></div>
    ${effects ? `<div class="d-section"><div class="d-k">effects</div><ul class="d-effects">${effects}</ul></div>` : ""}
    <div class="d-actions">
      <button class="btn" data-act="preview-revert">Preview revert</button>
      <button class="btn" data-act="preview-switch">Preview ${isOn ? "suspend" : "restore"}</button>
      <button class="btn danger" data-act="apply-revert" data-arm="0">Revert</button>
      <button class="btn warn" data-act="apply-switch" data-arm="0">${isOn ? "Suspend" : "Restore"}</button>
    </div>
    <div class="d-msg" hidden></div>
  `;

  detail.querySelector(".d-close").addEventListener("click", () => { detail.hidden = true; selectedId = null; clearSelectionClasses(); });
  detail.querySelectorAll(".xref").forEach((a) => a.addEventListener("click", (ev) => {
    ev.preventDefault();
    select(a.getAttribute("data-ref"), true);
  }));
  detail.querySelectorAll(".d-actions .btn").forEach((b) => b.addEventListener("click", () => onAction(b, id, isOn)));
}

function onAction(btn, id, isOn) {
  const act = btn.getAttribute("data-act");
  if (act === "preview-revert") { vscode.postMessage({ type: "preview", action: "revert", id }); return; }
  if (act === "preview-switch") { vscode.postMessage({ type: "preview", action: "switch", id, on: !isOn }); return; }
  // apply actions: two-click inline arm (no modal popup)
  const armed = btn.getAttribute("data-arm") === "1";
  if (!armed) {
    document.querySelectorAll(".d-actions .btn[data-arm]").forEach((b) => disarm(b));
    btn.setAttribute("data-arm", "1");
    btn.dataset.label = btn.textContent;
    btn.textContent = "Click again to confirm";
    btn.classList.add("armed");
    btn._t = setTimeout(() => disarm(btn), 3500);
    return;
  }
  disarm(btn);
  if (act === "apply-revert") vscode.postMessage({ type: "apply", action: "revert", id });
  else vscode.postMessage({ type: "apply", action: "switch", id, on: !isOn });
}
function disarm(btn) {
  if (btn.getAttribute("data-arm") !== "1") return;
  clearTimeout(btn._t);
  btn.setAttribute("data-arm", "0");
  btn.classList.remove("armed");
  if (btn.dataset.label) btn.textContent = btn.dataset.label;
}
function flashDetailError(message) {
  const box = document.querySelector("#detail .d-msg");
  if (box) { box.hidden = false; box.textContent = "✗ " + message; box.className = "d-msg err"; }
}

// ---- filter / selection / keyboard -------------------------------------------------------
function applyFilter() {
  const f = filter;
  const match = (n) => !f || `${n.intent} ${n.id} ${n.kind} ${shortLabel(n.intent)}`.toLowerCase().includes(f);
  for (const row of document.querySelectorAll(".row")) row.classList.toggle("dim", !match(layout.byId.get(row.dataset.id)));
  for (const c of document.querySelectorAll(".gnode")) c.classList.toggle("dim", !match(layout.byId.get(c.dataset.id)));
}
function clearSelectionClasses() {
  for (const row of document.querySelectorAll(".row.selected")) row.classList.remove("selected");
  for (const c of document.querySelectorAll(".gnode.sel")) c.classList.remove("sel");
  for (const p of document.querySelectorAll(".edge.hot")) p.classList.remove("hot");
}
function select(id, openPane) {
  if (!layout || !layout.byId.has(id)) return;
  selectedId = id;
  clearSelectionClasses();
  for (const row of document.querySelectorAll(".row")) {
    const on = row.dataset.id === id;
    row.classList.toggle("selected", on);
    row.tabIndex = on ? 0 : -1;
  }
  for (const c of document.querySelectorAll(".gnode")) c.classList.toggle("sel", c.dataset.id === id);
  for (const p of document.querySelectorAll(".edge")) p.classList.toggle("hot", p.dataset.from === id || p.dataset.to === id);
  const el = document.querySelector(`.row[data-id="${CSS.escape(id)}"]`);
  if (el) el.scrollIntoView({ block: "nearest" });
  if (openPane) openDetail(id);
}
function moveSelection(delta) {
  if (!layout || !layout.order.length) return;
  let idx = layout.order.findIndex((n) => n.id === selectedId);
  idx = idx < 0 ? 0 : Math.max(0, Math.min(layout.order.length - 1, idx + delta));
  const id = layout.order[idx].id;
  select(id, true);
  const el = document.querySelector(`.row[data-id="${CSS.escape(id)}"]`);
  if (el) el.focus();
}
const scroll = document.getElementById("scroll");
scroll.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { e.preventDefault(); moveSelection(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveSelection(-1); }
  else if ((e.key === "Enter" || e.key === " ") && selectedId) { e.preventDefault(); select(selectedId, true); }
});

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
document.getElementById("filter").addEventListener("input", debounce((e) => { filter = e.target.value.toLowerCase().trim(); applyFilter(); }, 120));
document.getElementById("refresh").addEventListener("click", () => vscode.postMessage({ type: "ready" }));
let rsz;
window.addEventListener("resize", () => { clearTimeout(rsz); rsz = setTimeout(() => { if (layout) drawMinimap(); }, 120); });
new MutationObserver(() => { if (graphData) render(); }).observe(document.body, { attributes: true, attributeFilter: ["class"] });

vscode.postMessage({ type: "ready" });
