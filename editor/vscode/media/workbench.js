// The Composition Workbench webview's client script, grounded in
// experiments/patch_clustering/out/rail3.html: a rail pane (hierarchy left, shared commit-index
// timeline right, drawn as one scrollable SVG with a divider between the two regions -- true
// side-by-side scroll-synced panes land with windowing in a later phase) and an inspector pane on
// the right (detail, action bar, and a code(I) panel driven by one `foldAt` round-trip per
// selection). A titlebar composition selector (QuickPick over HEAD + sessions) and an oracle chip
// round out the 3-pane skeleton. Color is resolved host-side (workbench.ts calls the same
// colorForNode() color.ts already uses) and arrives pre-resolved on every node -- this file never
// reimplements the OKLCH generator. Hover = transient dim/light preview; click = sticky select +
// inspector + action bar; hovering an action live-previews its real impact via `previewVerb`
// postMessage round-trips to the extension host, which spawns `sgt preview <verb> ... --json`.
//
// Phase-3 scope: the armed-action pattern below only generalizes merge/move (as it always has);
// generalizing it over every verb is a later phase.
//
// Phase 4 adds the draggable playhead: a handle over the shared commit-index axis, dragged or
// clicked to any commit index, folding that `{commitIndex}` frontier live (debounced 250ms,
// snapped to the axis's op columns since there is one column per commit index already). It is a
// read-only exploration mode layered on top of the existing selection/action-bar state, not a
// replacement for the composition selector -- previews and applies still run against the real
// composition (state.compositionRef), never against the scrubbed frontier.

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

  const state = vscode.getState() || {
    collapsed: [], selected: null, compositionLabel: "HEAD", compositionRef: "HEAD",
  };
  let compose = {
    map: { nodes: [], roots: [], edges: [] }, history: { commits: [], ops: [] },
    status: { oracle: { configured: false, status: "pending" } }, sessions: { sessions: [] }, proposals: [],
  };
  let map = compose.map;
  let history = compose.history;
  let layout = computeLayout(map, history, { collapsed: state.collapsed });
  let armedVerb = null; // {verb, feature} while "Merge into..."/"Move ops..." is picking a target
  let previewSeq = 0;
  let pendingPreview = null;
  let foldSeq = 0;
  let pendingFold = null;
  let foldResultCache = {}; // featureId -> {files, oracle_verdict, forked, error}, reset per composition

  // Playhead (Phase 4): a commit-index frontier the user is scrubbing, independent of `state`
  // (it's a transient exploration mode, not worth persisting across a webview reload).
  let playheadCommitIndex = null;
  let playheadLineEl = null;
  let playheadHandleEl = null;
  let playheadDragging = false;
  let playheadSeq = 0;
  let pendingPlayhead = null;
  let playheadResultCache = {}; // commitIndex -> {op_count, files, oracle_verdict, forked, error}
  let scrubTimer = null;

  const rail = document.getElementById("rail");
  const inspector = document.getElementById("inspector");
  const compositionBtn = document.getElementById("compositionBtn");
  const oracleChip = document.getElementById("oracleChip");
  const offscreenAbove = document.getElementById("offscreenAbove");
  const offscreenBelow = document.getElementById("offscreenBelow");

  const SVG_TAGS = new Set(["svg", "g", "path", "circle", "rect", "text", "line"]);

  function mk(tag, attrs, children) {
    const ns = SVG_TAGS.has(tag) ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml";
    const el = document.createElementNS(ns, tag);
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

  function renderTitlebar() {
    compositionBtn.textContent = state.compositionLabel || "HEAD";
    const oracle = (compose.status && compose.status.oracle) || { configured: false, status: "pending" };
    const st = oracle.configured ? oracle.status : "unconfigured";
    oracleChip.dataset.state = st;
    oracleChip.textContent = `oracle: ${st}`;
  }

  function render() {
    renderTitlebar();
    rail.innerHTML = "";
    const height = Math.max(1, layout.rows.length) * ROWH + 20;
    const svg = mk("svg", { width: AXIS_X + AXIS_W + 40, height, class: "railsvg" });
    const edgeLayer = mk("g", { class: "edges" });
    const rowLayer = mk("g", { class: "rows" });
    svg.appendChild(mk("line", { x1: AXIS_X - 14, x2: AXIS_X - 14, y1: 0, y2: height, class: "rail-divider" }));
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

    const canScrub = layout.commitCount > 1;
    if (!canScrub) playheadCommitIndex = null;
    playheadLineEl = mk("line", { x1: 0, x2: 0, y1: 0, y2: height, class: "playhead-line playhead-hidden" });
    playheadHandleEl = mk("circle", { cx: 0, cy: 6, r: 5, class: "playhead-handle playhead-hidden" });
    svg.appendChild(playheadLineEl);
    svg.appendChild(playheadHandleEl);
    if (canScrub) {
      positionPlayhead(playheadCommitIndex);
      svg.addEventListener("pointerdown", onPlayheadPointerDown);
    }

    rail.appendChild(svg);
    renderInspector();
  }

  // ─── Playhead (Phase 4) ────────────────────────────────────────────────────────────────────
  // The axis has exactly `layout.commitCount` columns (one per mined commit), evenly spaced over
  // [AXIS_X, AXIS_X+AXIS_W] -- rounding to the nearest column is the "snap to op columns" the
  // plan asks for, since there's nothing finer-grained to snap between.
  function commitIndexToX(idx) {
    return AXIS_X + (idx / Math.max(1, layout.commitCount - 1)) * AXIS_W;
  }

  function xToCommitIndex(x) {
    const frac = (x - AXIS_X) / AXIS_W;
    const idx = Math.round(frac * Math.max(1, layout.commitCount - 1));
    return Math.max(0, Math.min(layout.commitCount - 1, idx));
  }

  function positionPlayhead(idx) {
    if (!playheadLineEl || !playheadHandleEl) return;
    const visible = idx != null;
    const x = commitIndexToX(visible ? idx : 0);
    playheadLineEl.setAttribute("x1", x);
    playheadLineEl.setAttribute("x2", x);
    playheadHandleEl.setAttribute("cx", x);
    playheadLineEl.classList.toggle("playhead-hidden", !visible);
    playheadHandleEl.classList.toggle("playhead-hidden", !visible);
  }

  function setPlayhead(idx) {
    if (playheadCommitIndex === idx) return;
    playheadCommitIndex = idx;
    positionPlayhead(idx);
    renderInspector();
    scheduleScrub(idx);
  }

  function clearPlayhead() {
    if (playheadCommitIndex == null) return;
    clearTimeout(scrubTimer);
    playheadCommitIndex = null;
    positionPlayhead(null);
    renderInspector();
  }

  function scheduleScrub(idx) {
    clearTimeout(scrubTimer);
    scrubTimer = setTimeout(() => requestScrub(idx), 250);
  }

  function requestScrub(idx) {
    if (playheadCommitIndex !== idx) return; // superseded by a later drag position
    if (playheadResultCache[idx]) {
      renderInspector();
      return;
    }
    const seq = ++playheadSeq;
    pendingPlayhead = { seq, commitIndex: idx };
    vscode.postMessage({ type: "scrubPlayhead", commitIndex: idx, seq });
    renderInspector(); // "Loading…" for this frontier
  }

  function svgLocalX(svg, clientX) {
    return clientX - svg.getBoundingClientRect().left;
  }

  function onPlayheadPointerDown(ev) {
    const svg = ev.currentTarget;
    const isBackground = ev.target === svg;
    const isHandle = ev.target === playheadHandleEl;
    if (!isBackground && !isHandle) return; // let a row's own dot/label/lifebar/glyph click through
    const x = svgLocalX(svg, ev.clientX);
    if (x < AXIS_X - 14) return; // left of the rail divider: not the timeline
    playheadDragging = true;
    if (svg.setPointerCapture) svg.setPointerCapture(ev.pointerId);
    setPlayhead(xToCommitIndex(x));
    window.addEventListener("pointermove", onPlayheadPointerMove);
    window.addEventListener("pointerup", onPlayheadPointerUp);
  }

  function onPlayheadPointerMove(ev) {
    if (!playheadDragging) return;
    const svg = rail.querySelector("svg");
    if (!svg) return;
    setPlayhead(xToCommitIndex(svgLocalX(svg, ev.clientX)));
  }

  function onPlayheadPointerUp() {
    playheadDragging = false;
    window.removeEventListener("pointermove", onPlayheadPointerMove);
    window.removeEventListener("pointerup", onPlayheadPointerUp);
  }

  // Mirrors `overall_status()` in sgt/core/oracle.py: override wins if present; else "fail" if
  // any recorded tier failed; else "pass" if at least one tier ran; else "pending".
  function oracleStatus(record) {
    if (!record) return "pending";
    if (record.override) return record.override.status;
    const tiers = Object.values(record.tiers || {});
    if (tiers.some((t) => t.status === "fail")) return "fail";
    return tiers.length ? "pass" : "pending";
  }

  function depthOfSafe(id) {
    return layout.depthOf[id] || 0;
  }

  function renderRow(id, row) {
    const node = byId(id) || { id, label: id, kind: "feature", children: [], color: "#888" };
    const depth = depthOfSafe(id);
    const y = row * ROWH + 20;
    const g = mk("g", { class: "row", "data-id": id, transform: `translate(0, ${y})` });
    if (id === state.selected) g.classList.add("selected");

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
        const x1 = commitIndexToX(bar.start);
        const x2 = commitIndexToX(bar.end);
        g.appendChild(mk("line", { x1, x2: Math.max(x2, x1 + 2), y1: 0, y2: 0, class: "lifebar", stroke: node.color || "#888" }));
      }
      for (const op of layout.opsByFeature[id] || []) {
        const x = commitIndexToX(op.commit_index);
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

  function opIdsFor(featureId) {
    return (layout.opsByFeature[featureId] || []).map((op) => op.id);
  }

  function onHover(id) {
    const svg = rail.querySelector("svg");
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
        rail.querySelectorAll(".row").forEach((el) => {
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
      previewAndBlast("merge", [targetId, feature]);
    } else if (verb === "move") {
      previewAndBlast("move", [...opIdsFor(feature), targetId]);
    }
  }

  function selectRow(id) {
    state.selected = state.selected === id ? null : id;
    saveState();
    render();
    const node = state.selected && byId(state.selected);
    if (node && node.kind === "feature") requestFold(state.selected);
  }

  function clearGhosts() {
    rail.querySelectorAll(".row.ghost-blast, .row.ghost-target").forEach((el) => {
      el.classList.remove("ghost-blast", "ghost-target");
    });
    clearOffscreenPills();
  }

  function requestPreview(verb, args, onResult) {
    const seq = ++previewSeq;
    vscode.postMessage({ type: "previewVerb", verb, args, seq });
    pendingPreview = { seq, onResult };
  }

  // Every hover-preview site wants the same thing: paint the blast radius if the preview came
  // back ok, do nothing otherwise.
  function previewAndBlast(verb, args) {
    requestPreview(verb, args, (res) => {
      if (res && res.ok) paintBlast(res.affected_features || []);
    });
  }

  // One non-interactive fold per selection (Phase 3): ask the host to fold the current
  // composition and hand back only the files under this feature's directory. Stale responses
  // (an older selection's fold landing after a newer one) are dropped by sequence number, same
  // pattern as `requestPreview`.
  function requestFold(featureId) {
    const seq = ++foldSeq;
    pendingFold = { seq, featureId };
    vscode.postMessage({ type: "requestFold", featureId, ref: state.compositionRef || "HEAD", seq });
    renderInspector(); // paint a "Loading…" placeholder immediately
  }

  function renderInspector() {
    inspector.innerHTML = "";
    const id = state.selected;
    const node = id && byId(id);

    if (node) {
      const h = document.createElement("div");
      h.className = "detail-title";
      h.textContent = node.label || id;
      inspector.appendChild(h);

      const why = document.createElement("div");
      why.className = "detail-why";
      why.textContent = node.why || "";
      inspector.appendChild(why);

      const meta = document.createElement("div");
      meta.className = "detail-meta";
      meta.textContent = `${node.id} · ${node.size} member(s) · ${node.op_count} op(s)`;
      inspector.appendChild(meta);

      if (node.kind === "feature") inspector.appendChild(renderActionBar(id));
    }

    // The playhead panel takes over the code(I) slot while scrubbing -- it's a read-only view of
    // a different frontier than the one the action bar above still previews/applies against, so
    // the two never render at once.
    if (playheadCommitIndex != null) {
      renderPlayheadPanel(playheadCommitIndex, node && node.kind === "feature" ? node : null);
    } else if (node && node.kind === "feature") {
      renderCodePanel(id);
    }
  }

  function renderFileList(container, files) {
    const paths = Object.keys(files || {}).sort();
    if (!paths.length) {
      container.appendChild(statusLine("No files under this path at this frontier."));
      return;
    }
    for (const path of paths) {
      const file = document.createElement("div");
      file.className = "code-file";
      const label = document.createElement("div");
      label.className = "code-file-path";
      label.textContent = path;
      const pre = document.createElement("pre");
      pre.className = "code-file-body";
      pre.textContent = files[path];
      file.appendChild(label);
      file.appendChild(pre);
      container.appendChild(file);
    }
  }

  // Loading/error/file-list/forked-warning body shared by the selection code panel and the
  // playhead panel: `cached` is undefined while loading, `{error}` on failure, or a result with
  // `files`/`forked` on success.
  function renderCachedFrontierBody(section, cached, files) {
    if (!cached) {
      section.appendChild(statusLine("Loading…"));
      return;
    }
    if (cached.error) {
      section.appendChild(statusLine(cached.error, "error"));
      return;
    }
    renderFileList(section, files);
    if (cached.forked) {
      section.appendChild(statusLine("This frontier has an open fork here.", "warn"));
    }
  }

  function renderCodePanel(id) {
    const section = document.createElement("div");
    section.className = "code-panel";
    const heading = document.createElement("div");
    heading.className = "code-panel-heading";
    heading.textContent = `code(I) @ ${state.compositionLabel || "HEAD"}`;
    section.appendChild(heading);

    const cached = foldResultCache[id];
    renderCachedFrontierBody(section, cached, cached && cached.files);
    inspector.appendChild(section);
  }

  // The playhead's frontier is unfiltered (the host folds the whole thing, once per commit
  // index) -- filtering to the selected feature's `dir` happens here, client-side, so dragging
  // through a run of commit indices never re-requests per feature-selection change.
  function renderPlayheadPanel(idx, featureNode) {
    const section = document.createElement("div");
    section.className = "code-panel";

    const heading = document.createElement("div");
    heading.className = "code-panel-heading";
    const cached = playheadResultCache[idx];
    const iFlag = cached && cached.op_count != null ? ` · I·${cached.op_count}` : "";
    heading.textContent = `code(I) @ commit ${idx}${iFlag}`;
    section.appendChild(heading);

    const back = document.createElement("button");
    back.className = "code-panel-back";
    back.textContent = `Back to ${state.compositionLabel || "HEAD"}`;
    back.addEventListener("click", clearPlayhead);
    section.appendChild(back);

    if (cached && !cached.error) {
      const st = oracleStatus(cached.oracle_verdict);
      section.appendChild(statusLine(`oracle: ${st}`, st === "fail" ? "error" : undefined));
    }

    const allPaths = (cached && cached.files) || {};
    const files = featureNode
      ? Object.fromEntries(Object.entries(allPaths).filter(([p]) => p.startsWith(featureNode.dir)))
      : allPaths;
    renderCachedFrontierBody(section, cached, files);
    inspector.appendChild(section);
  }

  function statusLine(text, kind) {
    const el = document.createElement("div");
    el.className = "code-panel-status" + (kind ? ` ${kind}` : "");
    el.textContent = text;
    return el;
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
    if (verb === "split" || verb === "revert") previewAndBlast(verb, [id]);
  }

  function paintBlast(featureIds) {
    rail.querySelectorAll(".row.ghost-blast").forEach((el) => el.classList.remove("ghost-blast"));
    rail.querySelectorAll(".row").forEach((el) => {
      if (featureIds.includes(el.getAttribute("data-id"))) el.classList.add("ghost-blast");
    });
    renderOffscreenPills(featureIds);
  }

  // Off-screen affected (huge-tree scale): a blast can paint rows outside #rail's current scroll
  // window, where a hover preview would otherwise be invisible. Pills are anchored to #main (not
  // #rail) so they stay pinned regardless of scroll, per-row visibility is a plain
  // getBoundingClientRect comparison against #rail's own rect (no virtualization to account for
  // yet -- every row is a real DOM node today), and lookups match `paintBlast`'s own
  // getAttribute equality rather than a template-literal attribute selector, so a feature id with
  // a quote in it can't break the query.
  function findRow(id) {
    let found = null;
    rail.querySelectorAll(".row").forEach((el) => {
      if (el.getAttribute("data-id") === id) found = el;
    });
    return found;
  }

  function renderOffscreenPills(featureIds) {
    if (!featureIds || !featureIds.length) {
      clearOffscreenPills();
      return;
    }
    const railRect = rail.getBoundingClientRect();
    const above = [];
    const below = [];
    for (const id of featureIds) {
      const row = findRow(id);
      if (!row) continue;
      const r = row.getBoundingClientRect();
      if (r.bottom < railRect.top) above.push(id);
      else if (r.top > railRect.bottom) below.push(id);
    }
    offscreenAbove.hidden = !above.length;
    if (above.length) {
      offscreenAbove.textContent = `▲ ${above.length} above`;
      offscreenAbove.onclick = () => scrollRowIntoView(above[0]);
    }
    offscreenBelow.hidden = !below.length;
    if (below.length) {
      offscreenBelow.textContent = `▼ ${below.length} below`;
      offscreenBelow.onclick = () => scrollRowIntoView(below[0]);
    }
  }

  function scrollRowIntoView(id) {
    const row = findRow(id);
    if (row) row.scrollIntoView({ block: "center" });
  }

  function clearOffscreenPills() {
    offscreenAbove.hidden = true;
    offscreenBelow.hidden = true;
  }

  function triggerAction(verb, id) {
    if (verb === "rename") {
      vscode.postMessage({ type: "renamePrompt", feature: id });
      return;
    }
    if (verb === "merge" || verb === "move") {
      armedVerb = { verb, feature: id };
      rail.classList.add("arming");
      return;
    }
    if (verb === "split" || verb === "revert") {
      vscode.postMessage({ type: "applyVerb", verb, args: [id] });
    }
  }

  function confirmArmed(targetId) {
    const { verb, feature } = armedVerb;
    armedVerb = null;
    rail.classList.remove("arming");
    clearGhosts();
    if (targetId === feature) return;
    if (verb === "merge") {
      vscode.postMessage({ type: "applyVerb", verb: "merge", args: [targetId, feature] });
    } else if (verb === "move") {
      vscode.postMessage({ type: "applyVerb", verb: "move", args: [...opIdsFor(feature), targetId] });
    }
  }

  compositionBtn.addEventListener("click", () => vscode.postMessage({ type: "pickComposition" }));

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (msg.type === "state") {
      compose = msg.compose || compose;
      map = compose.map || { nodes: [], roots: [], edges: [] };
      history = compose.history || { commits: [], ops: [] };
      foldResultCache = {};
      playheadResultCache = {};
      playheadCommitIndex = null; // a new composition's commit-index axis means different columns
      recompute();
      render();
    } else if (msg.type === "previewResult" && pendingPreview && pendingPreview.seq === msg.seq) {
      pendingPreview.onResult(msg.result);
    } else if (msg.type === "foldResult" && pendingFold && pendingFold.seq === msg.seq) {
      pendingFold = null;
      foldResultCache[msg.featureId] = {
        files: msg.files, oracle_verdict: msg.oracle_verdict, forked: msg.forked, error: msg.error,
      };
      if (state.selected === msg.featureId) renderInspector();
    } else if (msg.type === "playheadResult" && pendingPlayhead && pendingPlayhead.seq === msg.seq) {
      pendingPlayhead = null;
      playheadResultCache[msg.commitIndex] = {
        op_count: msg.op_count, files: msg.files, oracle_verdict: msg.oracle_verdict,
        forked: msg.forked, error: msg.error,
      };
      if (playheadCommitIndex === msg.commitIndex) renderInspector();
    } else if (msg.type === "compositionPicked") {
      state.compositionLabel = msg.label;
      state.compositionRef = msg.ref;
      saveState();
      foldResultCache = {};
      renderTitlebar();
      if (state.selected) requestFold(state.selected);
    } else if (msg.type === "error") {
      inspector.innerHTML = "";
      inspector.appendChild(statusLine(msg.message, "error"));
    }
  });

  window.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (armedVerb) {
      armedVerb = null;
      rail.classList.remove("arming");
      clearGhosts();
      return;
    }
    clearPlayhead();
  });

  vscode.postMessage({ type: "ready" });
  render();
})();
