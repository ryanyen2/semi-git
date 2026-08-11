// The Composition Workbench webview's client script. Left pane is the DEPENDENCY GRAPH (see
// `computeGraphLayout`): features as identity-colored discs (area = op count), co-change edges,
// laid out by temporal rank (x = the generation of a feature's median op) and barycenter
// crossing-min order (y), with collapsed subsystems as meta-nodes and a bottom time-axis frontier
// scrubber. A commit-index is not a snapshot node -- it's a set of ops across features -- so we
// draw the features, not a commit spine. The right pane is the inspector (detail, action bar, a
// code(I) panel driven by one `foldAt` per selection, the op-set decomposition of a scrubbed
// point, and the working-changes quick-save card). A titlebar composition selector (QuickPick over
// HEAD + sessions) and an oracle chip round out the skeleton. Color is resolved host-side
// (workbench.ts calls color.ts's colorForNode) and arrives pre-resolved on every node -- this file
// never reimplements the OKLCH generator.
//
// Interaction: hover a node = dim the field, light it + its co-change neighbors + their edges;
// click a feature = sticky select + inspector + action bar; click a meta-node = expand the
// subsystem; hovering an action live-previews its blast radius via `previewVerb` postMessage
// round-trips to the host (`sgt preview <verb> ... --json`). The armed-action pattern generalizes
// merge/move. The frontier scrubber (bottom axis) is a read-only fold point: it dims nodes past
// the point (the graph accretes) and drives the code(I)/op-set panels, but previews/applies still
// run against the real composition (state.compositionRef), never the scrubbed frontier.

// ---- graph-layout (test slice boundary) ----
// The feature timeline -- a Gantt. A commit-index is NOT a snapshot node: it's a set of ops spread
// across features. So we don't draw a commit spine or a node cloud; we draw one LANE per feature.
//   x  = real commit-time, a shared labeled axis. A feature's bar spans [firstCommit, lastCommit]
//        (born → last touched); its ops are binned along it as a density heatstrip by the renderer,
//        so a 2000-op commit is one dark column, never a wall of glyphs.
//   y  = features grouped into SUBSYSTEM SWIMLANES, ordered by first appearance (foundations up
//        top). Vertical position means "which feature / which subsystem" -- something a reader can
//        actually use -- so there is no barycenter/crossing-min pass at all.
//   frontier = a "now" line on the time axis; ops past it don't count yet, so scrubbing accretes.
// Co-change stays OFF the default view (it made the old node-link diagram a hairball): edges are
// still computed (top-K per feature) but only lit on hover. Pure (no DOM/color): sliced out and
// exercised under node (tests/test_graph_layout.py).
function computeGraphLayout(map, grid, opts) {
  opts = opts || {};
  const collapsed = new Set(opts.collapsed || []);
  const frontier = opts.frontier == null ? Infinity : opts.frontier;
  const topK = opts.topK || 4;

  const byId = {};
  for (const n of map.nodes || []) byId[n.id] = n;

  // The op -> (feature, commit) join is `grid_view`'s already-computed cell table (plan U3, the
  // canonical `sgt.api.grid_view`): a cell carries the ops one feature touched in one commit, so a
  // feature's ops are just the cells bearing its id -- no per-op DAG walk here. An op with no
  // feature has no cell and never appears. `frontier` folds by commit index so scrubbing accretes.
  const opsByFeature = {};
  for (const cell of (grid && grid.cells) || []) {
    if (cell.commit_index > frontier) continue;
    const bucket = opsByFeature[cell.feature_id] || (opsByFeature[cell.feature_id] = []);
    for (const oid of cell.op_ids || []) bucket.push({ id: oid, commit_index: cell.commit_index });
  }
  for (const fid in opsByFeature) opsByFeature[fid].sort((a, b) => a.commit_index - b.commit_index);

  // Visible lanes: DFS the tree; a collapsed subsystem folds to ONE meta-lane (its subtree
  // aggregated), an expanded subsystem contributes its feature descendants as lanes under a header,
  // a feature is always a lane. `subsystem` = the grouping key (a meta-lane groups under itself).
  const visible = [];
  function nearestSubsystem(id) {
    let cur = byId[id] ? byId[id].parent : null;
    while (cur != null) {
      const p = byId[cur];
      if (p && p.kind === "subsystem") return cur;
      cur = p ? p.parent : null;
    }
    return null;
  }
  function leavesUnder(id) {
    const out = [];
    const stack = [id];
    while (stack.length) {
      const node = byId[stack.pop()];
      if (!node) continue;
      if (node.children && node.children.length) for (const c of node.children) stack.push(c);
      else out.push(node.id);
    }
    return out;
  }
  const seen = new Set();
  function visit(id) {
    const node = byId[id];
    if (!node) return;
    // The map is a DAG: a feature can be a child of more than one subsystem, so the same node is
    // reachable by multiple paths. Emit it once -- a duplicate lane shares an id, and the id-keyed
    // `laneById` would drop all but the last copy, leaving a stray, un-rowed duplicate.
    if (seen.has(id)) return;
    seen.add(id);
    const isSub = node.kind === "subsystem";
    if (isSub && !collapsed.has(id)) {
      for (const c of node.children || []) visit(c);
      return;
    }
    const isMeta = isSub; // a visible subsystem here is necessarily collapsed
    const leaves = isMeta ? leavesUnder(id) : [id];
    visible.push({ id, isMeta, leaves, subsystem: isMeta ? id : nearestSubsystem(id) });
  }
  for (const r of (map.roots || []).slice().sort()) visit(r);

  // Aggregate ops -> op count + first/last commit + the sorted commit list (renderers bin it into a
  // density strip at their own column resolution). Lanes with no ops at this frontier don't exist
  // yet -- dropped, so scrubbing left empties the timeline.
  const lanes = [];
  for (const v of visible) {
    const commits = [];
    for (const leaf of v.leaves) for (const op of opsByFeature[leaf] || []) commits.push(op.commit_index);
    if (!commits.length) continue;
    commits.sort((a, b) => a - b);
    lanes.push({
      ...v, opCount: commits.length, firstCommit: commits[0], lastCommit: commits[commits.length - 1], commits,
    });
  }
  const laneById = {};
  for (const l of lanes) laneById[l.id] = l;

  // Visible co-change edges (hover overlay only): reroute each endpoint up to its nearest visible
  // lane, drop self-loops, merge parallels (sum weight), keep top-K per lane by weight.
  const idToVisible = {};
  for (const l of lanes) for (const leaf of l.leaves) idToVisible[leaf] = l.id;
  const laneIds = new Set(lanes.map((l) => l.id));
  function resolveVisible(id) {
    if (idToVisible[id]) return idToVisible[id];
    let cur = id;
    while (cur != null && !laneIds.has(cur)) cur = byId[cur] ? byId[cur].parent : null;
    return cur;
  }
  const merged = {};
  for (const e of map.edges || []) {
    const a = resolveVisible(e.a);
    const b = resolveVisible(e.b);
    if (a == null || b == null || a === b) continue;
    const key = a < b ? `${a} ${b}` : `${b} ${a}`;
    merged[key] = (merged[key] || 0) + (e.weight || 0);
  }
  let allEdges = Object.keys(merged).map((k) => {
    const [a, b] = k.split(" ");
    return { a, b, weight: merged[k] };
  });
  allEdges.sort((x, y) => y.weight - x.weight || (x.a + x.b < y.a + y.b ? -1 : 1));
  const perNode = {};
  const edges = [];
  const overflow = {};
  for (const e of allEdges) {
    const ca = perNode[e.a] || 0, cb = perNode[e.b] || 0;
    if (ca < topK && cb < topK) {
      edges.push(e);
      perNode[e.a] = ca + 1;
      perNode[e.b] = cb + 1;
    } else {
      overflow[e.a] = (overflow[e.a] || 0) + 1;
      overflow[e.b] = (overflow[e.b] || 0) + 1;
    }
  }

  // Emit rows in TREE order so nesting is visible: walk the map from its roots; at each level
  // siblings are ordered by first appearance (min descendant firstCommit); an expanded subsystem is
  // a header row with its descendants rendered one level deeper; a collapsed subsystem is a single
  // meta-lane; a feature is a lane. `depth` (root = 0, +1 per subsystem level) drives the render
  // indent, so a sub-subsystem steps in visually under its parent instead of flattening onto the
  // same level. Ordering by first appearance is preserved WITHIN each level (the old flat behaviour,
  // now applied recursively) rather than across the whole tree.
  const laneSet = new Set(lanes.map((l) => l.id));
  const childrenOf = (id) => (byId[id] && byId[id].children) || [];
  // A node earns a row iff it's a lane, or an expanded subsystem with >=1 descendant lane.
  const presentCache = {};
  function isPresent(id) {
    if (id in presentCache) return presentCache[id];
    let present = laneSet.has(id);
    if (!present) for (const c of childrenOf(id)) if (isPresent(c)) { present = true; break; }
    return (presentCache[id] = present);
  }
  // Earliest firstCommit anywhere under a node (a lane returns its own) -- the per-level sort key.
  const firstCache = {};
  function subtreeFirst(id) {
    if (id in firstCache) return firstCache[id];
    const l = laneById[id];
    let best = l ? l.firstCommit : Infinity;
    if (!l) for (const c of childrenOf(id)) best = Math.min(best, subtreeFirst(c));
    return (firstCache[id] = best);
  }
  // A header's rolled-up magnitude over every descendant feature lane (collapsed metas included).
  function rollup(id) {
    let opCount = 0, laneCount = 0, lastCommit = -Infinity;
    (function walk(nid) {
      const l = laneById[nid];
      if (l) { opCount += l.opCount; lastCommit = Math.max(lastCommit, l.lastCommit); laneCount += l.isMeta ? l.leaves.length : 1; }
      else for (const c of childrenOf(nid)) walk(c);
    })(id);
    return { opCount, laneCount, lastCommit };
  }
  const sortByFirst = (a, b) => subtreeFirst(a) - subtreeFirst(b) || (a < b ? -1 : 1);

  let row = 0;
  const headers = [];
  function emit(id, depth) {
    const lane = laneById[id];
    if (lane) { // a feature leaf or a collapsed-subsystem meta-lane -- one row, no recursion
      lane.row = row++;
      lane.depth = depth;
      lane.groupKey = byId[id] ? byId[id].parent : null;
      return;
    }
    const node = byId[id];
    if (!node || node.kind !== "subsystem" || !isPresent(id)) return; // expanded subsystem header
    const roll = rollup(id);
    headers.push({
      key: id, label: node.label || id, collapsedId: id, row, depth,
      firstCommit: subtreeFirst(id), lastCommit: roll.lastCommit,
      opCount: roll.opCount, laneCount: roll.laneCount,
    });
    row++; // the header occupies its own row
    for (const c of childrenOf(id).filter(isPresent).sort(sortByFirst)) emit(c, depth + 1);
  }
  for (const r of (map.roots || []).filter(isPresent).sort(sortByFirst)) emit(r, 0);
  const rowCount = Math.max(1, row);

  return { lanes, headers, edges, overflow, laneById, opsByFeature, rowCount,
    commitCount: ((grid && grid.commits) || []).length };
}
// ---- end-graph-layout (test slice boundary) ----

// ---- segment-layout (test slice boundary) ----
// The chunk-car timeline layout: the visual atom is the intent *segment* (a `<feature>@<n>`
// checkpoint), not the raw op. Reuses `computeGraphLayout` verbatim for the gutter -- tree walk,
// visibility, collapse-to-meta, ordering by first appearance, lanes-with-no-ops dropped -- then
// threads each visible lane's leaf feature(s) segments onto it as an ordered train of `cars` (a
// collapsed subsystem's meta-lane naturally gets the union of its features' cars, sorted together
// -- the "aggregate car strip" the redesign calls for).
//
// `segments` is the flat list the compose feed's `intent.segments` already carries (one entry per
// checkpoint: `feature_id`, `seg_index`, `checkpoint`, `intent` (label), `op_ids`, `op_count`,
// `first_index`, `last_index`, `tier`, `source`) -- passed in, never re-derived here, so this stays
// pure like `computeGraphLayout`. The Python counterpart is `segment_layout` in sgt/tui/graph.py,
// kept behaviour-parallel.
//
// A car whose `first_index` is past `frontier` is kept and flagged `isFuture` rather than dropped:
// the renderer dims it, but a lane's car *count* and positions stay stable while scrubbing -- only
// cell density (via `computeGraphLayout`'s own op filtering) accretes.
function computeSegmentLayout(map, grid, segments, opts) {
  opts = opts || {};
  const frontier = opts.frontier == null ? Infinity : opts.frontier;
  const commitIndexOf = {};
  for (const cell of (grid && grid.cells) || []) {
    for (const oid of cell.op_ids || []) commitIndexOf[oid] = cell.commit_index;
  }

  const byFeature = {};
  for (const seg of segments || []) {
    (byFeature[seg.feature_id] || (byFeature[seg.feature_id] = [])).push(seg);
  }

  const base = computeGraphLayout(map, grid, opts);

  const lanes = base.lanes.map((l) => {
    const cars = [];
    for (const leaf of l.leaves) {
      for (const seg of byFeature[leaf] || []) {
        const bins = new Map();
        for (const oid of seg.op_ids || []) {
          const ci = commitIndexOf[oid];
          if (ci != null) bins.set(ci, (bins.get(ci) || 0) + 1);
        }
        cars.push({
          featureId: leaf, segIndex: seg.seg_index, checkpoint: seg.checkpoint, label: seg.intent,
          opCount: seg.op_count, tier: seg.tier, source: seg.source,
          firstIndex: seg.first_index, lastIndex: seg.last_index,
          subBins: [...bins.entries()].sort((a, b) => a[0] - b[0]),
          isFuture: seg.first_index > frontier,
          words: seg.words || [],  // the chapter's captured words (intent-ledger P1 zoom)
        });
      }
    }
    cars.sort((a, b) => a.firstIndex - b.firstIndex
      || (a.featureId < b.featureId ? -1 : a.featureId > b.featureId ? 1 : 0)
      || a.segIndex - b.segIndex);
    return { ...l, cars };
  });
  const laneById = {};
  for (const l of lanes) laneById[l.id] = l;

  return { ...base, lanes, laneById };
}
// ---- end-segment-layout (test slice boundary) ----

// The episodic projection (Stage C): roll the flat op stream into EPISODES -- one per commit that
// carried ops -- and group episodes by their dominant feature into collapsible episode-groups (the
// "co-commit cluster" a developer rewinds as a unit). Sessions are empty on mined history (only
// sgt's own land/checkpoint stamp them), so the episode axis is projected from provenance: an op's
// commit_index identifies its earliest provenance commit, so ops sharing a commit_index were
// advanced in the same commit = one episode -- exactly the co-commit signal Stage B clusters on.
// Real sgt sessions supersede this going forward; the shape is identical. Pure (no DOM); the Python
// counterpart is `episodes()` in sgt/tui/graph.py, kept behaviour-parallel.
function rollupEpisodes(map, grid) {
  const labels = {};
  for (const n of (map && map.nodes) || []) labels[n.id] = n.label || n.id;
  const subjectOf = {}, shaOf = {};
  for (const c of (grid && grid.commits) || []) {
    subjectOf[c.index] = c.subject || "";
    shaOf[c.index] = c.sha;
  }
  // Re-roll `grid_view`'s per-(feature, commit) cells back across features into one episode per
  // commit (plan U3). An op with no feature has no cell, so an all-unattributed commit forms no
  // episode -- the same omission the grid itself makes.
  const byIndex = new Map();
  for (const cell of (grid && grid.cells) || []) {
    const idx = cell.commit_index;
    let ep = byIndex.get(idx);
    if (!ep) {
      ep = { index: idx, sha: shaOf[idx], subject: subjectOf[idx] || "", opIds: [], features: {}, kinds: {} };
      byIndex.set(idx, ep);
    }
    for (const oid of cell.op_ids || []) ep.opIds.push(oid);
    ep.features[cell.feature_id] = (ep.features[cell.feature_id] || 0) + cell.op_count;
    for (const k of Object.keys(cell.kinds || {})) ep.kinds[k] = (ep.kinds[k] || 0) + cell.kinds[k];
  }
  const episodes = [...byIndex.keys()].sort((a, b) => a - b).map((idx) => {
    const ep = byIndex.get(idx);
    ep.opCount = ep.opIds.length;
    // Dominant feature: most ops in this commit; ties broken by larger id for determinism.
    let dom = null, best = -1;
    for (const f of Object.keys(ep.features)) {
      const c = ep.features[f];
      if (c > best || (c === best && f > dom)) { best = c; dom = f; }
    }
    ep.dominantFeature = dom;
    return ep;
  });
  // Episode-groups: episodes sharing a dominant feature (the collapsible "thing I was doing"),
  // ordered by first appearance; unattributed episodes (no feature) fall under a null group.
  const groups = new Map();
  for (const ep of episodes) {
    const key = ep.dominantFeature;
    let g = groups.get(key);
    if (!g) {
      g = { featureId: key, label: key ? (labels[key] || key) : "(unattributed)",
            episodeIndices: [], opCount: 0, kinds: {}, firstIndex: ep.index, lastIndex: ep.index };
      groups.set(key, g);
    }
    g.episodeIndices.push(ep.index);
    g.opCount += ep.opCount;
    g.lastIndex = ep.index; // episodes are index-sorted, so the latest append is the last index
    for (const k of Object.keys(ep.kinds)) g.kinds[k] = (g.kinds[k] || 0) + ep.kinds[k];
  }
  const groupsOut = [...groups.values()].sort(
    (a, b) => a.firstIndex - b.firstIndex || String(a.featureId).localeCompare(String(b.featureId)));
  return { episodes, groups: groupsOut };
}
// ---- end-episodes (test slice boundary) ----

// Classify a verb preview's affected features into the three roles the closure overlay paints, so
// the graph reads the same as `sgt revert`'s terminal preview (blast/foundation are the CLI's own
// buckets -- sgt.api._affected_rows). `target` = the acted-on feature; `blast` = OTHER features
// losing ops in the closure (collateral); `foundation` = features gaining re-drafted hollow ops.
// Pure (no DOM); sliced for the node harness (tests/test_closure.py).
function classifyAffected(result, targetId) {
  const blast = [], foundation = [];
  for (const r of (result && result.affected) || []) {
    if (r.feature_id === targetId) continue; // the target is drawn as `target`, never collateral
    if (r.direction === "foundation") foundation.push(r.feature_id);
    else blast.push(r.feature_id);
  }
  return { target: targetId, blast, foundation };
}
// ---- end-closure (test slice boundary) ----

// Lay episodes out as a vertical git-log rail (Stage C): newest episode on top (row 0), each
// feature a lane column (its episodes a straight vertical line), lanes reused by features whose
// row-spans don't overlap (greedy interval-graph coloring) -- the compaction that keeps the column
// count small no matter how many features exist. Pure; the Python counterpart is
// `episode_rail_layout` in sgt/tui/graph.py, kept behaviour-parallel. Sliced for the node harness.
function episodeRailLayout(epView) {
  const episodes = (epView && epView.episodes) || [];
  const ordered = episodes.slice().sort((a, b) => b.index - a.index); // newest (largest index) top
  const rowOf = new Map();
  ordered.forEach((e, r) => rowOf.set(e.index, r));

  const span = new Map(); // fid -> [top, bot] (fid may be null -> Map, not an object, allows it)
  for (const e of episodes) {
    const fid = e.dominantFeature;
    const r = rowOf.get(e.index);
    const s = span.get(fid);
    if (!s) span.set(fid, [r, r]);
    else { s[0] = Math.min(s[0], r); s[1] = Math.max(s[1], r); }
  }

  // Greedy interval coloring: features top-first; a lane is reusable once its last occupant ends
  // above (smaller row than) this feature's top. Lowest free lane wins (minimal columns).
  const feats = [...span.keys()].sort((a, b) =>
    span.get(a)[0] - span.get(b)[0] || String(a).localeCompare(String(b)));
  const laneOf = new Map();
  const laneBot = [];
  for (const fid of feats) {
    const [top, bot] = span.get(fid);
    let lane = -1;
    for (let L = 0; L < laneBot.length; L++) { if (laneBot[L] < top) { lane = L; break; } }
    if (lane < 0) { lane = laneBot.length; laneBot.push(bot); }
    else laneBot[lane] = bot;
    laneOf.set(fid, lane);
  }

  const rows = ordered.map((e) => ({
    index: e.index, row: rowOf.get(e.index), feature: e.dominantFeature,
    lane: laneOf.has(e.dominantFeature) ? laneOf.get(e.dominantFeature) : 0,
    subject: e.subject, opCount: e.opCount, sha: e.sha,
    // The save's per-feature attribution (mirrors sgt/tui/graph.py::episode_rail_layout): a row is
    // a save, and its chips name every feature it touched, `feature` (the dominant one) first.
    features: e.features,
  }));
  return { rows, laneCount: Math.max(1, laneBot.length), rowCount: ordered.length };
}
// ---- end-rail (test slice boundary) ----

// ─── Rendering + interaction ──────────────────────────────────────────────────────────────────
// Everything below touches the DOM/vscode API and is not exercised by the node harness.

(function () {
  const vscode = acquireVsCodeApi();
  // Op-kind glyphs, reused by the op-set decomposition's per-kind tally.
  const GLYPH = { add: "◆", extend: "+", rework: "~", prune: "−", move: "⋔", merge: "⋈", touched: "·" };

  const state = vscode.getState() || {
    collapsed: [], selected: null, compositionLabel: "HEAD", compositionRef: "HEAD",
  };
  if (state.selectedStep === undefined) state.selectedStep = null;
  if (state.selectedPlanSession === undefined) state.selectedPlanSession = null;
  if (!Array.isArray(state.multi)) state.multi = state.selected ? [state.selected] : [];
  if (state.view !== "rail") state.view = "gantt"; // "gantt" (feature timeline) | "rail" (episodes)
  let compose = {
    map: { nodes: [], roots: [], edges: [] }, history: { commits: [], ops: [] },
    grid: { commits: [], cells: [] },
    status: { oracle: { configured: false, status: "pending" } }, sessions: { sessions: [] }, proposals: [],
  };
  let map = compose.map;
  // id -> node index, rebuilt whenever `map` is reassigned (the "state" handler below). byId is a
  // hot path -- called per lane and per rail row (directly and via laneColor) -- so it's a Map
  // lookup rather than an O(nodes) scan on every call.
  let nodeIndex = new Map((map.nodes || []).map((n) => [n.id, n]));
  let history = compose.history; // still the per-op stream the render half reads (op-set panel, plan/drift joins)
  let grid = compose.grid;       // grid_view's cell table -- the layout functions' canonical join (plan U3)
  let layout = computeSegmentLayout(map, grid, segmentsOf(compose), { collapsed: state.collapsed });
  let lastRenderWidth = -1; // rail width the last render() drew at; the resize observer skips no-op width reflows
  let lastRenderHeight = -1; // rail height the last render() drew at; the observer reflows on vertical resize too
  // The pane measurement a draw is sized from, recorded at the ONE place the DOM is measured so the
  // resize gate's baseline can never drift from the geometry actually drawn. It used to be stamped at
  // the top of render(), which meant anything throwing before the draw (or a draw that never ran) left
  // the gate claiming the new size while the SVG still showed the old one -- and since the gate then
  // saw no delta, no later resize ever reflowed: the timeline stayed squeezed into a corner of a wide
  // pane. 320 is the floor the geometry falls back to; the raw measurement is what gets recorded.
  function panePx() {
    lastRenderWidth = rail.clientWidth;
    lastRenderHeight = rail.clientHeight;
    return { w: Math.max(lastRenderWidth || 0, 320), h: lastRenderHeight || 0 };
  }

  // A hidden or collapsed pane (the panel folded shut, another view holding the slot) measures 0x0.
  // Drawing then bakes the 320px floor and a natural-height axis into the DOM, which is exactly the
  // squeezed-into-the-corner state a wide pane must never be left in. Skip the draw instead -- the
  // last good SVG stays put, and reflowIfStale() redraws the moment the pane is measurable again.
  function paneMeasurable() {
    return rail.clientWidth > 0;
  }
  let armedVerb = null; // {verb, feature} while "Merge into..."/"Move ops..." is picking a target
  let previewSeq = 0;
  let pendingPreview = null;
  let previewActive = false; // a held Focus & Morph consequence overlay owns the field dim
  let foldSeq = 0;
  let pendingFold = null;
  let foldResultCache = {}; // featureId -> {files, oracle_verdict, forked, error}, reset per composition

  // Multi-select union closure (Stage C): ⌘/ctrl/shift-click accretes a set of feature lanes; the
  // host resolves the union via `sgt select` and we show the closure count + paint it. Transient
  // (a selection is exploratory, not worth persisting): the set lives on state.multi, the resolved
  // closure here.
  let selectionSeq = 0;
  let pendingSelection = null;
  let selectionResult = null; // { refs, view } for the current state.multi, or null
  let pendingReveal = null; // an editor->graph reveal target awaiting the graph's next render (task 4)

  // Composition-picker hover-preview: while the titlebar's composition QuickPick is open, arrowing
  // over a session/branch item folds it live and takes over the code(I) slot -- "what would
  // switching to this show," seen before committing to the real `sgt switch`.
  let compositionPreviewActive = null; // the ref currently previewed, or null when the picker is closed
  let compositionPreviewCache = {}; // ref -> {files, oracle_verdict, forked, error}
  let latestCompositionPreviewSeq = 0; // discards a stale reply that lands after a newer hover

  // Frontier scrubber: a commit-index the user is scrubbing on the bottom time axis, independent of
  // `state` (a transient exploration mode, not worth persisting across a webview reload).
  let playheadCommitIndex = null;
  let playheadDragging = false;
  let playheadSeq = 0;
  let pendingPlayhead = null;
  let playheadResultCache = {}; // commitIndex -> {op_count, files, oracle_verdict, forked, error}
  let scrubTimer = null;
  let scrubRaf = 0; // rAF handle coalescing pointermove scrubs to one applyFrontier/renderInspector per frame
  let scrubPendingIdx = null; // the latest scrub commit-index awaiting that frame

  // Plan marks: predicted steps render as a dashed accent ring + count badge on their predicted
  // feature's node (see collectPlanMarks / renderNodeBadges). `knownPlanSteps`/`prevKnownPlanSteps`
  // snapshot ids across renders so an "entering" pulse fires only on a genuine transition.
  let planMarks = { steps: [], byFeature: {}, floating: [], sessions: [] };
  // Checkpoints: a feature's intent segments (compose.intent.segments) grouped by feature, in
  // chronological order -- the "which version do I rewind to" list the inspector shows per feature.
  let checkpointsByFeature = {};
  let knownPlanSteps = new Set();
  let pendingPlanSteps = new Set();
  let prevKnownPlanSteps = new Set();
  let prevPendingPlanSteps = new Set();
  let planStepEnterStagger = {}; // step id -> 0-based order among this render's newly-entering steps

  // Drift marks: a mined op no active plan predicted -- flags the owning node with a solid ring.
  let driftMarks = { ids: new Set(), unplaced: [] };
  let knownDriftIds = new Set();
  let prevKnownDriftIds = new Set();

  let forkMarks = { byFeature: {}, unplaced: [] };

  // Save-preview marks: which features would GAIN ops on the next `sgt save` (compose.save_preview)
  // -- rendered as a dashed "ghost car" at the now-frontier of each affected lane, so the user sees
  // the consequence of saving before saving. Feature-granular by design (op-level reconciliation
  // stays out of the default surface). `prevPendingFeatures`/`prevCarCounts` snapshot across renders
  // so a ghost that just LANDED (the save turned it into a real car) can play a one-shot solidify
  // transition instead of the ghost popping and a solid car appearing from nowhere.
  let savePreviewMarks = { byFeature: {}, newWork: 0 };
  let prevPendingFeatures = new Set();
  let prevCarCounts = {};
  let landingFeatures = new Set(); // leaf lanes whose newest car should play the solidify anim

  const rail = document.getElementById("rail");
  const inspector = document.getElementById("inspector");
  const compositionBtn = document.getElementById("compositionBtn");
  const oracleChip = document.getElementById("oracleChip");
  const offscreenAbove = document.getElementById("offscreenAbove");
  const offscreenBelow = document.getElementById("offscreenBelow");
  const previewContext = document.getElementById("previewContext"); // "＋N unchanged" context tally
  const previewRefusal = document.getElementById("previewRefusal"); // blocked-restore remedies overlay
  const viewSeg = document.getElementById("viewSeg"); // segmented Timeline│Rail control
  const plansChip = document.getElementById("plansChip"); // consolidated "Plans M/N" chip + popover trigger
  const plansPopover = document.getElementById("plansPopover");
  const driftChip = document.getElementById("driftChip"); // ◇ unplanned / ⑂ unplaced-fork indicator
  const inspectorToggle = document.getElementById("inspectorToggle");

  const SVG_TAGS = new Set(["svg", "g", "path", "circle", "rect", "text", "line", "title"]);

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
    return nodeIndex.get(id);
  }

  // ─── Plan marks (Phase 6) ─────────────────────────────────────────────────────────────────────
  // `predicted_feature` is always a real existing feature id or null (sgt/loop/plan.py's decompose
  // prompt never lets the LLM invent one) -- so a pending step always has a real row to attach to,
  // or none. Rather than injecting a synthetic ghost subtree ahead of `computeLayout` (Phase 5's
  // approach), a pending step is treated as a hypothetical *next op* on its predicted feature's
  // own row: `map`/`history` stay pristine, and this just indexes plan.sessions for the renderer
  // to consult per-row. A matched step produces no mark at all -- the confirmed op is already a
  // real entry in `history.ops` and renders as a normal glyph on its true feature row; `steps`
  // still carries matched entries too, purely so the landing/comet transition (render()) can see
  // what a step *was* on the one render where it just resolved.
  function collectPlanMarks(plan, hist) {
    const opFeature = {};
    for (const op of (hist && hist.ops) || []) opFeature[op.id] = op.feature_id;

    // Footprint-overlap candidates (`sgt checkpoint`'s preview) a human hasn't confirmed yet --
    // `step.status` only flips to "matched" after `sgt checkpoint --confirm-hollow/--confirm-op`
    // actually runs, so a pending step can already have a candidate group here.
    const groupByHollow = {};
    for (const group of (plan && plan.checkpoint && plan.checkpoint.matches) || []) {
      for (const h of group.hollow_ids) groupByHollow[h] = group;
    }

    const steps = [];
    const sessions = [];
    for (const session of (plan && plan.sessions) || []) {
      let matchedCount = 0;
      for (const step of session.steps || []) {
        const matched = step.status === "matched";
        if (matched) matchedCount++;
        let matchedFeature = null;
        let matchedOpId = null;
        for (const opId of step.matched_op_ids || []) {
          if (opFeature[opId] != null) {
            matchedFeature = opFeature[opId];
            matchedOpId = opId;
            break;
          }
        }
        steps.push({
          id: step.hollow_id, sessionId: session.session_id, label: step.title, matched,
          matchedFeature, matchedOpId, predictedFeature: step.predicted_feature || null,
          rationale: step.rationale, footprint: step.predicted_footprint || [], files: step.files || [],
          checkpointMatch: matched ? null : groupByHollow[step.hollow_id] || null,
        });
      }
      sessions.push({
        sessionId: session.session_id, planText: session.plan_text,
        matchedCount, stepCount: (session.steps || []).length,
        derivedStatus: session.derived_status || null,
        pendingCount: session.pending_count != null ? session.pending_count : null,
        remainingTitles: session.remaining_titles || [],
      });
    }

    const byFeature = {};
    const floating = [];
    for (const step of steps) {
      if (step.matched) continue;
      if (step.predictedFeature) {
        (byFeature[step.predictedFeature] || (byFeature[step.predictedFeature] = [])).push(step);
      } else {
        floating.push(step);
      }
    }
    return { steps, byFeature, floating, sessions };
  }

  // ─── Drift marks ──────────────────────────────────────────────────────────────────────────────
  // A drift op (`compose.drift.entries`) is a mined op no active plan session predicted -- "the
  // actual diverged from plan." `DriftEntry` carries only `op_id`/`kind`/`footprint`/`files`, no
  // feature/commit-index, so this joins through `history.ops` (same join style as
  // `collectPlanMarks`'s `opFeature` map) to find where on the rail it already lives: a drift op
  // is a REAL op, already drawn as an ordinary glyph by `renderOpsForRow` -- this never adds a
  // second mark, it flags the existing one (a ring drawn around it, decided in renderOpsForRow).
  // An op with no feature (unattributed) has no row to ring; those are reported as `unplaced` for
  // the inspector's floating list, mirroring plan's own floating-step idiom.
  function collectDriftMarks(drift, hist) {
    const byOp = {};
    for (const op of (hist && hist.ops) || []) byOp[op.id] = op;

    const ids = new Set();
    const unplaced = [];
    for (const entry of (drift && drift.entries) || []) {
      const op = byOp[entry.op_id];
      if (op && op.feature_id != null) {
        ids.add(entry.op_id);
      } else {
        unplaced.push(entry);
      }
    }
    return { ids, unplaced };
  }

  // ─── Checkpoint marks ───────────────────────────────────────────────────────────────────────────
  // Group `compose.intent.segments` by feature, chronological. Each segment is a "checkpoint" -- a
  // contiguous chapter of a feature's history sharing one intent, addressable/revertable as
  // `<feature>@<seg_index>`. The inspector lists these so a feature is a short story, not 100 ops.
  function segmentsOf(c) {
    return (c && c.intent && c.intent.segments) || [];
  }

  function collectCheckpoints(intent) {
    const byFeature = {};
    for (const seg of (intent && intent.segments) || []) {
      (byFeature[seg.feature_id] = byFeature[seg.feature_id] || []).push(seg);
    }
    for (const fid of Object.keys(byFeature)) {
      byFeature[fid].sort((a, b) => a.seg_index - b.seg_index);
    }
    return byFeature;
  }

  // ─── Fork marks ───────────────────────────────────────────────────────────────────────────────
  // An open same-symbol chain fork (`compose.forks.forks`) has no commit-index column of its own
  // -- both tips are, by construction, excluded from every verb-visible ideal (they never reach
  // `history.ops`) -- so it's placed by `dir`-prefix match against `MapNode.dir` (the same locator
  // `requestFold` already uses), picking the LONGEST matching feature dir so a subsystem's dir
  // (always a prefix of its children's) never steals a fork from the feature that actually owns
  // it. A fork whose file matches no feature's dir (e.g. every op on that path was itself forked
  // away) has nowhere on the rail to attach and is reported as `unplaced`, mirroring drift's own
  // unplaced idiom rather than inventing a synthetic row.
  function collectForkMarks(forksView, nodes) {
    const features = (nodes || []).filter((n) => n.kind === "feature" && n.dir);
    const byFeature = {};
    const unplaced = [];
    for (const fork of (forksView && forksView.forks) || []) {
      let best = null;
      for (const n of features) {
        if (fork.file.startsWith(n.dir) && (!best || n.dir.length > best.dir.length)) best = n;
      }
      if (best) {
        (byFeature[best.id] || (byFeature[best.id] = [])).push(fork);
      } else {
        unplaced.push(fork);
      }
    }
    return { byFeature, unplaced };
  }

  // Save-preview marks: fold `compose.save_preview` into a {feature_id -> pending op_count} map plus
  // the new-work count (ops belonging to no built feature). Feature-granular by design -- the ghost
  // car answers "which features gain work on save", never op/symbol-level reconciliation.
  function collectSavePreview(sp) {
    const byFeature = {};
    for (const row of (sp && sp.affected) || []) byFeature[row.feature_id] = row.op_count;
    return { byFeature, newWork: (sp && sp.new_work_count) || 0 };
  }

  function saveState() {
    vscode.setState(state);
  }

  function recompute() {
    // Full history: the frontier scrubber dims nodes/cars past its point (see applyFrontier)
    // rather than re-laying-out on every drag, so the layout stays stable while scrubbing.
    layout = computeSegmentLayout(map, grid, segmentsOf(compose), { collapsed: state.collapsed });
  }

  // First-load clustering: open the rail folded to its subsystem rows -- the root(s) expanded to
  // their direct children, deeper subsystems collapsed -- instead of every leaf feature at once
  // (this repo alone is 26 features across 5 subsystems: a wall of rows where nothing reads).
  // "What changed, at a glance," drill in on demand. Applied exactly once, and only after real
  // nodes have arrived; any later expand/collapse persists and is never overwritten.
  function applyClusterDefaultOnce() {
    if (state.clusteredOnce) return;
    const roots = new Set(map.roots || []);
    const subs = (map.nodes || []).filter(
      (n) => n.kind === "subsystem" && n.children && n.children.length && !roots.has(n.id)
    );
    if (!subs.length) return; // nothing to cluster yet; retry on the next state with real nodes
    state.collapsed = subs.map((n) => n.id);
    state.clusteredOnce = true;
    saveState();
  }

  function renderTitlebar() {
    // ── Nav zone: composition + segmented view control ──────────────────────────────────────────
    compositionBtn.textContent = `${state.compositionLabel || "HEAD"} ▾`;
    for (const btn of viewSeg.querySelectorAll(".seg-btn")) {
      btn.classList.toggle("active", btn.dataset.view === state.view);
    }

    // ── Status zone: oracle dot, consolidated plans chip, drift indicator ───────────────────────
    const oracle = (compose.status && compose.status.oracle) || { configured: false, status: "pending" };
    const st = oracle.configured ? oracle.status : "unconfigured";
    oracleChip.dataset.state = st;
    oracleChip.querySelector(".oracle-label").textContent = st === "unconfigured" ? "oracle" : `oracle · ${st}`;

    // Fold the N per-session chips into one "Plans matched/total" chip whose ring shows aggregate
    // progress; the per-session breakdown moves into a click popover (renderPlansPopover).
    const sessions = planMarks.sessions;
    const totalMatched = sessions.reduce((n, s) => n + s.matchedCount, 0);
    const totalSteps = sessions.reduce((n, s) => n + s.stepCount, 0);
    const allComplete = sessions.length > 0 && totalMatched === totalSteps;
    plansChip.classList.toggle("complete", allComplete);
    plansChip.hidden = sessions.length === 0;
    const ringSvg = plansChip.querySelector(".plans-ring");
    ringSvg.innerHTML = "";
    ringSvg.appendChild(renderPlanRing(0, totalMatched, totalSteps));
    plansChip.querySelector(".plans-label").textContent = `Plans ${totalMatched}/${totalSteps}`;
    if (!plansPopover.hidden) renderPlansPopover(); // keep an open popover in sync with fresh state

    // Drift/forks with no matching row have nowhere on the rail to attach -- one compact indicator
    // rather than the signal being dropped silently.
    const driftN = driftMarks.unplaced.length;
    const forkN = forkMarks.unplaced.length;
    driftChip.hidden = !(driftN || forkN);
    driftChip.textContent = [driftN ? `◇${driftN}` : "", forkN ? `⑂${forkN}` : ""].filter(Boolean).join(" ");
    driftChip.title = [
      ...driftMarks.unplaced.map((e) => `unplanned: ${e.footprint.join(", ")}`),
      ...forkMarks.unplaced.map((f) => `unplaced fork: ${f.symbol}`),
    ].join("\n");
    driftChip.onclick = forkN ? () => vscode.postMessage({ type: "resolveFork", symbol: forkMarks.unplaced[0].symbol }) : null;

    // ── Actions zone: inspector toggle (Save/Commit/Undo are wired once at init) ─────────────────
    inspectorToggle.textContent = state.inspectorCollapsed ? "◨" : "◧";
    inspectorToggle.title = state.inspectorCollapsed ? "Show detail panel" : "Hide detail panel";
    document.getElementById("app").classList.toggle("inspector-collapsed", !!state.inspectorCollapsed);
  }

  // The plans popover: one row per active session (○/● glyph + name + matched/total, ✓ when
  // complete). Built from the same planMarks.sessions data the consolidated chip aggregates.
  function renderPlansPopover() {
    plansPopover.innerHTML = "";
    if (!planMarks.sessions.length) {
      const empty = document.createElement("div");
      empty.className = "plans-pop-empty";
      empty.textContent = "No active plan sessions";
      plansPopover.appendChild(empty);
      return;
    }
    for (const session of planMarks.sessions) {
      const done = session.stepCount > 0 && session.matchedCount === session.stepCount;
      const stalled = session.derivedStatus === "stalled";
      const floatingN = planMarks.floating.filter((s) => s.sessionId === session.sessionId).length;
      const row = document.createElement("button");
      row.className = "plans-pop-row" + (done ? " complete" : "") + (stalled ? " stalled" : "");
      row.title = stalled ? `Interrupted — ${session.planText}` : session.planText;
      const glyph = document.createElement("span");
      glyph.className = "plans-pop-glyph";
      // A stalled plan reads paused (⏸), distinct from building (●/○) and done (✓).
      glyph.textContent = stalled ? "⏸" : done ? "✓" : session.matchedCount > 0 ? "●" : "○";
      const name = document.createElement("span");
      name.className = "plans-pop-name";
      name.textContent = session.planText;
      const count = document.createElement("span");
      count.className = "plans-pop-count";
      count.textContent = `${session.matchedCount}/${session.stepCount}` + (floatingN ? ` · ${floatingN}⤶` : "");
      row.append(glyph, name, count);
      row.addEventListener("click", () => {
        plansPopover.hidden = true;
        selectPlanSession(session.sessionId);
      });
      plansPopover.appendChild(row);
      // Stalled plans get the one clear next action right in the list -- hand it back to Claude
      // Code. A <span role=button> (not a nested <button>, which is invalid inside the row button).
      if (stalled) {
        const resume = document.createElement("span");
        resume.className = "plan-resume";
        resume.setAttribute("role", "button");
        resume.setAttribute("tabindex", "0");
        resume.textContent = "Resume";
        resume.title = "Relaunch this Claude Code session in a terminal";
        resume.addEventListener("click", (e) => {
          e.stopPropagation();
          plansPopover.hidden = true;
          vscode.postMessage({ type: "resumePlan", sessionId: session.sessionId });
        });
        row.appendChild(resume);
      }
    }
  }

  function render() {
    renderTitlebar();
    // Plan-badge transition bookkeeping: a step newly seen pending gets an entering pulse; a step
    // that just matched drops its pending badge. (The rail's cross-row comet/FLIP morphs retired
    // with the rail -- the graph re-lays out on structural change and node identity carries
    // continuity, so per-row Y morphs no longer apply.)
    prevKnownPlanSteps = knownPlanSteps;
    prevPendingPlanSteps = pendingPlanSteps;
    prevKnownDriftIds = knownDriftIds;
    planStepEnterStagger = {};
    let enterN = 0;
    for (const step of planMarks.steps) {
      if (!step.matched && !prevKnownPlanSteps.has(step.id)) planStepEnterStagger[step.id] = enterN++;
    }
    const nextKnown = new Set();
    const nextPending = new Set();
    for (const step of planMarks.steps) {
      nextKnown.add(step.id);
      if (!step.matched) nextPending.add(step.id);
    }
    knownPlanSteps = nextKnown;
    pendingPlanSteps = nextPending;
    knownDriftIds = new Set(driftMarks.ids);

    // Save-preview landing: a leaf lane that HAD a ghost last render and now has a genuinely new car
    // (its car count rose because the save committed the pending work into a real checkpoint) plays
    // the one-shot solidify transition. A ghost that vanished with no new car (the user reverted the
    // uncommitted edit instead of saving) is not a landing -- so gate on the car-count rise.
    const nowPending = new Set(Object.keys(savePreviewMarks.byFeature));
    const carCounts = {};
    for (const l of (layout.lanes || [])) carCounts[l.id] = (l.cars || []).length;
    landingFeatures = new Set();
    if (!prefersReducedMotion()) {
      for (const fid of prevPendingFeatures) {
        if (!nowPending.has(fid) && (carCounts[fid] || 0) > (prevCarCounts[fid] || 0)) {
          landingFeatures.add(fid);
        }
      }
    }
    prevPendingFeatures = nowPending;
    prevCarCounts = carCounts;

    renderGraph();
    renderInspector();
    renderPresence();
  }

  // The persistent "where am I" band (Stage C): composition · view · current selection + its live
  // closure count · scrub position · uncommitted work. Always visible, so the developer never loses
  // their place regardless of what's selected or scrubbed.
  function renderPresence() {
    const el = document.getElementById("presence");
    if (!el) return;
    const parts = [`◆ ${state.compositionLabel || "HEAD"}`, state.view === "rail" ? "rail" : "timeline"];
    const multi = state.multi || [];
    if (multi.length >= 2) {
      const view = selectionResult && selectionResult.view;
      const clo = view && view.ok ? ` → ${view.closure_op_count} edits` : "";
      parts.push(`${multi.length} selected${clo}`);
    } else if (state.selected) {
      const n = byId(state.selected);
      parts.push(`▸ ${(n && n.label) || state.selected}`);
    }
    if (playheadCommitIndex != null) parts.push(`@ commit ${playheadCommitIndex}`);
    const drift = (compose.status && compose.status.drift) || { paths: [] };
    const dpaths = (drift.paths || []).length;
    if (dpaths) parts.push(`⚠ ${dpaths} uncommitted`);
    el.textContent = parts.join("  ·  ");
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // ─── The feature timeline (Gantt) ───────────────────────────────────────────────────────────
  // One lane per feature: a left gutter (identity swatch + label) and a train of chunk-CARS, one
  // per intent segment (`<feature>@<n>` checkpoint) -- the visual atom is the checkpoint, not a
  // raw op or a shared time column (see computeSegmentLayout). Cars pack left->right in seg_index
  // order, sized by op_count, shaded inside by their own per-commit density. Rows are grouped into
  // subsystem swimlanes. The width tracks the pane; it scrolls vertically only when there are more
  // lanes than fit. A resize re-runs render() via the ResizeObserver, so it reflows continuously.
  // The bottom axis + frontier scrubber still read real commit-time (recovered cross-feature
  // alignment lives in the episode rail); a car past the scrubbed frontier just dims in place.
  // `ghostW` is the width a forecast car needs to carry a READABLE name (~12 glyphs at 6px). It is
  // deliberately much wider than a history car's minimum: history can lean on position-in-time to say
  // what it is, a forecast has only its label. Truncating a step title to 6 glyphs ("Reserv…") would
  // leave the reader exactly where the old `+1` badge did, so the band prefers fewer, legible cars
  // over more, unreadable ones -- and collapses the remainder into a stack card (see renderForecastCars).
  const GANTT = { padT: 14, rowH: 26, barH: 12, axisH: 34, minBarW: 6, minCarW: 9, carGap: 1.5, labelMinW: 34, gutterPad: 8, cellGap: 0.5, indent: 14, ghostW: 72, nowPad: 7 };
  let graphView = null; // { geom, handleEl, frontierEl, veilEl } -- set each render for the scrubber

  // `forecastCars` is the widest forecast a single lane carries this render (0 when nothing is
  // pending or planned). It buys a FORECAST BAND to the right of the `now` rule: measured time ends
  // at `now`, and anticipated work lives past it. Without a band the future has literally no room on
  // an axis whose domain is [c0, lastCommit] -- which is why pending work used to degenerate into a
  // badge jammed against the plot edge instead of reading as a car.
  function ganttGeom(forecastCars = 0) {
    const pane = panePx();
    const paneW = pane.w;
    // Keep the label column wide enough to stay legible even when the inspector is dragged wide and
    // the rail is squeezed -- the labels never collapse; instead the plot compresses (and clips at
    // the pane edge) when there isn't room, which is the acceptable trade here.
    const labelW = Math.round(Math.max(130, Math.min(220, paneW * 0.4)));
    const plotX0 = labelW + GANTT.gutterPad;
    const fullW = Math.max(60, paneW - plotX0 - 16);
    // The band asks for one nameable slot per forecast car, then gets clamped to 38% of the plot so a
    // forecast can never crowd out measured history (and the history side never drops below 40px).
    // `forecastSlots` reports how many slots SURVIVED that clamp -- the renderer collapses anything
    // beyond them into a stack card, which is what makes the band responsive: squeeze the pane and the
    // band sheds cards rather than shrinking them all into illegible stubs.
    const slotW = GANTT.ghostW + GANTT.carGap;
    const wantW = forecastCars > 0 ? forecastCars * slotW + GANTT.nowPad * 2 : 0;
    const forecastW = Math.min(wantW, Math.max(0, Math.round(fullW * 0.38)), Math.max(0, fullW - 40));
    const forecastSlots = forecastCars > 0
      ? Math.max(1, Math.floor((forecastW - GANTT.nowPad * 2 + GANTT.carGap) / slotW)) : 0;
    const plotW = Math.max(40, fullW - forecastW);
    const nowX = plotX0 + plotW;          // the `now` rule: right edge of measured time
    const forecastX0 = nowX + GANTT.nowPad;
    const w = paneW;
    const rowsH = layout.rowCount * GANTT.rowH;
    // Rows stay top-anchored, but the axis pins to the bottom of the pane: the SVG grows to fill the
    // rail's viewport (minus its 8px padding each side) so a short timeline no longer leaves a dead
    // band of background below it -- the void becomes an honest empty plot with a full-height axis.
    const naturalH = GANTT.padT + rowsH + 12 + GANTT.axisH;
    const h = Math.max(naturalH, pane.h - 16);
    const axisY = h - GANTT.axisH;
    const maxCommit = Math.max(1, layout.commitCount - 1);
    const xOf = (ci) => plotX0 + (Math.max(0, Math.min(maxCommit, ci)) / maxCommit) * plotW;
    return {
      labelW, plotX0, plotW, w, h, axisY, maxCommit,
      forecastW, forecastSlots, nowX, forecastX0,
      xOf,
      rowY: (row) => GANTT.padT + row * GANTT.rowH, // top of the row
      midY: (row) => GANTT.padT + row * GANTT.rowH + GANTT.rowH / 2,
      scrubX: (idx) => xOf(idx),
      xToCommit: (x) => Math.max(0, Math.min(maxCommit,
        Math.round(((x - plotX0) / Math.max(1, plotW)) * maxCommit))),
    };
  }

  function laneColor(id) {
    const n = byId(id);
    return (n && n.color) || "#8a8a8a"; // meta/subsystem lanes have no identity hue -> neutral
  }

  // Draw one lane's checkpoints as chunk-cars positioned on the SHARED commit-time axis: each car
  // spans [xOf(firstIndex), xOf(lastIndex)], so a lane's cars show *when* that feature was worked
  // on and when it went quiet -- reading left-to-right against the same bottom axis every other
  // lane uses. A single left->right pass enforces a minimum width and a small gap so short chapters
  // stay legible/clickable and never overlap; a burst of chapters in a narrow time band degrades
  // gracefully by nudging right rather than stacking. Each car is wrapped in its own <g data-first>
  // so the frontier scrubber (applyFrontier) dims it in place without a relayout. Returns the x just
  // past the last car (or the plain lifetime track when the feature has no mined segments yet), so
  // the caller can place the op-count label and badges against it.
  function renderCars(g, l, geom, color, barY, midY) {
    const cars = l.cars || [];
    if (!cars.length) {
      const x1 = geom.xOf(l.firstCommit), x2 = geom.xOf(l.lastCommit);
      g.appendChild(mk("rect", {
        x: x1, y: barY, width: Math.max(GANTT.minBarW, x2 - x1), height: GANTT.barH, rx: 3,
        class: "gbar-track", fill: color,
      }));
      return x2;
    }
    const plotR = geom.plotX0 + geom.plotW;
    // A lane flagged for landing (its ghost just became a real save): the newest (last) car plays
    // the solidify transition -- entering from the dashed/low-opacity ghost look into a solid car.
    const landing = landingFeatures.has(l.id);
    // The "big event": the fattest chapter in this lane. It gets a stronger fill and first claim on
    // an inline label, so the lane's most consequential edit reads at a glance.
    const laneMaxOps = Math.max(1, ...cars.map((c) => c.opCount));
    // Exactly one car per lane is "the" big event. Ties are the common case (a lane of one-op
    // chapters), and promoting every tied car drew every one of their tags at once -- a lane's worth
    // of centered labels overprinting each other into an unreadable smear above the strip, plus a
    // whole row brightened as if every chapter were the notable one. First one attaining the max wins.
    const bigIndex = cars.findIndex((c) => c.opCount === laneMaxOps);
    // Gap-fill tiling: a car fills from its first commit through the END of its last commit's column
    // (one colStep wide), so a single-commit chapter occupies a whole column instead of a sliver --
    // wide enough to carry its label inline. It never runs into the next car (capped at that car's
    // start, less a gap); the final car fills to the plot edge. Distant chapters still leave a dim gap
    // between them, so "when the feature went quiet" stays readable.
    const colStep = geom.plotW / Math.max(1, geom.maxCommit);
    let cursor = geom.plotX0;
    let lastRight = geom.plotX0;
    for (let i = 0; i < cars.length; i++) {
      const car = cars[i];
      const isBig = cars.length > 1 && i === bigIndex;
      let x = Math.max(geom.xOf(car.firstIndex), cursor); // anchored in time, never behind the last car
      let rightEnd = car.lastIndex >= geom.maxCommit ? plotR : geom.xOf(car.lastIndex) + colStep;
      if (i + 1 < cars.length) rightEnd = Math.min(rightEnd, geom.xOf(cars[i + 1].firstIndex) - GANTT.carGap);
      let w = Math.max(GANTT.minCarW, rightEnd - x);
      if (x + w > plotR) { x = Math.max(cursor, plotR - w); } // keep it on-screen
      if (x + w > plotR) { w = Math.max(GANTT.minCarW, plotR - x); }
      const selected = car.checkpoint === state.selectedCheckpoint;
      const wrap = mk("g", {
        class: "gcar-wrap" + (isBig ? " gcar-big" : "") + (selected ? " gcar-selected" : ""),
        "data-first": car.firstIndex,
      });
      // Native tooltip: an SVG element ignores a `title` attribute, so the hover text has to be a
      // `<title>` child of the rect (not attrs.title) to actually show on hover.
      const tip = `${car.label}\n${car.tier}` +
        (car.source === "fallback" ? "" : ` · ${car.source}`) +
        // The chapter in the user's own words on hover (intent-ledger P1): the words captured for
        // its commits, up to two. `sgt feature why <sha>` carries the full text + resume handle.
        ((car.words && car.words.length)
          ? "\n" + car.words.slice(0, 2).map((w) => `“${w}”`).join("\n") : "") +
        `\nRewind: sgt revert ${car.checkpoint}`;
      wrap.appendChild(mk("rect", {
        x, y: barY, width: w, height: GANTT.barH, rx: 3,
        class: "gcar" + (car.tier === "thematic" ? " gcar-thematic" : "") + (isBig ? " gcar-big-rect" : "") +
          (landing && i === cars.length - 1 ? " gcar-landing" : ""),
        fill: color, "data-checkpoint": car.checkpoint,
      }, [mk("title", { text: tip })]));
      // Within-car density texture: the chapter's own per-commit runs, opacity by sqrt(count /
      // that chapter's own max) -- a single-commit car (the common case) has nothing to spread
      // across, so it's left as one flat fill rather than one bright sliver + dead space.
      const bins = car.subBins && car.subBins.length ? car.subBins : [];
      if (bins.length > 1 && w >= 6) {
        const cellW = w / bins.length;
        const localMax = Math.max(1, ...bins.map((b) => b[1]));
        for (let j = 0; j < bins.length; j++) {
          const cell = mk("rect", {
            x: x + j * cellW, y: barY, width: Math.max(0.5, cellW - GANTT.cellGap), height: GANTT.barH,
            class: "gcar-cell", fill: color,
          });
          cell.setAttribute("fill-opacity", (0.3 + 0.55 * Math.sqrt(bins[j][1] / localMax)).toFixed(3));
          wrap.appendChild(cell);
        }
      }
      // Inline label: the chapter's intent, not the bare @n index (which reads as a meaningless
      // "0"). Only when the car is wide enough to hold a few glyphs; otherwise the hover tooltip
      // carries it. The big-event car gets a labelled tag just above the strip even when narrow.
      if (w >= GANTT.labelMinW && car.label) {
        wrap.appendChild(mk("text", {
          x: x + w / 2, y: midY + 3, class: "gcar-label", text: truncate(car.label, Math.floor(w / 6)),
        }));
      } else if (isBig && car.label) {
        wrap.appendChild(mk("text", {
          x: x + w / 2, y: barY - 3, class: "gcar-tag", text: truncate(car.label, 22),
        }));
      }
      // A car is its own click target: selecting it picks the CHECKPOINT (`f-XXXX@n`, the revert
      // unit), distinct from a row/label click that picks the whole feature. stopPropagation keeps
      // it from bubbling up to the lane's feature-select handler.
      wrap.addEventListener("click", (ev) => selectCar(car, l.id, ev));
      wrap.addEventListener("mouseenter", () => { if (!armedVerb) previewAndBlast("revert", [car.checkpoint]); });
      g.appendChild(wrap);
      cursor = x + w + GANTT.carGap;
      lastRight = x + w;
    }
    return lastRight;
  }

  // Pending ops a lane would gain on the next save. A meta (collapsed-subsystem) lane rolls up its
  // member leaves; a normal lane reads its own entry.
  function pendingOpsForLane(l) {
    if (l.isMeta) return (l.leaves || []).reduce((s, f) => s + (savePreviewMarks.byFeature[f] || 0), 0);
    return savePreviewMarks.byFeature[l.id] || 0;
  }

  // ─── The forecast band: anticipated work as cars, not badges ────────────────────────────────
  // A lane's future in ONE grammar. sgt has two kinds of not-yet-real work, and they used to be drawn
  // as two unrelated marks in two unrelated units: uncommitted ops became a dashed stub car with a
  // floating `+N` (N = edits), while pending plan steps became a dashed underline under the whole bar
  // with a different `+N` (N = steps). Same glyph, two denominators, two shapes, for one idea. Both
  // are now CARS in the forecast band -- the same rounded rect, the same identity hue, the same
  // three-tier label rule as history -- so "what is coming here" reads with the vocabulary the reader
  // already learned from the left of the `now` rule. Dash weight, never hue, separates the two kinds:
  //   save ghost  (3 3 dash, filled, slow pulse) = real edits on disk right now, will land on save
  //   plan ghost  (1.5 2.5 dash, unfilled)       = a step someone intends; no code exists yet
  // Motion is meaning here: only the save ghost pulses, because only it describes work that exists.
  function laneForecast(l) {
    const out = [];
    const pending = pendingOpsForLane(l);
    if (pending > 0) {
      out.push({
        kind: "save", label: "uncommitted",
        detail: `${pending} edit(s) on disk now — will land on the next save`,
      });
    }
    // A meta (collapsed-subsystem) lane rolls up its leaves' steps, matching pendingOpsForLane.
    const steps = l.isMeta
      ? (l.leaves || []).flatMap((f) => planMarks.byFeature[f] || [])
      : (planMarks.byFeature[l.id] || []);
    for (const step of steps) {
      const syms = step.footprint || [];
      out.push({
        kind: "plan", label: step.label, step,
        // The honest magnitude for a prediction is its FOOTPRINT, not an op count (no ops exist yet).
        // This is the answer to "what content is going to be added?" -- the symbols it says it'll touch.
        detail: [step.label, step.rationale || null,
          syms.length ? `predicted: ${syms.slice(0, 6).join(", ")}` +
            (syms.length > 6 ? ` +${syms.length - 6} more` : "") : "no predicted footprint",
        ].filter(Boolean).join("\n"),
      });
    }
    return out;
  }

  // Draw a lane's forecast cars in the band right of the `now` rule. Ghosts flow left->right in the
  // order they'd land (uncommitted work first, then plan steps in plan order). When more ghosts exist
  // than slots survived the clamp, the LAST slot becomes a STACK CARD standing for the remainder --
  // drawn as a card with a second outline offset behind it, the ordinary "there are more behind this"
  // idiom. The stack is labelled by NAME while no other card is named yet, and only falls back to a
  // `＋N` count once named cards are already on screen. That ordering matters: a reader should never
  // be shown a bare number as their only information about what is coming, which is the whole failure
  // of the badge this replaced.
  function renderForecastCars(g, l, geom, color, barY, midY, ghosts) {
    if (!ghosts.length || geom.forecastW <= 0) return;
    const slots = Math.max(1, geom.forecastSlots);
    const overflow = ghosts.length > slots;
    const namedCount = overflow ? slots - 1 : ghosts.length;
    const named = ghosts.slice(0, namedCount);
    const rest = ghosts.slice(namedCount);
    const total = named.length + (rest.length ? 1 : 0);
    const bandW = geom.forecastW - GANTT.nowPad * 2;
    const w = Math.max(GANTT.minCarW, Math.min(GANTT.ghostW,
      (bandW - (total - 1) * GANTT.carGap) / total));
    let x = geom.forecastX0;

    const drawCard = (gh, opts) => {
      const isPlan = gh.kind === "plan";
      const wrap = mk("g", {
        class: "gcar-wrap gcar-ghost-wrap" + (isPlan ? " gcar-plan-wrap" : " gcar-pending-wrap") +
          (opts.stack ? " gcar-ghost-stack" : ""),
      });
      // The stack's back edge: one offset outline behind the front card. Shape, not a number, says
      // "several" -- and it survives at any width, where a count label would not.
      if (opts.stack) {
        wrap.appendChild(mk("rect", {
          x: x + 2.5, y: barY - 2.5, width: Math.max(GANTT.minCarW, w - 2.5), height: GANTT.barH,
          rx: 3, class: "gcar-ghost-stackback", stroke: color,
        }));
      }
      wrap.appendChild(mk("rect", {
        x, y: barY, width: w, height: GANTT.barH, rx: 3,
        class: "gcar gcar-ghost " + (isPlan ? "gcar-plan-ghost" : "gcar-pending"), fill: color,
      }, [mk("title", { text: opts.detail })]));
      // The same three-tier label rule history uses (renderCars): inline when the car can hold a few
      // glyphs, a tag floated above when it can't, and the tooltip always. A plan step's title is its
      // name exactly as a checkpoint's intent is -- so the reader never has to ask what `+1` meant.
      const text = opts.label;
      if (w >= GANTT.labelMinW) {
        wrap.appendChild(mk("text", {
          x: x + w / 2, y: midY + 3, class: "gcar-label gcar-ghost-label",
          text: truncate(text, Math.floor(w / 6)),
        }));
      } else {
        wrap.appendChild(mk("text", {
          x: x + w / 2, y: barY - 3, class: "gcar-tag gcar-ghost-tag", text: truncate(text, 14),
        }));
      }
      // Staggered entry for a newly-arrived plan step: the steps of one plan settle in reading order
      // rather than all popping at once. `planStepEnterStagger` was already computed each render and
      // never read -- this is its consumer.
      const order = isPlan && gh.step ? planStepEnterStagger[gh.step.id] : undefined;
      if (order != null && !prefersReducedMotion()) {
        wrap.classList.add("gcar-ghost-enter");
        wrap.style.animationDelay = `${Math.min(order, 6) * 55}ms`;
      }
      // A plan ghost is the same click target as its card in the inspector: the mark in the graph and
      // the row in the list select one thing. The old badge was not clickable at all.
      if (isPlan && gh.step) {
        wrap.style.pointerEvents = "auto";
        wrap.style.cursor = "pointer";
        wrap.addEventListener("click", (ev) => { ev.stopPropagation(); selectPlanStep(gh.step.id); });
      }
      g.appendChild(wrap);
      x += w + GANTT.carGap;
    };

    for (const gh of named) drawCard(gh, { label: gh.label, detail: gh.detail });
    if (rest.length) {
      drawCard(rest[0], {
        stack: true,
        // Name the next thing while nothing else is named; count only once names are already visible.
        label: named.length ? `＋${rest.length}` : rest[0].label,
        detail: rest.map((gh) => `• ${gh.label}`).join("\n"),
      });
    }
  }

  function renderGraph() {
    if (state.view === "rail") { renderRail(); return; }
    if (!paneMeasurable()) return;
    const prevScroll = rail.scrollTop;
    rail.innerHTML = "";
    // Size the forecast band to the busiest lane's forecast, once, before geometry: every lane shares
    // one band edge so the `now` rule is a single straight line down the plot (a per-lane band would
    // make "now" ragged, and the eye reads a ragged boundary as data).
    const forecasts = new Map(layout.lanes.map((l) => [l.id, laneForecast(l)]));
    const widest = Math.max(0, ...[...forecasts.values()].map((f) => f.length));
    const geom = ganttGeom(widest);
    const svg = mk("svg", { width: geom.w, height: geom.h, class: "railsvg gantt" });
    const bandLayer = mk("g", { class: "swimlanes" });
    const laneLayer = mk("g", { class: "glanes" });
    // The forecast ground, drawn first so every ghost car sits ON it: a faint wash from the `now` rule
    // to the plot edge. It is what makes the band a *place* ("past here is not measured yet") rather
    // than a few floating dashed marks the reader has to infer a region from.
    if (geom.forecastW > 0) {
      svg.appendChild(mk("rect", {
        x: geom.nowX, y: GANTT.padT - 4, width: geom.forecastW,
        height: Math.max(0, geom.axisY - GANTT.padT + 4), class: "forecast-band",
      }));
    }
    svg.appendChild(bandLayer);
    svg.appendChild(laneLayer);

    for (const hd of layout.headers) bandLayer.appendChild(renderSwimlaneHeader(hd, geom));
    for (const l of layout.lanes) laneLayer.appendChild(renderLane(l, geom, forecasts.get(l.id) || []));
    renderTimeAxis(svg, geom);

    rail.appendChild(svg);
    rail.scrollTop = prevScroll;
    applySpotlight(); // re-pin a label-click spotlight across the re-render
  }

  // ─── The episode rail (vertical git-log) ────────────────────────────────────────────────────
  // "What I did, in order": newest commit-episode on top, each feature a lane column (its episodes
  // a straight vertical spine), lanes reused across non-overlapping spans (episodeRailLayout's
  // interval coloring). Clicking a row selects that episode's feature -- the same select path the
  // Gantt uses, so revert/preview/multi-select all work identically from here.
  const RAIL = { rowH: 22, laneW: 16, padT: 10, dotR: 4, padL: 12, shaW: 58, maxRows: 200 };

  function renderRail() {
    if (!paneMeasurable()) return;
    const prevScroll = rail.scrollTop;
    rail.innerHTML = "";
    graphView = null; // no frontier scrubber in rail mode; drop the stale Gantt handle
    const rlayout = episodeRailLayout(rollupEpisodes(map, grid));
    // Cap the DOM to the newest RAIL.maxRows episodes: one <g> per episode (5+ nodes each) meant
    // ~20k live nodes on a multi-thousand-commit repo, freezing the webview. Rows come newest-first,
    // so the head is the recent history a reader wants; the tail is summarized in a footer line.
    const allRows = rlayout.rows;
    const rows = allRows.slice(0, RAIL.maxRows);
    const hiddenRows = allRows.length - rows.length;
    const paneW = panePx().w;
    const gutterW = RAIL.padL + rlayout.laneCount * RAIL.laneW;
    const h = RAIL.padT * 2 + rows.length * RAIL.rowH + (hiddenRows > 0 ? RAIL.rowH : 0);
    const svg = mk("svg", { width: paneW, height: Math.max(h, 40), class: "railsvg rail" });
    const yOf = (row) => RAIL.padT + row * RAIL.rowH + RAIL.rowH / 2;
    const xOf = (lane) => RAIL.padL + lane * RAIL.laneW + RAIL.laneW / 2;

    if (!allRows.length) {
      const t = mk("text", { x: RAIL.padL, y: 24, class: "rail-subject", text: "No episodes yet." });
      svg.appendChild(t);
      rail.appendChild(svg);
      return;
    }

    // Feature spines: one vertical line per feature across its row-span (drawn behind the dots), so
    // a feature touched across many commits reads as one continuous column.
    const span = new Map(); // fid -> {top, bot, lane}
    for (const r of rows) {
      const s = span.get(r.feature);
      if (!s) span.set(r.feature, { top: r.row, bot: r.row, lane: r.lane });
      else { s.top = Math.min(s.top, r.row); s.bot = Math.max(s.bot, r.row); }
    }
    const spineLayer = mk("g", { class: "rail-spines" });
    for (const [fid, s] of span) {
      if (s.bot === s.top) continue;
      spineLayer.appendChild(mk("line", {
        x1: xOf(s.lane), x2: xOf(s.lane), y1: yOf(s.top), y2: yOf(s.bot),
        class: "rail-spine", stroke: laneColor(fid || ""), "data-feature": fid || "",
      }));
    }
    svg.appendChild(spineLayer);

    const textX = gutterW + 8;
    // Reserve a right-hand zone for each save's feature chips; the subject takes what's left.
    const chipZoneW = Math.min(Math.round(paneW * 0.42), 280);
    const chipX0 = paneW - chipZoneW;
    const subjChars = Math.max(6, Math.floor((chipX0 - 8 - (textX + RAIL.shaW)) / 6.2));
    for (const r of rows) {
      const inSel = r.feature === state.selected || (state.multi || []).includes(r.feature);
      const g = mk("g", { class: "rail-row" + (inSel ? " selected" : ""), "data-id": r.feature || "" });
      g.appendChild(mk("rect", {
        x: 0, y: RAIL.padT + r.row * RAIL.rowH, width: paneW, height: RAIL.rowH, class: "rail-hit",
      }));
      g.appendChild(mk("circle", {
        cx: xOf(r.lane), cy: yOf(r.row), r: RAIL.dotR, class: "rail-dot",
        fill: laneColor(r.feature || ""), "data-feature": r.feature || "",
      }));
      g.appendChild(mk("text", { x: textX, y: yOf(r.row) + 4, class: "rail-sha", text: (r.sha || "").slice(0, 7) }));
      const subj = mk("text", { x: textX + RAIL.shaW, y: yOf(r.row) + 4, class: "rail-subject" });
      subj.textContent = truncate((r.subject || "").replace(/\n/g, " "), subjChars);
      g.appendChild(subj);
      renderRailChips(g, r, chipX0, yOf(r.row) + 4, chipZoneW - 8);
      if (r.feature) {
        g.addEventListener("click", (ev) => selectRow(r.feature, ev.metaKey || ev.ctrlKey || ev.shiftKey));
      }
      // Hover a save-row -> light the lane columns of EVERY feature that save touched (task 5), not
      // just its dominant one, so the save->feature spread reads at a glance.
      g.addEventListener("mouseenter", () => lightRailFeatures(Object.keys(r.features || {})));
      g.addEventListener("mouseleave", () => lightRailFeatures(null));
      svg.appendChild(g);
    }
    // The capped tail: older episodes stay off the DOM but are accounted for, so a big repo doesn't
    // look like its history stops at RAIL.maxRows.
    if (hiddenRows > 0) {
      svg.appendChild(mk("text", {
        x: textX, y: RAIL.padT + rows.length * RAIL.rowH + RAIL.rowH / 2 + 4,
        class: "rail-chip-more", text: `+${hiddenRows} older episode(s) not shown`,
      }));
    }
    rail.appendChild(svg);
    rail.scrollTop = prevScroll;
  }

  // One save-row's feature chips: each touched feature's label in its own identity hue, the
  // dominant feature first, then densest-first (mirrors sgt/tui/graph.py's `chips`). Up to three,
  // with a dim "+N" for the rest -- the save -> feature mapping, on every row. SVG text spans flow
  // left->right in the reserved right-hand zone; widths are the same ~6px/char estimate the rail's
  // subject truncation uses (no text metrics available in the webview).
  function renderRailChips(g, r, x0, y, maxW) {
    const feats = r.features || {};
    const ids = Object.keys(feats);
    if (!ids.length) return;
    const main = r.feature;
    ids.sort((a, b) => {
      const am = a === main ? 0 : 1, bm = b === main ? 0 : 1;
      if (am !== bm) return am - bm;
      if (feats[a] !== feats[b]) return feats[b] - feats[a];
      return a < b ? -1 : a > b ? 1 : 0;
    });
    const shown = ids.slice(0, 3);
    const right = x0 + maxW;
    let x = x0;
    for (let i = 0; i < shown.length; i++) {
      if (x >= right) return;
      if (i > 0) {
        g.appendChild(mk("text", { x, y, class: "rail-chip-sep", text: "·" }));
        x += 8;
      }
      const fid = shown[i];
      const node = byId(fid);
      const label = truncate((node && node.label) || fid || "(unattributed)", 16);
      const t = mk("text", { x, y, class: "rail-chip", fill: laneColor(fid || "") });
      t.textContent = label;
      g.appendChild(t);
      x += label.length * 6.2 + 2;
    }
    const extra = ids.length - shown.length;
    if (extra > 0 && x < right) {
      g.appendChild(mk("text", { x, y, class: "rail-chip-more", text: `+${extra}` }));
    }
  }

  // Light the rail lane-columns (feature spines + dots) of a hovered save's touched features, so a
  // save reads as "these feature columns". Hover-only; clears when `featureIds` is null/empty.
  function lightRailFeatures(featureIds) {
    const svg = rail.querySelector("svg");
    if (!svg) return;
    svg.querySelectorAll(".rail-lit").forEach((el) => el.classList.remove("rail-lit"));
    if (!featureIds || !featureIds.length) return;
    const set = new Set(featureIds);
    svg.querySelectorAll(".rail-spine, .rail-dot").forEach((el) => {
      if (set.has(el.getAttribute("data-feature"))) el.classList.add("rail-lit");
    });
  }

  // A subsystem swimlane header: a faint full-width band with a ▾ caret + label + "(N feat)", and
  // the group's [first,last] span drawn faintly in the plot. Clicking collapses the subsystem back
  // to a single meta-lane (toggleCollapse), so it's the "fold this cluster" affordance.
  function renderSwimlaneHeader(hd, geom) {
    const y = geom.rowY(hd.row);
    const ind = (hd.depth || 0) * GANTT.indent; // nested subsystems step in like their member lanes
    const g = mk("g", { class: "swimlane", "data-id": hd.collapsedId, "data-first": hd.firstCommit });
    g.appendChild(mk("rect", { x: 0, y, width: geom.w, height: GANTT.rowH, class: "swimlane-band" }));
    g.appendChild(mk("text", { x: 8 + ind, y: y + GANTT.rowH / 2 + 4, class: "swimlane-caret", text: "▾" }));
    // The end-anchored meta grows leftward from ~labelW into the same column, so reserve its width
    // (10px font ~= 6px/char, + an 8px gap) out of the label's char budget or the two overprint.
    const metaText = `${hd.laneCount} feat`;
    const metaW = metaText.length * 6 + 8;
    const label = mk("text", { x: 22 + ind, y: y + GANTT.rowH / 2 + 4, class: "swimlane-label" });
    label.textContent = truncate(hd.label, Math.max(4, Math.floor((geom.labelW - 30 - ind - metaW) / 6.5)));
    g.appendChild(label);
    // The subsystem's own activity envelope in the plot, so the header still shows "when".
    const bx = geom.xOf(hd.firstCommit), bx2 = geom.xOf(hd.lastCommit);
    g.appendChild(mk("rect", {
      x: bx, y: y + GANTT.rowH / 2 - 2, width: Math.max(GANTT.minBarW, bx2 - bx), height: 4,
      rx: 2, class: "swimlane-span", "data-first": hd.firstCommit,
    }));
    const meta = mk("text", { x: geom.plotX0 - 8, y: y + GANTT.rowH / 2 + 4, class: "swimlane-meta" });
    meta.textContent = metaText;
    g.appendChild(meta);
    g.addEventListener("click", () => toggleCollapse(hd.collapsedId)); // fold cluster -> meta-lane
    return g;
  }

  function renderLane(l, geom, ghosts = []) {
    const y = geom.rowY(l.row);
    const midY = geom.midY(l.row);
    const barY = midY - GANTT.barH / 2;
    const color = laneColor(l.id);
    const inSelection = l.id === state.selected || (state.multi || []).includes(l.id);
    const g = mk("g", {
      class: "glane" + (inSelection ? " selected" : ""),
      "data-id": l.id, "data-first": l.firstCommit,
    });
    // full-row hit target (so hovering/clicking the gutter or empty time works, not just the bar)
    g.appendChild(mk("rect", { x: 0, y, width: geom.w, height: GANTT.rowH, class: "glane-hit" }));

    // Left gutter: identity swatch (▸ caret for a folded subsystem), then the label. Indented by
    // the lane's nesting depth so features step in under their (possibly nested) subsystem header.
    const gx = GANTT.gutterPad + (l.depth || 0) * GANTT.indent;
    if (l.isMeta) {
      g.appendChild(mk("text", { x: gx, y: midY + 4, class: "glane-caret", text: "▸" }));
      g.appendChild(mk("rect", { x: gx + 12, y: midY - 4, width: 8, height: 8, rx: 2, class: "glane-swatch", fill: color }));
    } else {
      g.appendChild(mk("rect", { x: gx, y: midY - 4, width: 8, height: 8, rx: 2, class: "glane-swatch", fill: color }));
    }
    const labelX = gx + (l.isMeta ? 24 : 12);
    const label = mk("text", { x: labelX, y: midY + 4, class: "glane-label" });
    const node = byId(l.id);
    const raw = (node && node.label) || l.id;
    label.textContent = truncate(l.isMeta ? `${raw} (${l.leaves.length})` : raw,
      Math.floor((geom.labelW - labelX) / 6.5));
    // A feature label is its own click target: it spotlights the feature (dim the field, light this
    // lane + co-change neighbors) rather than selecting it -- a distinct, reversible viewing gesture.
    // Meta (collapsed-subsystem) labels keep the row's fold-toggle behavior.
    if (!l.isMeta) {
      label.classList.add("glane-label-btn");
      label.style.pointerEvents = "auto";
      if (state.spotlight === l.id) label.classList.add("spotlit");
      label.addEventListener("click", (ev) => { ev.stopPropagation(); toggleSpotlight(l.id); });
    }
    g.appendChild(label);

    // Chunk-car train: the lane's checkpoints, packed left->right in seg_index order (see
    // renderCars) -- the visual atom is the intent segment, not a raw op or a shared time column.
    const lastX = renderCars(g, l, geom, color, barY, midY);
    // Op count just past the cars (clamped so it stays on-screen).
    const cx = Math.min(lastX + 6, geom.w - 30);
    g.appendChild(mk("text", { x: cx, y: midY + 4, class: "gbar-count", text: String(l.opCount) }));

    // This lane's future, in the band right of the `now` rule: uncommitted work + pending plan steps,
    // drawn as cars in the same grammar as history (see laneForecast / renderForecastCars).
    renderForecastCars(g, l, geom, color, barY, midY, ghosts);

    renderLaneBadges(g, l, geom, color, barY, midY, geom.plotX0, lastX);

    g.addEventListener("mouseenter", () => onHover(l.id));
    g.addEventListener("mouseleave", () => onHover(null));
    g.addEventListener("click", (ev) => {
      if (l.isMeta) { toggleCollapse(l.id); return; } // expand the subsystem into its features
      if (armedVerb) { confirmArmed(l.id); return; }
      selectRow(l.id, ev.metaKey || ev.ctrlKey || ev.shiftKey);
    });
    return g;
  }

  // Drift / fork are decorations ON the lane (never separate marks): a lane carrying a drift op gets
  // a solid identity outline; a forked lane gets a ⋔ badge in the gutter. Neither introduces a second
  // hue competing with identity. Pending PLAN steps used to live here too, as a dashed underline plus
  // a `+N` step count -- a second visual language for "not real yet" that competed with the save
  // preview's ghost car and left the reader with a bare count and no way to learn what it stood for.
  // They are forecast cars now (renderForecastCars), so a plan step is named, positioned, and
  // clickable like every other unit of work in this view.
  function renderLaneBadges(g, l, geom, color, barY, midY, x1, x2) {
    const barW = Math.max(GANTT.minBarW, x2 - x1);
    const hasDrift = (layout.opsByFeature[l.id] || []).some((op) => driftMarks.ids.has(op.id));
    if (hasDrift) {
      g.appendChild(mk("rect", {
        x: x1 - 1.5, y: barY - 1.5, width: barW + 3, height: GANTT.barH + 3, rx: 4,
        class: "gbar-drift", stroke: color,
      }));
    }
    if (forkMarks.byFeature[l.id]) {
      const f = mk("text", { x: geom.labelW - 6, y: midY + 4, class: "gbar-fork", text: "⋔" });
      f.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const forks = forkMarks.byFeature[l.id];
        if (forks && forks[0]) vscode.postMessage({ type: "resolveFork", symbol: forks[0].symbol });
      });
      g.appendChild(f);
    }
  }

  // The bottom time axis + frontier scrubber. Ticks label a handful of commit-index positions; the
  // draggable handle (and click anywhere in the plot) scrubs a fold frontier -- a translucent veil
  // covers everything to its right (the "future") and the code(I)/op-set panels reflect that point,
  // without a relayout on every drag.
  function renderTimeAxis(svg, geom) {
    if (layout.commitCount <= 1) return;
    const y = geom.axisY;
    svg.appendChild(mk("line", { x1: geom.plotX0, x2: geom.plotX0 + geom.plotW, y1: y, y2: y, class: "axis-track" }));
    for (let i = 0; i <= 4; i++) {
      const ci = Math.round((i / 4) * geom.maxCommit);
      const tx = geom.xOf(ci);
      // Faint full-height gridline so the (now bottom-pinned) plot reads as a structured field of
      // time columns rather than an empty expanse above the axis.
      svg.appendChild(mk("line", { x1: tx, x2: tx, y1: GANTT.padT, y2: y, class: "axis-gridline", "data-ci": ci }));
      svg.appendChild(mk("text", {
        x: tx, y: y + 16, class: "axis-tick" + (i === 0 ? " start" : i === 4 ? " end" : ""), text: `c${ci}`, "data-ci": ci,
      }));
    }
    svg.appendChild(mk("text", { x: geom.plotX0, y: GANTT.padT - 3, class: "axis-title", text: "time →" }));

    // The `now` rule: the honest right edge of measured time. Everything left of it happened;
    // everything right of it is forecast. Drawn only when there IS a forecast, so a repo with nothing
    // pending keeps exactly the axis it had. Labelled once, at the top, in the dim channel -- the band
    // wash plus this one word is the whole explanation, so no ghost car needs its own caption.
    if (geom.forecastW > 0) {
      svg.appendChild(mk("line", {
        x1: geom.nowX, x2: geom.nowX, y1: GANTT.padT - 4, y2: y, class: "now-rule",
      }));
      svg.appendChild(mk("text", {
        x: geom.nowX + 4, y: GANTT.padT - 3, class: "axis-title now-label", text: "next",
      }));
    }

    const frontier = playheadCommitIndex == null ? geom.maxCommit : playheadCommitIndex;
    const fx = geom.scrubX(frontier);
    const veil = mk("rect", {
      x: fx, y: GANTT.padT, width: Math.max(0, geom.plotX0 + geom.plotW - fx), height: y - GANTT.padT,
      class: "future-veil" + (playheadCommitIndex == null ? " at-head" : ""),
    });
    const line = mk("line", { x1: fx, x2: fx, y1: GANTT.padT - 2, y2: y, class: "frontier-line" + (playheadCommitIndex == null ? " at-head" : "") });
    const handle = mk("path", { d: `M ${fx - 5} ${y + 3} L ${fx + 5} ${y + 3} L ${fx} ${y - 4} Z`, class: "frontier-handle", "data-cx": fx });
    // A wide invisible grab-band over the frontier line, spanning the plot height. Scrubbing is a
    // deliberate "grab the playhead and drag" gesture -- via this band or the bottom handle -- never
    // a plain click: a lane click still selects its feature, and clicking empty plot does nothing.
    const band = mk("rect", { x: fx - 6, y: GANTT.padT - 2, width: 12, height: y - (GANTT.padT - 2), class: "frontier-band" });
    // A floating readout above the playhead, shown only while scrubbing: the commit index it's on
    // plus the checkpoint(s) that begin/end there when snapped to a boundary -- so a scrub reads as
    // "landing on this event", not "somewhere on a bare axis".
    const readout = mk("text", { x: fx, y: GANTT.padT - 5, class: "scrub-readout", text: "" });
    readout.style.display = "none";
    svg.appendChild(veil);
    svg.appendChild(line);
    svg.appendChild(band);
    svg.appendChild(handle);
    svg.appendChild(readout);
    // Snap targets: every checkpoint boundary (a car's first/last commit) -- the commit indices where
    // "something happened", so the playhead clicks onto real events instead of arbitrary columns.
    const snap = new Set();
    for (const l of layout.lanes) for (const c of (l.cars || [])) { snap.add(c.firstIndex); snap.add(c.lastIndex); }
    graphView = { geom, handleEl: handle, frontierEl: line, veilEl: veil, scrubBandEl: band,
      readoutEl: readout, snap: [...snap].sort((a, b) => a - b) };
    handle.addEventListener("pointerdown", onScrubPointerDown);
    band.addEventListener("pointerdown", onScrubPointerDown);
  }

  // Snap a raw commit index to the nearest checkpoint boundary when it's within a few px, so the
  // playhead lands on events; otherwise leave it free for fine positioning between them.
  function snapCommit(idx, geom) {
    const pts = graphView && graphView.snap;
    if (!pts || !pts.length) return idx;
    const x = geom.xOf(idx);
    let best = idx, bestPx = Infinity;
    for (const p of pts) { const d = Math.abs(geom.xOf(p) - x); if (d < bestPx) { bestPx = d; best = p; } }
    return bestPx <= 8 ? best : idx;
  }

  // The checkpoint labels whose chapter begins or ends exactly at this commit -- what the scrub
  // readout names when the playhead snaps onto a boundary.
  function eventsAt(idx) {
    const out = [];
    for (const l of layout.lanes) for (const c of (l.cars || [])) {
      if (c.firstIndex === idx || c.lastIndex === idx) out.push(c.label);
    }
    return out;
  }

  function truncate(s, n) {
    s = String(s);
    return s.length > n ? s.slice(0, Math.max(1, n - 1)) + "…" : s;
  }

  // A circular progress arc for a plan session's `matchedCount/stepCount` -- geometric, no
  // side-text. Used by the titlebar session chip.
  function renderPlanRing(cx, matched, total) {
    const r = 5;
    const c = 2 * Math.PI * r;
    const frac = total ? matched / total : 0;
    const g = mk("g", { class: "plan-ring-group", transform: `translate(${cx}, 0)` });
    g.appendChild(mk("circle", { cx: 0, cy: 0, r, class: "plan-ring plan-ring-track" }));
    g.appendChild(mk("circle", {
      cx: 0, cy: 0, r, class: "plan-ring plan-ring-fill", transform: "rotate(-90)",
      "stroke-dasharray": `${c} ${c}`, "stroke-dashoffset": `${(c * (1 - frac)).toFixed(2)}`,
    }));
    return g;
  }

  // ─── Frontier scrubber ──────────────────────────────────────────────────────────────────────
  // A commit-index dragged along the time axis. It's a *fold point*, not a snapshot pointer: a
  // translucent veil covers the plot to its right (the "future"), lanes not yet born dim, and the
  // code(I) + op-set panels reflect the fold at that commit -- but the layout itself never moves,
  // so scrubbing stays smooth (moving the veil + line is O(1), no relayout per pointermove).
  function svgLocalX(svg, clientX) {
    return clientX - svg.getBoundingClientRect().left;
  }

  function applyFrontier() {
    const svg = rail.querySelector("svg");
    if (!svg || !graphView) return;
    const geom = graphView.geom;
    const atHead = playheadCommitIndex == null;
    const idx = atHead ? geom.maxCommit : playheadCommitIndex;
    const fx = geom.scrubX(idx);
    graphView.frontierEl.setAttribute("x1", fx);
    graphView.frontierEl.setAttribute("x2", fx);
    graphView.frontierEl.classList.toggle("at-head", atHead);
    graphView.veilEl.setAttribute("x", fx);
    graphView.veilEl.setAttribute("width", Math.max(0, geom.plotX0 + geom.plotW - fx));
    graphView.veilEl.classList.toggle("at-head", atHead);
    const hd = graphView.handleEl;
    const y = geom.axisY;
    hd.setAttribute("d", `M ${fx - 5} ${y + 3} L ${fx + 5} ${y + 3} L ${fx} ${y - 4} Z`);
    if (graphView.scrubBandEl) graphView.scrubBandEl.setAttribute("x", fx - 6); // keep the grab-band on the line
    if (graphView.readoutEl) {
      const ro = graphView.readoutEl;
      if (playheadDragging && !atHead) {
        const events = eventsAt(idx);
        ro.textContent = `c${idx}` + (events.length ? " · " + truncate(events[0], 26) : "");
        ro.setAttribute("x", Math.max(geom.plotX0 + 20, Math.min(geom.plotX0 + geom.plotW - 20, fx)));
        ro.setAttribute("text-anchor", fx > geom.plotX0 + geom.plotW - 80 ? "end" : "middle");
        ro.style.display = "";
      } else {
        ro.style.display = "none";
      }
    }
    // Dim the gutter (label + swatch) of lanes/swimlanes not yet born, and any car whose checkpoint
    // starts past the frontier -- the veil handles the rest of the plot.
    for (const el of svg.querySelectorAll(".glane, .swimlane, .gcar-wrap")) {
      const first = Number(el.getAttribute("data-first"));
      el.classList.toggle("beyond", !atHead && first > playheadCommitIndex);
    }
    renderPresence(); // keep the "where am I" band's scrub position live while dragging
  }

  function setPlayhead(idx) {
    if (playheadCommitIndex === idx) return;
    playheadCommitIndex = idx;
    applyFrontier();
    renderInspector();
    scheduleScrub(idx);
  }

  function clearPlayhead() {
    if (playheadCommitIndex == null) return;
    clearTimeout(scrubTimer);
    playheadCommitIndex = null;
    applyFrontier();
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

  function onScrubPointerDown(ev) {
    ev.stopPropagation();
    ev.preventDefault(); // a scrub is a drag, not a text-selection gesture -- suppress the latter
    const svg = rail.querySelector("svg");
    if (!svg || !graphView) return;
    playheadDragging = true;
    if (svg.setPointerCapture) svg.setPointerCapture(ev.pointerId);
    const geom = graphView.geom;
    setPlayhead(snapCommit(geom.xToCommit(svgLocalX(svg, ev.clientX)), geom));
    window.addEventListener("pointermove", onScrubPointerMove);
    window.addEventListener("pointerup", onScrubPointerUp);
  }

  function onScrubPointerMove(ev) {
    if (!playheadDragging) return;
    const svg = rail.querySelector("svg");
    if (!svg || !graphView) return;
    const geom = graphView.geom;
    // Coalesce to one setPlayhead per frame: pointermove fires far above 60fps and setPlayhead runs
    // applyFrontier + renderInspector (a full panel rebuild) each call, so an uncoalesced drag on a
    // large graph stutters. Stash the latest index and flush the freshest one on the next frame.
    scrubPendingIdx = snapCommit(geom.xToCommit(svgLocalX(svg, ev.clientX)), geom);
    if (scrubRaf) return;
    scrubRaf = requestAnimationFrame(() => {
      scrubRaf = 0;
      setPlayhead(scrubPendingIdx);
    });
  }

  function onScrubPointerUp() {
    playheadDragging = false;
    // Flush any frame the last move scheduled but hasn't run, so the playhead lands where released.
    if (scrubRaf) { cancelAnimationFrame(scrubRaf); scrubRaf = 0; setPlayhead(scrubPendingIdx); }
    applyFrontier(); // hide the scrub readout now that the drag is over
    window.removeEventListener("pointermove", onScrubPointerMove);
    window.removeEventListener("pointerup", onScrubPointerUp);
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

  // Brighten the time-axis gridlines/ticks within a hovered lane's active commit span, so hovering a
  // feature also shows *when* on the shared axis it was worked (task 5). Hover-only; the same
  // dim/light grammar the lanes use, no new hue. `laneId == null` clears.
  function markAxisSpan(svg, laneId) {
    svg.querySelectorAll(".axis-gridline.lit, .axis-tick.lit").forEach((el) => el.classList.remove("lit"));
    if (laneId == null) return;
    const l = layout.laneById[laneId];
    if (!l) return;
    svg.querySelectorAll(".axis-gridline, .axis-tick").forEach((el) => {
      const ci = Number(el.getAttribute("data-ci"));
      if (ci >= l.firstCommit && ci <= l.lastCommit) el.classList.add("lit");
    });
  }

  function onHover(id) {
    const svg = rail.querySelector("svg");
    if (!svg) return;
    if (!id) {
      markAxisSpan(svg, null);
      if (!armedVerb) clearGhosts();
      if (state.spotlight) { applySpotlight(); return; } // a pinned spotlight survives mouse-out
      svg.classList.remove("focus");
      svg.querySelectorAll(".lit, .ctx").forEach((el) => el.classList.remove("lit", "ctx"));
      return;
    }
    if (armedVerb) {
      // Picking a target while "Merge into..."/"Move ops..." is armed: outline the candidate and
      // live-preview the real op-count/member delta it would produce, via the same blast paint.
      clearGhosts();
      if (id !== armedVerb.feature) {
        rail.querySelectorAll(".glane").forEach((el) => {
          if (el.getAttribute("data-id") === id) el.classList.add("ghost-target");
        });
        previewArmed(id);
      }
      return;
    }
    if (previewActive) return; // a held consequence preview owns the field dim; don't re-light co-change
    // Focus the hovered lane + its co-change neighbors; dim the rest (the "what changes with this").
    // This is the only place co-change is shown -- kept off the default view to avoid a hairball.
    svg.classList.add("focus");
    const neighbors = neighborsOf(id);
    svg.querySelectorAll(".glane").forEach((el) => {
      const rid = el.getAttribute("data-id");
      el.classList.toggle("lit", rid === id);
      el.classList.toggle("ctx", neighbors.has(rid));
    });
    markAxisSpan(svg, id); // brighten the time columns this lane spans
  }

  function previewArmed(targetId) {
    const { verb, feature } = armedVerb;
    if (verb === "merge") {
      previewAndBlast("merge", [targetId, feature]);
    } else if (verb === "move") {
      previewAndBlast("move", [...opIdsFor(feature), targetId]);
    }
  }

  // Plain click = single-select toggle (clears any multi set). ⌘/ctrl/shift-click = accrete/toggle
  // into the multi set (the VS Code parallel of the TUI's space-select). state.selected stays the
  // "primary" (last-touched) row that drives the per-feature inspector; state.multi is the set the
  // union-closure card + paint read.
  function selectRow(id, additive) {
    const multi = state.multi || [];
    if (additive) {
      const i = multi.indexOf(id);
      if (i >= 0) multi.splice(i, 1);
      else multi.push(id);
      state.multi = multi;
      state.selected = multi.length ? multi[multi.length - 1] : null;
    } else {
      const wasSole = multi.length === 1 && multi[0] === id && state.selected === id;
      state.multi = wasSole ? [] : [id];
      state.selected = wasSole ? null : id;
    }
    state.selectedStep = null;
    state.selectedPlanSession = null;
    state.selectedCheckpoint = null; // a feature-level select clears any checkpoint focus
    if ((state.multi || []).length < 2) selectionResult = null; // no union closure to show
    saveState();
    render();
    if ((state.multi || []).length >= 2) {
      requestSelectionClosure(state.multi);
    } else {
      const node = state.selected && byId(state.selected);
      if (node && node.kind === "feature") requestFold(state.selected);
    }
  }

  // Clicking a chunk-car selects its CHECKPOINT (`f-XXXX@n`), the revert unit -- distinct from a
  // row/swatch click (whole feature) or a label click (spotlight). The feature is selected so the
  // inspector shows its checkpoint list + code fold; the matching checkpoint row is highlighted and
  // scrolled into view. Clicking the same car again clears the checkpoint focus. An armed
  // merge/move still targets the feature (a car is just a point on it), matching lane-click.
  function selectCar(car, laneId, ev) {
    if (ev) ev.stopPropagation();
    if (armedVerb) { confirmArmed(laneId); return; }
    if (state.selected !== laneId) {
      state.multi = [laneId];
      state.selected = laneId;
      state.selectedStep = null;
      state.selectedPlanSession = null;
      selectionResult = null;
    }
    state.selectedCheckpoint = state.selectedCheckpoint === car.checkpoint ? null : car.checkpoint;
    saveState();
    render();
    const node = byId(laneId);
    if (node && node.kind === "feature") requestFold(laneId);
    const row = inspector.querySelector(".checkpoint.selected");
    if (row) row.scrollIntoView({ block: "nearest" });
  }

  // Toggle a checkpoint's highlight from the inspector's checkpoint list (the feature is already
  // selected there). Keeps the gantt car and the inspector row in agreement -- click either, both
  // light up.
  function highlightCheckpoint(ref) {
    state.selectedCheckpoint = state.selectedCheckpoint === ref ? null : ref;
    saveState();
    renderInspector();
    renderGraph();
  }

  // Clicking a feature LABEL spotlights it: dim the field, re-light this lane + its co-change
  // neighbors (the "what moves with this feature" question) -- a viewing aid, pinned until toggled
  // off, distinct from selecting the feature for an action. Reuses the same .focus/.lit/.ctx paint
  // the transient hover-focus uses.
  function toggleSpotlight(id) {
    state.spotlight = state.spotlight === id ? null : id;
    saveState();
    applySpotlight();
  }

  function applySpotlight() {
    const svg = rail.querySelector("svg");
    if (!svg) return;
    if (!state.spotlight) {
      svg.classList.remove("focus");
      svg.querySelectorAll(".lit, .ctx").forEach((el) => el.classList.remove("lit", "ctx"));
      return;
    }
    svg.classList.add("focus");
    const neighbors = neighborsOf(state.spotlight);
    svg.querySelectorAll(".glane").forEach((el) => {
      const rid = el.getAttribute("data-id");
      el.classList.toggle("lit", rid === state.spotlight);
      el.classList.toggle("ctx", neighbors.has(rid));
    });
  }

  // Reveal a feature from the editor (task 4): select it (so the inspector opens), pin a spotlight on
  // it, and scroll its lane/row into view -- the same primitives selectRow/toggleSpotlight use, but
  // deterministic (never toggling off). Robust to message ordering: if the graph hasn't loaded this
  // feature yet, stash it and re-apply after the next state render (see the "state" handler).
  function revealFeature(featureId) {
    if (!featureId || !byId(featureId)) { pendingReveal = featureId || null; return; }
    pendingReveal = null;
    // In the Gantt a feature folded inside a collapsed subsystem has no lane -- expand its ancestors
    // so the row exists to select and scroll to. (The rail ignores collapse, so this is a no-op there.)
    let changed = false;
    let cur = byId(featureId);
    cur = cur ? byId(cur.parent) : null;
    while (cur) {
      const i = state.collapsed.indexOf(cur.id);
      if (i >= 0) { state.collapsed.splice(i, 1); changed = true; }
      cur = byId(cur.parent);
    }
    state.multi = [featureId];
    state.selected = featureId;
    state.selectedStep = null;
    state.selectedPlanSession = null;
    state.selectedCheckpoint = null;
    selectionResult = null;
    state.spotlight = featureId; // pin the spotlight (applySpotlight runs inside render's renderGraph)
    saveState();
    if (changed) recompute();
    render();
    const node = byId(featureId);
    if (node && node.kind === "feature") requestFold(featureId);
    let target = null;
    rail.querySelectorAll(".glane, .rail-row").forEach((el) => {
      if (!target && el.getAttribute("data-id") === featureId) target = el;
    });
    if (target) target.scrollIntoView({ block: "center" });
  }

  function requestSelectionClosure(refs) {
    const seq = ++selectionSeq;
    pendingSelection = { seq, refs: refs.slice() };
    vscode.postMessage({ type: "selectClosure", refs, seq });
  }

  function selectPlanStep(stepId) {
    state.selectedStep = state.selectedStep === stepId ? null : stepId;
    state.selected = null;
    state.selectedPlanSession = null;
    saveState();
    render();
  }

  function selectPlanSession(sessionId) {
    state.selectedPlanSession = state.selectedPlanSession === sessionId ? null : sessionId;
    state.selected = null;
    state.selectedStep = null;
    saveState();
    render();
  }

  function clearGhosts() {
    rail.querySelectorAll(
      ".glane.ghost-blast, .glane.ghost-target, .glane.ghost-foundation, " +
      ".rail-row.ghost-blast, .rail-row.ghost-target, .rail-row.ghost-foundation").forEach((el) => {
      el.classList.remove("ghost-blast", "ghost-target", "ghost-foundation");
    });
    clearOffscreenPills();
    clearPreviewRefusal(); // a blocked-restore overlay clears on the same mouseleave path
    exitPreviewMode(); // a held Focus & Morph overlay tears down on the same mouseleave path
  }

  function requestPreview(verb, args, onResult) {
    const seq = ++previewSeq;
    vscode.postMessage({ type: "previewVerb", verb, args, seq });
    pendingPreview = { seq, onResult };
  }

  // Every hover-preview site wants the same thing: show the consequence if the preview came back
  // ok, do nothing otherwise. The target is args[0] (revert/restore take one feature). When the
  // backend hands back a `focus` subgraph (a feature map is built) and we're not mid-arming, use the
  // richer deep-dim morph; otherwise fall back to the flat three-role ghost paint.
  function previewAndBlast(verb, args) {
    requestPreview(verb, args, (res) => {
      if (!res || !res.ok) {
        // A blocked restore -- the symbol has a competing live version, so sgt refuses. Surface the
        // two ways out in the preview overlay instead of silently doing nothing.
        if (verb === "restore" && res && res.forked) showRestoreRefusal(res, args[0]);
        return;
      }
      const focus = res.focus;
      if (!armedVerb && focus && focus.nodes && focus.nodes.length) {
        enterPreviewMode(focus, args[0]);
      } else {
        paintClosure(classifyAffected(res, args[0]));
      }
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
    // A multi-select (>=2 lanes) takes over the inspector with the union-closure card -- there is
    // no single "primary" feature to show a code panel for; the question is "what does this SET
    // revert, together."
    if ((state.multi || []).length >= 2) {
      inspector.appendChild(renderSelectionCard());
      return;
    }
    const id = state.selected;
    const node = id && byId(id);
    const step = state.selectedStep && planMarks.steps.find((s) => s.id === state.selectedStep);
    const session = state.selectedPlanSession && planMarks.sessions.find((s) => s.sessionId === state.selectedPlanSession);

    if (step) {
      inspector.appendChild(renderPlanCard(step));
    } else if (session) {
      inspector.appendChild(renderPlanSessionCard(session));
    } else if (node) {
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
      meta.textContent = `${node.id} · ${node.size} member(s)`;
      inspector.appendChild(meta);

      if (node.kind === "feature") {
        inspector.appendChild(renderActionBar(id));
        inspector.appendChild(renderCheckpoints(id));
      }
    }

    // Each of these takes over the code(I) slot with a read-only view of a DIFFERENT frontier
    // than the one the action bar above still previews/applies against -- composition-preview
    // (hovering the composition QuickPick), then the playhead, then the ordinary selection.
    if (compositionPreviewActive != null) {
      renderCompositionPreviewPanel(node && node.kind === "feature" ? node : null);
    } else if (playheadCommitIndex != null) {
      renderPlayheadPanel(playheadCommitIndex, node && node.kind === "feature" ? node : null);
    } else if (node && node.kind === "feature") {
      renderCodePanel(id);
    } else if (!node && !step && !session) {
      // The panel's "home" state: nothing selected, not scrubbing. Surface the uncommitted work
      // as a record-and-save card so the primary daily action is one click from an idle view.
      renderWorkingChangesCard();
    }
  }

  // Working changes = files that differ from the recorded ideal (`status.drift`), i.e. edits not
  // yet mined into ops. This is the "record what I just did, fast" affordance the titlebar's Save
  // button had no context for -- here you see WHAT would be recorded before recording it.
  // The multi-select union-closure card (Stage C): "N features -> M ops in closure", the OTHER
  // features that selection pulls in (the blast beyond the direct pick), the selected lanes as
  // deselectable chips, and a Revert-all action. The VS Code parallel of the TUI's frontier
  // checklist header; the closure itself is `sgt select`'s report (selectionResult).
  function renderSelectionCard() {
    const wrap = document.createElement("div");
    const h = document.createElement("div");
    h.className = "detail-title";
    h.textContent = `Selection · ${state.multi.length} features`;
    wrap.appendChild(h);

    const view = selectionResult && selectionResult.view;
    if (!view) {
      wrap.appendChild(statusLine("Resolving closure…", ""));
    } else if (!view.ok) {
      wrap.appendChild(statusLine(view.message || "Cannot resolve selection.", "fail"));
    } else {
      const meta = document.createElement("div");
      meta.className = "detail-meta";
      meta.textContent =
        `${view.direct_op_count} direct edit(s) · ${view.closure_op_count} in closure · ${(view.files || []).length} file(s)`;
      wrap.appendChild(meta);

      const direct = new Set(view.feature_ids || []);
      const pulled = (view.pulled || []).filter((p) => p.feature_id && !direct.has(p.feature_id) && p.op_count > 0);
      if (pulled.length) {
        const why = document.createElement("div");
        why.className = "detail-why";
        why.textContent = "Pulls in ops from (amber on the graph):";
        wrap.appendChild(why);
        const chips = document.createElement("div");
        chips.className = "footprint-chips";
        for (const p of pulled) {
          const node = byId(p.feature_id);
          const chip = document.createElement("span");
          chip.className = "chip";
          chip.textContent = `${(node && node.label) || p.feature_id} · ${p.op_count}`;
          chips.appendChild(chip);
        }
        wrap.appendChild(chips);
      }
      if (view.hub) {
        wrap.appendChild(statusLine(`⚠ hub ${view.hub.symbol} pulls ${view.hub.pulled_op_count} edit(s)`, ""));
      }
    }

    // Selected lanes as deselectable chips.
    const picked = document.createElement("div");
    picked.className = "footprint-chips";
    for (const fid of state.multi) {
      const node = byId(fid);
      const chip = document.createElement("span");
      chip.className = "chip selected-chip";
      chip.textContent = (node && node.label) || fid;
      chip.title = "click to deselect";
      chip.addEventListener("click", () => selectRow(fid, true));
      picked.appendChild(chip);
    }
    wrap.appendChild(picked);

    const bar = document.createElement("div");
    bar.className = "action-bar";
    const clear = document.createElement("button");
    clear.className = "action";
    clear.textContent = "Clear";
    clear.addEventListener("click", clearSelection);
    bar.appendChild(clear);
    const revert = document.createElement("button");
    revert.className = "action primary";
    revert.textContent = "Revert all";
    revert.title = "Revert each selected feature in turn (stops if one refuses)";
    revert.addEventListener("mouseenter", () => paintSelectionClosure());
    revert.addEventListener("mouseleave", () => clearGhosts());
    revert.addEventListener("click", () => vscode.postMessage({ type: "revertSelection", refs: state.multi.slice() }));
    bar.appendChild(revert);
    wrap.appendChild(bar);
    return wrap;
  }

  function clearSelection() {
    state.multi = [];
    state.selected = null;
    selectionResult = null;
    saveState();
    render();
  }

  // Amber the features the current selection pulls in beyond the direct pick (the union's blast),
  // so "where this selection lands" is visible on the graph. Direct picks already read as
  // .selected; this adds the closure-only features.
  function paintSelectionClosure() {
    const view = selectionResult && selectionResult.view;
    if (!view || !view.ok) return;
    const direct = new Set(view.feature_ids || []);
    paintBlast((view.pulled || []).map((p) => p.feature_id).filter((f) => f && !direct.has(f)));
  }

  function renderWorkingChangesCard() {
    const drift = (compose.status && compose.status.drift) || { any: false, paths: [] };
    const paths = drift.paths || [];
    const wrap = document.createElement("div");
    wrap.className = "changes-card";

    const h = document.createElement("div");
    h.className = "detail-title";
    h.textContent = paths.length ? `Working changes · ${paths.length}` : "Working changes";
    wrap.appendChild(h);

    if (!paths.length) {
      wrap.appendChild(statusLine("Clean — everything is recorded.", ""));
      inspector.appendChild(wrap);
      return;
    }

    const sub = document.createElement("div");
    sub.className = "detail-why";
    sub.textContent = "Edits not yet recorded as ops. Save to checkpoint them into the ideal.";
    wrap.appendChild(sub);

    const list = document.createElement("div");
    list.className = "changes-list";
    const CAP = 12;
    for (const p of paths.slice(0, CAP)) {
      const row = document.createElement("div");
      row.className = "changes-file";
      row.textContent = p;
      row.title = p;
      list.appendChild(row);
    }
    if (paths.length > CAP) {
      const more = document.createElement("div");
      more.className = "changes-more";
      more.textContent = `+${paths.length - CAP} more`;
      list.appendChild(more);
    }
    wrap.appendChild(list);

    const bar = document.createElement("div");
    bar.className = "action-bar";
    const save = document.createElement("button");
    save.className = "action primary";
    save.textContent = "Save ⏎";
    save.title = "sgt save — record these changes as ops";
    save.addEventListener("click", () => vscode.postMessage({ type: "dailyLoop", verb: "save" }));
    bar.appendChild(save);
    const commit = document.createElement("button");
    commit.className = "action";
    commit.textContent = "Commit";
    commit.title = "sgt commit — land the recorded ideal as a git commit";
    commit.addEventListener("click", () => vscode.postMessage({ type: "dailyLoop", verb: "commit" }));
    bar.appendChild(commit);
    wrap.appendChild(bar);

    inspector.appendChild(wrap);
  }

  // A tight plan-step card: the rail encodings (open mark, landing slide, comet) already carry
  // the primary read, so this is the on-demand secondary -- one-line rationale, footprint as chips
  // (not a bulleted essay), predicted->matched shown as colored identity dots (arrow only when
  // they differ). No fold ref exists for a step id, so this replaces the action bar/code panel
  // rather than feeding them.
  function renderPlanCard(step) {
    const wrap = document.createElement("div");

    const h = document.createElement("div");
    h.className = "detail-title";
    h.textContent = `${step.matched ? "●" : "○"} ${step.label}`;
    wrap.appendChild(h);

    if (step.rationale) {
      const why = document.createElement("div");
      why.className = "detail-why";
      why.textContent = step.rationale;
      wrap.appendChild(why);
    }

    if (step.footprint && step.footprint.length) {
      const chips = document.createElement("div");
      chips.className = "footprint-chips";
      for (const sym of step.footprint) {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = sym;
        chips.appendChild(chip);
      }
      wrap.appendChild(chips);
    }

    const target = step.matched ? step.matchedFeature : step.predictedFeature;
    if (target) wrap.appendChild(renderPlanTargetLine(step, target));

    const firstSpan = step.matched && step.files && step.files[0] && step.files[0].spans && step.files[0].spans[0];
    // Mutually exclusive by construction: `checkpointMatch` is only ever set on an unmatched step
    // (see collectPlanMarks), `firstSpan` only on a matched one -- never both in the same card.
    if (firstSpan || step.checkpointMatch) {
      const bar = document.createElement("div");
      bar.className = "action-bar";
      if (firstSpan) {
        const btn = document.createElement("button");
        btn.className = "action";
        btn.textContent = "Show diff";
        btn.addEventListener("click", () => {
          const file = step.files[0];
          vscode.postMessage({
            type: "openPlanDiff",
            target: {
              kind: "match", title: step.label, rationale: step.rationale,
              predictedFootprint: step.footprint, path: file.path,
              startLine: firstSpan.start_line, endLine: firstSpan.end_line,
            },
          });
        });
        bar.appendChild(btn);
      }
      if (step.checkpointMatch) {
        // A footprint-overlap candidate `sgt checkpoint` already found between this pending step
        // and an unpredicted op -- confirming it is what actually flips the step to "matched" and
        // lands the rail's open-ring-to-solid-glyph transition on the next render.
        const group = step.checkpointMatch;
        const btn = document.createElement("button");
        btn.className = "action";
        btn.textContent = `Confirm match (${group.op_ids.length} edit(s))`;
        btn.addEventListener("click", () => {
          vscode.postMessage({ type: "confirmCheckpoint", hollowIds: group.hollow_ids, opIds: group.op_ids });
        });
        bar.appendChild(btn);
      }
      wrap.appendChild(bar);
    }

    return wrap;
  }

  // Predicted -> matched as colored identity dots (the same hue the dot/comet already use) rather
  // than a text diff -- an arrow only appears when the two actually differ. This is the *durable*
  // divergence read: the rail's comet-trail is transient (fades in ~1s), this card stays as long
  // as the step is selected.
  function renderPlanTargetLine(step, target) {
    const row = document.createElement("div");
    row.className = "plan-target-line";
    const swatch = (featureId, faint) => {
      const featureNode = byId(featureId);
      const s = document.createElement("span");
      s.className = "plan-target" + (faint ? " faint" : "");
      const dot = document.createElement("span");
      dot.className = "plan-target-dot";
      dot.style.background = (featureNode && featureNode.color) || "var(--dim)";
      s.appendChild(dot);
      const lbl = document.createElement("span");
      lbl.textContent = (featureNode && featureNode.label) || featureId;
      s.appendChild(lbl);
      return s;
    };
    if (step.matched && step.predictedFeature && step.predictedFeature !== step.matchedFeature) {
      row.appendChild(swatch(step.predictedFeature, true));
      const arrow = document.createElement("span");
      arrow.className = "plan-target-arrow";
      arrow.textContent = "→";
      row.appendChild(arrow);
    }
    row.appendChild(swatch(target, !step.matched));
    return row;
  }

  // The session-level card (titlebar chip click): plan text, overall progress, and any steps with
  // no predicted feature at all (nothing to attach to on the rail, so they're listed here instead
  // of inventing a fake row for them).
  function renderPlanSessionCard(session) {
    const wrap = document.createElement("div");

    const h = document.createElement("div");
    h.className = "detail-title";
    h.textContent = "Plan · " + session.planText;
    wrap.appendChild(h);

    const meta = document.createElement("div");
    meta.className = "detail-meta";
    meta.textContent = `${session.matchedCount}/${session.stepCount} step(s) landed`;
    wrap.appendChild(meta);

    // Stalled: the plan stopped part-way and no work is flowing toward it. State it plainly, then
    // offer the single clear next action -- hand the conversation back to the real Claude Code
    // session. This is the primary HIG affordance (what it is + one thing to do about it).
    if (session.derivedStatus === "stalled") {
      const pending = session.pendingCount != null
        ? session.pendingCount
        : session.stepCount - session.matchedCount;
      const interrupted = document.createElement("div");
      interrupted.className = "detail-why stalled";
      interrupted.textContent = `Interrupted — ${pending} step(s) not built`;
      wrap.appendChild(interrupted);

      const resume = document.createElement("button");
      resume.className = "action plan-resume-primary";
      resume.textContent = "▶ Resume in terminal";
      resume.title = "Relaunch this Claude Code session (claude --resume) to finish the plan";
      resume.addEventListener("click", () => {
        vscode.postMessage({ type: "resumePlan", sessionId: session.sessionId });
      });
      wrap.appendChild(resume);
    }

    const floatingSteps = planMarks.floating.filter((s) => s.sessionId === session.sessionId);
    if (floatingSteps.length) {
      const why = document.createElement("div");
      why.className = "detail-why";
      why.textContent = "No predicted feature -- unplaced:";
      wrap.appendChild(why);

      const bar = document.createElement("div");
      bar.className = "action-bar";
      for (const step of floatingSteps) {
        const btn = document.createElement("button");
        btn.className = "action";
        btn.textContent = `${step.matched ? "●" : "○"} ${step.label}`;
        btn.addEventListener("click", () => selectPlanStep(step.id));
        bar.appendChild(btn);
      }
      wrap.appendChild(bar);
    }

    return wrap;
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
    // A failing verdict tints the panel's own heading (a transition on the status channel this
    // surface already owns) rather than a separate line of text saying "oracle: fail".
    if (cached && !cached.error && oracleStatus(cached.oracle_verdict) === "fail") {
      heading.classList.add("oracle-fail");
    }
    renderCachedFrontierBody(section, cached, cached && cached.files);
    inspector.appendChild(section);
  }

  // The playhead's frontier is unfiltered (the host folds the whole thing, once per commit
  // index) -- filtering to the selected feature's `dir` happens here, client-side, so dragging
  // through a run of commit indices never re-requests per feature-selection change.
  // The "a history point is a SET of ops" encoding. A commit-index isn't a snapshot node -- it's
  // the set of ops mined at that index, spread across features. Rather than a wall of glyphs, show
  // the set as a proportional stacked bar (segment width = op count, fill = feature identity) plus
  // a per-feature breakdown with kind tallies. Area carries magnitude, so a 200-op point reads as
  // "mostly feature X" at a glance; clicking a feature drills in. Purely client-side over
  // `history.ops` -- the same op list the rail already draws, no host round-trip.
  function renderOpSetDecomposition(container, idx) {
    const ops = (history.ops || []).filter((o) => o.commit_index === idx);
    if (!ops.length) return;
    const byFeat = new Map();
    for (const op of ops) {
      const fid = op.feature_id || "—"; // unattributed ops still count toward the point's size
      let g = byFeat.get(fid);
      if (!g) byFeat.set(fid, (g = { count: 0, kinds: {} }));
      g.count++;
      g.kinds[op.kind] = (g.kinds[op.kind] || 0) + 1;
    }
    const groups = [...byFeat.entries()]
      .map(([fid, g]) => ({ fid, count: g.count, kinds: g.kinds, node: byId(fid) }))
      .sort((a, b) => b.count - a.count);
    const total = ops.length;

    const wrap = document.createElement("div");
    wrap.className = "opset";
    const h = document.createElement("div");
    h.className = "opset-heading";
    h.textContent = `${total} op${total === 1 ? "" : "s"} · ${groups.length} feature${groups.length === 1 ? "" : "s"} at this point`;
    wrap.appendChild(h);

    const bar = document.createElement("div");
    bar.className = "opset-bar";
    for (const g of groups) {
      const seg = document.createElement("div");
      seg.className = "opset-seg";
      seg.style.width = `${((g.count / total) * 100).toFixed(2)}%`;
      seg.style.background = (g.node && g.node.color) || "#888";
      seg.title = `${(g.node && g.node.label) || g.fid} · ${g.count}`;
      if (g.node) seg.addEventListener("click", () => selectRow(g.fid));
      bar.appendChild(seg);
    }
    wrap.appendChild(bar);

    for (const g of groups) {
      const row = document.createElement("div");
      row.className = "opset-row";
      if (g.node) row.addEventListener("click", () => selectRow(g.fid));
      const dot = document.createElement("span");
      dot.className = "opset-dot";
      dot.style.background = (g.node && g.node.color) || "#888";
      row.appendChild(dot);
      const label = document.createElement("span");
      label.className = "opset-label";
      label.textContent = (g.node && g.node.label) || g.fid;
      row.appendChild(label);
      const kinds = document.createElement("span");
      kinds.className = "opset-kinds";
      kinds.textContent = Object.keys(g.kinds).map((k) => (GLYPH[k] || "·").repeat(g.kinds[k])).join(" ");
      row.appendChild(kinds);
      const n = document.createElement("span");
      n.className = "opset-count";
      n.textContent = String(g.count);
      row.appendChild(n);
      wrap.appendChild(row);
    }
    container.appendChild(wrap);
  }

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

    // The op-set at this point comes first -- "what IS this history point" before "what does the
    // tree look like folded here." Not `dir`-filtered: the decomposition is the whole point's set.
    renderOpSetDecomposition(section, idx);

    if (cached && !cached.error && oracleStatus(cached.oracle_verdict) === "fail") {
      heading.classList.add("oracle-fail");
    }

    const allPaths = (cached && cached.files) || {};
    renderCachedFrontierBody(section, cached, filesForFeature(allPaths, featureNode));
    inspector.appendChild(section);
  }

  // code(I) file filter: keep a fold path if it's one of the feature's member files
  // (MapNode.members, `file::qualname`) OR sits under its majority-prefix dir. Dir alone drops a
  // feature's own production file when its members span dirs (e.g. a test-labeled leaf that also
  // owns `livehub/conflict.py`), which showed as an empty "No files under this path" panel.
  function filesForFeature(allPaths, featureNode) {
    if (!featureNode) return allPaths;
    const memberFiles = new Set((featureNode.members || []).map((m) => m.split("::")[0]));
    return Object.fromEntries(Object.entries(allPaths).filter(
      ([p]) => memberFiles.has(p) || p.startsWith(featureNode.dir)));
  }

  // Composition-picker hover-preview panel: same shape as the playhead panel (unfiltered fold,
  // filtered to the selected feature's files client-side), fed by `compositionPreviewResult`
  // instead of `playheadResult`. Lets arrowing through the composition QuickPick show what each
  // candidate would put in the code(I) panel before committing to a real `sgt switch`.
  function renderCompositionPreviewPanel(featureNode) {
    const section = document.createElement("div");
    section.className = "code-panel";

    const heading = document.createElement("div");
    heading.className = "code-panel-heading";
    const cached = compositionPreviewCache[compositionPreviewActive];
    heading.textContent = `code(I) @ ${compositionPreviewActive} (previewing)`;
    section.appendChild(heading);

    if (cached && !cached.error && oracleStatus(cached.oracle_verdict) === "fail") {
      heading.classList.add("oracle-fail");
    }

    const allPaths = (cached && cached.files) || {};
    renderCachedFrontierBody(section, cached, filesForFeature(allPaths, featureNode));
    inspector.appendChild(section);
  }

  function statusLine(text, kind) {
    const el = document.createElement("div");
    el.className = "code-panel-status" + (kind ? ` ${kind}` : "");
    el.textContent = text;
    return el;
  }

  // The selected feature's checkpoints: its history as a short list of labeled chapters, newest
  // last, each one a rewind target. A dimmed dot marks a low-novelty chapter (mostly tweaks) --
  // a low-value place to rewind to. Clicking a chapter rewinds it (`sgt revert <feature>@<n>`);
  // hovering it previews the ops it covers on the timeline. This is the answer to "which version
  // of this feature do I go back to" that a flat 100-op feature never gave.
  function renderCheckpoints(id) {
    const segs = checkpointsByFeature[id] || [];
    const wrap = document.createElement("div");
    wrap.className = "checkpoints";
    if (!segs.length) return wrap;

    const head = document.createElement("div");
    head.className = "checkpoints-head";
    const built = segs.some((s) => s.source === "llm");
    head.textContent = `Checkpoints · ${segs.length}` + (built ? "" : "  (run sgt intent build to name)");
    wrap.appendChild(head);

    for (const seg of segs) {
      const row = document.createElement("div");
      row.className = "checkpoint" + (seg.novelty <= 0.2 ? " trivial" : "") +
        (seg.checkpoint === state.selectedCheckpoint ? " selected" : "");
      row.dataset.checkpoint = seg.checkpoint;
      row.title = `${seg.rationale} · ${seg.tier}\nRewind: sgt revert ${seg.checkpoint}`;
      row.addEventListener("click", () => highlightCheckpoint(seg.checkpoint)); // sync with the gantt car

      const dot = document.createElement("span");
      dot.className = "checkpoint-dot";
      dot.textContent = seg.novelty > 0.6 ? "●" : seg.novelty > 0.2 ? "◐" : "○";
      row.appendChild(dot);

      const label = document.createElement("span");
      label.className = "checkpoint-label";
      label.textContent = seg.intent;
      row.appendChild(label);

      const rewind = document.createElement("button");
      rewind.className = "checkpoint-rewind";
      rewind.textContent = "⤺";
      rewind.title = `Rewind "${seg.intent}"`;
      rewind.addEventListener("click", (e) => {
        e.stopPropagation();
        vscode.postMessage({ type: "revertCheckpoint", ref: seg.checkpoint, label: seg.intent });
      });
      row.appendChild(rewind);

      // Hover a checkpoint -> preview the exact ops it covers as a revert blast on the timeline,
      // reusing the same closure-paint path every other revert-hover uses.
      row.addEventListener("mouseenter", () => previewAndBlast("revert", [seg.checkpoint]));
      row.addEventListener("mouseleave", () => clearGhosts());
      wrap.appendChild(row);
    }
    return wrap;
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
    bar.appendChild(btn("Restore", "restore"));
    return bar;
  }

  function previewAction(verb, id) {
    if (verb === "rename" || verb === "merge" || verb === "move") return; // needs a target/label first
    if (verb === "revert" || verb === "restore") previewAndBlast(verb, [id]);
    // Split has no `sgt preview split` branch server-side by design (`sgt split <feature>` with
    // no `--apply` already *is* that preview -- a second path would duplicate it), so this can't
    // go through the generic `previewVerb` round-trip the other verbs share.
    if (verb === "split") previewSplit(id);
  }

  function previewSplit(id) {
    const seq = ++previewSeq;
    vscode.postMessage({ type: "previewSplit", featureId: id, seq });
    pendingPreview = {
      seq,
      onResult: (res) => {
        // A split has no "affected other features" (unlike merge/move/revert) -- it only ever
        // touches the row being split, into `res.groups.length` pieces -- so the same amber
        // blast-radius treatment paints just this one row rather than a set of other rows.
        if (res && res.ok && Array.isArray(res.groups) && res.groups.length > 1) paintBlast([id]);
      },
    };
  }

  function paintBlast(featureIds) {
    rail.querySelectorAll(".glane.ghost-blast, .rail-row.ghost-blast").forEach((el) => el.classList.remove("ghost-blast"));
    rail.querySelectorAll(".glane, .rail-row").forEach((el) => {
      if (featureIds.includes(el.getAttribute("data-id"))) el.classList.add("ghost-blast");
    });
    renderOffscreenPills(featureIds);
  }

  // Paint a revert closure (classifyAffected) with the three distinct roles, so a hover reads as
  // "this one (target), these lose ops (blast), these get re-drafted (foundation)" -- not one
  // undifferentiated amber blob. Off-screen pills cover every role so nothing affected hides
  // outside the scroll window.
  function paintClosure(closure) {
    rail.querySelectorAll(".glane, .rail-row").forEach((el) => {
      el.classList.remove("ghost-target", "ghost-blast", "ghost-foundation");
      const id = el.getAttribute("data-id");
      if (id === closure.target) el.classList.add("ghost-target");
      else if (closure.blast.includes(id)) el.classList.add("ghost-blast");
      else if (closure.foundation.includes(id)) el.classList.add("ghost-foundation");
    });
    renderOffscreenPills([closure.target, ...closure.blast, ...closure.foundation]);
  }

  // The richer, held cousin of paintClosure: drive the "Focus & Morph" overlay from the backend
  // `focus` subgraph (sgt.api.focus_subgraph). Deep-dim the whole field, re-light only the affected
  // lanes with their role, and stamp each one's op-count with the "N -> M" delta -- so a revert
  // reads as "these features shrink, everything else is context" instead of an op-id wall. The morph
  // (a fully-emptied lane fading to a dashed ghost, a gaining lane landing) is CSS; this only sets
  // the classes. Torn down by clearGhosts (the same mouseleave path the ghosts use).
  function enterPreviewMode(focus, targetId) {
    const svg = rail.querySelector("svg");
    if (!svg) return;
    exitPreviewMode();
    // The shallow co-change hover dim (.focus) would fight the deep field dim -- drop it first.
    svg.classList.remove("focus");
    svg.querySelectorAll(".lit, .ctx").forEach((el) => el.classList.remove("lit", "ctx"));
    svg.classList.add("preview");
    previewActive = true;
    const lit = [];
    for (const n of focus.nodes) {
      const row = findRow(n.feature_id);
      if (!row) continue;
      lit.push(n.feature_id);
      row.classList.add("preview-lit");
      if (n.role === "target") {
        row.classList.add("preview-target");
      } else if (n.role === "foundation") {
        row.classList.add("preview-foundation");
        if (n.ops_after > n.ops_before) row.classList.add("preview-arriving"); // genuinely gains
      } else {
        row.classList.add("preview-blast");
        if (n.ops_after === 0) row.classList.add("preview-leaving"); // fully emptied -> ghost
      }
      // The "N -> M" delta lives in the lane's own count label (kept when motion is off).
      if (n.ops_before !== n.ops_after) {
        const count = row.querySelector(".gbar-count");
        if (count) {
          if (!count.hasAttribute("data-orig")) count.setAttribute("data-orig", count.textContent);
          count.textContent = `${n.ops_before} → ${n.ops_after}`;
          count.classList.add("preview-delta", n.ops_after < n.ops_before ? "losing" : "gaining");
        }
      }
    }
    renderOffscreenPills(lit);
    if (focus.context_count > 0) {
      previewContext.hidden = false;
      previewContext.textContent = `＋${focus.context_count} unchanged`;
    }
  }

  function exitPreviewMode() {
    if (!previewActive) return;
    previewActive = false;
    const svg = rail.querySelector("svg");
    if (svg) svg.classList.remove("preview");
    rail.querySelectorAll(".preview-lit").forEach((el) =>
      el.classList.remove(
        "preview-lit", "preview-target", "preview-blast", "preview-foundation",
        "preview-leaving", "preview-arriving",
      ));
    rail.querySelectorAll(".gbar-count.preview-delta").forEach((c) => {
      if (c.hasAttribute("data-orig")) {
        c.textContent = c.getAttribute("data-orig");
        c.removeAttribute("data-orig");
      }
      c.classList.remove("preview-delta", "losing", "gaining");
    });
    previewContext.hidden = true;
    clearOffscreenPills();
  }

  // A blocked-restore overlay: sgt refuses to restore a symbol that has a competing live version
  // (`ok:false, forked:true`). Show the two ways out as plain text -- swap (revert the live tip, then
  // restore) and reconcile (`sgt resolve <symbol>`) -- mirroring the CLI's own refusal, rather than
  // swallowing the preview. The symbol is the preview's `file::symbol` target, else its first
  // affected symbol. Hover-scoped; torn down by clearGhosts on the same mouseleave path as the ghosts.
  function showRestoreRefusal(res, fallbackId) {
    const sym = res.target && String(res.target).includes("::") ? res.target
      : (res.affected_symbols && res.affected_symbols[0]) || res.target || fallbackId;
    previewRefusal.innerHTML = "";
    const head = document.createElement("div");
    head.className = "refusal-head";
    head.textContent = res.message || "Can't restore — this symbol has a competing live version.";
    previewRefusal.appendChild(head);
    const swap = document.createElement("div");
    swap.className = "refusal-remedy";
    swap.textContent = "swap · revert the live tip, then restore";
    previewRefusal.appendChild(swap);
    const rec = document.createElement("div");
    rec.className = "refusal-remedy";
    rec.textContent = `reconcile · sgt resolve ${sym}`;
    previewRefusal.appendChild(rec);
    previewRefusal.hidden = false;
  }

  function clearPreviewRefusal() {
    previewRefusal.hidden = true;
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
    rail.querySelectorAll(".glane, .rail-row").forEach((el) => {
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
    if (verb === "split" || verb === "revert" || verb === "restore") {
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

  // The oracle chip is a live control, not a label: a plain click runs the configured tiers
  // (unconfigured/pending -> running -> pass/fail is a CSS color/border transition, not a text
  // swap); alt-click records a human override for cases the tiers can't decide. Both post through
  // the host so the CLI does the real work -- this never fakes a verdict client-side.
  oracleChip.addEventListener("click", (ev) => {
    if (ev.altKey) {
      vscode.postMessage({ type: "overrideOracle" });
      return;
    }
    oracleChip.dataset.state = "pending";
    oracleChip.querySelector(".oracle-label").textContent = "oracle · running…";
    vscode.postMessage({ type: "runOracle" });
  });

  // Segmented Timeline│Rail control: each segment sets state.view directly (active segment is filled
  // via .active in renderTitlebar).
  for (const btn of viewSeg.querySelectorAll(".seg-btn")) {
    btn.addEventListener("click", () => {
      if (state.view === btn.dataset.view) return;
      state.view = btn.dataset.view;
      saveState();
      render();
    });
  }

  // Consolidated plans chip toggles the per-session popover; dismiss on any outside click.
  plansChip.addEventListener("click", (ev) => {
    ev.stopPropagation();
    plansPopover.hidden = !plansPopover.hidden;
    if (!plansPopover.hidden) renderPlansPopover();
  });
  document.addEventListener("click", (ev) => {
    if (!plansPopover.hidden && !plansPopover.contains(ev.target) && ev.target !== plansChip) {
      plansPopover.hidden = true;
    }
  });

  // Minimize/restore the detail pane -- hands the full width to the timeline when docked narrow.
  inspectorToggle.addEventListener("click", () => {
    state.inspectorCollapsed = !state.inspectorCollapsed;
    saveState();
    render(); // re-measures the rail against the now-full width via the ResizeObserver too
  });

  const saveBtn = document.getElementById("saveBtn");
  const commitBtn = document.getElementById("commitBtn");
  const undoBtn = document.getElementById("undoBtn");
  saveBtn.addEventListener("click", () => vscode.postMessage({ type: "dailyLoop", verb: "save" }));
  commitBtn.addEventListener("click", () => vscode.postMessage({ type: "dailyLoop", verb: "commit" }));
  undoBtn.addEventListener("click", () => vscode.postMessage({ type: "dailyLoop", verb: "undo" }));

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (msg.type === "state") {
      compose = msg.compose || compose;
      history = compose.history || { commits: [], ops: [] };
      grid = compose.grid || { commits: [], cells: [] };
      map = compose.map || { nodes: [], roots: [], edges: [] };
      nodeIndex = new Map((map.nodes || []).map((n) => [n.id, n])); // keep byId's index in sync
      planMarks = collectPlanMarks(compose.plan, history);
      driftMarks = collectDriftMarks(compose.drift, history);
      forkMarks = collectForkMarks(compose.forks, map.nodes);
      savePreviewMarks = collectSavePreview(compose.save_preview);
      checkpointsByFeature = collectCheckpoints(compose.intent);
      foldResultCache = {};
      playheadResultCache = {};
      playheadCommitIndex = null; // a new composition means a different commit-index axis
      applyClusterDefaultOnce();
      // Prune a multi-select to lanes that still exist (a revert-all may have removed some), so a
      // stale id can't linger in the selection card after the composition changes under it.
      recompute();
      if ((state.multi || []).length) {
        state.multi = state.multi.filter((fid) => byId(fid));
        if (state.selected && !byId(state.selected)) state.selected = null;
        if (state.multi.length < 2) selectionResult = null;
      }
      render();
      if (pendingReveal) revealFeature(pendingReveal); // deliver a reveal that arrived before its lane existed
    } else if (msg.type === "selectionResult" && pendingSelection && pendingSelection.seq === msg.seq) {
      selectionResult = { refs: pendingSelection.refs, view: msg.result };
      pendingSelection = null;
      renderInspector();
      renderPresence();
      paintSelectionClosure();
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
    } else if (msg.type === "compositionPreviewStart") {
      latestCompositionPreviewSeq = msg.seq;
      compositionPreviewActive = msg.ref;
      renderInspector(); // "Loading…" for this candidate composition
    } else if (msg.type === "compositionPreviewResult" && msg.seq === latestCompositionPreviewSeq) {
      compositionPreviewCache[msg.ref] = { files: msg.files, oracle_verdict: msg.oracle_verdict, forked: msg.forked, error: msg.error };
      if (compositionPreviewActive === msg.ref) renderInspector();
    } else if (msg.type === "compositionPreviewEnd") {
      compositionPreviewActive = null;
      renderInspector();
    } else if (msg.type === "revealFeature") {
      revealFeature(msg.featureId);
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

  // Continuous reflow: the workbench is a WebviewView the user resizes freely both ways. The Gantt
  // axis width comes from the pane width, and its bottom-pinned frontier scrubber comes from the
  // pane height (ganttGeom derives h from rail.clientHeight) -- so a vertical resize must re-layout
  // too, or the scrubber drifts off the fold. Debounced, and gated on a real delta in either axis so
  // the scrollbar appearing/disappearing mid-render can't feed a render back into itself.
  let resizeTimer = null;

  // Is what's on screen still drawn for the pane we have? The width half asks the DOM itself -- the
  // rendered SVG's own `width` is the geometry it was laid out against -- rather than trusting a
  // bookkeeping variable, so a draw that was skipped or that died halfway cannot leave the gate
  // believing the pane is already up to date. The height half compares the last measurement, since the
  // SVG's height is max(natural, pane) and so can't be read back as the pane's.
  function reflowIfStale() {
    if (!paneMeasurable()) return; // hidden pane: nothing to reconcile against yet
    const svg = rail.querySelector("svg.railsvg");
    const drawnW = svg ? Number(svg.getAttribute("width")) : -1;
    if (svg && Math.abs(drawnW - Math.max(rail.clientWidth, 320)) < 4 &&
        Math.abs(rail.clientHeight - lastRenderHeight) < 4) return;
    render();
  }

  function scheduleReflow() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(reflowIfStale, 80);
  }

  new ResizeObserver(scheduleReflow).observe(rail);
  window.addEventListener("resize", scheduleReflow);
  // The pane can be resized (or moved between the panel and the sidebar) while this webview is hidden,
  // and a hidden document doesn't observe its own layout -- so the show is the event that has to
  // reconcile. Without this the workbench comes back drawn for whatever size it was last visible at.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) scheduleReflow(); });

  vscode.postMessage({ type: "ready" });
  render();
})();
