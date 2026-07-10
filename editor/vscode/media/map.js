// The feature-map webview's client script (rail visualization, redesigned from
// experiments/patch_clustering/out/rail2.html's language). Replaces the old sgtGraph sidebar
// TreeView: hierarchy on the left (subsystem -> feature, DFS rows, collapsible), a shared
// commit-index timeline on the right (op-kind glyphs + per-feature lifebars), and cross-feature
// structural dependency edges as curved connectors. Color is resolved host-side (mapView.ts calls
// the same colorForNode() color.ts already uses) and arrives pre-resolved on every node -- this
// file never reimplements the OKLCH generator. Hover = transient dim/light preview (CSS opacity
// transition only, no animation library); click = sticky select + detail panel + action bar;
// hovering an action live-previews its real impact via `previewVerb` postMessage round-trips to
// the extension host, which spawns `sgt preview <verb> ... --json` (sgt.ts/mapView.ts).

// ─── Layout engine ────────────────────────────────────────────────────────────────────────────
// Pure: takes `map_view` + `history_view` (plus render opts), returns row/lifebar/edge placement.
// No DOM, no color, no anime -- sliced out and exercised under node (tests/test_map_layout.py).
function computeLayout(map, history, opts) {
  const collapsed = new Set((opts && opts.collapsed) || []);
  const topK = (opts && opts.topK) || 6;
  const byId = {};
  for (const n of map.nodes || []) byId[n.id] = n;

  // DFS rows, depth-indented, skipping a collapsed node's children -- the tree's visible slice.
  const rows = [];
  const depthOf = {};
  function visit(id, depth) {
    rows.push(id);
    depthOf[id] = depth;
    if (collapsed.has(id)) return;
    const node = byId[id];
    for (const c of (node && node.children) || []) visit(c, depth + 1);
  }
  for (const r of (map.roots || []).slice().sort()) visit(r, 0);

  const rowOf = {};
  rows.forEach((id, i) => (rowOf[id] = i));

  // Every op, grouped by the feature it belongs to and sorted along the commit-index axis --
  // the per-leaf glyph sequence and lifebar span.
  const opsByFeature = {};
  for (const op of (history && history.ops) || []) {
    if (op.feature_id == null) continue;
    (opsByFeature[op.feature_id] || (opsByFeature[op.feature_id] = [])).push(op);
  }
  for (const fid in opsByFeature) {
    opsByFeature[fid].sort((a, b) => a.commit_index - b.commit_index);
  }
  const lifebars = {};
  for (const fid in opsByFeature) {
    const ops = opsByFeature[fid];
    lifebars[fid] = { start: ops[0].commit_index, end: ops[ops.length - 1].commit_index };
  }

  // A collapsed ancestor absorbs its descendants' edges rather than hiding them -- walk up the
  // parent chain to the nearest row that's actually visible right now.
  function resolveVisible(id) {
    let cur = id;
    while (cur != null && !(cur in rowOf)) {
      const node = byId[cur];
      cur = node ? node.parent : null;
    }
    return cur;
  }

  const rerouted = (map.edges || [])
    .map((e) => ({ a: resolveVisible(e.a), b: resolveVisible(e.b), weight: e.weight }))
    .filter((e) => e.a != null && e.b != null && e.a !== e.b);

  const merged = {};
  for (const e of rerouted) {
    const key = e.a < e.b ? `${e.a} ${e.b}` : `${e.b} ${e.a}`;
    merged[key] = (merged[key] || 0) + e.weight;
  }
  let allEdges = Object.keys(merged).map((key) => {
    const [a, b] = key.split(" ");
    return { a, b, weight: merged[key] };
  });
  allEdges.sort((x, y) => y.weight - x.weight || (x.a + x.b < y.a + y.b ? -1 : 1));

  // Top-K per node, greedily over the weight-sorted list -- never a silent drop: anything past K
  // is counted in `overflow` so the renderer can show a "+N more" affordance.
  const perNode = {};
  const kept = [];
  const overflow = {};
  for (const e of allEdges) {
    const ca = perNode[e.a] || 0, cb = perNode[e.b] || 0;
    if (ca < topK && cb < topK) {
      kept.push(e);
      perNode[e.a] = ca + 1;
      perNode[e.b] = cb + 1;
    } else {
      overflow[e.a] = (overflow[e.a] || 0) + 1;
      overflow[e.b] = (overflow[e.b] || 0) + 1;
    }
  }

  return {
    rows, rowOf, depthOf, lifebars, opsByFeature, edges: kept, overflow,
    commitCount: ((history && history.commits) || []).length,
  };
}
// ---- end-layout (test slice boundary) ----

// ─── Rendering + interaction ──────────────────────────────────────────────────────────────────
// Everything below touches the DOM/vscode API and is not exercised by the node harness.

(function () {
  const vscode = acquireVsCodeApi();
  const GLYPH = { add: "◆", extend: "+", rework: "~", prune: "−", move: "⋔", merge: "⋈", touched: "·" };
  const ROWH = 22;
  const INDENT = 14;
  const DOT_X = 10;
  const LABEL_X = 30;
  const AXIS_X = 340;
  const AXIS_W = 260;

  const state = vscode.getState() || { collapsed: [], selected: null, density: "default" };
  let map = { nodes: [], roots: [], edges: [] };
  let history = { commits: [], ops: [] };
  let layout = computeLayout(map, history, { collapsed: state.collapsed });
  let armedVerb = null; // {verb, feature} while "Merge into..."/"Move ops..." is picking a target
  let previewSeq = 0;

  const root = document.getElementById("root");

  function mk(tag, attrs, children) {
    const el = document.createElementNS(tag === "svg" || tag === "g" || tag === "path" || tag === "circle" || tag === "rect" || tag === "text"
      ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml", tag);
    for (const k in attrs || {}) {
      if (k === "text") el.textContent = attrs[k];
      else el.setAttribute(k, attrs[k]);
    }
    for (const c of children || []) el.appendChild(c);
    return el;
  }

  function byId(id) {
    return (map.nodes || []).find((n) => n.id === id);
  }

  function saveState() {
    vscode.setState(state);
  }

  function recompute() {
    layout = computeLayout(map, history, { collapsed: state.collapsed });
  }

  function render() {
    root.innerHTML = "";
    const height = Math.max(1, layout.rows.length) * ROWH + 20;
    const svg = mk("svg", { width: AXIS_X + AXIS_W + 40, height, class: "rail" });
    const edgeLayer = mk("g", { class: "edges" });
    const rowLayer = mk("g", { class: "rows" });
    svg.appendChild(edgeLayer);
    svg.appendChild(rowLayer);

    for (const e of layout.edges) {
      if (!(e.a in layout.rowOf) || !(e.b in layout.rowOf)) continue;
      const ra = layout.rowOf[e.a], rb = layout.rowOf[e.b];
      const y1 = ra * ROWH + 20, y2 = rb * ROWH + 20;
      const x = DOT_X + (byId(e.a) ? Math.max(depthOfSafe(e.a), depthOfSafe(e.b)) * INDENT : 0);
      const mid = (y1 + y2) / 2;
      const path = mk("path", {
        d: `M ${x} ${y1} C ${x - 24} ${mid}, ${x - 24} ${mid}, ${x} ${y2}`,
        class: "edge", "data-a": e.a, "data-b": e.b,
      });
      edgeLayer.appendChild(path);
    }

    layout.rows.forEach((id, row) => {
      rowLayer.appendChild(renderRow(id, row));
    });

    root.appendChild(svg);
    renderDetail();
  }

  function depthOfSafe(id) {
    return layout.depthOf[id] || 0;
  }

  function renderRow(id, row) {
    const node = byId(id) || { id, label: id, kind: "feature", children: [], color: "#888" };
    const depth = layout.depthOf[id] || 0;
    const y = row * ROWH + 20;
    const g = mk("g", { class: "row", "data-id": id, transform: `translate(0, ${y})` });

    if (node.children && node.children.length) {
      const collapsed = state.collapsed.includes(id);
      g.appendChild(mk("text", {
        x: depth * INDENT - 4, y: 4, class: "caret", text: collapsed ? "▸" : "▾",
      }));
    }
    g.appendChild(mk("circle", { cx: depth * INDENT + DOT_X, cy: 0, r: 4, fill: node.color || "#888", class: "dot" }));
    g.appendChild(mk("text", { x: depth * INDENT + LABEL_X, y: 4, class: "label", text: node.label || id }));

    if (!node.children || !node.children.length) {
      const bar = layout.lifebars[id];
      if (bar) {
        const x1 = AXIS_X + (bar.start / Math.max(1, layout.commitCount - 1)) * AXIS_W;
        const x2 = AXIS_X + (bar.end / Math.max(1, layout.commitCount - 1)) * AXIS_W;
        g.appendChild(mk("line", { x1, x2: Math.max(x2, x1 + 2), y1: 0, y2: 0, class: "lifebar", stroke: node.color || "#888" }));
      }
      for (const op of layout.opsByFeature[id] || []) {
        const x = AXIS_X + (op.commit_index / Math.max(1, layout.commitCount - 1)) * AXIS_W;
        g.appendChild(mk("text", { x, y: 4, class: `glyph glyph-${op.kind}`, text: GLYPH[op.kind] || "·" }));
      }
    }

    g.addEventListener("mouseenter", () => onHover(id));
    g.addEventListener("mouseleave", () => onHover(null));
    g.addEventListener("click", (ev) => {
      if (node.children && node.children.length && ev.target.classList.contains("caret")) {
        toggleCollapse(id);
        return;
      }
      if (armedVerb) {
        confirmArmed(id);
        return;
      }
      selectRow(id);
    });
    return g;
  }

  function toggleCollapse(id) {
    const i = state.collapsed.indexOf(id);
    if (i >= 0) state.collapsed.splice(i, 1);
    else state.collapsed.push(id);
    saveState();
    recompute();
    render();
  }

  const neighborsOf = (id) => {
    const out = new Set();
    for (const e of layout.edges) {
      if (e.a === id) out.add(e.b);
      if (e.b === id) out.add(e.a);
    }
    return out;
  };

  function onHover(id) {
    const svg = root.querySelector("svg");
    if (!svg) return;
    if (!id) {
      svg.classList.remove("focus");
      svg.querySelectorAll(".lit, .ctx").forEach((el) => el.classList.remove("lit", "ctx"));
      if (!armedVerb) clearGhosts();
      return;
    }
    if (armedVerb) {
      // Picking a target while "Merge into..."/"Move ops..." is armed: outline the candidate and
      // live-preview the real op-count/member delta it would produce, via the same blast paint.
      clearGhosts();
      if (id !== armedVerb.feature) {
        root.querySelectorAll(".row").forEach((el) => {
          if (el.getAttribute("data-id") === id) el.classList.add("ghost-target");
        });
        previewArmed(id);
      }
      return;
    }
    svg.classList.add("focus");
    const neighbors = neighborsOf(id);
    svg.querySelectorAll(".row").forEach((el) => {
      const rid = el.getAttribute("data-id");
      el.classList.toggle("lit", rid === id);
      el.classList.toggle("ctx", neighbors.has(rid));
    });
  }

  function previewArmed(targetId) {
    const { verb, feature } = armedVerb;
    if (verb === "merge") {
      requestPreview("merge", [targetId, feature], (res) => {
        if (res && res.ok) paintBlast(res.affected_features || []);
      });
    } else if (verb === "move") {
      const opIds = (layout.opsByFeature[feature] || []).map((op) => op.id);
      requestPreview("move", [...opIds, targetId], (res) => {
        if (res && res.ok) paintBlast(res.affected_features || []);
      });
    }
  }

  function selectRow(id) {
    state.selected = state.selected === id ? null : id;
    saveState();
    render();
  }

  function clearGhosts() {
    root.querySelectorAll(".row.ghost-blast, .row.ghost-target").forEach((el) => {
      el.classList.remove("ghost-blast", "ghost-target");
    });
  }

  function requestPreview(verb, args, onResult) {
    const seq = ++previewSeq;
    vscode.postMessage({ type: "previewVerb", verb, args, seq });
    pendingPreview = { seq, onResult };
  }
  let pendingPreview = null;

  function renderDetail() {
    const panel = document.getElementById("detail");
    panel.innerHTML = "";
    const id = state.selected;
    if (!id) {
      panel.classList.remove("open");
      return;
    }
    const node = byId(id);
    if (!node) {
      panel.classList.remove("open");
      return;
    }
    panel.classList.add("open");

    const h = document.createElement("div");
    h.className = "detail-title";
    h.textContent = node.label || id;
    panel.appendChild(h);

    const why = document.createElement("div");
    why.className = "detail-why";
    why.textContent = node.why || "";
    panel.appendChild(why);

    const meta = document.createElement("div");
    meta.className = "detail-meta";
    meta.textContent = `${node.id} · ${node.size} member(s) · ${node.op_count} op(s)`;
    panel.appendChild(meta);

    if (node.kind === "feature") {
      panel.appendChild(renderActionBar(id));
    }
  }

  function renderActionBar(id) {
    const bar = document.createElement("div");
    bar.className = "action-bar";

    const btn = (label, verb) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.className = "action";
      b.addEventListener("mouseenter", () => previewAction(verb, id));
      b.addEventListener("mouseleave", () => clearGhosts());
      b.addEventListener("click", () => triggerAction(verb, id));
      return b;
    };
    bar.appendChild(btn("Rename", "rename"));
    bar.appendChild(btn("Merge into…", "merge"));
    bar.appendChild(btn("Split", "split"));
    bar.appendChild(btn("Move ops…", "move"));
    bar.appendChild(btn("Revert", "revert"));
    return bar;
  }

  function previewAction(verb, id) {
    if (verb === "rename" || verb === "merge" || verb === "move") return; // needs a target/label first
    if (verb === "split") {
      requestPreview("split", [id], (res) => {
        if (res && res.ok) paintBlast(res.affected_features || []);
      });
    } else if (verb === "revert") {
      requestPreview("revert", [id], (res) => {
        if (res && res.ok) paintBlast(res.affected_features || []);
      });
    }
  }

  function paintBlast(featureIds) {
    root.querySelectorAll(".row.ghost-blast").forEach((el) => el.classList.remove("ghost-blast"));
    root.querySelectorAll(".row").forEach((el) => {
      if (featureIds.includes(el.getAttribute("data-id"))) el.classList.add("ghost-blast");
    });
  }

  function triggerAction(verb, id) {
    if (verb === "rename") {
      vscode.postMessage({ type: "renamePrompt", feature: id });
      return;
    }
    if (verb === "merge" || verb === "move") {
      armedVerb = { verb, feature: id };
      root.classList.add("arming");
      return;
    }
    if (verb === "split" || verb === "revert") {
      vscode.postMessage({ type: "applyVerb", verb, args: [id] });
    }
  }

  function confirmArmed(targetId) {
    const { verb, feature } = armedVerb;
    armedVerb = null;
    root.classList.remove("arming");
    clearGhosts();
    if (targetId === feature) return;
    if (verb === "merge") {
      vscode.postMessage({ type: "applyVerb", verb: "merge", args: [targetId, feature] });
    } else if (verb === "move") {
      const opIds = (layout.opsByFeature[feature] || []).map((op) => op.id);
      vscode.postMessage({ type: "applyVerb", verb: "move", args: [...opIds, targetId] });
    }
  }

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (msg.type === "state") {
      map = msg.map || { nodes: [], roots: [], edges: [] };
      history = msg.history || { commits: [], ops: [] };
      recompute();
      render();
    } else if (msg.type === "previewResult" && pendingPreview && pendingPreview.seq === msg.seq) {
      pendingPreview.onResult(msg.result);
    }
  });

  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && armedVerb) {
      armedVerb = null;
      root.classList.remove("arming");
      clearGhosts();
    }
  });

  vscode.postMessage({ type: "ready" });
  render();
})();
