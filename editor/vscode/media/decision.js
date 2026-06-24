// Decision Graph webview — vanilla SVG (the CSP forbids external scripts, so no D3).
// Renders the live decision_graph_view: x = time (landing), lane = feature, tip = latest.
// In-force (frontier) is a filled ● with a halo; out-of-force is a hollow ◇. builds-on edges are
// solid/derived, revises|fork are dashed. Status is glyph + dim, never hue (the color contract).

const vscode = acquireVsCodeApi();
const NS = "http://www.w3.org/2000/svg";
let state = null; // { decisions, edges, frontier, clash }
let selected = null;

// OKLCH identity color — mirrored byte-for-byte from editor/vscode/src/color.ts and
// sgt/tui/color.py (the webview can't import across the bundle boundary). A feature's hue is its
// identity; status is glyph + dim, never hue. tests/test_color_parity.py slices this block.
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
  return oklchToHex(0.72, 0.13, ((hashId(id) * GOLDEN) % 1) * 360); // fixed dark L/C
}

const svg = document.getElementById("svg");
const detail = document.getElementById("detail");
document.getElementById("refresh").onclick = () => vscode.postMessage({ type: "ready" });

window.addEventListener("message", (e) => {
  const m = e.data;
  if (m.type === "decisions") {
    state = m.graph;
    if (m.select) selected = m.select;
    render();
  } else if (m.type === "select") {
    selected = m.id;
    render();
  } else if (m.type === "error") {
    detail.innerHTML = `<div class="err">${esc(m.message)}</div>`;
  }
});

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function render() {
  if (!state) return;
  const decisions = state.decisions;
  const inForce = new Set(Object.values(state.frontier || {}));
  document.getElementById("count").textContent = `${decisions.length} decisions`;
  const head = Object.entries(state.frontier || {}).map(([f, d]) => d).join(" · ");
  document.getElementById("head").textContent = head ? `⌂ ${head}` : "";

  // lanes: features ordered by their first appearance (earliest landing)
  const firstLanding = {};
  for (const d of decisions) {
    if (firstLanding[d.feature] == null || d.landing < firstLanding[d.feature]) firstLanding[d.feature] = d.landing;
  }
  const features = [...new Set(decisions.map((d) => d.feature))].sort((a, b) => firstLanding[a] - firstLanding[b]);
  const laneOf = new Map(features.map((f, i) => [f, i]));
  // columns: distinct landings, compressed left→right
  const landings = [...new Set(decisions.map((d) => d.landing))].sort((a, b) => a - b);
  const colOf = new Map(landings.map((l, i) => [l, i]));

  const padX = 130, padY = 34, colW = 90, laneH = 64, r = 11;
  const W = padX + Math.max(1, landings.length) * colW + 40;
  const H = padY + Math.max(1, features.length) * laneH + 30;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const x = (landing) => padX + colOf.get(landing) * colW + colW / 2;
  const y = (feature) => padY + laneOf.get(feature) * laneH + laneH / 2;
  const byId = new Map(decisions.map((d) => [d.id, d]));

  // lane bands + labels
  for (const f of features) {
    const yy = y(f);
    line(0, yy, W - 20, yy, "lane-rule");
    const t = text(10, yy - 16, f, "lane-label");
    t.style.fill = colorFor(f);
    svg.appendChild(t);
  }

  // edges
  for (const e of state.edges) {
    const a = byId.get(e.src), b = byId.get(e.dst);
    if (!a || !b) continue;
    const p = document.createElementNS(NS, "path");
    const x1 = x(a.landing), y1 = y(a.feature), x2 = x(b.landing), y2 = y(b.feature);
    const mx = (x1 + x2) / 2;
    p.setAttribute("d", `M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`);
    p.setAttribute("class", `edge ${e.type === "builds-on" ? "builds-on" : "revises"}`);
    if (e.type !== "builds-on") p.setAttribute("stroke", colorFor(a.feature));
    svg.appendChild(p);
  }

  // clash markers (between two in-force decisions sharing an entity)
  for (const c of state.clash || []) {
    const a = byId.get(c.a), b = byId.get(c.b);
    if (!a || !b) continue;
    const t = text((x(a.landing) + x(b.landing)) / 2, (y(a.feature) + y(b.feature)) / 2 - 6, "⚠", "clash");
    svg.appendChild(t);
  }

  // nodes
  for (const d of decisions) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "node" + (selected === d.id ? " selected" : ""));
    g.setAttribute("transform", `translate(${x(d.landing)},${y(d.feature)})`);
    const force = inForce.has(d.id);
    const col = colorFor(d.feature);
    if (force) g.appendChild(circle(0, 0, r + 5, "halo", { stroke: col }));
    const disc = circle(0, 0, r, "disc", { stroke: col, fill: force ? col : "transparent" });
    g.appendChild(disc);
    g.appendChild(circle(0, 0, r + 8, "hit", {}));
    g.onclick = () => {
      selected = d.id;
      render();
    };
    svg.appendChild(g);
  }

  if (selected && byId.get(selected)) showDetail(byId.get(selected), inForce);
}

function showDetail(d, inForce) {
  const force = inForce.has(d.id);
  const alts = (d.alternatives || [])
    .map((a) => `<div class="alt">· ${esc(a.option)} <span class="lose">✗ ${esc(a.why_rejected)}</span> <span class="src">[${esc(a.source)}]</span></div>`)
    .join("");
  const tx = (d.commits || []).map((c) => `<span class="c">${esc(c.slice(0, 8))}</span>`).join("<span class='arr'>→</span>");
  const fp = (d.footprint || [])
    .map((k) => {
      const [file, target] = k.split("::");
      return `<button class="fp" data-file="${esc(file)}" data-target="${esc(target || "")}">${esc(k)}</button>`;
    })
    .join("");
  const pin = force
    ? `<div class="inforce">● in force (materializing the working tree)</div>`
    : `<button id="pin" class="pin">Pin to HEAD (compose)</button>`;
  detail.innerHTML = `
    <div class="dhead"><span class="dot" style="background:${colorFor(d.feature)}"></span>
      <b>${esc(d.id)}</b> <span class="kind">${esc(d.lifecycle.kind)}${d.lifecycle.of ? " of " + esc(d.lifecycle.of) : ""}</span></div>
    <div class="cdc">
      <div><span class="k">Context</span> ${esc(d.intent.context) || "<i>—</i>"}</div>
      <div><span class="k">Decision</span> ${esc(d.intent.decision)}</div>
      <div><span class="k">Conseq.</span> ${esc(d.intent.consequence) || "<i>—</i>"}</div>
    </div>
    ${alts ? `<div class="alts"><b>alternatives</b>${alts}</div>` : ""}
    <div class="sec"><b>git txn</b> ${tx || "<i>none</i>"}</div>
    <div class="sec"><b>footprint</b><div class="fps">${fp || "<i>none</i>"}</div></div>
    ${pin}`;
  const pinBtn = document.getElementById("pin");
  if (pinBtn) pinBtn.onclick = () => vscode.postMessage({ type: "compose", feature: d.feature, decision: d.id });
  for (const b of detail.querySelectorAll(".fp")) {
    b.onclick = () => vscode.postMessage({ type: "reveal", file: b.dataset.file, target: b.dataset.target });
  }
}


function line(x1, y1, x2, y2, cls) {
  const l = document.createElementNS(NS, "line");
  l.setAttribute("x1", x1); l.setAttribute("y1", y1); l.setAttribute("x2", x2); l.setAttribute("y2", y2);
  l.setAttribute("class", cls);
  svg.appendChild(l);
  return l;
}
function circle(cx, cy, rr, cls, attrs) {
  const c = document.createElementNS(NS, "circle");
  c.setAttribute("cx", cx); c.setAttribute("cy", cy); c.setAttribute("r", rr); c.setAttribute("class", cls);
  for (const k in attrs) c.setAttribute(k, attrs[k]);
  return c;
}
function text(x, y, s, cls) {
  const t = document.createElementNS(NS, "text");
  t.setAttribute("x", x); t.setAttribute("y", y); t.setAttribute("class", cls);
  t.textContent = s;
  return t;
}

vscode.postMessage({ type: "ready" });
