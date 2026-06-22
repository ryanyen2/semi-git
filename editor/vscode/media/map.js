// @ts-check
// Code Map renderer. Entities grouped by connected component, each colored by its owning feature
// (color computed host-side in color.ts and delivered on the entity). Edges shown are the
// transitive reduction (direct relationships only). A checkpoint scrubber requests timeframe
// frames; between adjacent frames, born / grown / retired entities are highlighted so the user
// reads the codebase developing rather than two unrelated snapshots.

const vscode = acquireVsCodeApi();

const $ = (id) => document.getElementById(id);
let frames = 1;
let atNow = true;
let prevIds = new Set(); // entity ids in the previously-rendered frame (for delta highlight)

const STATUS_DIM = "var(--vscode-descriptionForeground)";

function shortLabel(e) {
  // "file.py::Class.method" -> "Class.method  ·  file.py"
  const file = e.file.split("/").slice(-1)[0];
  return { name: e.name, file };
}

function render(map, isFrame) {
  const container = $("components");
  const empty = $("empty");
  container.textContent = "";
  const entities = map.entities || [];
  $("count").textContent = `${map.count} entities · ${map.clusters?.length || 0} capabilities`;
  empty.hidden = entities.length > 0;

  const ids = new Set(entities.map((e) => e.id));
  // depends_on already reflects the reduced edge set (per entity) from the projection.
  const byComp = new Map();
  (map.components || []).forEach((comp, i) => byComp.set(i, []));
  const compOf = new Map();
  (map.components || []).forEach((comp, i) => comp.forEach((id) => compOf.set(id, i)));
  for (const e of entities) {
    const c = compOf.has(e.id) ? compOf.get(e.id) : -1;
    if (!byComp.has(c)) byComp.set(c, []);
    byComp.get(c).push(e);
  }

  for (const [, members] of [...byComp.entries()].sort((a, b) => a[0] - b[0])) {
    if (!members.length) continue;
    const box = document.createElement("div");
    box.className = "component";
    for (const e of members.sort((a, b) => a.id.localeCompare(b.id))) {
      box.appendChild(rowFor(e, isFrame));
    }
    container.appendChild(box);
  }
  prevIds = ids;
}

function rowFor(e, isFrame) {
  const row = document.createElement("div");
  row.className = "entity-row";
  if (isFrame && !prevIds.has(e.id)) row.classList.add("born"); // new vs previous frame
  const { name, file } = shortLabel(e);

  const dot = document.createElement("span");
  dot.className = "dot";
  if (e.color) {
    dot.style.color = e.color; // owning-feature identity hue
    dot.textContent = "●";
  } else {
    dot.style.color = STATUS_DIM; // unattributed: untracked / TypeScript / module-level
    dot.textContent = "○";
  }

  const kind = document.createElement("span");
  kind.className = "kind";
  kind.textContent = e.kind;

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = name;

  const path = document.createElement("span");
  path.className = "path";
  path.textContent = file;

  const deps = document.createElement("span");
  deps.className = "deps";
  if (e.depends_on && e.depends_on.length) {
    deps.textContent = "→ " + e.depends_on.map((d) => d.split("::").slice(-1)[0]).join(", ");
  }

  row.append(dot, kind, label, path, deps);
  row.title = e.id + (e.node_id ? `  ·  ${e.node_id}` : "  ·  unattributed");
  return row;
}

function setupScrubber() {
  const s = /** @type {HTMLInputElement} */ ($("scrubber"));
  s.max = String(frames);
  s.value = String(frames);
  s.oninput = () => {
    const v = Number(s.value);
    atNow = v >= frames;
    $("frame-label").textContent = atNow ? "now" : `frame ${v}`;
    if (atNow) {
      vscode.postMessage({ type: "ready" }); // live map
    } else {
      vscode.postMessage({ type: "scrub", frame: v });
    }
  };
  $("now").onclick = () => {
    s.value = String(frames);
    s.oninput?.(new Event("input"));
  };
}

window.addEventListener("message", (ev) => {
  const m = ev.data;
  if (!m) return;
  if (m.type === "map") {
    frames = Math.max(1, m.frames || 1);
    atNow = true;
    setupScrubber();
    $("frame-label").textContent = "now";
    prevIds = new Set(); // reset delta baseline on a fresh live load
    render(m.map, false);
  } else if (m.type === "frame") {
    render(m.map, true);
  } else if (m.type === "error") {
    $("count").textContent = "error: " + m.message;
  }
});

vscode.postMessage({ type: "ready" });
