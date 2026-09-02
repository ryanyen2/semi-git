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
  // yet -- dropped, so scrubbing left empties the timeline. Dropped too: a lane whose features hold
  // no symbols of their own, because a cluster whose ops touch only the residue/anchor sentinels is
  // not something anyone can act on -- `sgt show` answers it with "0 symbols in 0 files" and
  // reverting it removes nothing. The terminal has applied that filter for a while
  // (`sgt/tui/graph.py`, `_print_map_tree`) and this surface did not, which is one of the reasons
  // the workbench, the sidebar and `sgt log --map` listed different features for the same repo.
  const lanes = [];
  for (const v of visible) {
    // Husks leave the leaf SET, not just the listing: `(N)` on a folded row and a header's `N feat`
    // are leaf counts, so a husk counted inside a fold promises rows that opening it does not
    // deliver. A node carrying no `own_symbols` key at all is unknown, not empty -- treat it as
    // present, the same way the Python filter's `("?",)` default does.
    const leaves = v.leaves.filter((leaf) => {
      const own = (byId[leaf] || {}).own_symbols;
      return own == null || own.length > 0;
    });
    if (!leaves.length) continue;
    const commits = [];
    for (const leaf of leaves) for (const op of opsByFeature[leaf] || []) commits.push(op.commit_index);
    if (!commits.length) continue;
    commits.sort((a, b) => a - b);
    lanes.push({
      ...v, leaves, opCount: commits.length, firstCommit: commits[0], lastCommit: commits[commits.length - 1], commits,
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
  // One parent's rows in reading order: its own feature lanes first, then its sub-groups -- each
  // half in first-appearance order. Ordering a level by time ALONE interleaved the two kinds, and a
  // subsystem is not one row but a block: a feature born after a subsystem was emitted below that
  // subsystem's entire subtree, where the indent then read as membership. On seedbank-v3 that filed
  // four of the repo's own features (`seed catalog`, `sort the grid`, `show what is on the shelf`,
  // `seed tray`) under the `Plant Discovery` swimlane, whose header says "6 feat" above nine rows.
  // Grouping is what lets the indent mean containment; inside a group, time still orders.
  // Mirrors `ordered_children` in sgt/tui/graph.py -- the two layouts stay behaviour-parallel.
  //
  // The halves split by NODE KIND, not by "is it a lane": a collapsed subsystem is a lane (one
  // meta row) while an expanded one is a header, so splitting on lane-ness moved the whole block
  // from the leaves half to the groups half whenever its fold state changed -- expanding a
  // subsystem visibly teleported it below every flat feature and the reader lost the row they
  // had just clicked. Kind is fold-invariant: a subsystem holds one slot, folded or open.
  const isSubsystemNode = (c) => byId[c] && byId[c].kind === "subsystem";
  const orderedChildren = (ids) => {
    const present = ids.filter(isPresent);
    return present.filter((c) => !isSubsystemNode(c)).sort(sortByFirst)
      .concat(present.filter(isSubsystemNode).sort(sortByFirst));
  };

  let row = 0;
  const headers = [];
  const emitted = new Set();
  function emit(id, depth) {
    // The map is a DAG, so the same node can be reached down two paths. `visit` above gives it one
    // lane; give it one row too, or the second visit overwrites `row` and leaves a blank line where
    // the first one was.
    if (emitted.has(id)) return;
    emitted.add(id);
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
    for (const c of orderedChildren(childrenOf(id))) emit(c, depth + 1);
  }
  for (const r of orderedChildren(map.roots || [])) emit(r, 0);
  const rowCount = Math.max(1, row);

  return { lanes, headers, edges, overflow, laneById, opsByFeature, rowCount,
    commitCount: ((grid && grid.commits) || []).length };
}
// A post-layout VIEW transform, not a second layout: `computeGraphLayout` above is one half of a
// pair whose other half is `graph_layout` in sgt/tui/graph.py, and a rule changed in one and not the
// other drifts silently. So the flat view reuses that output and only reassigns rows.
//
// The alternate view, not the default. Flat rows are leaves ordered by most-recently-touched --
// "what was I just working on" -- at the cost of the hierarchy: every leaf is a sibling, so the
// grouping the tree exists to show disappears (the study's pilots met this list and read it as a
// commit log with different labels). It was briefly the default while subsystems had no identity
// hue and the folded tree opened grey; the host colors subsystems now, so the tree is back as the
// default and this stays as the toggle.
function flattenLayout(layout) {
  const lanes = layout.lanes
    .slice()
    .sort((a, b) => (b.lastCommit - a.lastCommit) || (a.label < b.label ? -1 : 1))
    .map((l, i) => ({ ...l, row: i, depth: 0 }));
  return { ...layout, lanes, headers: [], rowCount: Math.max(1, lanes.length) };
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

  const byFeature = {};
  for (const seg of segments || []) {
    (byFeature[seg.feature_id] || (byFeature[seg.feature_id] = [])).push(seg);
  }

  // The op -> commit join that feeds each chapter's per-commit bins, kept PER FEATURE rather than
  // as one index over the whole repository.
  //
  // It used to be a single object literal keyed by every op id in the history. An object keyed by
  // millions of distinct strings leaves V8's fast path and becomes a dictionary, and the cost is not
  // marginal -- measured on a 2.94M-cell history, building it took 1,734ms of a 3,533ms layout. A
  // `Map` alone brought that to 572ms; building one SMALL map per feature and discarding it brings
  // the whole join to 492ms, because each map fits in cache instead of rehashing a table the size of
  // the repository. The bins produced are identical, op id for op id: this is the same exact join,
  // not an approximation by commit range (chapters of one feature can share a commit, so a range
  // join would double-count).
  //
  // Cells are grouped unfiltered, deliberately: `computeGraphLayout` drops cells past the frontier
  // and this must not, or a chapter beyond the playhead would lose the density it is drawn with.
  const cellsByFeature = new Map();
  if (segments && segments.length) {
    for (const cell of (grid && grid.cells) || []) {
      let bucket = cellsByFeature.get(cell.feature_id);
      if (!bucket) cellsByFeature.set(cell.feature_id, (bucket = []));
      bucket.push(cell);
    }
  }

  const base = computeGraphLayout(map, grid, opts);

  const lanes = base.lanes.map((l) => {
    const cars = [];
    for (const leaf of l.leaves) {
      const segsHere = byFeature[leaf];
      if (!segsHere || !segsHere.length) continue;
      // One small index for this feature, built once and dropped when the feature is done.
      const commitIndexOf = new Map();
      for (const cell of cellsByFeature.get(leaf) || []) {
        for (const oid of cell.op_ids || []) commitIndexOf.set(oid, cell.commit_index);
      }
      for (const seg of segsHere) {
        const bins = new Map();
        for (const oid of seg.op_ids || []) {
          const ci = commitIndexOf.get(oid);
          if (ci != null) bins.set(ci, (bins.get(ci) || 0) + 1);
        }
        cars.push({
          featureId: leaf, segIndex: seg.seg_index, checkpoint: seg.checkpoint, label: seg.intent,
          opCount: seg.op_count, tier: seg.tier, source: seg.source,
          // Whether this chapter's ops are still in HEAD's ideal (`present_op_count`, from
          // `sgt.api._segments_out`). A revert leaves the chapter in the store and takes it out of
          // the ideal, so a client reading only the store cannot tell a rewound chapter from a live
          // one -- the inspector listed a reverted checkpoint with a working rewind button on it.
          // `null` (an unreadable ideal, or a payload written before the field existed) is no claim
          // and must not read as removed.
          presentOpCount: seg.present_op_count == null ? null : seg.present_op_count,
          reverted: seg.present_op_count === 0,
          firstIndex: seg.first_index, lastIndex: seg.last_index,
          subBins: [...bins.entries()].sort((a, b) => a[0] - b[0]),
          isFuture: seg.first_index > frontier,
          asks: seg.asks || [],  // the chapter's captured asks: excerpt + whose words (weave P4)
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

// ---- car-impact (test slice boundary) ----
// The chunk-grain half of a consequence preview. A verb preview carries the exact op ids it
// removes/adds; intent segments carry the op ids each chapter (car) owns. Joining the two says
// WHICH recorded chunks change and by how much -- so the graph draws each affected car as a
// dashed version of the state it will END in: a revert drains it toward the hollow look a
// reverted car already has, a restore fills a hollow car back toward solid. Dash = not yet true;
// the body of the car = what will be true. Dependencies included for free: a removed op in
// another feature's chapter marks THAT car, which is "the dependencies also come out", in situ.
// Pure (no DOM); the painter half applies classes.
function classifyCarImpact(removedIds, addedIds, segments, targetOpIds, verb) {
  const removed = new Set(removedIds || []);
  const added = new Set(addedIds || []);
  // The op-set the user actually NAMED (emit's `target_ops`) is forced into the direction the verb
  // moves it, because the mechanical edit can rewrite some of it in place instead of dropping or
  // adding ops -- `removed`/`added` alone can be a subset (or empty) there, and the asked-about
  // chapter must never preview as untouched.
  //
  // The direction is the verb's, and it used to be hardcoded to `removed`. On a RESTORE that put
  // the op-set being brought back into the leaving set, so the chapter the user asked to restore
  // previewed as draining to hollow -- which is exactly what a revert of it would look like. Both
  // verbs drew the same picture on the one car the user was looking at, and the picture was the
  // wrong one. (`gcar-preview-in` and `-out` have always been distinct: 0.55 fill with a pulse
  // versus 0.12 and dashed. Nothing was wrong with the paint; the classification never asked which
  // way the edit went.)
  for (const id of targetOpIds || []) (verb === "restore" ? added : removed).add(id);
  if (!removed.size && !added.size) return [];
  const out = [];
  for (const seg of segments || []) {
    const ids = seg.op_ids || [];
    if (!ids.length) continue;
    let nOut = 0, nIn = 0;
    for (const id of ids) {
      if (removed.has(id)) nOut++;
      else if (added.has(id)) nIn++;
    }
    if (!nOut && !nIn) continue;
    const dir = nIn > nOut ? "in" : "out"; // a keep-revert can touch both; the dominant move wins
    const touched = Math.max(nOut, nIn);
    out.push({
      checkpoint: seg.checkpoint, featureId: seg.feature_id, dir, touched,
      coverage: touched >= ids.length ? "full" : "partial",
    });
  }
  return out;
}
// ---- end-car-impact


// ---- change-tree (test slice boundary) ----
// What a feature -- or one of its chapters -- CHANGED, as a tree the reader can scan and click
// into. The inspector used to print each of the selected feature's files in full, which answers
// "what does this code say" and never "what did this do": the reader was left to diff two
// screenfuls of mostly-unchanged text by eye, in a 12px pre with no syntax highlighting, in the
// one panel that exists to explain a selection.
//
// The input is `sgt <verb> <ref> --emit --json`'s `files`: per changed path a `before`/`after`
// pair plus each side's entity spans (`sgt.api._side_entity_spans`). That projection *is* a
// feature's change in this model -- the difference between the tree holding its ops and the tree
// without them -- so the tree shows the closure too, not only the lane's own lines. Which stored
// side holds the work is a property of the verb, never of the field name: `before` is always the
// current ideal, so reverting a live chapter puts its work in `before` and restoring a retired one
// puts it in `after`. Read the wrong way round, every feature renders as a pure deletion.
//
// Pure (no DOM, no vscode API), so the node harness holds the shape.

// A file's lines, without the phantom last element `"a\n".split("\n")` produces. A trailing
// newline is not a line, and counting it as one reports a +1 on every file that gains content at
// its end.
function changeLines(text) {
  const lines = String(text == null ? "" : text).split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

// Myers' O(ND) line diff. Returns the 1-based line numbers each side holds ALONE -- enough both to
// count and to place a change inside an entity's span, which is all the tree needs.
//
// `maxD` is not a quality knob. A pathological pair (a generated file, a wholesale rewrite) walks
// a trace that costs O(D^2) memory, and this runs inside a webview that has to stay responsive, so
// past the cap we stop aligning and report the untrimmed middles wholesale. That is honest -- all
// of it did change -- and it is flagged, so the caller can say so rather than quietly reporting a
// smaller number than the truth.
function lineDiff(a, b, maxD) {
  maxD = maxD || 800;
  const n0 = a.length, m0 = b.length;
  let lo = 0;
  while (lo < n0 && lo < m0 && a[lo] === b[lo]) lo++;
  let hi = 0;
  while (hi < n0 - lo && hi < m0 - lo && a[n0 - 1 - hi] === b[m0 - 1 - hi]) hi++;
  const x0 = a.slice(lo, n0 - hi), y0 = b.slice(lo, m0 - hi);
  const n = x0.length, m = y0.length;
  const span = (start, count) => {
    const out = [];
    for (let i = 0; i < count; i++) out.push(start + i);
    return out;
  };
  if (!n && !m) return { aOnly: [], bOnly: [], capped: false };
  if (!n || !m) return { aOnly: span(lo + 1, n), bOnly: span(lo + 1, m), capped: false };

  const max = Math.min(n + m, maxD);
  const off = max + 1;
  const v = new Int32Array(2 * max + 3);
  const trace = [];
  for (let d = 0; d <= max; d++) {
    // Only the band [-d, d] is live at step d, so the trace stores that band and nothing else: a
    // whole-array copy per step costs O(D * (N+M)) and blows up on exactly the files that most
    // need the cap. Every k in the band is copied, not every other one: the backtrack reads the
    // k-lines of the PREVIOUS step, which are the opposite parity to the ones step d writes, and
    // sampling only d's own parity hands it stale values from step d-2 -- an alignment that walked
    // off the front of the file and reported line numbers below 1.
    const band = new Int32Array(2 * d + 1);
    for (let k = -d; k <= d; k++) band[k + d] = v[off + k];
    trace.push(band);
    for (let k = -d; k <= d; k += 2) {
      let x = k === -d || (k !== d && v[off + k - 1] < v[off + k + 1]) ? v[off + k + 1] : v[off + k - 1] + 1;
      let y = x - k;
      while (x < n && y < m && x0[x] === y0[y]) { x++; y++; }
      v[off + k] = x;
      if (x >= n && y >= m) return backtrackDiff(trace, d, x0, y0, lo);
    }
  }
  return { aOnly: span(lo + 1, n), bOnly: span(lo + 1, m), capped: true };
}

// Walk the recorded d-bands back from the end point, collecting the one line each step consumes.
// The snake between two steps is common text and contributes nothing.
function backtrackDiff(trace, d, x0, y0, lo) {
  const aOnly = [], bOnly = [];
  let x = x0.length, y = y0.length;
  for (; d > 0; d--) {
    const band = trace[d];
    const at = (k) => band[k + d];
    const k = x - y;
    const prevK = k === -d || (k !== d && at(k - 1) < at(k + 1)) ? k + 1 : k - 1;
    const prevX = at(prevK), prevY = prevX - prevK;
    while (x > prevX && y > prevY) { x--; y--; }
    if (x > prevX) aOnly.push(lo + x); // a line only `a` has: 1-based, undoing the prefix trim
    else bOnly.push(lo + y);
    x = prevX; y = prevY;
  }
  aOnly.reverse();
  bOnly.reverse();
  return { aOnly, bOnly, capped: false };
}

// Line -> owning entity, innermost first. Entities nest (a method sits inside its class), and the
// useful answer for a changed line is the smallest thing that contains it, so wider spans are
// painted first and narrower ones overwrite them. Lines outside every span -- imports, module-level
// statements, the gaps between definitions -- own nothing and are reported at file level.
function spanOwners(spans, lineCount) {
  const owner = new Array(lineCount + 1).fill(null);
  const ordered = (spans || []).slice().sort(
    (p, q) => (q.end_line - q.start_line) - (p.end_line - p.start_line));
  for (const s of ordered) {
    for (let ln = Math.max(1, s.start_line); ln <= Math.min(lineCount, s.end_line); ln++) {
      owner[ln] = s;
    }
  }
  return owner;
}

// How an entity reads on a row. An import is a real symbol here -- the extractor mints one per
// specifier and a revert can take one out on its own -- but its stored name is the `__import__::`
// marker `_symbol_kind` needs, which is machinery, not a name. Read off `kind` rather than
// sniffing the string: that is what the field is for, and `_readable_symbols` already showed what
// happens when a layout marker reaches a person's eyes verbatim.
function entityName(span) {
  if (!span) return "(top level)";
  const name = span.symbol.split("::").slice(1).join("::");
  return span.kind === "import" ? "import " + name.replace(/^__import__::/, "") : name;
}

// One changed path's per-entity tally. `added`/`removed` are relative to the WORK, not to a stored
// field: a line only the with-side has is a line this work wrote.
function fileChange(path, pair, withSide, maxD) {
  const other = withSide === "before" ? "after" : "before";
  const withLines = changeLines(pair[withSide]);
  const otherLines = changeLines(pair[other]);
  const d = lineDiff(otherLines, withLines, maxD);
  const withOwner = spanOwners(pair[withSide + "_spans"], withLines.length);
  const otherOwner = spanOwners(pair[other + "_spans"], otherLines.length);

  const entities = new Map();
  const bump = (span, side, line, text) => {
    const key = span ? span.symbol : " top";
    let e = entities.get(key);
    if (!e) {
      entities.set(key, (e = {
        kind: "entity", path,
        symbol: span ? span.symbol : null,
        name: entityName(span),
        entityKind: span ? span.kind : null,
        order: span ? span.start_line : Infinity,
        added: 0, removed: 0, lines: [],
      }));
    }
    // Document order comes from the with-side where the entity is on it; an entity this work
    // deleted outright has no with-side span, so it keeps its other-side position and sorts among
    // the rest rather than being dumped at the end.
    if (span && e.order === Infinity) e.order = span.start_line;
    e[side]++;
    // The line itself, not only the tally. Counting was all this did, and a panel of counts answers
    // "how much" for a reader who asked "what" -- the whole reason they opened the checkpoint.
    // Carried here because both sides' text is already in hand and neither is fetched again.
    e.lines.push({ side: side === "added" ? "+" : "-", line, text });
  };
  for (const ln of d.bOnly) bump(withOwner[ln], "added", ln, withLines[ln - 1]);
  for (const ln of d.aOnly) bump(otherOwner[ln], "removed", ln, otherLines[ln - 1]);
  // Removed lines are collected on the other side's numbering, so a plain concat interleaves two
  // unrelated coordinate systems. Sort each entity's lines by position within its own side, with a
  // removal placed before the addition that replaces it -- which is how a diff reads.
  for (const e of entities.values()) {
    e.lines.sort((p, q) => p.line - q.line || (p.side === "-" ? -1 : 1));
  }

  const children = [...entities.values()].sort(
    (p, q) => p.order - q.order || String(p.name).localeCompare(String(q.name)));
  // "(top level)" is the residue bucket -- imports and module statements, not a thing anyone can
  // point at -- so it reads last however early in the file it happens to sit.
  children.sort((p, q) => (p.symbol ? 0 : 1) - (q.symbol ? 0 : 1));
  return {
    kind: "file", path, name: path.split("/").pop(), children,
    added: d.bOnly.length, removed: d.aOnly.length, capped: d.capped,
  };
}

// The whole projection as a file-explorer tree: directories, then files, then the entities inside
// them, every node carrying its subtree's totals. Directories with a single child directory fold
// into one row (`footfall/pages`), the way VS Code's explorer compacts them -- a panel this narrow
// cannot spend a row and an indent level on a name with nothing to choose between.
function changeTree(files, opts) {
  opts = opts || {};
  const withSide = opts.withSide === "after" ? "after" : "before";
  const root = { kind: "dir", name: "", path: "", children: [], added: 0, removed: 0 };
  let capped = false;
  let fileCount = 0;

  for (const path of Object.keys(files || {}).sort()) {
    const node = fileChange(path, files[path] || {}, withSide, opts.maxD);
    if (!node.added && !node.removed) continue; // a byte-identical pair is not a change
    capped = capped || node.capped;
    fileCount++;
    const parts = path.split("/");
    let dir = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const sub = parts.slice(0, i + 1).join("/");
      let next = dir.children.find((c) => c.kind === "dir" && c.path === sub);
      if (!next) {
        next = { kind: "dir", name: parts[i], path: sub, children: [], added: 0, removed: 0 };
        dir.children.push(next);
      }
      dir = next;
    }
    dir.children.push(node);
  }

  const roll = (node, isRoot) => {
    if (node.kind !== "dir") return;
    // Fold a lone-child directory into this row before rolling, so the compacted name is the one
    // the totals hang off. Never the root: it is the container the rows sit in, not a row, and
    // folding into it deletes the one directory name the reader was going to read.
    while (!isRoot && node.children.length === 1 && node.children[0].kind === "dir") {
      const only = node.children[0];
      node.name = node.name ? node.name + "/" + only.name : only.name;
      node.path = only.path;
      node.children = only.children;
    }
    node.children.sort((p, q) => (p.kind === q.kind ? 0 : p.kind === "dir" ? -1 : 1)
      || String(p.name).localeCompare(String(q.name)));
    node.added = 0;
    node.removed = 0;
    for (const c of node.children) {
      roll(c, false);
      node.added += c.added;
      node.removed += c.removed;
    }
  };
  roll(root, true);
  return { root, added: root.added, removed: root.removed, fileCount, capped };
}

// GitHub's five-block diffstat, in the terminal grammar the rest of this view already speaks: a
// fixed-width strip so the column scans, split by the change's own proportion, with `.` filling the
// remainder. Every non-zero side keeps at least one glyph -- a 200/1 change drawn as five plus
// signs would say the removal never happened, which is the one thing a reader most needs to see.
function changeMeter(added, removed, width) {
  width = width || 5;
  const total = added + removed;
  if (!total) return { plus: "", minus: "", rest: ".".repeat(width) };
  let a = added ? Math.max(1, Math.round((added / total) * width)) : 0;
  let r = removed ? Math.max(1, Math.round((removed / total) * width)) : 0;
  while (a + r > width) { if (a >= r) a--; else r--; }
  return { plus: "+".repeat(a), minus: "-".repeat(r), rest: ".".repeat(width - a - r) };
}
// ---- end-change-tree (test slice boundary) ----

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
  // "tree" (the subsystem hierarchy, folded to its top level) | "flat" (feature rows,
  // newest-touched first). Tree is the default again: the grey-wall objection that made flat the
  // default is gone (the host now gives subsystems an identity hue too, see workbench.ts), and a
  // flat list of leaves is how the study's pilots lost the hierarchy this surface exists to show.
  if (state.grouping !== "flat") state.grouping = "tree";
  if (state.themeFocus === undefined) state.themeFocus = null; // a cross-feature theme under TableLens focus
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
  let layout = groupedLayout(map, grid, compose);
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

  // The inspector's change panel: one `--emit` projection per selected scope (`<verb><ref>`, so a
  // chapter and its feature never share an answer), reset per composition like every other read.
  // The two fold sets are transient by design -- see `toggleChangeFold`.
  let changeSeq = 0;
  let pendingChange = null;
  let changeCache = {};
  const changeFolded = new Set();
  const changeUnfolded = new Set();

  // The words behind the selected chapter. Two levels, because they cost different things: the
  // excerpt is already in the payload for every chapter and is drawn for free, and the verbatim
  // prompts are fetched only when a reader opens one (`requestAsked` -> `sgt show --asked`). A
  // prompt is a paragraph; forty of them would be most of a panel refresh, for words nobody is
  // reading yet.
  //
  // `askedOpen` is the checkpoint whose full text is showing -- one at a time, since this is a
  // reading state and two open transcripts in a narrow panel is neither. `askedAnimated` records
  // which chapter's block has already played its enter, so a poll that re-renders the panel does
  // not replay the animation under the reader's eyes.
  let askedSeq = 0;
  let pendingAsked = null; // {seq, ref}
  let askedCache = {};     // checkpoint -> {asks} | {error}
  let askedOpen = null;    // checkpoint whose verbatim prompts are expanded
  let askedAnimated = null;

  // Multi-select union closure (Stage C): ⌘/ctrl/shift-click accretes a set of feature lanes; the
  // host resolves the union via `sgt select` and we show the closure count + paint it. Transient
  // (a selection is exploratory, not worth persisting): the set lives on state.multi, the resolved
  // closure here.
  let selectionSeq = 0;
  let pendingSelection = null;
  let selectionResult = null; // { refs, view } for the current state.multi, or null
  let pendingReveal = null; // an editor->graph reveal target awaiting the graph's next render (task 4)

  // The staged destructive action (the in-graph confirm): a Revert/Restore/Back-to-here click
  // holds its consequence preview on the field and raises the confirm bar; nothing runs until
  // Apply. While `applyBusy` is set the bar shows the host's real phases instead of the buttons.
  let stagedAction = null; // {verb, ref, targetId, label, kind: "feature"|"chapter"|"backto", refs?, res?}
  let applyBusy = null; // {verb, phase, detail} while the host applies a staged action
  let pendingSettle = []; // feature ids to flash once the post-apply state lands (the receipt)
  let semanticState = null; // the meaning rung of find: {query, pending, hits, mode, message}

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

  // Theme focus (the TableLens read): one cross-feature group emphasized. Resolved fresh from
  // compose.intent.themes each recompute -- the payload is the authority, state holds only the id.
  // While set, ganttGeom compresses every non-member lane to a thin density row and renderCars
  // outlines the member chapters, so "one piece of work across five features" is one visible band
  // instead of a footnote in `sgt intent list`.
  let themeMarks = null; // {id, label, tier, featureIds:Set, commitIdx:Set} | null
  function resolveThemeMarks() {
    if (!state.themeFocus) return null;
    const t = (((compose || {}).intent || {}).themes || []).find((x) => x.theme_id === state.themeFocus);
    if (!t) return null;
    const idxOf = new Map(((grid && grid.commits) || []).map((c) => [c.sha, c.index]));
    const commitIdx = new Set((t.atom_shas || []).map((sh) => idxOf.get(sh)).filter((i) => i != null));
    return { id: t.theme_id, label: t.label, tier: t.tier,
             featureIds: new Set(t.feature_span || []), commitIdx };
  }
  // The spanning work worth showing: multi-feature, and genuinely NAMED. The "(unwitnessed)"
  // rollup is the catch-all for commits outside any theme -- it spans most of the repo by
  // construction and reads as a work item called "(unwitnessed)", which is noise on every
  // surface that lists work by name.
  function spanningThemes() {
    return ((((compose || {}).intent) || {}).themes || [])
      .filter((t) => (t.feature_span || []).length > 1 && t.label && t.label !== "(unwitnessed)");
  }

  function laneInThemeFocus(l) {
    if (!themeMarks) return true;
    return themeMarks.featureIds.has(l.id) || (l.leaves || []).some((f) => themeMarks.featureIds.has(f));
  }

  // A save picked in find: its chapters flash across every lane it touched, then the flash clears.
  let saveFlashIdx = null;

  // Where each chapter was actually drawn this render (checkpoint -> {x, w, midY, laneId}).
  // The cross-feature spines anchor on these exact rectangles rather than re-deriving the
  // gap-fill tiling renderCars does -- one layout, two consumers.
  let carRects = new Map();

  const rail = document.getElementById("rail");
  const inspector = document.getElementById("inspector");
  const offscreenAbove = document.getElementById("offscreenAbove");
  const offscreenBelow = document.getElementById("offscreenBelow");
  const previewContext = document.getElementById("previewContext"); // "＋N unchanged" context tally
  const armedBanner = document.getElementById("armedBanner"); // the armed merge/move mode, stated
  const previewRefusal = document.getElementById("previewRefusal"); // blocked-restore remedies overlay
  const inspectorToggle = document.getElementById("inspectorToggle");
  const confirmBar = document.getElementById("confirmBar"); // staged-action consequence + Apply/Cancel

  const SVG_TAGS = new Set(["svg", "g", "path", "circle", "rect", "text", "line", "title", "tspan"]);

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
    themeMarks = resolveThemeMarks(); // before geometry: row heights depend on it
    layout = groupedLayout(map, grid, compose);
  }

  // The tree view (the default) opens folded to its subsystem rows: root(s) expanded to their
  // direct children, deeper subsystems collapsed, so the reader meets ~20 rows instead of every
  // leaf at once. Applied exactly once, and only after real nodes have arrived; any later
  // expand/collapse persists and is never overwritten.
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

  const groupBtn = document.getElementById("groupBtn");
  if (groupBtn) {
    groupBtn.addEventListener("click", () => {
      state.grouping = state.grouping === "tree" ? "flat" : "tree";
      saveState();
      // Grouping is a LAYOUT choice, so the layout has to be rebuilt -- `render()` alone redraws
      // the cached one, which flipped the button's label over an unchanged set of rows.
      applyClusterDefaultOnce();  // the tree view's first open is still folded to its subsystems
      recompute();
      render();
    });
  }

  // The reader's grouping choice, applied in one place so the two layout call sites cannot
  // disagree about which view is on screen. "flat" (the default) is feature rows newest-first;
  // "tree" is the subsystem hierarchy, folded.
  function groupedLayout(m, g, comp) {
    if (state.grouping === "tree") {
      return computeSegmentLayout(m, g, segmentsOf(comp), { collapsed: state.collapsed });
    }
    // Flat means FEATURES, so it folds nothing: a collapsed subsystem would come back as a
    // meta-lane, and a meta-lane is not a feature -- it has no identity hue, and one grey row in a
    // coloured list reads as a broken row rather than as a different kind of thing. The reader's
    // fold state is left untouched so switching back to the tree finds it as they left it.
    return flattenLayout(computeSegmentLayout(m, g, segmentsOf(comp), { collapsed: [] }));
  }

  // The Save/Undo pair, derived from whichever verb is running in the host. They had no in-flight
  // state at all, so the only feedback was the toast at the end -- and a reader who sees nothing
  // clicks again. Both go inert while either one runs, not just the one clicked: they mutate the
  // same ideal, so an undo fired mid-save is a race the reader did not mean to start. Only the
  // running one changes its label, because the other is unavailable rather than busy.
  //
  // This was a Save/Commit/Undo trio. `sgt save` mines and commits in one verb, so the middle
  // button's label promised a step the first had already taken, while the command it ran
  // (`advanced commit`) lands a staged rewrite candidate and otherwise refuses -- drawn permanently
  // for a state that is rare and gated. Landing moved into the Working-changes card, where the state
  // is visible and the oracle gate can be shown instead of arriving as a failure after the click.
  function loopButtonState(busy) {
    return [["save", "Save", "Saving…"], ["undo", "Undo", "Undoing…"]].map(([verb, idle, running]) => ({
      verb, label: busy === verb ? running : idle, disabled: busy !== null,
    }));
  }
  // ---- end-signals

  function renderTitlebar() {
    // The button is labelled with what it SHOWS, not with what clicking it does. A toggle labelled
    // with its action makes the reader work out which state they are currently in from the label of
    // the state they are not in.
    if (groupBtn) {
      groupBtn.textContent = state.grouping === "tree" ? "Subsystems" : "Features";
    }

    // ── Actions zone: inspector toggle (Save/Commit/Undo are wired once at init) ─────────────────
    inspectorToggle.textContent = state.inspectorCollapsed ? "◨" : "◧";
    inspectorToggle.title = state.inspectorCollapsed ? "Show detail panel" : "Hide detail panel";
    document.getElementById("app").classList.toggle("inspector-collapsed", !!state.inspectorCollapsed);
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
    renderThemeBanner();
  }

  // Enter/exit the theme focus (a toggle: focusing the focused theme clears it). The state holds
  // only the id; everything else re-derives from the payload on recompute.
  function setThemeFocus(themeId) {
    state.themeFocus = state.themeFocus === themeId ? null : themeId;
    state.spotlight = null; // one lens at a time -- a spotlight under a theme focus double-dims
    saveState();
    recompute();
    render();
  }

  // The theme-focus banner: what is focused, what the dimming means, and the two whole-group verbs.
  // Lives above the plot (like the armed banner) because the focus recolors the whole field -- a
  // mostly-thin map with no caption reads as a bug, not a lens.
  function renderThemeBanner() {
    const el = document.getElementById("themeBanner");
    if (!el) return;
    if (!themeMarks) { el.hidden = true; el.innerHTML = ""; return; }
    el.hidden = false;
    el.innerHTML = "";
    const name = document.createElement("span");
    name.className = "theme-banner-name";
    name.textContent = `◈ ${themeMarks.label}`;
    const note = document.createElement("span");
    note.className = "theme-banner-note";
    note.textContent = `one piece of work across ${themeMarks.featureIds.size} features — its checkpoints are ringed, other lanes compressed`;
    el.append(name, note);
    const mkBtn = (label, title, fn) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.title = title;
      b.addEventListener("click", fn);
      el.appendChild(b);
      return b;
    };
    // The whole-group verbs take the theme's LABEL -- the same string `sgt revert "<name>"`
    // resolves -- through the ordinary staged-confirm path, so the consequence is previewed and
    // nothing runs until Apply.
    const anchor = [...themeMarks.featureIds][0] || null;
    mkBtn("Revert this work", `sgt revert "${themeMarks.label}" — previews first`, () =>
      stageAction({ verb: "revert", ref: themeMarks.label, targetId: anchor,
                    label: themeMarks.label, kind: "feature" }));
    mkBtn("Restore", `sgt restore "${themeMarks.label}" — previews first`, () =>
      stageAction({ verb: "restore", ref: themeMarks.label, targetId: anchor,
                    label: themeMarks.label, kind: "feature" }));
    mkBtn("✕ Show everything", "clear the focus", () => setThemeFocus(themeMarks.id));
  }

  // Reveal a save from find: flash its chapters on every lane that commit touched, unfolding any
  // subsystem that hides one, and say what was highlighted. Clears itself -- a flash is an answer,
  // not a mode.
  let saveFlashTimer = null;
  function revealSave(commitIndex, label) {
    saveFlashIdx = commitIndex;
    const touched = new Set(((grid && grid.cells) || [])
      .filter((c) => c.commit_index === commitIndex).map((c) => c.feature_id));
    let changed = false;
    for (const fid of touched) {
      let cur = byId(fid);
      cur = cur ? byId(cur.parent) : null;
      while (cur) {
        const i = state.collapsed.indexOf(cur.id);
        if (i >= 0) { state.collapsed.splice(i, 1); changed = true; }
        cur = byId(cur.parent);
      }
    }
    if (changed) { saveState(); recompute(); }
    render();
    const hit = rail.querySelector(".gcar-save-hit");
    if (hit && hit.scrollIntoView) hit.scrollIntoView({ block: "center" });
    setPreviewContext(`${label || "save"} — lit on ${touched.size} lane(s)`, "identity");
    clearTimeout(saveFlashTimer);
    saveFlashTimer = setTimeout(() => {
      saveFlashIdx = null;
      if (previewContext.classList.contains("identity")) setPreviewContext(null);
      render();
    }, 4000);
  }

  // The persistent "where am I" band (Stage C): composition · view · current selection + its live
  // closure count · scrub position · uncommitted work. Always visible, so the developer never loses
  // their place regardless of what's selected or scrubbed.
  function renderPresence() {
    const el = document.getElementById("presence");
    if (!el) return;
    const parts = [`◆ ${state.compositionLabel || "HEAD"}`];
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
    // Staged paths used to land in that count and be described as uncommitted edits. They are the
    // opposite: a rewrite of ops already recorded, and while one is live every materializing verb
    // refuses -- which makes it the single most useful thing this always-visible line can carry.
    if ((compose.status && compose.status.staged && compose.status.staged.paths || []).length) {
      parts.push("⧗ staged rewrite");
    }
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
  // `countW` reserves the right-hand column the per-lane op count is set in. It used to be placed
  // at `lastCar + 6`, clamped only to the SVG edge -- so on a lane whose work runs to the present
  // the number landed ON the plot edge and the frontier scrubber, and on every other lane it sat
  // at a different x. Ragged and overlapping, for a column of numbers that only means anything
  // read down the page.
  // `padT` is the plot's top margin. At 14 the `time →` axis title (baseline padT-3, a 9px face)
  // ascended to y=2 and collided with the first swimlane band at y=14 -- the title read as
  // clipped by the top of the panel. 20 clears it.
  const GANTT = { maxLanes: 400, padT: 20, rowH: 26, barH: 12, axisH: 34, minBarW: 6, minCarW: 9, carGap: 1.5, labelMinW: 34, gutterPad: 8, cellGap: 0.5, indent: 14, ghostW: 72, nowPad: 7, countW: 40 };
  let graphView = null; // { geom, handleEl, frontierEl, veilEl } -- set each render for the scrubber

  // ─── The checkpoint name chip ────────────────────────────────────────────────────────────────
  // Most chapters are too narrow to hold their own name (`renderCars` only sets a label inline when
  // the WHOLE name fits), so on a real repository the timeline was a field of coloured bars whose
  // names lived in a native SVG `<title>` -- a second's delay, an OS tooltip, unreadable in a
  // recording, and invisible to anyone scanning for "what can I go back to". The names were there;
  // nothing put them on screen.
  //
  // The chip is an overlay, not a resize, and it does not animate its geometry either (see the
  // `.gchip` rules: it fades, it never unfolds). Widening the hovered bar to fit its name was the
  // obvious move and it is a lie: x means WHEN on this plot, so a bar that grows to fit its text is
  // claiming time it does not own, and its neighbours appear to shift. Instead the bar keeps its
  // geometry and a chip is drawn over the row in the lane's own hue, centred on the bar, with a tick pointing at
  // the bar's true centre so the chip still names the right thing after it has been clamped inside
  // the plot. Overlapping neighbouring bars is fine here in a way it is not for the persistent
  // in-row tag: this appears only under the cursor and only for the one chapter being pointed at.
  //
  // A selected chapter keeps its chip. Clicking a bar is the reader saying "this one", and the name
  // of the thing they picked should stay on screen while they decide what to do with it.
  const CHIP = { h: 18, padX: 8, tickW: 3, maxW: 260 };
  let chipLayer = null;   // the top <g>, re-made every render (SVG paints in document order)
  let pinnedChip = null;  // the selected car's chip args, redrawn whenever a hover chip retracts

  function drawCarChip(args, pinned) {
    if (!chipLayer) return;
    const { car, color, x, w, midY, geom } = args;
    const name = car.label || `checkpoint ${car.segIndex}`;
    const detail = `  ${car.opCount} edit${car.opCount === 1 ? "" : "s"}`
      + (car.reverted ? " · reverted" : "");
    const fit = fitText(name, CHIP.maxW, "gchip-name");
    let cw = Math.min(
      CHIP.maxW + CHIP.padX * 2,
      textWidth(fit.text, "gchip-name") + textWidth(detail, "gchip-detail") + CHIP.padX * 2,
    );
    const plotR = geom.plotX0 + geom.plotW + geom.forecastW;
    const anchor = x + w / 2;                       // where the chapter actually is
    // Hover must always read as GROWTH. On a car wider than its own name, a name-sized capsule
    // painted over the middle visually REPLACED the wide bar with a narrower one -- the chapter
    // looked like it shrank on hover. So the capsule never goes below the car's own width: on a
    // wide car it is the car itself, lit, slightly outset, with the name centered inside it.
    const snug = w >= cw;
    let cx;
    if (snug) {
      cw = w + 6;
      cx = x - 3;
    } else {
      cx = Math.max(geom.plotX0, Math.min(anchor - cw / 2, plotR - cw));
    }
    const y = midY - CHIP.h / 2;
    const g = mk("g", { class: "gchip" + (pinned ? " gchip-pinned" : "") });
    g.appendChild(mk("rect", {
      x: cx, y, width: cw, height: CHIP.h, rx: 4, class: "gchip-bg", fill: color,
    }));
    // The tick keeps the chip honest once clamping has moved it off its bar: it marks the bar's real
    // centre, so a chip pinned against the plot edge still points at the chapter it names.
    g.appendChild(mk("rect", {
      x: Math.max(cx + 2, Math.min(anchor - CHIP.tickW / 2, cx + cw - CHIP.tickW - 2)),
      y: y + CHIP.h, width: CHIP.tickW, height: 3, class: "gchip-tick", fill: color,
    }));
    const t = snug
      ? mk("text", { x: anchor, y: midY + 3, class: "gchip-name", "text-anchor": "middle" })
      : mk("text", { x: cx + CHIP.padX, y: midY + 3, class: "gchip-name" });
    t.appendChild(mk("tspan", { text: fit.text }));
    t.appendChild(mk("tspan", { class: "gchip-detail", text: detail }));
    g.appendChild(t);
    chipLayer.appendChild(g);
  }

  // A chip that is on its way out, kept so a re-entered hover can cancel its own retraction rather
  // than fighting it. Without this, sweeping the cursor along a lane left every chip it touched
  // mid-retraction on screen at once.
  let retracting = null;

  function clearRetraction() {
    if (!retracting) return;
    retracting.el.remove();
    clearTimeout(retracting.timer);
    retracting = null;
  }

  // Chip hover-intent: the name waits for the cursor to REST on a chapter. Naming is cheap, but a
  // chip that unfolds on every car the cursor merely CROSSES is motion the reader did not ask for
  // -- sweeping a dense lane popped a chip per car. Its own timer, not `onHoverIntent`'s preview
  // timer, so a leave cancels a pending chip without cancelling a pending preview elsewhere.
  // The node harness (dev/smoke.js) sets `window.__CHIP_INTENT_MS__ = 0`, which shows the chip
  // synchronously so its assertions can stay synchronous.
  const CHIP_INTENT_MS = (typeof window !== "undefined" && window.__CHIP_INTENT_MS__ != null)
    ? window.__CHIP_INTENT_MS__ : 260;
  let chipTimer = null;

  function cancelChipIntent() {
    if (chipTimer !== null) {
      clearTimeout(chipTimer);
      chipTimer = null;
    }
  }

  function chipIntent(fn) {
    cancelChipIntent();
    if (CHIP_INTENT_MS <= 0) { fn(); return; }
    chipTimer = setTimeout(() => { chipTimer = null; fn(); }, CHIP_INTENT_MS);
  }

  function showCarChip(args) {
    if (!chipLayer) return;
    clearRetraction();
    chipLayer.replaceChildren();
    drawCarChip(args, false);
  }

  // Retract to whatever should be on screen with no cursor on the plot: the selected chapter's chip,
  // or nothing. The chip ANIMATES back into its bar instead of being deleted where it stands --
  // deleting it is what made the name feel thrown at the reader and then taken away, because the
  // only motion in the whole interaction pointed one way.
  function hideCarChip() {
    if (!chipLayer) return;
    clearRetraction();
    // Filtered in JS rather than with `:not()` in the selector: the hover chip is the only child
    // that is not pinned, and a plain class check is one less thing for a DOM to disagree about.
    // Spread first -- a browser's `querySelectorAll` returns a NodeList, which has `forEach` but NOT
    // `find`, so calling it directly silently yields nothing and the retraction never runs.
    const live = [...chipLayer.querySelectorAll(".gchip")]
      .find((c) => !c.classList.contains("gchip-pinned"));
    chipLayer.replaceChildren();
    if (pinnedChip) drawCarChip(pinnedChip, true);
    if (!live) return;
    // Reduced motion: the retreat is the flourish, so there is none -- the chip simply goes. Left to
    // the animation path it would sit fully visible for the backstop timeout, because with
    // `animation: none` there is no `animationend` to remove it on.
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    // Re-attached ON TOP so the pinned chip (redrawn above) does not paint over the retreat, and
    // removed on `animationend` -- with a timer as the backstop, since an animation on a detached or
    // reduced-motion element may never fire the event and a leaked node would sit there forever.
    live.classList.add("gchip-out");
    chipLayer.appendChild(live);
    const done = () => { if (retracting && retracting.el === live) clearRetraction(); };
    live.addEventListener("animationend", done, { once: true });
    retracting = { el: live, timer: setTimeout(done, 400) };
  }

  // `forecastCars` is the widest forecast a single lane carries this render (0 when nothing is
  // pending or planned). It buys a FORECAST BAND to the right of the `now` rule: measured time ends
  // at `now`, and anticipated work lives past it. Without a band the future has literally no room on
  // an axis whose domain is [c0, lastCommit] -- which is why pending work used to degenerate into a
  // badge jammed against the plot edge instead of reading as a car.
  // The name column's width, in px. A hard `clamp(130, paneW*0.4, 220)` truncated most of the
  // repo's feature names on a pane with room to spare -- 220px is about 34 glyphs, and these names
  // are sentences -- and offered the reader no way to see the rest. So: fit to the longest name the
  // view is actually drawing (bounded by a share of the pane, so the plot is never squeezed out),
  // and let the reader override that by dragging the divider. Every table view works this way.
  // Two different ceilings, because automatic and deliberate are different acts. Left to itself the
  // column takes at most 45% of the pane -- names matter, but this is a TIMELINE, and a fit that
  // quietly ate two thirds of a docked pane would trade one unreadable field for another. A drag is
  // the reader saying "right now I am reading names", so it may go to 70%.
  const LABEL_MIN_W = 90;                 // narrower than this and a name is a stub, not a name
  const LABEL_FIT_FRAC = 0.45;            // ...how far the automatic fit may go
  const LABEL_DRAG_FRAC = 0.7;            // ...and how far a deliberate drag may
  const labelFitW = (paneW) => Math.max(LABEL_MIN_W, Math.round(paneW * LABEL_FIT_FRAC));
  const labelMaxW = (paneW) => Math.max(LABEL_MIN_W, Math.round(paneW * LABEL_DRAG_FRAC));

  // The natural width: the widest label actually on screen, plus its indent and swatch. Measured,
  // not estimated -- see textWidth.
  function naturalLabelW() {
    let widest = 0;
    for (const l of layout.lanes) {
      const node = byId(l.id);
      const raw = (node && node.label) || l.id;
      const text = l.isMeta ? `${raw} (${l.leaves.length})` : raw;
      const labelX = GANTT.gutterPad + (l.depth || 0) * GANTT.indent + (l.isMeta ? 24 : 12);
      widest = Math.max(widest, labelX + textWidth(text, "glane-label"));
    }
    for (const hd of layout.headers) {
      const ind = (hd.depth || 0) * GANTT.indent;
      widest = Math.max(widest, 22 + ind + textWidth(hd.label, "swimlane-label")
        + 12 + textWidth(`${hd.laneCount} feat`, "swimlane-meta"));
    }
    return Math.ceil(widest) + 10; // a little air before the divider
  }

  function ganttGeom(forecastCars = 0) {
    const pane = panePx();
    const paneW = pane.w;
    // A width the reader dragged wins over the fit, clamped to what this pane can hold (so a column
    // dragged wide in a maximized panel doesn't swallow the plot when the panel is docked narrow).
    const labelW = Math.round(state.labelW != null
      ? Math.max(LABEL_MIN_W, Math.min(state.labelW, labelMaxW(paneW)))
      : Math.max(LABEL_MIN_W, Math.min(naturalLabelW(), labelFitW(paneW))));
    const plotX0 = labelW + GANTT.gutterPad;
    const fullW = Math.max(60, paneW - plotX0 - 16 - GANTT.countW);
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
    // Per-row heights (TableLens): a theme focus keeps every lane on screen but gives the
    // emphasized lanes full rows and compresses the rest to thin density strips -- focus is a
    // reallocation of ink, never a removal of context. With no focus this is the identity.
    const ROW_DIM = 16;
    const heights = new Array(layout.rowCount).fill(GANTT.rowH);
    if (themeMarks) {
      for (const l of layout.lanes || []) {
        if (l.row != null && !laneInThemeFocus(l)) heights[l.row] = ROW_DIM;
      }
    }
    const yTop = new Array(layout.rowCount + 1);
    yTop[0] = GANTT.padT;
    for (let i = 0; i < layout.rowCount; i++) yTop[i + 1] = yTop[i] + heights[i];
    const rowsH = yTop[layout.rowCount] - GANTT.padT;
    // Rows stay top-anchored, but the axis pins to the bottom of the pane: the SVG grows to fill the
    // rail's viewport (minus its 8px padding each side) so a short timeline no longer leaves a dead
    // band of background below it -- the void becomes an honest empty plot with a full-height axis.
    // The axis is PINNED: it lives in its own SVG below the scroller, so the plot's height is its
    // rows and nothing else. It used to sit at the bottom of the one scrolling SVG, which meant the
    // `c0 … cN` ruler -- the thing that makes every column mean a time -- scrolled off the moment a
    // repository had more features than the pane is tall. On this repo that was already true at 24
    // rows, with the tick labels clipped by the pane's own edge.
    const naturalH = GANTT.padT + rowsH + 12;
    const h = Math.max(naturalH, pane.h - 16 - GANTT.axisH);
    const axisY = h;  // gridlines, the future-veil and the frontier line run the full plot height
    const maxCommit = Math.max(1, layout.commitCount - 1);
    const xOf = (ci) => plotX0 + (Math.max(0, Math.min(maxCommit, ci)) / maxCommit) * plotW;
    return {
      labelW, plotX0, plotW, w, h, axisY, maxCommit,
      forecastW, forecastSlots, nowX, forecastX0,
      xOf,
      rowY: (row) => yTop[Math.max(0, Math.min(row, layout.rowCount))], // top of the row
      midY: (row) => {
        const r = Math.max(0, Math.min(row, layout.rowCount - 1));
        return yTop[r] + heights[r] / 2;
      },
      rowH: (row) => heights[Math.max(0, Math.min(row, layout.rowCount - 1))] || GANTT.rowH,
      scrubX: (idx) => xOf(idx),
      xToCommit: (x) => Math.max(0, Math.min(maxCommit,
        Math.round(((x - plotX0) / Math.max(1, plotW)) * maxCommit))),
    };
  }

  function laneColor(id) {
    const n = byId(id);
    return (n && n.color) || "#8a8a8a"; // neutral only for a node the host didn't color (unknown id)
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
  function renderCars(g, l, geom, color, barY, midY, carOpts) {
    carOpts = carOpts || {};
    const barH = carOpts.barH || GANTT.barH;
    const quiet = !!carOpts.quiet; // a compressed context lane: bars only, no labels or chips
    const laneNode = byId(l.id);
    const laneName = (laneNode && laneNode.label) || l.id;
    const cars = l.cars || [];
    if (!cars.length) {
      const x1 = geom.xOf(l.firstCommit), x2 = geom.xOf(l.lastCommit);
      g.appendChild(mk("rect", {
        x: x1, y: barY, width: Math.max(GANTT.minBarW, x2 - x1), height: barH, rx: 3,
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
    let tagRight = 0; // how far right an in-row big-event tag reached, so the op count clears it
    for (let i = 0; i < cars.length; i++) {
      const car = cars[i];
      const isBig = cars.length > 1 && i === bigIndex;
      let x = Math.max(geom.xOf(car.firstIndex), cursor); // anchored in time, never behind the last car
      let rightEnd = car.lastIndex >= geom.maxCommit ? plotR : geom.xOf(car.lastIndex) + colStep;
      if (i + 1 < cars.length) rightEnd = Math.min(rightEnd, geom.xOf(cars[i + 1].firstIndex) - GANTT.carGap);
      let w = Math.max(GANTT.minCarW, rightEnd - x);
      // Never past the plot's right edge. x means WHEN, so a car drawn beyond the axis is claiming a
      // time that does not exist -- and in a dense lane that is exactly what happened: each car is
      // nudged right to clear the previous one and floored at `minCarW`, so a train of short
      // chapters walked `cursor` off the end and the clamp, which also floored at `minCarW`, could
      // not pull it back. On seedbank-v3's folded `Plant Discovery` row the last chapters ran 57px
      // past the axis and through the op-count column. Pinning to the edge and letting the final
      // cars thin to slivers is the honest degradation: too many chapters for the width.
      if (x + w > plotR) x = Math.max(geom.plotX0, plotR - w);
      if (x + w > plotR) w = Math.max(2, plotR - x);
      const selected = car.checkpoint === state.selectedCheckpoint;
      const wrap = mk("g", {
        class: "gcar-wrap" + (isBig ? " gcar-big" : "") + (selected ? " gcar-selected" : ""),
        "data-first": car.firstIndex, "data-checkpoint": car.checkpoint,
      });
      carRects.set(car.checkpoint, { x, w, midY, laneId: l.id, subBins: car.subBins || [] });
      // Native tooltip: an SVG element ignores a `title` attribute, so the hover text has to be a
      // `<title>` child of the rect (not attrs.title) to actually show on hover.
      const ask = dominantAsk(car.asks);
      const tip = `${car.label}\n${car.tier}` +
        (car.source === "fallback" ? "" : ` · ${car.source}`) +
        // The chapter in the user's own words on hover: the ask that accounts for most of it, as
        // an excerpt, with whose words they were. This used to print up to two captured prompts
        // verbatim -- and a real prompt is a 900-character paragraph, so a native tooltip (no
        // wrapping, no scrolling, no styling) became a wall that covered the timeline it was
        // describing. Selecting the chapter opens the rest.
        (ask ? `\n“${ask.gist}”\n— ${ask.source}` : "") +
        // No raw `f-<64 hex>@n` here. The ref is unreadable, unmemorable, and nobody retypes it
        // -- it filled the tooltip with the one string on screen carrying no information. What a
        // reader wants from a chapter they are pointing at is its size, its state, and the fact
        // that clicking does something.
        `\n${car.opCount} edit(s)` +
        (car.reverted ? " · reverted — Restore brings it back" : " · click to select");
      // A reverted car is drawn hollow in its own identity hue: the edits are still recorded and
      // still addressable (`sgt restore`), they are just no longer in the ideal, so the car has to
      // stay in place and read as emptied rather than vanish or redraw as live. The inline stroke
      // is needed because `.gcar` sets `stroke: var(--bg)`, which a class rule cannot override
      // per-lane.
      const carRect = mk("rect", {
        x, y: barY, width: w, height: barH, rx: 3,
        class: "gcar" + (car.tier === "thematic" ? " gcar-thematic" : "") + (isBig ? " gcar-big-rect" : "") +
          (car.reverted ? " gcar-reverted" : "") +
          (landing && i === cars.length - 1 ? " gcar-landing" : ""),
        fill: color, "data-checkpoint": car.checkpoint,
      }, [mk("title", { text: tip })]);
      if (car.reverted) carRect.style.stroke = color;
      // A focused theme's member chapters carry a shared accent ring across every lane they sit
      // on -- the one visual that makes "this work spans these lanes" a single object on screen.
      if (themeMarks && !quiet && (car.subBins || []).some((b) => themeMarks.commitIdx.has(b[0]))) {
        wrap.classList.add("gcar-theme-member");
      }
      // A save picked in find: flash its chapters wherever that commit landed, across lanes.
      if (saveFlashIdx != null && (car.subBins || []).some((b) => b[0] === saveFlashIdx)) {
        wrap.classList.add("gcar-save-hit");
      }
      wrap.appendChild(carRect);
      // Within-car density texture: the chapter's own per-commit runs, opacity by sqrt(count /
      // that chapter's own max) -- a single-commit car (the common case) has nothing to spread
      // across, so it's left as one flat fill rather than one bright sliver + dead space.
      const bins = car.subBins && car.subBins.length ? car.subBins : [];
      if (bins.length > 1 && w >= 6 && !car.reverted) {  // density cells would repaint a hollow car solid
        // Resolution is bounded by the PIXELS available, not by the number of commits.
        //
        // This drew one rect per commit in the chapter's span. On a 4,000-commit history that is 400
        // rects inside a 30px car -- each 0.075px wide, none of them visible, and 588,499 of them
        // across the view: a 5.2-second render and 599,317 SVG nodes for a texture no one can see.
        // Detail finer than a pixel is not detail. Commits are folded into at most one bucket per
        // `DENSITY_PX`, which leaves the texture identical wherever it was ever legible and turns
        // the pathological case into a handful of rects.
        const DENSITY_PX = 2;
        const slots = Math.max(1, Math.min(bins.length, Math.floor(w / DENSITY_PX)));
        const buckets = new Array(slots).fill(0);
        for (let j = 0; j < bins.length; j++) {
          buckets[Math.min(slots - 1, Math.floor((j * slots) / bins.length))] += bins[j][1];
        }
        // A loop, not `Math.max(1, ...buckets)`: spreading a long array into a call blows the stack
        // outright past ~65k elements, and `bins` is one per commit in the span.
        let localMax = 1;
        for (const v of buckets) if (v > localMax) localMax = v;
        const cellW = w / slots;
        for (let j = 0; j < slots; j++) {
          if (buckets[j] <= 0) continue;   // a bucket nothing landed in is not ink
          const cell = mk("rect", {
            x: x + j * cellW, y: barY, width: Math.max(0.5, cellW - GANTT.cellGap), height: barH,
            class: "gcar-cell", fill: color,
          });
          cell.setAttribute("fill-opacity", (0.3 + 0.55 * Math.sqrt(buckets[j] / localMax)).toFixed(3));
          wrap.appendChild(cell);
        }
      }
      // Inline label: the chapter's intent, not the bare @n index (which reads as a meaningless
      // "0"). Only when the car is wide enough to hold a few glyphs; otherwise the hover tooltip
      // carries it. The big-event car gets a labelled tag just above the strip even when narrow.
      //
      // ...unless the label only echoes the lane's own name (`Sort The Grid` on the row called
      // `sort the grid by name, species or days to harvest`), in which case it is printing the row
      // header a second time, inside the row. The floated tag is the worse case: it costs a line of
      // vertical space the 26px row does not have, so an echo pushed a duplicate of the lane name
      // up into the row above it. The tooltip still carries the chapter name either way.
      // ...and the inline label goes in only when the whole name FITS. A car sized between "wide
      // enough for a few glyphs" and "wide enough for the name" printed `Variet…` -- which could be
      // `Variety Side Panel` or `Varieties Grid`, so it names nothing while looking like it does.
      // The tooltip (and, for the lane's big event, the in-row tag) carries what doesn't fit.
      const inlineFit = car.label ? fitText(car.label, w - 6, "gcar-label") : null;
      if (car.label && !quiet && !echoesLane(car.label, laneName)) {
        if (w >= GANTT.labelMinW && !inlineFit.clipped) {
          wrap.appendChild(mk("text", {
            x: x + w / 2, y: midY + 3, class: "gcar-label", text: inlineFit.text,
          }));
        } else if (isBig) {
          // In the row, just past the car -- not floating above it. A tag centred over a narrow car
          // sat in the 7px gutter BETWEEN two rows, as near the bar above as to its own, so the one
          // label a lane gets was ambiguous about which lane it named. `rank matches by field…` read
          // as belonging to `sort the grid`, the row above it. Set beside its car it cannot.
          // Bounded by the NEXT CAR, not by the plot's right edge. Measured to `plotR` a tag ran
          // for up to 140px straight across every chapter that followed it -- on a dense lane the
          // labels and the cars they were meant to name overlapped into an unreadable smear, which
          // is exactly what the row is supposed to be showing. A label may use the empty time after
          // its own chapter and not one pixel of anyone else's.
          const nextX = i + 1 < cars.length
            ? geom.xOf(cars[i + 1].firstIndex) - GANTT.carGap
            : plotR;
          const room = Math.min(140, nextX - (x + w) - 8);
          if (room >= 34) {
            const tag = mk("text", {
              x: x + w + 5, y: midY + 3, class: "gcar-tag gcar-tag-inrow",
              text: fitText(car.label, room, "gcar-tag").text,
            });
            wrap.appendChild(tag);
            tagRight = Math.max(tagRight, x + w + 5 + room);
          }
        }
      }
      // A car is its own click target: selecting it picks the CHECKPOINT (`f-XXXX@n`, the revert
      // unit), distinct from a row/label click that picks the whole feature. stopPropagation keeps
      // it from bubbling up to the lane's feature-select handler.
      wrap.addEventListener("click", (ev) => selectCar(car, l.id, ev));
      // Hovering a chapter IDENTIFIES it; it does not preview destroying it. This used to fire a
      // full revert preview -- so merely running the cursor over the timeline to read what was
      // there dimmed the field and drained cars, showing the consequence of an action the reader
      // had not chosen and might never choose. Feedforward belongs to the verb the reader is
      // pointing at, and pointing at a chapter is not pointing at a verb. Instant and local: no
      // round-trip, no hover-intent delay, because naming what is already on screen costs nothing.
      // The chapter's own name, on screen, under the cursor -- see drawCarChip for why this is an
      // overlay and not a wider bar.
      const chipArgs = { car, color, x, w, midY, geom };
      if (selected && !quiet) pinnedChip = chipArgs;
      wrap.addEventListener("mouseenter", () => {
        if (armedVerb || stagedAction) return;
        wrap.classList.add("gcar-hovered");
        if (quiet) { // a compressed context lane has no room for the chip; the pill still names it
          setPreviewContext(`${car.label} · ${car.opCount} edit(s)`, "identity");
          return;
        }
        // The highlight is instant (the affordance); the chip waits for the cursor to rest
        // (`chipIntent`), so sweeping the lane does not unfold a name per car crossed.
        chipIntent(() => showCarChip(chipArgs));
        setPreviewContext(`${car.label} · ${car.opCount} edit(s)`
          + (car.reverted ? " · reverted" : "") + " — click to select", "identity");
      });
      wrap.addEventListener("mouseleave", () => {
        wrap.classList.remove("gcar-hovered");
        cancelChipIntent(); // a pending chip must not appear after the cursor has gone
        hideCarChip();
        // Only ever retract this hover's own sentence.
        if (previewContext.classList.contains("identity")) setPreviewContext(null);
      });
      g.appendChild(wrap);
      cursor = x + w + GANTT.carGap;
      lastRight = x + w;
    }
    return Math.max(lastRight, tagRight);
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
  function renderForecastCars(g, l, geom, color, barY, midY, ghosts, barH) {
    barH = barH || GANTT.barH;
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
          x: x + 2.5, y: barY - 2.5, width: Math.max(GANTT.minCarW, w - 2.5), height: barH,
          rx: 3, class: "gcar-ghost-stackback", stroke: color,
        }));
      }
      wrap.appendChild(mk("rect", {
        x, y: barY, width: w, height: barH, rx: 3,
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

  // ─── Cross-feature work, drawn IN the graph ─────────────────────────────────────────────────
  // Links on demand, presence for cheap. Always-on spines were tried and, with several themes on
  // real data, drew crossing dotted lines and floating labels nobody could attribute -- classic
  // link spaghetti. So the DEFAULT is one small ◆ on the time axis per spanning work (the Gantt
  // milestone idiom): hovering it rings that work's member chapters and names it in the presence
  // pill; clicking enters the TableLens focus, and only THEN does the one focused spine draw --
  // a dotted vertical with a dot at each member lane, no elbows, members already ringed.
  function renderThemeSpines(layer, svg, geom) {
    const themes = spanningThemes();
    if (!themes.length) return;
    const idxOf = new Map(((grid && grid.commits) || []).map((c) => [c.sha, c.index]));
    const spines = [];
    for (const t of themes) {
      const cid = new Set((t.atom_shas || []).map((sh) => idxOf.get(sh)).filter((i) => i != null));
      if (!cid.size) continue;
      // ONE anchor per lane -- the chapter holding most of this work there.
      const bestByLane = new Map();
      for (const [chk, r] of carRects) {
        const overlap = r.subBins.reduce((n, b) => n + (cid.has(b[0]) ? b[1] : 0), 0);
        if (!overlap) continue;
        const cur = bestByLane.get(r.laneId);
        if (!cur || overlap > cur.overlap) bestByLane.set(r.laneId, { chk, overlap, ...r });
      }
      const members = [...bestByLane.values()];
      if (members.length < 2) continue; // folded into one visible lane: nothing to link
      const centers = members.map((m) => m.x + m.w / 2).sort((a, b) => a - b);
      const x = centers[Math.floor(centers.length / 2)];
      spines.push({ t, members, x, span: members.length });
    }
    if (!spines.length) return;
    spines.sort((a, b) => a.x - b.x);
    let lastX = -Infinity;
    for (const sp of spines) {
      if (sp.x - lastX < 10) sp.x = lastX + 10;
      lastX = sp.x;
    }

    const ring = (sp, on) => {
      for (const m of sp.members) {
        svg.querySelectorAll(".gcar-wrap").forEach((w) => {
          if (w.getAttribute("data-checkpoint") === m.chk) {
            w.classList[on ? "add" : "remove"]("gcar-theme-member");
          }
        });
      }
    };
    const drawSpine = (sp, cls) => {
      const ys = sp.members.map((m) => m.midY);
      const y1 = Math.min(...ys);
      const y2 = Math.max(...ys);
      const g = mk("g", { class: cls, "data-theme": sp.t.theme_id });
      g.appendChild(mk("line", { x1: sp.x, x2: sp.x, y1: y1 - 6, y2: y2 + 6, class: "theme-spine-line" }));
      for (const m of sp.members) {
        g.appendChild(mk("circle", { cx: sp.x, cy: m.midY, r: 3, class: "theme-spine-dot" }));
      }
      const label = mk("text", {
        x: sp.x, y: Math.max(12, y1 - 12), "text-anchor": "middle",
        class: "theme-spine-label", text: sp.t.label,
      });
      g.appendChild(label);
      return g;
    };

    // Focused: exactly one piece of work is the subject -- its spine draws, nothing else.
    if (themeMarks) {
      const sp = spines.find((x) => x.t.theme_id === themeMarks.id);
      if (sp) layer.appendChild(drawSpine(sp, "theme-spine theme-spine-focused"));
      return;
    }

    // Default: one ◆ per spanning work, sitting on the time axis. Presence without spaghetti.
    let hoverSpine = null;
    for (const sp of spines) {
      const g = mk("g", { class: "theme-mark", "data-theme": sp.t.theme_id });
      g.appendChild(mk("rect", { x: sp.x - 7, y: 4, width: 14, height: 16, class: "theme-mark-hit" }));
      g.appendChild(mk("rect", {
        x: sp.x - 3.6, y: 8.4, width: 7.2, height: 7.2, rx: 1.5,
        class: "theme-mark-glyph", transform: `rotate(45 ${sp.x} 12)`,
      }));
      g.appendChild(mk("title", {
        text: `${sp.t.label}\none piece of work across ${sp.span} features — click to focus it`,
      }));
      g.addEventListener("mouseenter", () => {
        g.classList.add("theme-mark-hot");
        ring(sp, true);
        hoverSpine = drawSpine(sp, "theme-spine theme-spine-hot");
        layer.appendChild(hoverSpine);
        setPreviewContext(`◆ ${sp.t.label} — across ${sp.span} features · click to focus`, "identity");
      });
      g.addEventListener("mouseleave", () => {
        g.classList.remove("theme-mark-hot");
        ring(sp, false);
        if (hoverSpine) { hoverSpine.remove(); hoverSpine = null; }
        if (previewContext.classList.contains("identity")) setPreviewContext(null);
      });
      g.addEventListener("click", (ev) => { ev.stopPropagation(); setThemeFocus(sp.t.theme_id); });
      layer.appendChild(g);
    }
  }

  function renderGraph() {
    if (!paneMeasurable()) return;
    resetFontCache(); // the editor font can change under us; re-resolve before measuring anything
    const prevScroll = rail.scrollTop;
    rail.innerHTML = "";
    // Size the forecast band to the busiest lane's forecast, once, before geometry: every lane shares
    // one band edge so the `now` rule is a single straight line down the plot (a per-lane band would
    // make "now" ragged, and the eye reads a ragged boundary as data).
    const forecasts = new Map(layout.lanes.map((l) => [l.id, laneForecast(l)]));
    const widest = Math.max(0, ...[...forecasts.values()].map((f) => f.length));
    const geom = ganttGeom(widest);
    const scroller = mk("div", { class: "plot-scroll" });
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
    const spineLayer = mk("g", { class: "theme-spines" });
    svg.appendChild(spineLayer); // above the lane bars, below the chips

    // SVG has no z-index -- paint order is document order -- so the chip layer is made before the
    // lanes (its handlers capture it) and appended after them, or a chip would be drawn under the
    // rows it is meant to name.
    chipLayer = mk("g", { class: "gchip-layer" });
    pinnedChip = null; // re-established by renderCars for whichever car is selected this pass
    for (const hd of layout.headers) bandLayer.appendChild(renderSwimlaneHeader(hd, geom));
    // A backstop, not a design: every lane is ~10 SVG nodes, so a repository with thousands of
    // features would build a document the browser cannot lay out. Measured on a synthetic 600-feature
    // history this render was 131,053 nodes and 3.7s. The flat view is ordered by most-recently
    // touched, so the cap keeps what a reader is most likely to be looking for -- and it SAYS what it
    // dropped, because a view that silently stops at 250 rows is lying about the size of the repo.
    // (The real fix is virtualising on scroll; this bounds the damage until then.)
    const drawn = layout.lanes.slice(0, GANTT.maxLanes);
    const hiddenLanes = layout.lanes.length - drawn.length;
    carRects = new Map();
    for (const l of drawn) laneLayer.appendChild(renderLane(l, geom, forecasts.get(l.id) || []));
    renderThemeSpines(spineLayer, svg, geom); // after the lanes: anchors come from carRects
    if (hiddenLanes > 0) {
      svg.appendChild(mk("text", {
        x: GANTT.gutterPad, y: geom.rowY(drawn.length) + 12, class: "glane-overflow",
        text: `+${hiddenLanes} more feature${hiddenLanes === 1 ? "" : "s"} not drawn — narrow the view with find, or group by subsystem`,
      }));
    }
    svg.appendChild(renderColumnDivider(geom));
    svg.appendChild(chipLayer);
    if (pinnedChip) drawCarChip(pinnedChip, true);
    const axisSvg = mk("svg", {
      width: geom.w, height: GANTT.axisH, class: "railsvg gantt gantt-axis",
    });
    renderTimeAxis(svg, axisSvg, geom);

    scroller.appendChild(svg);
    rail.appendChild(scroller);
    rail.appendChild(axisSvg);
    scroller.scrollTop = prevScroll;
    glideRows(svg, geom); // rows that moved glide to their new slot instead of teleporting
    applyLens(); // re-apply a pinned lane or a live search across the re-render
  }

  // FLIP row transitions: every re-render rebuilds the SVG from scratch, which reads as the whole
  // map teleporting whenever anything folds, focuses or lands. Rows are the unit a reader tracks,
  // so each row's PREVIOUS y is remembered across renders (keyed by lane/header id) and a row that
  // moved starts at its old offset and glides to its new slot; a row that is genuinely new fades
  // in in place. Pure view-side motion: layout stays exact, and reduced-motion gets the old cut.
  let prevRowYById = new Map();
  function glideRows(svg, geom) {
    const next = new Map();
    for (const l of layout.lanes || []) if (l.row != null) next.set("L:" + l.id, geom.rowY(l.row));
    for (const hd of layout.headers || []) next.set("H:" + hd.collapsedId, geom.rowY(hd.row));
    if (!prefersReducedMotion() && prevRowYById.size) {
      const els = new Map();
      svg.querySelectorAll(".glane").forEach((n) => els.set("L:" + n.getAttribute("data-id"), n));
      svg.querySelectorAll(".swimlane").forEach((n) => els.set("H:" + n.getAttribute("data-id"), n));
      for (const [key, newY] of next) {
        const n = els.get(key);
        if (!n) continue;
        const oldY = prevRowYById.get(key);
        if (oldY == null) {
          n.classList.add("grow-enter");
          n.addEventListener("animationend", () => n.classList.remove("grow-enter"), { once: true });
          continue;
        }
        const dy = oldY - newY;
        if (!dy) continue;
        n.style.transform = `translateY(${dy}px)`;
        n.getBoundingClientRect(); // commit the start frame before the transition class lands
        n.classList.add("grow-glide");
        n.style.transform = "";
        n.addEventListener("transitionend", () => n.classList.remove("grow-glide"), { once: true });
      }
    }
    prevRowYById = next;
  }

  // The divider between the name column and the plot -- draggable, the way a column header edge is
  // draggable in every table view. It replaces a fixed 220px cap that truncated most of this repo's
  // feature names with no recourse: the reader could not widen the column, and the cut names carried
  // no tooltip either, so a row could be unidentifiable and stay that way.
  //
  // Drag sets `state.labelW` (persisted, so the column survives a reload); double-click clears it
  // back to the measured fit. The visible rule is 1px; the grab target is 9px, because a 1px target
  // is a target only in theory.
  function renderColumnDivider(geom) {
    const x = geom.labelW + GANTT.gutterPad / 2;
    const g = mk("g", { class: "col-divider" });
    g.appendChild(mk("line", { x1: x, x2: x, y1: GANTT.padT - 6, y2: geom.axisY, class: "col-divider-rule" }));
    const grip = mk("rect", {
      x: x - 4.5, y: 0, width: 9, height: Math.max(0, geom.axisY),
      class: "col-divider-grip",
    });
    grip.appendChild(mk("title", { text: "Drag to resize the name column · double-click to fit" }));
    g.appendChild(grip);

    grip.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      ev.stopPropagation(); // never let the grab fall through to the frontier scrubber underneath
      grip.setPointerCapture(ev.pointerId);
      const startX = ev.clientX, startW = geom.labelW;
      const maxW = labelMaxW(panePx().w);
      g.classList.add("dragging");
      const onMove = (e) => {
        const next = Math.round(Math.max(LABEL_MIN_W, Math.min(startW + (e.clientX - startX), maxW)));
        if (next === state.labelW) return;
        state.labelW = next;
        // Redraw at the new width rather than sliding a preview line and reflowing on release: the
        // point of the drag is to read the names, so the names have to grow while it is happening.
        renderGraph();
      };
      const onUp = () => {
        saveState();
        document.removeEventListener("pointermove", onMove);
        document.removeEventListener("pointerup", onUp);
      };
      document.addEventListener("pointermove", onMove);
      document.addEventListener("pointerup", onUp);
    });
    grip.addEventListener("dblclick", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      state.labelW = null; // back to the measured fit
      saveState();
      renderGraph();
    });
    return g;
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
    // The end-anchored meta grows leftward from the divider into the same column, so reserve its
    // measured width (plus a gap) out of the label's budget or the two overprint.
    const metaText = `${hd.laneCount} feat`;
    const metaW = textWidth(metaText, "swimlane-meta") + 12;
    const labelX = 22 + ind;
    const label = mk("text", { x: labelX, y: y + GANTT.rowH / 2 + 4, class: "swimlane-label" });
    setLabel(label, hd.label, geom.labelW - labelX - metaW);
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
    // A context lane under a theme focus renders quiet: thin row, thin bars, no chip ink.
    const rh = geom.rowH ? geom.rowH(l.row) : GANTT.rowH;
    const quiet = rh < GANTT.rowH;
    const barH = quiet ? 5 : GANTT.barH;
    const barY = midY - barH / 2;
    const color = laneColor(l.id);
    const inSelection = l.id === state.selected || (state.multi || []).includes(l.id);
    const g = mk("g", {
      class: "glane" + (inSelection ? " selected" : "") + (quiet ? " glane-quiet" : ""),
      "data-id": l.id, "data-first": l.firstCommit,
    });
    // full-row hit target (so hovering/clicking the gutter or empty time works, not just the bar)
    g.appendChild(mk("rect", { x: 0, y, width: geom.w, height: rh, class: "glane-hit" }));

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
    // Measured, and carrying its full text on hover whenever it had to be cut. A feature's name is
    // the only thing on this row a reader can identify it by, and `put a search box in the h…` had
    // no way at all to finish itself -- not a wider column, not a tooltip, not a drag.
    const clipped = setLabel(label, l.isMeta ? `${raw} (${l.leaves.length})` : raw,
                             geom.labelW - labelX - 4);
    // A feature label is its own click target, and it does something different from the row it sits
    // in: the NAME pins a lens (dim the rest, keep this lane and what changes with it), the ROW opens
    // the lane in the detail panel. Nothing said so -- an underline on hover was the entire signal,
    // and an underline does not name an outcome -- so the tooltip says it, and a ◉ appears in the
    // row's left gutter while the name is hovered and stays there while it is pinned, which is the
    // same glyph the banner is headed with. Meta (collapsed-subsystem) labels keep the row's
    // fold-toggle behavior.
    if (!l.isMeta) {
      label.classList.add("glane-label-btn");
      label.style.pointerEvents = "auto";
      const pinned = state.spotlight === l.id;
      if (pinned) label.classList.add("spotlit");
      // ONE <title>, not two. `setLabel` adds its own when the name had to be cut, and a second
      // <title> on the same element is simply never shown -- so a long name would have kept the
      // full-text tooltip and silently lost this one, which is the half of the pair that answers
      // the question nobody could answer from the screen.
      label.querySelectorAll("title").forEach((t) => t.remove());
      label.appendChild(mk("title", {
        text: (clipped ? raw + "\n\n" : "")
          + (pinned
            ? "◉ pinned — click the name again to bring the other lanes back"
            : "◉ click the NAME to dim the other lanes and keep this one and what changes with it\n"
              + "click anywhere else on the ROW to open it in the detail panel"),
      }));
      label.addEventListener("click", (ev) => { ev.stopPropagation(); toggleSpotlight(l.id); });
      g.appendChild(label);
      // In the row's own left gutter (x=1), not beside the name: the name's x depends on nesting
      // depth and on whether the row has a caret, and every candidate offset from it landed on the
      // identity swatch at one depth or another. One column, every row, always free.
      //
      // Immediately after the label in document order, because the CSS reaches it with `:hover +`.
      g.appendChild(mk("text", {
        x: 1, y: midY + 4,
        class: "glane-lens-mark" + (pinned ? " on" : ""),
        text: "\u25c9",
      }));
    } else {
      g.appendChild(label);
    }

    // Chunk-car train: the lane's checkpoints, packed left->right in seg_index order (see
    // renderCars) -- the visual atom is the intent segment, not a raw op or a shared time column.
    const lastX = renderCars(g, l, geom, color, barY, midY, { barH, quiet });
    // Op count in its own right-hand column: one x for every row, so the numbers read down the page
    // and none of them can land on the plot edge or the frontier scrubber.
    const count = mk("text", {
      // Anchored to the plot's own right edge, not the SVG's: the SVG is drawn at the pane's
      // clientWidth, which includes the rail's 8px padding, so an x measured from `geom.w` sits
      // past the visible content and only appears once the pane is scrolled sideways.
      x: geom.plotX0 + geom.plotW + geom.forecastW + GANTT.countW - 6,
      y: midY + 4, class: "gbar-count", text: String(l.opCount),
    });
    count.appendChild(mk("title", { text: `${l.opCount} edit(s) in this feature` }));
    g.appendChild(count);

    // This lane's future, in the band right of the `now` rule: uncommitted work + pending plan steps,
    // drawn as cars in the same grammar as history (see laneForecast / renderForecastCars).
    renderForecastCars(g, l, geom, color, barY, midY, ghosts, barH);

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
  // `svg` is the scrolling plot; `axisSvg` is the pinned strip beneath it. Marks that measure the
  // plot's own height -- gridlines, the future-veil, the frontier line and its grab-band -- belong
  // to the plot and scroll with it. The ruler and the drag handle belong to the axis and do not.
  function renderTimeAxis(svg, axisSvg, geom) {
    if (layout.commitCount <= 1) return;
    const y = geom.axisY;          // the plot's bottom edge, in plot coordinates
    const ay = 8;                  // the track's y, in the axis strip's own coordinates
    axisSvg.appendChild(mk("line", {
      x1: geom.plotX0, x2: geom.plotX0 + geom.plotW, y1: ay, y2: ay, class: "axis-track",
    }));
    for (let i = 0; i <= 4; i++) {
      const ci = Math.round((i / 4) * geom.maxCommit);
      const tx = geom.xOf(ci);
      // Faint full-height gridline so the plot reads as a structured field of time columns rather
      // than an empty expanse above the axis.
      svg.appendChild(mk("line", { x1: tx, x2: tx, y1: GANTT.padT, y2: y, class: "axis-gridline", "data-ci": ci }));
      axisSvg.appendChild(mk("text", {
        x: tx, y: ay + 16, class: "axis-tick" + (i === 0 ? " start" : i === 4 ? " end" : ""), text: `c${ci}`, "data-ci": ci,
      }));
    }
    svg.appendChild(mk("text", { x: geom.plotX0, y: GANTT.padT - 3, class: "axis-title", text: "time →" }));
    // The count column had no header at all: a strip of bare numbers (97, 328, 204…) down the right
    // edge of the plot, in a view whose every other column says what it is. A reader who cannot
    // tell whether that is edits, commits, or symbols cannot use it.
    svg.appendChild(mk("text", {
      x: geom.plotX0 + geom.plotW + GANTT.countW - 4, y: GANTT.padT - 3,
      class: "axis-title count-title", text: "edits",
    }));

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
    const handle = mk("path", { d: `M ${fx - 5} ${ay + 3} L ${fx + 5} ${ay + 3} L ${fx} ${ay - 4} Z`, class: "frontier-handle", "data-cx": fx });
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
    axisSvg.appendChild(handle);   // the handle rides the pinned ruler, always reachable
    svg.appendChild(readout);
    // Snap targets: every checkpoint boundary (a car's first/last commit) -- the commit indices where
    // "something happened", so the playhead clicks onto real events instead of arbitrary columns.
    const snap = new Set();
    for (const l of layout.lanes) for (const c of (l.cars || [])) { snap.add(c.firstIndex); snap.add(c.lastIndex); }
    graphView = { geom, handleEl: handle, frontierEl: line, veilEl: veil, scrubBandEl: band,
      readoutEl: readout, handleY: ay, snap: [...snap].sort((a, b) => a - b) };
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

  // ─── Measuring label text ───────────────────────────────────────────────────────────────────
  // Every label in the gutter used to be cut with `truncate(s, floor(availablePx / 6.5))` -- a
  // guessed 6.5px per glyph, applied to whatever font the user's VS Code happens to render the
  // webview in. The guess is wrong in both directions: a narrow font clipped names that had room to
  // spare, a wide one let them overrun into the plot. Measure the actual string in the actual font
  // instead, and cut to a PIXEL budget rather than a character count.
  const _measureCtx = document.createElement("canvas").getContext("2d");
  const _measureCache = new Map();   // `${font}\0${text}` -> px
  const _fontByClass = new Map();    // css class -> resolved shorthand, re-read once per render
  const _probes = new Map();

  // The font a label class is ACTUALLY drawn in. VS Code resolves the family and size from the
  // user's own settings at runtime, so the only honest way to get it is to ask a real node styled
  // exactly like the labels -- hence one off-screen probe per class, kept for the session.
  function fontFor(cls) {
    let font = _fontByClass.get(cls);
    if (font) return font;
    let probe = _probes.get(cls);
    if (!probe) {
      const svg = mk("svg", { class: "railsvg gantt", width: "0", height: "0" });
      svg.setAttribute("style", "position:absolute;left:-9999px;top:0;visibility:hidden");
      probe = mk("text", { class: cls });
      svg.appendChild(probe);
      document.body.appendChild(svg);
      _probes.set(cls, probe);
    }
    const cs = getComputedStyle(probe);
    font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    _fontByClass.set(cls, font);
    return font;
  }

  // Drop the resolved fonts at the start of each draw: the user can change the editor font
  // mid-session, and a cache keyed to a stale font measures lies with total confidence.
  function resetFontCache() {
    _fontByClass.clear();
    if (_measureCache.size > 4000) _measureCache.clear(); // a long session, not a leak
  }

  function textWidth(s, cls = "glane-label") {
    s = String(s);
    const font = fontFor(cls);
    const key = font + " " + s;
    let w = _measureCache.get(key);
    if (w === undefined) {
      _measureCtx.font = font;
      w = _measureCtx.measureText(s).width;
      _measureCache.set(key, w);
    }
    return w;
  }

  // Cut `s` to at most `px` pixels, ellipsis included. Returns `{text, clipped}` so the caller can
  // decide whether the row needs a tooltip -- a name the reader cannot finish is exactly the case
  // where hovering has to be able to finish it, and until now nothing in the gutter carried one.
  function fitText(s, px, cls) {
    s = String(s);
    if (px <= 0) return { text: "", clipped: s.length > 0 };
    if (textWidth(s, cls) <= px) return { text: s, clipped: false };
    const ell = textWidth("…", cls);
    let lo = 0, hi = s.length;
    while (lo < hi) { // widest prefix whose glyphs + `…` still fit
      const mid = (lo + hi + 1) >> 1;
      if (textWidth(s.slice(0, mid), cls) + ell <= px) lo = mid; else hi = mid - 1;
    }
    return { text: s.slice(0, lo).trimEnd() + "…", clipped: true };
  }

  // True when a chapter's name only repeats words from the lane it sits on -- `Sort The Grid` on the
  // row called `sort the grid by name, species or days to harvest`. Those labels are most of the ink
  // and none of the information: the row says it, then the bar inside the row says it again in title
  // case. Compared on the first few words, since the two strings are ellipsized independently and a
  // tail cut at a different place would otherwise read as new content.
  // The terminal's `_echoes` in sgt/tui/graph.py is the same rule -- keep the two in step.
  const WORD_RE = /[a-z0-9]+/g;
  function echoesLane(chapter, lane) {
    const laneWords = new Set(String(lane).toLowerCase().match(WORD_RE) || []);
    if (!laneWords.size) return false;
    const words = (String(chapter).toLowerCase().match(WORD_RE) || []).slice(0, 6);
    return words.length > 0 && words.every((w) => laneWords.has(w));
  }

  // Put the full text on hover whenever the drawn text is a cut of it. An SVG <text> ignores a
  // `title` attribute, so the tooltip has to be a <title> CHILD (same rule the chapter cars follow).
  function setLabel(el, full, px, cls) {
    const fit = fitText(full, px, cls || el.getAttribute("class"));
    el.textContent = fit.text;
    if (fit.clipped) el.appendChild(mk("title", { text: String(full) }));
    return fit.clipped;
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
    // The handle lives in the PINNED axis SVG, so its y is that strip's own coordinate -- not the
    // plot's bottom edge, which is where it used to sit when the two shared one SVG.
    const y = graphView.handleY != null ? graphView.handleY : geom.axisY;
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
      svg.querySelectorAll(".glane.hovered").forEach((el) => el.classList.remove("hovered"));
      if (!armedVerb) clearGhosts();
      applyLens(); // a pinned lane or a live search survives mouse-out; nothing pinned clears it
      return;
    }
    if (armedVerb) {
      // Picking a target while "Merge into..."/"Move ops..." is armed: outline the candidate and
      // live-preview the real op-count/member delta it would produce, via the same blast paint.
      clearGhosts();
      if (id !== armedVerb.feature) {
        // A collapsed subsystem is a meta-lane, not a leaf feature, so no feature verb can take it as
        // a target. It used to receive the candidate outline anyway -- a false affordance -- preview
        // as silence, and fail on click. The layout already knows, so answer here rather than
        // round-tripping to be refused. Esc first, because while armed a lane click confirms the
        // verb and cannot expand anything.
        if (((layout.laneById || {})[id] || {}).isMeta) {
          showRefusal("That's a collapsed subsystem, not a feature.",
                      ["Esc · expand it, then pick a feature inside"]);
          return;
        }
        renderArmedBanner(); // the previous candidate's answer is stale the moment the cursor moves
        rail.querySelectorAll(".glane").forEach((el) => {
          if (el.getAttribute("data-id") === id) el.classList.add("ghost-target");
        });
        onHoverIntent(() => previewArmed(id));
      }
      return;
    }
    if (previewActive) return; // a held consequence preview owns the field dim
    // Hover marks the row under the cursor and NOTHING else. It used to dim the whole field and
    // recolour the hovered lane's co-change neighbours accent-blue, which meant moving the cursor
    // across the map re-dimmed thirty rows and turned an unpredictable handful of them a different
    // colour -- three questions raised for every one answered, on a gesture that was not a question.
    // Co-change is worth showing and still is, on the lens: a deliberate, held, captioned act.
    svg.querySelectorAll(".glane.hovered").forEach((el) => el.classList.remove("hovered"));
    svg.querySelectorAll(".glane").forEach((el) => {
      if (el.getAttribute("data-id") === id) el.classList.add("hovered");
    });
    markAxisSpan(svg, id); // brighten the time columns this lane spans -- local to the hovered lane
  }

  // ---- armed-result
  // The moment of choice had the weakest preview in this file. While arming, `previewAndBlast`
  // deliberately skips the deep-dim morph (it would fight the crosshair field) and falls back to the
  // flat ghost paint -- which colours two lanes and says nothing about what the pair becomes. The
  // banner keeps asking its question ("into which lane?"), worded identically whichever candidate is
  // under the cursor, so the reader was choosing a target from role paint alone. Both numbers that
  // answer it are already in the payload and were being thrown away, the same way split's `groups`
  // were: merge's `op_count`/`member_count` are the *combined* totals -- the lane that results.
  function armedResultText(verb, res, sourceLabel, targetLabel, targetOps) {
    if (!res || !res.ok) return null; // setPreviewContext(null) hides the pill; inventing a sentence
                                      // here would describe a result the backend just refused
    const name = (l) => `"${l || "that lane"}"`;
    const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
    if (verb === "merge") {
      const ops = res.op_count, mem = res.member_count;
      return `→ ${name(targetLabel)}: `
        + (ops ? `one lane of ${plural(ops, "edit")}` : "one lane")
        + (mem ? ` · ${plural(mem, "symbol")}` : "")
        + `, and ${name(sourceLabel)} is gone.`;
    }
    const moved = (res.op_ids || []).length;
    return `→ ${name(targetLabel)}: ${plural(moved, "edit")} land here (${targetOps} → ${targetOps + moved}); `
      + `${name(sourceLabel)} keeps its symbols and leaves the graph.`;
  }
  // ---- end-armed-result

  function previewArmed(targetId) {
    const { verb, feature } = armedVerb;
    const src = byId(feature), tgt = byId(targetId);
    const targetOps = ((layout.laneById || {})[targetId] || {}).opCount || 0;
    const say = (res) => {
      // Both endpoints, named explicitly rather than read off the payload. `merge`'s preview reports
      // `affected: []` (metadata-only, so no blast/foundation direction applies), which made
      // `classifyAffected` paint the candidate alone and leave the lane that is about to cease
      // existing unmarked -- the one lane whose fate the choice is about. For `move` this is the same
      // classification its rows already produce, since every op being moved comes from this feature.
      paintClosure({ target: targetId, blast: [feature], foundation: [] });
      // Both operands are known here, so this is the one hover that can show the whole
      // transition: the chunks leaving this lane AND arriving in the candidate one.
      paintMigration(feature, targetId);
      // The sentence goes in the banner, not the bottom-right pill: while the cursor is out on a lane
      // the pill is peripheral, and the answer belongs where the question was asked. One locus, so
      // choosing a target does not mean reading two corners of the pane.
      renderArmedBanner(armedResultText(verb, res, src && src.label, tgt && tgt.label, targetOps));
    };
    if (verb === "merge") {
      previewAndBlast("merge", [targetId, feature], say);
    } else if (verb === "move") {
      previewAndBlast("move", [...opIdsFor(feature), targetId], say);
    }
  }

  // Plain click = single-select toggle (clears any multi set). ⌘/ctrl/shift-click = accrete/toggle
  // into the multi set (the VS Code parallel of the TUI's space-select). state.selected stays the
  // "primary" (last-touched) row that drives the per-feature inspector; state.multi is the set the
  // union-closure card + paint read.
  function selectRow(id, additive) {
    if (stagedAction && !applyBusy) cancelStaged(); // navigating away withdraws the question
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
    }
  }

  // Clicking a chunk-car selects its CHECKPOINT (`f-XXXX@n`), the revert unit -- distinct from a
  // row/swatch click (whole feature) or a label click (spotlight). The feature is selected so the
  // inspector shows its checkpoint list + code fold; the matching checkpoint row is highlighted and
  // scrolled into view. Clicking the same car again clears the checkpoint focus. An armed
  // merge/move still targets the feature (a car is just a point on it), matching lane-click.
  function selectCar(car, laneId, ev) {
    if (ev) ev.stopPropagation();
    if (stagedAction && !applyBusy) cancelStaged(); // navigating away withdraws the question
    if (armedVerb) { confirmArmed(laneId); return; }
    if (state.selected !== laneId) {
      state.multi = [laneId];
      state.selected = laneId;
      state.selectedStep = null;
      state.selectedPlanSession = null;
      selectionResult = null;
    }
    state.selectedCheckpoint = state.selectedCheckpoint === car.checkpoint ? null : car.checkpoint;
    if (askedOpen !== state.selectedCheckpoint) askedOpen = null;
    saveState();
    render();
    const row = inspector.querySelector(".checkpoint.selected");
    if (row) row.scrollIntoView({ block: "nearest" });
  }

  // Toggle a checkpoint's highlight from the inspector's checkpoint list (the feature is already
  // selected there). Keeps the gantt car and the inspector row in agreement -- click either, both
  // light up.
  function highlightCheckpoint(ref) {
    if (stagedAction && !applyBusy) cancelStaged(); // navigating away withdraws the question
    state.selectedCheckpoint = state.selectedCheckpoint === ref ? null : ref;
    if (askedOpen !== state.selectedCheckpoint) askedOpen = null;
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

  // ---- the lens
  // ONE held, captioned state, from two sources: a pinned lane (clicking its name) or live search
  // text. Both mean the same thing -- "these lanes, the rest is context" -- so they share the paint,
  // the banner and the way out, and only one can be on at a time (search wins while there is text
  // in the box, because the text is on screen and the pin is not).
  //
  // Search used to do none of this. It filled a dropdown and left the map exactly as it was, so the
  // answer to "where is the thing that formats dates" was a list of names you then had to find by
  // eye in thirty rows -- the map, the one surface that could have answered, stayed silent.
  function lensState() {
    const box = document.getElementById("findBox");
    const query = box ? box.value.trim() : "";
    if (query) {
      const hits = localFindHits(query, (map || {}).nodes, checkpointsByFeature, 500,
                                 ((compose || {}).intent || {}).themes, (grid || {}).commits);
      const ids = new Set();
      for (const h of hits) {
        if (h.feature) ids.add(h.feature);
        // A theme hit is work across lanes; a save hit is a commit with no lane of its own. Both
        // light every lane they reach, or searching for one would dim the whole map.
        if (h.theme) {
          const t = ((((compose || {}).intent) || {}).themes || []).find((x) => x.theme_id === h.theme);
          for (const f of (t && t.feature_span) || []) ids.add(f);
        }
      }
      for (const h of (semanticState && semanticState.query === query ? semanticState.hits : [])) {
        if (h.feature) ids.add(h.feature);
      }
      return { kind: "find", query, ids, neighbors: new Set() };
    }
    if (state.spotlight) {
      return { kind: "pin", query: "", ids: new Set([state.spotlight]),
               neighbors: neighborsOf(state.spotlight) };
    }
    return null;
  }

  function applyLens() {
    const svg = rail.querySelector("svg");
    const lens = lensState();
    renderLensBanner(lens);
    if (!svg) return;
    // A held revert/restore preview owns the field: its dim is deeper and says something else
    // ("these lanes change"), and stacking a lens under it multiplies two opacities into a floor
    // nothing can be read at. The preview's own teardown re-applies whatever lens was pinned.
    if (previewActive) return;
    if (!lens) {
      svg.classList.remove("focus");
      svg.querySelectorAll(".lit, .ctx").forEach((el) => el.classList.remove("lit", "ctx"));
      return;
    }
    svg.classList.add("focus");
    // A lane counts as lit when it IS a hit or when a hit sits inside it -- otherwise searching for
    // a symbol dims the collapsed subsystem that contains it, which is where the reader has to go.
    svg.querySelectorAll(".glane").forEach((el) => {
      const rid = el.getAttribute("data-id");
      const lane = (layout.laneById || {})[rid] || {};
      const inside = (lane.leaves || []).some((f) => lens.ids.has(f));
      el.classList.toggle("lit", lens.ids.has(rid) || inside);
      el.classList.toggle("ctx", lens.neighbors.has(rid));
    });
  }

  // Kept as the old name for the call sites that mean "re-pin after a re-render".
  function applySpotlight() { applyLens(); }

  // What receded, why, and how to get it back. A map with half its rows dimmed and no caption is
  // read as a rendering fault -- the terminal's `sgt log --focus` has said so above its own rows
  // for months and this surface had nothing.
  function renderLensBanner(lens) {
    const el = document.getElementById("lensBanner");
    if (!el) return;
    if (!lens) { el.hidden = true; el.innerHTML = ""; return; }
    el.hidden = false;
    el.innerHTML = "";
    el.classList.toggle("stacked", !!themeMarks); // sit under the theme caption, not on top of it
    const lanes = (layout.lanes || []).filter((l) => !l.isMeta).length;
    const name = document.createElement("span");
    name.className = "theme-banner-name";
    const note = document.createElement("span");
    note.className = "theme-banner-note";
    if (lens.kind === "find") {
      name.textContent = `◉ ${lens.query}`;
      note.textContent = lens.ids.size
        ? `${lens.ids.size} of ${lanes} features match — the rest are dimmed, not hidden`
        : "nothing in this graph matches — press ⏎ to search by meaning";
    } else {
      const label = ((byId(lens.ids.values().next().value) || {}).label) || "this feature";
      name.textContent = `◉ ${label}`;
      note.textContent = lens.neighbors.size
        ? `pinned, with the ${lens.neighbors.size} features that change alongside it`
        : "pinned — nothing else changes alongside it";
    }
    el.append(name, note);
    const clear = document.createElement("button");
    clear.textContent = "✕ Show everything";
    clear.title = lens.kind === "find" ? "clear the search (Esc)" : "unpin (click the name again)";
    clear.addEventListener("click", () => {
      if (lens.kind === "find") {
        const box = document.getElementById("findBox");
        if (box) box.value = "";
        semanticState = null;
        const results = document.getElementById("findResults");
        if (results) results.hidden = true;
      } else {
        state.spotlight = null;
        saveState();
      }
      render();
    });
    el.appendChild(clear);
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

  // ---- hover-intent
  // Every hover preview shells out: the host runs `sgt <verb> --emit` in a subprocess to compute the
  // consequence. Fired straight off `mouseenter` that meant one process per row *crossed* -- a sweep
  // down forty lanes to reach the fortieth started forty of them, and every result that landed
  // painted, so the consequence flickered through each row on the way to the one being asked about.
  // Resting on a row is the intent; crossing it is not. One timer, restarted on each enter and
  // cancelled on each leave, so only the row under a settled cursor costs anything.
  const HOVER_INTENT_MS = 130;
  let hoverTimer = null;

  function cancelHoverIntent() {
    if (hoverTimer !== null) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
  }

  // A staged confirm suspends hover previews entirely (its consequence paint must not be
  // repainted under the reader). The gate is injected (`setHoverGate`, below the slice) rather
  // than read directly so this block stays self-contained for the node harness.
  let hoverGate = null;

  function setHoverGate(fn) {
    hoverGate = fn;
  }

  function onHoverIntent(fn) {
    if (hoverGate && hoverGate()) return;
    cancelHoverIntent();
    hoverTimer = setTimeout(() => {
      hoverTimer = null;
      fn();
    }, HOVER_INTENT_MS);
  }
  // ---- end-hover-intent
  setHoverGate(() => !!stagedAction);

  // ---- armed-banner
  // "Merge into…" and "Move ops…" arm a mode: the next lane click is a target, not a selection. The
  // only sign of that mode was `cursor: crosshair` on the lanes -- a mode that never states itself,
  // which is how a reader who looked away comes back, clicks a lane meaning to select it, and merges
  // two features instead. The banner says which verb is waiting, on what, what a click does now, and
  // the way out.
  function armedBannerText(armed, label) {
    if (!armed) return null;
    const subject = `"${label || armed.feature}"`;
    const what = armed.verb === "merge"
      ? `Merge ${subject} into which lane?`
      : `Move ${subject}'s edits into which lane?`;
    return `${what}  ·  click one, or Esc to cancel`;
  }
  // ---- end-armed-banner

  // ---- staged-summary (test slice boundary)
  // The in-graph confirmation's one sentence. A native modal answers "are you sure?" with a wall
  // of prose in the middle of the screen; the graph behind it already knows how to SHOW the
  // consequence. So a Revert/Restore click now stages instead: the preview is held on the field
  // and the confirm bar states the consequence in one line -- confirming is reading the picture,
  // not re-reading a paragraph. Pure over the staged payload so the node harness can hold it.
  function stagedSummaryText(staged) {
    const res = staged.res;
    if (!res) return "computing consequence…";
    if (res.ok === false) return humanizeRefusal(res.message) || `sgt refuses this ${staged.verb}.`;
    const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
    if (staged.kind === "multi") {
      return staged.summary || "computing union closure…";
    }
    if (staged.kind === "split") {
      return splitPreviewText(res).message;
    }
    if (staged.kind === "backto") {
      const n = (staged.refs || []).length;
      const head = `removes ${plural(n, "later checkpoint")} · ${plural(staged.opCount || 0, "edit")}`;
      const blast = staged.blastCount || 0;
      const tail = staged.blastDone
        ? (blast ? `touches ${plural(blast, "other feature")}` : "no other feature touched")
        : (blast ? `touches ≥${blast} other feature(s) · still checking…` : "checking other features…");
      return `${head} · ${tail} · this checkpoint stays`;
    }
    // Prefer the backend's own so-what headline -- one vocabulary across CLI, TUI and here.
    const soWhat = res.so_what || (res.focus && res.focus.so_what);
    if (soWhat) return soWhat;
    const removed = (res.removed || []).length;
    const added = (res.added || []).length;
    const files = Object.keys(res.files || {}).length;
    const others = (res.affected || []).filter((r) => r.feature_id !== staged.targetId).length;
    return [
      staged.verb === "restore" ? `brings back ${plural(added, "edit")}` : `${plural(removed, "edit")} come out`,
      files ? `${plural(files, "file")} rewritten` : "no files change",
      others ? `${plural(others, "other feature")} affected` : "no other feature touched",
    ].join(" · ");
  }
  // ---- end-staged-summary

  // ---- staged-confirm (DOM half)
  const APPLY_PHASE_LABEL = {
    checking: "checking consequences",
    applying: "rewriting + committing",
    refreshing: "rebuilding the graph",
  };

  function stageAction(staged) {
    // Entering a staged confirm replaces any armed merge/move mode and any prior stage.
    if (armedVerb) {
      armedVerb = null;
      rail.classList.remove("arming");
      renderArmedBanner();
    }
    if (stagedAction) { stagedAction = null; clearGhosts(); }
    setPreviewContext(null); // the confirm bar carries the sentence now, not the hover pill
    stagedAction = staged;
    applyBusy = null;
    renderConfirmBar();
    if (staged.kind === "backto") {
      paintBackToCars(staged.targetId, staged.refs);
      chainBackToPreviews(staged, 0, new Set());
      return;
    }
    if (staged.kind === "multi") {
      return; // the union closure is already fetched (selectionResult) and painted by the caller
    }
    if (staged.kind === "split") {
      beginPreviewPending(staged.targetId, "previewing split…");
      const seq = ++previewSeq;
      vscode.postMessage({ type: "previewSplit", featureId: staged.ref, seq });
      pendingPreview = {
        seq,
        onResult: (res) => {
          clearPreviewPending();
          if (stagedAction !== staged) return;
          staged.res = res;
          if (res && res.ok) {
            paintClosure({ target: staged.targetId, blast: [], foundation: [] });
            paintSplitPreview(staged.targetId, res);
          }
          renderConfirmBar();
        },
      };
      return;
    }
    // The click usually follows the hover that already computed this preview, so the host answers
    // from its cache and the paint holds without a visible wait; a cold click shows the pending
    // shimmer for exactly as long as the subprocess takes.
    beginPreviewPending(staged.targetId, `previewing ${staged.verb}…`);
    requestPreview(staged.verb, [staged.ref], (res) => {
      clearPreviewPending();
      if (stagedAction !== staged) return;
      staged.res = res;
      if (res && res.ok) {
        // `staged.targetId` is already the feature id; `res.target` for a chapter is the raw
        // `<f>@<n>` ref, which matches no row.
        const focus = res.focus;
        if (focus && focus.nodes && focus.nodes.length) enterPreviewMode(focus, staged.targetId, staged.verb);
        else paintClosure(classifyAffected(res, staged.targetId));
        paintCarPreview(classifyCarImpact(res.removed, res.added, segmentsOf(compose),
                                          res.target_ops, staged.verb));
      }
      renderConfirmBar();
    });
  }

  // "Revert to here"'s cross-feature consequence, accumulated progressively: one preview per later
  // chapter, requested strictly in sequence (the host's latest-wins guard would drop a parallel
  // burst), the union of their blasts painted and counted as each answer lands. Intermediate
  // feedback -- the number firms up in front of the reader instead of a spinner until omniscience.
  function chainBackToPreviews(staged, i, blast) {
    if (stagedAction !== staged) return;
    if (i >= staged.refs.length) {
      staged.blastDone = true;
      renderConfirmBar();
      return;
    }
    requestPreview("revert", [staged.refs[i]], (res) => {
      if (stagedAction !== staged) return;
      if (res && res.ok) {
        for (const r of res.affected || []) {
          if (r.direction !== "foundation" && r.feature_id !== staged.targetId) blast.add(r.feature_id);
        }
      }
      staged.blastCount = blast.size;
      renderConfirmBar();
      // Each answer adds its dependency cars in other lanes -- the blast firming up chunk by chunk.
      paintCarPreview(classifyCarImpact(res.removed, res.added, segmentsOf(compose),
                                        res.target_ops, staged.verb));
      for (const f of blast) {
        const row = findRow(f);
        if (row) row.classList.add("ghost-blast");
      }
      renderOffscreenPills([staged.targetId, ...blast]);
      chainBackToPreviews(staged, i + 1, blast);
    });
  }

  // The instant half of "revert to here": the cars that would come out are exactly this lane's
  // later chapters, and the layout already owns them -- zero round-trips, painted on the
  // keystroke, in the same draining-to-hollow grammar every revert preview uses.
  function paintBackToCars(targetId, refs) {
    const gone = new Set(refs);
    const impacts = segmentsOf(compose)
      .filter((sg) => gone.has(sg.checkpoint))
      .map((sg) => ({ checkpoint: sg.checkpoint, featureId: sg.feature_id, dir: "out", coverage: "full" }));
    paintCarPreview(impacts);
    const row = findRow(targetId);
    if (row) row.classList.add("ghost-target");
  }

  function applyStagedAction() {
    if (!stagedAction || applyBusy) return;
    const staged = stagedAction;
    if (staged.res && staged.res.ok === false) return; // refused: nothing to apply
    applyBusy = { verb: staged.verb, phase: "checking", detail: null };
    renderConfirmBar();
    if (staged.kind === "backto" || staged.kind === "multi") {
      vscode.postMessage({
        type: "revertSequence",
        refs: staged.refs.slice(),
        label: staged.kind === "backto" ? staged.label : undefined,
        noun: staged.kind === "multi" ? "feature" : "chapter",
      });
    } else {
      vscode.postMessage({ type: "applyStaged", verb: staged.verb, ref: staged.ref });
    }
  }

  function cancelStaged() {
    if (!stagedAction) return;
    stagedAction = null;
    applyBusy = null;
    renderConfirmBar();
    clearGhosts();
  }

  // The host's phase reports while a staged action runs: checking → applying → refreshing, then
  // done / failed / cancelled. Progress paints in the bar -- where the decision was taken -- not
  // in a corner toast the eye has already left.
  function onApplyProgress(msg) {
    if (!stagedAction) return;
    if (msg.phase === "done") {
      // Settle only if something actually applied: a flow that ended during "checking" (e.g.
      // "removes nothing here") changed nothing, and a receipt for it would flash a lie later.
      const applied = applyBusy && applyBusy.phase !== "checking";
      pendingSettle = applied && stagedAction.targetId ? [stagedAction.targetId] : [];
      // What happened, left on the bar where the decision was taken. The host used to say this in
      // a VS Code notification instead, which stacked with every other notification the same click
      // produced and covered the corner of the timeline the reader was watching. The bar is the
      // place the question was asked, so it is the place the answer belongs.
      showApplyReceipt(msg.verb, stagedAction.label || stagedAction.ref, msg.detail);
      stagedAction = null;
      applyBusy = null;
      renderConfirmBar();
      clearGhosts();
      return;
    }
    if (msg.phase === "cancelled") {
      // The host-side flow bailed (a dismissed dependents QuickPick, no target): back to the
      // staged state, where Apply and Esc are both still live.
      applyBusy = null;
      renderConfirmBar();
      return;
    }
    applyBusy = { verb: msg.verb, phase: msg.phase, detail: msg.detail || null };
    renderConfirmBar();
  }

  // The receipt an applied action leaves behind: the same bar, for a few seconds, saying what it
  // did. Long enough to read a sentence and short enough that it is gone before it becomes chrome.
  let applyDone = null;
  let applyDoneTimer = null;
  const RECEIPT_MS = 7000;

  function showApplyReceipt(verb, label, detail) {
    if (!detail) return;
    applyDone = { verb, label, detail };
    clearTimeout(applyDoneTimer);
    applyDoneTimer = setTimeout(() => { applyDone = null; renderConfirmBar(); }, RECEIPT_MS);
  }

  function dismissReceipt() {
    clearTimeout(applyDoneTimer);
    applyDone = null;
    renderConfirmBar();
  }

  function renderConfirmBar() {
    if (!confirmBar) return;
    confirmBar.innerHTML = "";
    const staged = stagedAction;
    confirmBar.hidden = !staged && !applyDone;
    if (!staged) {
      if (!applyDone) return;
      const head = el("div", "confirm-head");
      head.appendChild(el("span", "confirm-verb done", "Done"));
      head.appendChild(el("span", "confirm-target", applyDone.label));
      confirmBar.appendChild(head);
      const line = el("div", "confirm-progress done");
      line.appendChild(el("span", "confirm-detail", applyDone.detail));
      const dismiss = el("button", "confirm-btn", "Dismiss");
      dismiss.addEventListener("click", dismissReceipt);
      line.appendChild(dismiss);
      confirmBar.appendChild(line);
      return;
    }
    applyDone = null; // a new question replaces the last answer

    const head = el("div", "confirm-head");
    head.appendChild(el("span",
      "confirm-verb" + (staged.verb === "restore" ? " restore" : staged.verb === "split" ? " split" : ""),
      staged.kind === "backto" ? "Revert to"
        : staged.verb === "restore" ? "Restore"
        : staged.verb === "split" ? "Split" : "Revert"));
    // (a multi stage reads "Revert · N selected features" through the same two spans)
    head.appendChild(el("span", "confirm-target", staged.label || staged.ref));
    confirmBar.appendChild(head);

    if (applyBusy) {
      if (applyBusy.phase === "failed") {
        const fail = el("div", "confirm-progress failed");
        fail.appendChild(el("span", "confirm-fail", applyBusy.detail || "failed"));
        const dismiss = el("button", "confirm-btn", "Dismiss");
        dismiss.addEventListener("click", cancelStaged);
        fail.appendChild(dismiss);
        confirmBar.appendChild(fail);
        return;
      }
      // The three real stages, named, current one lit. Not a spinner: a spinner says "busy",
      // this says busy WITH WHAT, and how far along.
      const prog = el("div", "confirm-progress");
      const order = ["checking", "applying", "refreshing"];
      const at = order.indexOf(applyBusy.phase);
      order.forEach((ph, i) => {
        prog.appendChild(el("span",
          "confirm-step" + (i < at ? " done" : i === at ? " live" : ""), APPLY_PHASE_LABEL[ph]));
      });
      if (applyBusy.detail) prog.appendChild(el("span", "confirm-detail", applyBusy.detail));
      confirmBar.appendChild(prog);
      return;
    }

    confirmBar.appendChild(el("div", "confirm-summary", stagedSummaryText(staged)));

    const actions = el("div", "confirm-actions");
    // The diff exists only for the exact ideal edits (one emit-able ref): feature/chapter scope.
    if (staged.kind === "feature" || staged.kind === "chapter") {
      const diff = el("button", "confirm-btn subtle", "Open diff");
      diff.title = "Open the exact before → after in editor tabs";
      diff.addEventListener("click", () =>
        vscode.postMessage({ type: "openStagedDiff", verb: staged.verb, ref: staged.ref }));
      actions.appendChild(diff);
    }
    const cancel = el("button", "confirm-btn", "Cancel");
    cancel.title = "Esc";
    cancel.addEventListener("click", cancelStaged);
    actions.appendChild(cancel);
    const refused = staged.res && staged.res.ok === false;
    const apply = el("button",
      "confirm-btn confirm-apply"
        + (staged.verb === "restore" ? " restore" : staged.verb === "split" ? " split" : ""),
      staged.verb === "restore" ? "Restore" : staged.verb === "split" ? "Split" : "Revert");
    // Disabled until the consequence is known (usually instant off the hover's cached preview):
    // an Apply that runs before the picture exists would be the old blind modal wearing new paint.
    apply.disabled = !staged.res || !!refused;
    apply.addEventListener("click", applyStagedAction);
    actions.appendChild(apply);
    confirmBar.appendChild(actions);
  }
  // ---- end-staged-confirm

  function clearGhosts() {
    // A staged confirm OWNS the current consequence paint: the mouseleave that would normally
    // clear a hover must not strip the picture the user is being asked to approve. Cancel/Apply
    // tear it down through cancelStaged/onApplyProgress, which null stagedAction first.
    if (stagedAction) return;
    rail.querySelectorAll(
      ".glane.ghost-blast, .glane.ghost-target, .glane.ghost-foundation, " +
      ".rail-row.ghost-blast, .rail-row.ghost-target, .rail-row.ghost-foundation").forEach((el) => {
      el.classList.remove("ghost-blast", "ghost-target", "ghost-foundation");
    });
    clearCarPreview();
    cancelHoverIntent(); // a preview still waiting out the hover delay is abandoned here too
    clearPreviewPending(); // ...and its pending shimmer/pill, wherever it had got to
    clearOffscreenPills();
    clearPreviewRefusal(); // a refusal overlay clears on the same mouseleave path
    setPreviewContext(null); // ...as does a sentence pill set without a morph behind it (split)
    exitPreviewMode(); // a held Focus & Morph overlay tears down on the same mouseleave path
  }

  function requestPreview(verb, args, onResult) {
    const seq = ++previewSeq;
    vscode.postMessage({ type: "previewVerb", verb, args, seq });
    pendingPreview = { seq, onResult };
  }

  // ---- pending-ack
  // The instant acknowledgement layer. Every consequence preview shells out and queues behind the
  // store flock, so the honest answer can be a second or more away -- and a system that paints
  // nothing in that window reads as one that did not hear the hover. The row under the cursor
  // takes a quiet shimmer and the pill states what is being computed. Both appear only after a
  // grace period: feedback for work that finishes inside ~200ms would be flicker, not information.
  const PENDING_GRACE_MS = 180;
  let previewPendingTimer = null;
  let previewPendingRow = null;

  function beginPreviewPending(targetId, say) {
    clearPreviewPending();
    previewPendingTimer = setTimeout(() => {
      previewPendingTimer = null;
      const row = targetId && findRow(targetId);
      if (row) {
        row.classList.add("preview-pending");
        previewPendingRow = row;
      }
      setPreviewContext(say, true);
    }, PENDING_GRACE_MS);
  }

  function clearPreviewPending() {
    if (previewPendingTimer !== null) {
      clearTimeout(previewPendingTimer);
      previewPendingTimer = null;
    }
    if (previewPendingRow) {
      previewPendingRow.classList.remove("preview-pending");
      previewPendingRow = null;
    }
    if (previewContext.classList.contains("pending")) setPreviewContext(null);
  }
  // ---- end-pending-ack

  // Every hover-preview site wants the same thing: show the consequence if the preview came back
  // ok, do nothing otherwise. The target is args[0] (revert/restore take one feature). When the
  // backend hands back a `focus` subgraph (a feature map is built) and we're not mid-arming, use the
  // richer deep-dim morph; otherwise fall back to the flat three-role ghost paint.
  // `say` is the armed path's only addition: a function(res) called after the paint. Only
  // `previewArmed` passes one, because it is the only caller whose two operands are both known at
  // hover time and therefore the only one that can name the result.
  function previewAndBlast(verb, args, say) {
    // A checkpoint ref (`f-xxx@n`) shimmers its feature's row -- rows are keyed by feature id.
    beginPreviewPending(String(args[0]).split("@")[0], `previewing ${verb}…`);
    requestPreview(verb, args, (res) => {
      clearPreviewPending();
      if (!res || !res.ok) {
        // A blocked restore -- the symbol has a competing live version, so sgt refuses. Surface the
        // two ways out in the preview overlay instead of silently doing nothing.
        if (verb === "restore" && res && res.forked) showRestoreRefusal(res, args[0]);
        // `say` marks the armed path, whose whole purpose is telling good targets from bad before the
        // click. Everything it refuses used to preview as silence -- indistinguishable from a preview
        // still in flight -- and then fail in a toast after the choice was already made.
        else if (say) showRefusal((res && res.message) || `Can't ${verb} into that lane.`);
        return;
      }
      // `say` is set only by the armed path (every other caller is guarded by `!armedVerb`), and that
      // path paints its own two endpoints -- see `previewArmed`.
      if (say) { say(res); return; }
      const focus = res.focus;
      // Rows are keyed by feature id: a `<f>@<n>` checkpoint ref must paint its feature's row,
      // never miss every lane and list the acted-on feature among its own collateral.
      const targetRow = String(res.target || args[0]).split("@")[0];
      if (focus && focus.nodes && focus.nodes.length) {
        enterPreviewMode(focus, targetRow, staged.verb);
      } else {
        paintClosure(classifyAffected(res, targetRow));
      }
      // The chunk-grain layer over the field treatment: the exact cars this verb changes, drawn
      // as the state they will end in.
      paintCarPreview(classifyCarImpact(res.removed, res.added, segmentsOf(compose),
                                        res.target_ops, verb));
    });
  }

  // ─── Find ────────────────────────────────────────────────────────────────────────────────────
  // Describe the thing; get the ids. Every other route into this graph needs a name you already
  // know, which is the one thing someone reading unfamiliar history does not have. A hit is a
  // starting point, not an action: clicking one reveals it on the rail and selects it, and every
  // verb stays where it was. Search that could change something is search nobody runs.
  let findSeq = 0;
  let latestFindSeq = 0;

  // ---- local-find (test slice boundary)
  // The instant rung of search: substring matching over what the client already holds -- feature
  // labels and ids, chapter intents, member symbols. Zero round-trips, so results land on the
  // keystroke; `sgt find`'s meaning rung layers in underneath on Enter. Scoring is deliberately
  // simple (prefix > word-start > substring; features nudged above symbols on ties) and capped, so
  // the dropdown stays a glance, not a page.
  function localFindHits(query, nodes, segsByFeature, cap, themes, commits) {
    cap = cap || 12;
    const q = String(query || "").trim().toLowerCase();
    if (!q) return [];
    const BOUNDARY = new Set([" ", "-", "_", "/", ".", ":"]);
    const score = (text) => {
      const t = String(text || "").toLowerCase();
      const i = t.indexOf(q);
      if (i < 0) return -1;
      if (i === 0) return 3;
      if (BOUNDARY.has(t[i - 1])) return 2;
      return 1;
    };
    const hits = [];
    for (const n of nodes || []) {
      if (n.kind !== "feature") continue;
      const sf = Math.max(score(n.label), score(n.id));
      if (sf > 0) {
        hits.push({ kind: "feature", label: n.label || n.id,
                    detail: `${n.op_count || 0} op(s)`, feature: n.id, s: sf + 0.2 });
      }
      for (const m of n.members || []) {
        const sym = m.split("::").pop();
        const ss = score(sym);
        // `symbol` and `file` ride along so the click can open the code rather than only light up
        // the lane the code lives in. A result that says `metrics.py` and then does not take you
        // to `metrics.py` is the search saying it found something and then declining to show it.
        if (ss > 0) {
          hits.push({ kind: "symbol", label: sym,
                      detail: `${m.split("::")[0]} · in ${n.label || n.id}`, feature: n.id,
                      symbol: m, file: m.split("::")[0], s: ss - 0.2 });
        }
      }
    }
    for (const fid in segsByFeature || {}) {
      for (const seg of segsByFeature[fid]) {
        const sc = score(seg.intent);
        if (sc > 0) {
          // Not `seg.checkpoint`: that is the handle (`f-0252…@1`), which is the one string on the
          // row a reader cannot use to decide whether this is their result. The lane it sits in is.
          const owner = (nodes || []).find((n) => n.id === fid);
          hits.push({ kind: "checkpoint", label: seg.intent,
                      detail: `in ${(owner && owner.label) || fid}`,
                      feature: fid, checkpoint: seg.checkpoint, s: sc });
        }
      }
    }
    // Cross-feature themes: the one unit that spans lanes. Ranked slightly above features on a
    // tie -- a query matching a theme's name is usually asking about the work, not one lane.
    for (const t of themes || []) {
      if ((t.feature_span || []).length < 2 || !t.label || t.label === "(unwitnessed)") continue;
      const st = Math.max(score(t.label), score(t.theme_id));
      if (st > 0) {
        hits.push({ kind: "work", label: t.label,
                    detail: `across ${t.feature_span.length} features`, theme: t.theme_id, s: st + 0.3 });
      }
    }
    // Saves, by subject or sha prefix -- the tokens `sgt find` and the terminal print. A save has
    // no single lane, so its hit carries the commit index and the click flashes every lane it
    // touched (revealSave) instead of selecting nothing.
    for (const c of commits || []) {
      if (c.bookkeeping) continue;
      const sha = String(c.sha || "");
      const bySha = q.length >= 4 && sha.toLowerCase().startsWith(q) ? 3 : -1;
      const sc = Math.max(bySha, score(c.subject));
      if (sc > 0) {
        hits.push({ kind: "save", label: c.subject || sha.slice(0, 7), detail: sha.slice(0, 7),
                    commitIndex: c.index, s: sc - 0.1 });
      }
    }
    hits.sort((a, b) => b.s - a.s || String(a.label).localeCompare(String(b.label)));
    const seen = new Set();
    const out = [];
    for (const h of hits) {
      const key = `${h.kind}\u0000${h.label}\u0000${h.feature}`;
      if (seen.has(key)) continue; // one row per (kind, label, feature)
      seen.add(key);
      out.push(h);
      if (out.length >= cap) break;
    }
    return out;
  }
  // ---- end-local-find

  function initFind() {
    const box = document.getElementById("findBox");
    const results = document.getElementById("findResults");
    if (!box || !results) return;

    // Typing answers instantly from the local rung; Enter adds the meaning rung (a CLI
    // round-trip). The dropdown never sits blank while something is already known.
    box.addEventListener("input", () => {
      semanticState = null; // a changed query orphans any older meaning results
      renderFind(box.value.trim());
      applyLens(); // and the map answers too, not just the dropdown
    });

    box.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") {
        box.value = "";
        results.hidden = true;
        semanticState = null;
        box.blur();
        applyLens(); // clearing the text is what un-dims the map, so it has to run here too
        return;
      }
      if (ev.key !== "Enter") return;
      const query = box.value.trim();
      if (!query) {
        results.hidden = true;
        return;
      }
      const seq = ++findSeq;
      latestFindSeq = seq;
      semanticState = { query, pending: true, hits: [], mode: null, message: null };
      renderFind(query);
      applyLens();
      vscode.postMessage({ type: "find", query, seq });
    });

    document.addEventListener("click", (ev) => {
      if (!results.hidden && !results.contains(ev.target) && ev.target !== box) results.hidden = true;
    });
  }

  // One result row. The kind is a badge rather than a word in the same ink as the name: a reader
  // scanning ten results is sorting them by kind first, and a column of grey nouns does not sort.
  function findHitRow(hit) {
    const row = el("div", `find-hit find-hit-${hit.kind}`);
    row.appendChild(el("span", "find-kind", FIND_KIND_GLYPH[hit.kind] || "•"));
    const text = el("div", "find-text");
    text.appendChild(el("span", "find-label", hit.label));
    if (hit.detail) text.appendChild(el("span", "find-detail", hit.detail));
    row.appendChild(text);
    // What clicking does, on the row, before it is clicked. Every kind lands somewhere different
    // and none of them said so, so the only way to learn what a result was for was to click it.
    row.appendChild(el("span", "find-go", FIND_KIND_ACTION[hit.kind] || ""));
    // The dropdown is only as wide as the box above it, so a commit subject clips. The tooltip
    // carries the whole row -- a result you cannot read the name of is not a result.
    row.title = [hit.label, hit.detail, FIND_KIND_ACTION[hit.kind]].filter(Boolean).join(" — ");
    row.addEventListener("click", () => {
      const results = document.getElementById("findResults");
      if (results) results.hidden = true;
      // Every hit that belongs somewhere takes you there. A chapter hit lands on its exact car;
      // a theme enters the cross-lane focus; a save flashes every lane its commit touched.
      if (hit.theme) { setThemeFocus(hit.theme); return; }
      if (hit.commitIndex != null) { revealSave(hit.commitIndex, hit.label); return; }
      if (hit.feature) revealFeature(hit.feature);
      if (hit.checkpoint) {
        state.selectedCheckpoint = hit.checkpoint;
        saveState();
        render();
      }
      // A symbol result is a piece of code. Lighting its lane says which work it belongs to;
      // opening it says what it is. Both, in that order, so the map keeps its place.
      if (hit.file) vscode.postMessage({ type: "openFile", path: hit.file, symbol: hit.symbol });
    });
    return row;
  }

  const FIND_KIND_GLYPH = {
    work: "◆", feature: "▤", checkpoint: "▸", symbol: "ƒ", save: "◇",
  };

  const FIND_KIND_ACTION = {
    work: "show the lanes this work touched",
    feature: "go to this row on the timeline",
    checkpoint: "go to this checkpoint",
    symbol: "open this code",
    save: "show what this save touched",
  };

  function renderFind(query) {
    const results = document.getElementById("findResults");
    if (!results) return;
    results.innerHTML = "";
    if (!query) {
      results.hidden = true;
      return;
    }
    results.hidden = false;
    const local = localFindHits(query, map.nodes, checkpointsByFeature, 12,
                                ((compose || {}).intent || {}).themes, (grid || {}).commits);
    if (local.length) {
      results.appendChild(el("div", "find-section", "matching by name"));
      for (const h of local) results.appendChild(findHitRow(h));
    }
    if (semanticState && semanticState.query === query) {
      results.appendChild(el("div", "find-section", "by meaning"));
      if (semanticState.pending) {
        results.appendChild(skeletonRows(3, "find"));
      } else if (!semanticState.hits.length) {
        results.appendChild(el("div", "find-note", semanticState.message || "nothing matched"));
      } else {
        const dupe = new Set(local.map((h) => `${h.kind}\u0000${h.feature}\u0000${h.label}`));
        let shown = 0;
        for (const hit of semanticState.hits) {
          if (dupe.has(`${hit.kind}\u0000${hit.feature}\u0000${hit.label}`)) continue;
          results.appendChild(findHitRow(hit));
          shown++;
        }
        if (!shown) results.appendChild(el("div", "find-note", "nothing beyond the matches above"));
        // A word-overlap answer and a meaning answer look identical in a list, and only one of
        // them means "there is nothing like this here" when it comes back short.
        if (semanticState.mode === "lexical") {
          results.appendChild(el("div", "find-note", "matched on words, not meaning"));
        }
      }
    } else if (local.length) {
      results.appendChild(el("div", "find-note", "press ⏎ to also search by meaning"));
    } else {
      results.appendChild(el("div", "find-note", "no name matches — press ⏎ to search by meaning"));
    }
  }

  function renderFindResults(msg) {
    if (msg.seq !== latestFindSeq || !semanticState) return; // a slower earlier query landing late
    semanticState.pending = false;
    semanticState.mode = msg.mode || null;
    semanticState.message = msg.message || null;
    const idxOfSha = new Map(((grid && grid.commits) || []).map((c) => [String(c.sha || "").slice(0, 8), c.index]));
    semanticState.hits = (msg.hits || []).map((h) => {
      // `sgt find` labels a symbol with its whole `path::name`, and the local rung labels the same
      // symbol with its bare name. Two spellings of one thing read as two results, and the dedupe
      // below (keyed on the label) could not see they were the same. One shape for both rungs:
      // the name is the label, the file is part of the detail, and the full symbol travels
      // separately so the click can open it.
      const isSym = h.kind === "symbol";
      const full = String(h.id || "");
      const file = isSym && full.includes("::") ? full.split("::")[0] : null;
      return {
        kind: h.kind,
        label: isSym && file ? full.split("::").pop() : h.label,
        detail: isSym ? [file, h.detail || ""].filter(Boolean).join(" · ") : (h.detail || ""),
        feature: h.feature || (h.kind === "feature" ? h.id : null),
        symbol: isSym ? full : undefined,
        file: file || undefined,
        // `sgt find` names a save by its sha; joining it to a commit index is what makes the hit
        // land somewhere instead of being a dead row.
        commitIndex: h.kind === "save" ? idxOfSha.get(String(h.id || h.detail || "").slice(0, 8)) : undefined,
      };
    });
    renderFind(semanticState.query);
    applyLens(); // the meaning rung usually reaches lanes the substring rung did not
  }

  // ---- ask derivations (test slice boundary)
  //
  // The one ask to put on a single line: the one accounting for most of the chapter, latest first
  // on a tie -- a correction is the standing word. Same rule as `sgt.intent.stint.dominant_ask`,
  // so the tooltip, the panel and the CLI all quote the same sentence back.
  function dominantAsk(asks) {
    if (!asks || !asks.length) return null;
    return asks.reduce((best, a) =>
      !best || a.claimed > best.claimed || (a.claimed === best.claimed && (a.ts || 0) > (best.ts || 0))
        ? a : best, null);
  }

  // "9 days ago" for a captured timestamp (seconds since the epoch, as the store keeps it).
  function askAge(ts) {
    if (!ts) return "";
    const secs = Math.max(0, Date.now() / 1000 - ts);
    if (secs < 90) return "just now";
    if (secs < 5400) return `${Math.round(secs / 60)} min ago`;
    if (secs < 172800) return `${Math.round(secs / 3600)}h ago`;
    return `${Math.round(secs / 86400)}d ago`;
  }

  // Whose words, when, and -- only when it distinguishes anything -- how much of the chapter they
  // account for. On a single-ask chapter that share is every edit in it, so the clause would be a
  // second line saying what the card above already said; the prompt's length goes there instead,
  // which is what the reader needs to decide whether to open it.
  function askedMeta(ask, index, withClaim) {
    const bits = [ask.source];
    const age = askAge(ask.ts);
    if (age) bits.push(age);
    if (withClaim && ask.claimed) {
      bits.push(`accounts for ${ask.claimed} edit${ask.claimed === 1 ? "" : "s"}`);
    }
    if (ask.trimmed && !withClaim) bits.push(`${ask.chars} characters in full`);
    return (index ? `${index}. ` : "") + bits.join(" · ");
  }

  // What is behind the control, in a label short enough to stay an inline control: a longer prompt
  // or more asks. Full-width, it read as a second action equal to "Revert this checkpoint", which
  // is not a pair a panel should offer at the same weight. The length of what it opens is on the
  // provenance line above it.
  function askedMoreLabel(top, more) {
    if (more > 0) return `Read all ${more + 1} asks`;
    return "Read the whole prompt";
  }

  // ---- end-ask-derivations

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  // An agent's revert or restore, painted as it happens.
  //
  // Deliberately the same paint as a hover: the participant should learn one
  // vocabulary for "this is what that would do", not one for their own mouse and
  // another for the assistant. The only additions are a line saying who asked,
  // since nothing else on screen would say so, and a hold after the action
  // finishes -- an agent's call returns in well under a second, and a paint that
  // vanished that fast would be a flicker nobody could read.
  let agentActionTimer = null;

  function showAgentAction(msg) {
    if (!msg || !msg.verb || !msg.ref) return;
    if (agentActionTimer) {
      clearTimeout(agentActionTimer);
      agentActionTimer = null;
    }
    if (msg.state === "failed") {
      clearGhosts();
      setAgentBanner(null);
      return;
    }

    setAgentBanner(
      msg.state === "done"
        ? `the assistant ${msg.emit ? "previewed" : "ran"} ${msg.verb} on ${shortRef(msg.ref)}`
        : `the assistant is running ${msg.verb} on ${shortRef(msg.ref)}`
    );
    // Not `previewAndBlast(verb, [msg.ref])`: an agent's ref is often a label
    // ("Waitlist Join"), and painting with the raw ref as the target id would
    // leave the target lane unmarked and list it among its own collateral. The
    // preview result carries the resolved feature id; paint with that.
    requestPreview(msg.verb, [msg.ref], (res) => {
      if (!res || !res.ok) return;
      const resolved = String(res.target || msg.ref).split("@")[0];
      const focus = res.focus;
      if (!armedVerb && focus && focus.nodes && focus.nodes.length) {
        enterPreviewMode(focus, resolved, msg.verb);
      } else {
        paintClosure(classifyAffected(res, resolved));
      }
      paintCarPreview(classifyCarImpact(res.removed, res.added, segmentsOf(compose),
                                        res.target_ops, msg.verb));
    });

    if (msg.state === "done") {
      agentActionTimer = setTimeout(() => {
        clearGhosts();
        setAgentBanner(null);
        agentActionTimer = null;
      }, 6000);
    }
  }

  function shortRef(ref) {
    return String(ref).startsWith("f-") ? String(ref).slice(0, 10) : String(ref);
  }

  function setAgentBanner(text) {
    let el = document.getElementById("agent-banner");
    if (!text) {
      if (el) el.remove();
      return;
    }
    if (!el) {
      el = document.createElement("div");
      el.id = "agent-banner";
      el.className = "agent-banner";
      document.body.appendChild(el);
    }
    el.textContent = text;
  }

  // One non-interactive fold: ask the host to fold the current composition and hand back only the
  // files under this feature's directory. No longer fired on every selection -- the change panel
  // is what a selection asks for, and this is its fallback when there is no projection to read, so
  // it is requested from inside the render that will draw the skeleton for it. Stale responses (an
  // older selection's fold landing after a newer one) are dropped by sequence number, same pattern
  // as `requestPreview`.
  function requestFold(featureId) {
    if (pendingFold && pendingFold.featureId === featureId) return;
    const seq = ++foldSeq;
    pendingFold = { seq, featureId };
    vscode.postMessage({ type: "requestFold", featureId, ref: state.compositionRef || "HEAD", seq });
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

      // The member count, not the id. `f-05845707a7cefe2e0e42f8e3f99484f8bc69b6f5e966fbabef90…`
      // led this line: 64 hex characters of internal handle, wrapped over three lines, directly
      // under the name -- so the most prominent thing about a piece of work was the one string
      // about it a reader can neither use nor remember. Nothing on this surface asks anyone to
      // type an id; the panels that need one carry it themselves.
      const meta = document.createElement("div");
      meta.className = "detail-meta";
      meta.textContent = `${node.size} member(s)`;
      meta.title = node.id;
      inspector.appendChild(meta);

      if (node.kind === "feature") {
        renderTouchedFiles(id, node);
        inspector.appendChild(renderActionBar(id));
        inspector.appendChild(renderCheckpoints(id));
      }
    }

    // Each of these takes over the code(I) slot with a read-only view of a DIFFERENT frontier
    // than the one the action bar above still previews/applies against -- composition-preview
    // (hovering the composition QuickPick), then the playhead, then the ordinary selection.
    // A staged candidate blocks `save`/`switch`/every materializing verb, so it outranks whatever is
    // selected: drawn first and drawn always, not just in the idle "home" state below. Drift keeps
    // the home-only placement -- an invitation to record, which can wait until the reader is idle --
    // but a state that makes the next click fail cannot be behind a selection.
    const stagedNow = compose.status && compose.status.staged && compose.status.staged.any;
    if (stagedNow) renderWorkingChangesCard();

    if (compositionPreviewActive != null) {
      renderCompositionPreviewPanel(node && node.kind === "feature" ? node : null);
    } else if (playheadCommitIndex != null) {
      renderPlayheadPanel(playheadCommitIndex, node && node.kind === "feature" ? node : null);
    } else if (node && node.kind === "feature") {
      renderChangePanel(id);
    } else if (!node && !step && !session) {
      // The panel's "home" state: nothing selected, not scrubbing. Surface the uncommitted work
      // as a record-and-save card so the primary daily action is one click from an idle view.
      // Guarded: a staged candidate already drew this card above, and drawing it twice would put two
      // Abandon buttons on screen.
      if (!stagedNow) renderWorkingChangesCard();
      renderThemesCard();
    }
  }

  // The cross-feature themes, on the idle panel: work that ran across lanes has no row of its own
  // in the graph, so with nothing selected the panel lists the groups by name -- the same names
  // `sgt log` footers and `sgt revert "<name>"` take. Clicking one enters the TableLens focus.
  function renderThemesCard() {
    const themes = spanningThemes();
    if (!themes.length) return;
    themes.sort((a, b) => (b.feature_span || []).length - (a.feature_span || []).length);
    const wrap = document.createElement("div");
    const h = document.createElement("div");
    h.className = "detail-title";
    h.textContent = "Work that spans several features";
    wrap.appendChild(h);
    const why = document.createElement("div");
    why.className = "detail-meta";
    why.textContent = "one task, landed across several rows — click to see it linked on the graph";
    wrap.appendChild(why);
    for (const t of themes.slice(0, 8)) {
      const row = document.createElement("button");
      row.className = "theme-row" + (state.themeFocus === t.theme_id ? " theme-row-active" : "");
      const name = document.createElement("span");
      name.className = "theme-row-name";
      name.textContent = `◈ ${t.label}`;
      const meta = document.createElement("span");
      meta.className = "theme-row-meta";
      meta.textContent = `${(t.feature_span || []).length} features`;
      row.append(name, meta);
      row.addEventListener("click", () => setThemeFocus(t.theme_id));
      wrap.appendChild(row);
    }
    inspector.appendChild(wrap);
  }

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
      wrap.appendChild(skeletonRows(2, "find"));
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
    revert.addEventListener("click", () => {
      // Staged like every other destructive verb: the union closure is already painted amber and
      // counted (selectionResult); the confirm bar takes the decision in-graph, no modal.
      const view = selectionResult && selectionResult.view;
      stageAction({
        verb: "revert", kind: "multi", targetId: state.multi[0],
        label: `${state.multi.length} selected features`,
        refs: state.multi.slice(),
        summary: view && view.ok
          ? `${view.closure_op_count} edit(s) in closure · ${(view.files || []).length} file(s) rewritten `
            + "· each feature reverted in turn, stopping if one refuses"
          : null,
        res: { ok: true },
      });
      paintSelectionClosure();
    });
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

  // ---- working-changes
  // What the card says about a tree that is not clean, which is two different situations the old
  // card conflated into one. Split out as a pure function because the conflation was the bug and a
  // test can hold this shape but not the DOM: `status.drift` used to carry staged paths too, so a
  // staged rewrite candidate was titled "Working changes", explained as "edits not yet recorded",
  // and offered a Save that `lens.put`'s staged guard refuses -- the one button drawn was the one
  // that could not run. The two states want opposite sentences: drift is bytes the ideal has never
  // seen (Save records them), a candidate is bytes that *replace* recorded ops (landing swaps them
  // in, and only once the oracle agrees the rewrite preserved behavior).
  function workingChangesCard(status, rewrite) {
    const staged = (status && status.staged) || { any: false, paths: [] };
    const drift = (status && status.drift) || { any: false, paths: [] };
    const candidate = (rewrite && rewrite.staged) || null;
    if (staged.any) {
      // Keyed on `status.staged` alone, not on the candidate projection as well. The two come from
      // different view functions, so a refresh can deliver the paths without the detail -- and the
      // fallback then has to be a staged card with less to say, never the drift card, because a tree
      // holding a candidate is the one tree that must not be described as clean or as savable.
      const verb = candidate && candidate.verb;
      const n = candidate && candidate.op_count;
      // The oracle verdict is the *candidate's*, not the current ideal's, so it cannot be read off
      // the titlebar chip. Naming the gate is the whole point: unshown, it arrives as a refusal
      // after the click, and a refusal after the click teaches nothing about why.
      const gate = (candidate && candidate.oracle_status) || "pending";
      const abandon = { verb: "unstage", label: "Abandon", hint: "sgt advanced unstage — discard the candidate" };
      return {
        state: "staged",
        title: verb ? `Staged · ${verb}` : "Staged rewrite",
        why: (n ? `${n} recorded op(s) rewritten, ` : "A rewrite of recorded ops, ")
          + "on disk and not yet in the ideal. Landing replaces those ops; abandoning discards the "
          + "rewrite and restores the recorded bytes.",
        paths: staged.paths || [],
        gate: gate === "pass"
          ? "Oracle passed — safe to land."
          : `Oracle ${gate} — landing is blocked until it passes.`,
        actions: gate === "pass"
          ? [{ verb: "land", label: "Land", primary: true,
               hint: "sgt advanced commit — replace those ops in the ideal" },
             abandon]
          : [{ verb: "oracle", label: "Check", primary: true,
               hint: "sgt advanced oracle run — verify the rewrite preserved behavior" },
             { verb: "land", label: "Land", disabled: true,
               hint: `oracle is ${gate}; landing a rewrite it has not passed needs an override` },
             abandon],
      };
    }
    const paths = drift.paths || [];
    if (!paths.length) return { state: "clean", title: "Working changes", clean: "Clean — everything is recorded." };
    return {
      state: "drift",
      title: `Working changes · ${paths.length}`,
      // `sgt save` mines *and* commits, so this is one action and not the Save/Commit pair the card
      // used to draw. The second button shipped a promise the daily loop had already kept.
      why: "Edits the ideal has not seen. Save records them as ops and commits.",
      paths,
      actions: [{ verb: "save", label: "Save ⏎", primary: true, hint: "sgt save — record these changes as ops" }],
    };
  }

  const CARD_MESSAGE = {
    land: { type: "landCandidate" },
    save: { type: "dailyLoop", verb: "save" },
    oracle: { type: "runOracle" },
    // Not `applyVerb`: that switch is feature verbs only and answers anything else by throwing
    // `unknown feature verb`. Its own message type, because abandoning asks for confirmation first.
    unstage: { type: "abandonCandidate" },
  };

  // ---- end-working-changes

  function renderWorkingChangesCard() {
    const card = workingChangesCard(compose.status, compose.rewrite);
    const wrap = document.createElement("div");
    wrap.className = "changes-card";
    wrap.dataset.state = card.state;

    const h = document.createElement("div");
    h.className = "detail-title";
    h.textContent = card.title;
    wrap.appendChild(h);

    if (card.clean) {
      wrap.appendChild(statusLine(card.clean, ""));
      inspector.appendChild(wrap);
      return;
    }

    const sub = document.createElement("div");
    sub.className = "detail-why";
    sub.textContent = card.why;
    wrap.appendChild(sub);

    const list = document.createElement("div");
    list.className = "changes-list";
    const CAP = 12;
    for (const p of card.paths.slice(0, CAP)) {
      const row = document.createElement("div");
      row.className = "changes-file";
      row.textContent = p;
      row.title = p;
      list.appendChild(row);
    }
    if (card.paths.length > CAP) {
      const more = document.createElement("div");
      more.className = "changes-more";
      more.textContent = `+${card.paths.length - CAP} more`;
      list.appendChild(more);
    }
    wrap.appendChild(list);

    if (card.gate) wrap.appendChild(statusLine(card.gate, card.gate.startsWith("Oracle passed") ? "ok" : "warn"));

    const bar = document.createElement("div");
    bar.className = "action-bar";
    for (const a of card.actions) {
      const btn = document.createElement("button");
      btn.className = "action" + (a.primary ? " primary" : "");
      btn.textContent = a.label;
      btn.title = a.hint;
      btn.disabled = !!a.disabled;
      if (!a.disabled) btn.addEventListener("click", () => vscode.postMessage(CARD_MESSAGE[a.verb]));
      bar.appendChild(btn);
    }
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
      section.appendChild(skeletonRows(6, "code"));
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
    if (cached === undefined) requestFold(id);
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

    // The scrub's exit into real editors: this history point as read-only now ⇄ then diffs. The
    // snippet panel below orients; this is how you actually READ the codebase at c{idx}.
    const visit = document.createElement("button");
    visit.className = "code-panel-back code-panel-visit";
    visit.textContent = `Open files @ c${idx}…`;
    visit.title = "Open files as they stood at this point — read-only; your working tree is untouched";
    visit.addEventListener("click", () => vscode.postMessage({ type: "openFoldFiles", commitIndex: idx }));
    section.appendChild(visit);

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

  // ---- skeleton
  // The waiting shapes. "Loading…" is a word about the system; a skeleton is a promise about the
  // answer's shape -- lines where code will be, rows where hits will be -- and its shimmer says
  // "alive", where a static caption after two seconds reads "wedged". Reduced-motion users get
  // static blocks (CSS kills the animation, keeps the shape).
  function skeletonRows(n, kind) {
    const wrap = el("div", "skel" + (kind ? ` skel-${kind}` : ""));
    const widths = [86, 61, 74, 47, 68, 55];
    for (let i = 0; i < n; i++) {
      const line = el("div", "skel-line");
      line.style.width = widths[i % widths.length] + "%";
      wrap.appendChild(line);
    }
    return wrap;
  }
  // ---- end-skeleton

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
    // A reverted chapter stays on this list -- it is still recorded and still addressable, and a
    // restore needs it named -- so the head says how many are gone rather than quietly shrinking.
    //
    // It used to end with "(run sgt intent build to name)" whenever the names were fallbacks. That
    // is a maintenance instruction, printed in the reader's way, for a job the reader did not ask
    // to do and mostly cannot do anything about -- and the names it offers to improve are already
    // on screen beneath it.
    const nGone = segs.filter((s) => s.present_op_count === 0).length;
    head.textContent = `Checkpoints · ${segs.length}` + (nGone ? ` · ${nGone} reverted` : "");
    wrap.appendChild(head);
    // Retired work is the half of this list nobody found. Name the way back once, at the top,
    // where the count already is -- rather than leaving it to a glyph on a faded row.
    if (nGone) {
      wrap.appendChild(el("div", "checkpoints-hint",
        "Faded checkpoints were reverted — click one, then Restore to bring it back."));
    }

    for (const seg of segs) {
      // `present_op_count` is how many of the chapter's ops are still in HEAD's ideal. `null` (an
      // unreadable ideal, or an older payload) is no claim and must not read as reverted.
      const gone = seg.present_op_count === 0;
      const partial = seg.present_op_count != null && seg.present_op_count > 0 &&
        seg.present_op_count < seg.op_count;
      const row = document.createElement("div");
      row.className = "checkpoint" + (seg.novelty <= 0.2 ? " trivial" : "") + (gone ? " reverted" : "") +
        (seg.checkpoint === state.selectedCheckpoint ? " selected" : "");
      row.dataset.checkpoint = seg.checkpoint;
      row.title = `${seg.rationale} · ${seg.tier}\n` + (gone
        ? `Reverted — restore: sgt restore ${seg.checkpoint}`
        : (partial ? `${seg.op_count - seg.present_op_count} of ${seg.op_count} edit(s) reverted\n` : "") +
          `Revert this checkpoint: sgt revert ${seg.checkpoint}`);
      row.addEventListener("click", () => highlightCheckpoint(seg.checkpoint)); // sync with the gantt car

      const dot = document.createElement("span");
      dot.className = "checkpoint-dot";
      dot.textContent = seg.novelty > 0.6 ? "●" : seg.novelty > 0.2 ? "◐" : "○";
      row.appendChild(dot);

      const label = document.createElement("span");
      label.className = "checkpoint-label";
      label.textContent = seg.intent;
      row.appendChild(label);

      // One button, whichever direction is actually available. Before this the row offered `⤺`
      // even on an already-reverted chapter, where a second revert can only report "no change" --
      // a button that looks live and does nothing. A reverted chapter's one useful move is the
      // inverse, and it is addressable by the same `<feature>@<n>` handle.
      const rewind = document.createElement("button");
      rewind.className = "checkpoint-rewind";
      rewind.textContent = gone ? "⤻" : "⤺";
      rewind.title = gone
        ? `Restore this checkpoint — bring "${seg.intent}" back`
        : `Revert this checkpoint — take out "${seg.intent}"; later checkpoints stay`;
      rewind.addEventListener("mouseenter", () =>
        onHoverIntent(() => previewAndBlast(gone ? "restore" : "revert", [seg.checkpoint])));
      rewind.addEventListener("mouseleave", () => clearGhosts());
      rewind.addEventListener("click", (e) => {
        e.stopPropagation();
        // Staged in the graph (consequence held + confirm bar), not the old modal round-trip.
        stageAction({
          verb: gone ? "restore" : "revert", ref: seg.checkpoint, targetId: id,
          label: seg.intent, kind: "chapter",
        });
      });
      row.appendChild(rewind);

      // Hovering the ROW identifies its car on the timeline (a cheap, local "this one"); only the
      // action button below previews the consequence. Same rule as the cars themselves: reading is
      // not previewing.
      row.addEventListener("mouseenter", () => {
        if (armedVerb || stagedAction) return;
        const wrap = findCar(seg.checkpoint);
        if (wrap) wrap.classList.add("gcar-hovered");
      });
      row.addEventListener("mouseleave", () => {
        rail.querySelectorAll(".gcar-hovered").forEach((el) => el.classList.remove("gcar-hovered"));
      });
      wrap.appendChild(row);
    }
    return wrap;
  }

  // ---- the asked disclosure
  //
  // What a chapter was asked for, on the card the reader is deciding from. Two levels, and the
  // reader chooses: the excerpt (drawn as soon as a checkpoint is selected -- it is already in the
  // payload and costs nothing) and the verbatim prompts (fetched on click). The excerpt is not a
  // summary anybody wrote: it is a cut of the prompt starting at the request (`sgt.intent.gist`),
  // so opening the full text shows the same sentence again inside the paragraph it came from --
  // which is what makes the excerpt trustworthy rather than a claim to take on faith.
  //
  // Drawn once per screen. It was briefly under the selected row in the checkpoint list as well,
  // which put the same quotation on screen twice with two open/close controls for one reading
  // state.
  function askedBlock(seg) {
    const asks = seg.asks || [];
    if (!asks.length) return null;
    const top = dominantAsk(asks);
    const open = askedOpen === seg.checkpoint;
    const cached = askedCache[seg.checkpoint];
    const loading = !!pendingAsked && pendingAsked.ref === seg.checkpoint;

    const block = el("div", "asked" + (askedAnimated === seg.checkpoint ? "" : " asked-enter"));
    askedAnimated = seg.checkpoint;

    if (open && cached && cached.asks && cached.asks.length) {
      // How many turns there are, before the first one -- the scroller below is capped, and a
      // reader who cannot see the extent of what they opened reads the visible part as all of it.
      if (cached.asks.length > 1) {
        block.appendChild(el("div", "asked-head",
                             `${cached.asks.length} asks, oldest first`));
      }
      // The conversation, in order, in ONE capped scroller -- outside it go the count above and
      // the control below, because a control that scrolls away with the content is a control the
      // reader has to go looking for to close what they opened.
      const turns = el("div", "asked-turns");
      block.appendChild(turns);
      // Each turn keeps its own attribution line: a chapter's words can be one ask, an ask and its
      // correction, or an ask relayed by an agent, and collapsing them would lose which is which.
      cached.asks.forEach((a, i) => {
        const turn = el("div", "asked-turn");
        turn.appendChild(el("div", "asked-meta",
                              askedMeta(a, cached.asks.length > 1 ? i + 1 : 0, cached.asks.length > 1)));
        turn.appendChild(el("div", "asked-text", a.text || a.gist));
        if (a.resumable && a.claude_session_id) {
          // Offered only when the transcript is still on this machine (the host checks). A
          // `claude --resume` that fails teaches the reader that these lines are decoration.
          const resume = el("button", "asked-resume", "Reopen this conversation");
          resume.title = `claude --resume ${a.claude_session_id}`;
          resume.addEventListener("click", (e) => {
            e.stopPropagation();
            vscode.postMessage({ type: "resumePlan", sessionId: a.claude_session_id });
          });
          turn.appendChild(resume);
        }
        turns.appendChild(turn);
      });
    } else {
      const quote = el("div", "asked-quote", `“${top.gist}”`);
      block.appendChild(quote);
      block.appendChild(el("div", "asked-meta", askedMeta(top, 0, asks.length > 1)));
      if (open && cached && cached.error) {
        // A disclosure that opened onto nothing would read as "there is nothing here", which is a
        // different claim from "this could not be read".
        block.appendChild(el("div", "asked-error", `Could not read the conversation — ${cached.error}`));
      }
    }

    // The control says what it will do, and only appears when there is something behind it: an
    // excerpt that IS the whole prompt, with no other asks, has nothing to open.
    const more = asks.length - 1;
    if (top.trimmed || more > 0) {
      const toggle = el("button", "asked-more",
        loading ? "reading…" : open ? "Show less" : askedMoreLabel(top, more));
      toggle.disabled = loading;
      toggle.addEventListener("click", (e) => {
        e.stopPropagation(); // the row underneath toggles the selection; this toggles the reading
        if (open) {
          askedOpen = null;
          renderInspector();
          return;
        }
        askedOpen = seg.checkpoint;
        if (!askedCache[seg.checkpoint]) requestAsked(seg.checkpoint);
        renderInspector();
      });
      block.appendChild(toggle);
    }
    return block;
  }

  function requestAsked(ref) {
    const seq = ++askedSeq;
    pendingAsked = { seq, ref };
    vscode.postMessage({ type: "requestAsked", ref, seq });
  }

  // ---- retired-work (test slice boundary)
  // What a feature has RECORDED but no longer has LIVE. `present_op_count` is how many of a
  // chapter's ops survive in the ideal, so a chapter at 0 is fully retired and one below its
  // op_count is partly so. Restore's whole affordance was invisible: the action bar offered a
  // "Restore" that, on a feature with nothing retired, could only fail -- and it failed by
  // rendering the kernel's raw invalid-ideal exception, a wall of hex. A verb that can only
  // refuse should not be a live button; a verb that CAN do something should say what.
  function retiredWork(segs) {
    let chapters = 0, edits = 0, partial = 0;
    for (const sg of segs || []) {
      const present = sg.present_op_count;
      if (present == null) continue; // no claim (older payload) -- never counted as retired
      if (present === 0) { chapters++; edits += sg.op_count; }
      else if (present < sg.op_count) { partial++; edits += sg.op_count - present; }
    }
    return { chapters, partial, edits, any: chapters + partial > 0 };
  }
  // ---- end-retired-work

  // ---- chapter-scope (test slice boundary)
  // What the action bar is FOR once a checkpoint is selected. Clicking a chapter and clicking its
  // feature used to reach the same six feature-scoped buttons, so "Revert" beside a highlighted
  // chapter removed the WHOLE feature -- the classic mis-scope, and the exact pilot complaint.
  // This derives the chapter's own affordances from the segment list alone: the one live direction
  // for THIS chapter (rewind or restore), and "back to here" -- every LIVE chapter after it,
  // newest first, which is what "revert to this checkpoint" means in an op-set world. Pure, so the
  // node harness can hold the scoping.
  function chapterScope(segs, ref) {
    const i = (segs || []).findIndex((sg) => sg.checkpoint === ref);
    if (i < 0) return null;
    const seg = segs[i];
    const later = segs.slice(i + 1).filter((sg) => sg.present_op_count !== 0);
    return {
      seg,
      gone: seg.present_op_count === 0,
      // Newest first: each `sgt revert <feature>@<n>` peels the top chapter, so applying in this
      // order is always removing the current tip, never digging under later work.
      laterRefs: later.map((sg) => sg.checkpoint).reverse(),
      laterCount: later.length,
      laterOps: later.reduce(
        (n, sg) => n + (sg.present_op_count == null ? sg.op_count : sg.present_op_count), 0),
    };
  }
  // ---- end-chapter-scope

  // The chapter-scoped action bar: drawn instead of the feature bar while a checkpoint is
  // selected, so the highlighted thing and the acted-on thing are the same thing. The feature-wide
  // verbs stay one explicit scope-switch away rather than being the loaded default.
  function renderChapterActionBar(id, scope) {
    const wrap = document.createElement("div");
    const node = byId(id);
    const seg = scope.seg;

    const head = el("div", "chapter-scope-head");
    const dot = el("span", "chapter-scope-dot");
    dot.style.background = (node && node.color) || "var(--dim)";
    head.appendChild(dot);
    head.appendChild(el("span", "chapter-scope-label", `Checkpoint · ${seg.intent}`));
    wrap.appendChild(head);

    // What this chapter was asked for, between its name and the verb that removes it. Here rather
    // than under its row in the list below: this is the card the reader is deciding from, and the
    // words somebody typed are the thing that says whether the chapter under the cursor is the one
    // they meant. A name can be a generated approximation; the ask is evidence.
    const asked = askedBlock(seg);
    if (asked) wrap.appendChild(asked);

    const bar = el("div", "action-bar");
    const one = el("button", "action", scope.gone ? "⤻ Restore this checkpoint" : "⤺ Revert this checkpoint");
    one.title = scope.gone
      ? `sgt restore ${seg.checkpoint} — bring this checkpoint's ${seg.op_count} edit(s) back`
      : `sgt revert ${seg.checkpoint} — take out this checkpoint's edits only; later ones stay`;
    one.addEventListener("mouseenter", () =>
      onHoverIntent(() => previewAndBlast(scope.gone ? "restore" : "revert", [seg.checkpoint])));
    one.addEventListener("mouseleave", () => clearGhosts());
    one.addEventListener("click", () => stageAction({
      verb: scope.gone ? "restore" : "revert", ref: seg.checkpoint, targetId: id,
      label: seg.intent, kind: "chapter",
    }));
    bar.appendChild(one);

    if (!scope.gone && scope.laterCount) {
      const back = el("button", "action", "⇤ Revert to here");
      back.title = `Revert the feature to "${seg.intent}" — removes the ${scope.laterCount} `
        + `checkpoint(s) after it; this one stays`;
      back.addEventListener("mouseenter", () => onHoverIntent(() => {
        paintBackToCars(id, scope.laterRefs);
        setPreviewContext(`revert to "${seg.intent}" — the ${scope.laterCount} checkpoint(s) after `
          + `it come out · ${scope.laterOps} edit(s)`);
      }));
      back.addEventListener("mouseleave", () => clearGhosts());
      back.addEventListener("click", () => stageAction({
        verb: "revert", kind: "backto", targetId: id, ref: seg.checkpoint, label: seg.intent,
        refs: scope.laterRefs.slice(), opCount: scope.laterOps,
        blastCount: 0, blastDone: false, res: { ok: true },
      }));
      bar.appendChild(back);
    }
    wrap.appendChild(bar);

    // The scope switch, stated as one: not six duplicate buttons that differ only in blast radius.
    const featureRow = el("div", "action-bar feature-scope");
    const whole = el("button", "action subtle", "Whole feature…");
    whole.title = "Switch to feature scope — rename, merge, split, move, revert every chapter";
    whole.addEventListener("click", () => {
      state.selectedCheckpoint = null;
      saveState();
      render();
    });
    featureRow.appendChild(whole);
    wrap.appendChild(featureRow);
    return wrap;
  }

  // What this feature's own work touches, BY FILE -- the bridge from a lens to the running app.
  // The study's quiz asks "which parts of the dashboard did this change", and the parts are files
  // (one page per file under pages/); a participant staring at a lens called "Event Day Tracking"
  // had no way to answer without reading code. Files answer it in one glance, so they come first
  // on the card, page files ahead of the rest. Client-side over `own_symbols` -- what the lane's
  // ops really touched, the same number every other surface reports -- never `members`.
  function renderTouchedFiles(id, node) {
    const own = node.own_symbols || [];
    if (!own.length) return;
    const perFile = new Map();
    for (const sym of own) {
      const file = String(sym).split("::")[0];
      perFile.set(file, (perFile.get(file) || 0) + 1);
    }
    const shortName = (f) => {
      const parts = f.split("/");
      return parts.length > 1 ? parts.slice(1).join("/") : f;
    };
    const rows = [...perFile.entries()]
      .map(([file, n]) => ({ file, n, page: /(^|\/)pages\//.test(file) }))
      .sort((a, b) => (b.page - a.page) || (b.n - a.n) || a.file.localeCompare(b.file));
    const wrap = document.createElement("div");
    wrap.className = "touched-files";
    const label = document.createElement("span");
    label.className = "touched-files-label";
    label.textContent = "touches";
    wrap.appendChild(label);
    for (const r of rows.slice(0, 8)) {
      // A hit target, not a label. The chip already answers "which part of the dashboard"; the one
      // move it left the reader with -- go look at it -- was theirs to make by hand, through a file
      // tree, from a name the panel was already showing. Opening the document is all this has to
      // do: blame.ts decorates whatever editor becomes active, so the file arrives with this
      // feature's own spans already tinted in its identity color.
      const chip = document.createElement("button");
      chip.className = "chip file-chip" + (r.page ? " file-chip-page" : "");
      chip.textContent = shortName(r.file);
      chip.title = `${r.file} — ${r.n} symbol(s) of this feature's work · click to open`;
      chip.addEventListener("click", () => vscode.postMessage({ type: "openFile", path: r.file }));
      wrap.appendChild(chip);
    }
    if (rows.length > 8) {
      const more = document.createElement("span");
      more.className = "touched-files-more";
      more.textContent = `+${rows.length - 8}`;
      wrap.appendChild(more);
    }
    inspector.appendChild(wrap);
  }

  // ─── The change panel ─────────────────────────────────────────────────────────────────────────
  // What the selection CHANGED, as a file-explorer tree with a way into a real diff editor on every
  // row. It replaces the panel that printed each of the feature's files in full: that answered
  // "what does this code say", and the only question a selection raises is "what did this do".

  // Which dry run answers for this selection, and what to call it. A chapter is its own scope --
  // clicking a checkpoint and clicking its feature must not project the same change, which is the
  // same mis-scope the action bar already fixed. The verb is whichever direction actually exists:
  // a retired chapter has nothing to revert, and its change is the one a restore would bring back.
  function changeScope(id) {
    const segs = checkpointsByFeature[id] || [];
    const scope = state.selectedCheckpoint && chapterScope(segs, state.selectedCheckpoint);
    if (scope) {
      // "checkpoint", the word the list above this panel uses. It said "chapter" -- sgt's internal
      // name for the same thing -- so one panel headed `Checkpoints · 10` sat directly above another
      // reading `changed by this chapter`, and a reader had to work out that the two were one noun.
      return { ref: scope.seg.checkpoint, verb: scope.gone ? "restore" : "revert",
               label: scope.seg.intent, noun: "checkpoint" };
    }
    const retired = retiredWork(segs);
    const gone = segs.length > 0 && retired.chapters === segs.length;
    const node = byId(id);
    return { ref: id, verb: gone ? "restore" : "revert",
             label: (node && node.label) || id, noun: "feature" };
  }

  function requestChange(verb, ref, key) {
    if (pendingChange && pendingChange.key === key) return; // already in flight for this scope
    const seq = ++changeSeq;
    pendingChange = { seq, key };
    // Deliberately no repaint: this is called from inside a render that is already drawing the
    // skeleton for exactly this request.
    vscode.postMessage({ type: "requestChange", verb, ref, seq });
  }

  function renderChangePanel(id) {
    const scope = changeScope(id);
    // Separated on U+001F, spelled rather than typed: a checkpoint ref can be `<feature>:<slug>`,
    // so a punctuation separator is one a real ref can contain.
    const key = `${scope.verb}\u001f${scope.ref}`;
    const section = el("div", "code-panel change-panel");
    const heading = el("div", "code-panel-heading", `changed by this ${scope.noun}`);
    // The scope is wider than the lane, and saying so here is cheaper than a reader discovering it
    // from a diff: a revert takes its dependents with it, so the projection is the whole closure.
    heading.title = `sgt ${scope.verb} ${scope.ref} --emit\n`
      + `The difference between the ideal holding this ${scope.noun}'s edits and the ideal without `
      + `them — dependents included, which is what the ${scope.verb} would actually do.`;
    section.appendChild(heading);
    inspector.appendChild(section);

    const cached = changeCache[key];
    if (cached === undefined) {
      requestChange(scope.verb, scope.ref, key);
      section.appendChild(skeletonRows(5));
      return;
    }
    if (!cached.ok) {
      section.appendChild(statusLine(humanizeRefusal(cached.message), "warn"));
      renderCodePanel(id); // no projection to read, so offer the code at this frontier instead
      return;
    }
    const tree = changeTree(cached.files, { withSide: scope.verb === "restore" ? "after" : "before" });
    if (!tree.fileCount) {
      section.appendChild(statusLine(`This ${scope.noun} changes no file in the current ideal.`));
      renderCodePanel(id);
      return;
    }

    const sum = el("div", "change-summary");
    sum.appendChild(el("span", "change-files",
      `${tree.fileCount} file${tree.fileCount === 1 ? "" : "s"}`));
    sum.appendChild(el("span", "change-plus", `+${tree.added}`));
    sum.appendChild(el("span", "change-minus", `−${tree.removed}`));
    section.appendChild(sum);
    if (tree.capped) {
      // Never a silent truncation: the count is a floor, and the caption says which way it errs.
      section.appendChild(statusLine(
        "A file changed too much to align line by line — its counts read whole regions as changed.",
        "warn"));
    }

    // Files start folded only when the change is genuinely big.
    //
    // The old rule -- fold past 40 rows, to keep the action bar on screen -- was written when this
    // panel sat above the verbs. It does not: the bar renders before it, so folding protects
    // nothing, and 40 counted rows is three files on a real feature. Folding there hides the one
    // thing this panel is for.
    //
    // So: one rule, total rows, and a ceiling set from what real work actually measures rather
    // than from a round number. Every feature in the two study projects lands between 29 and 107
    // rows (7 files is typical, not large), and all of them should open: a reader who selected a
    // feature to find out what it did is not helped by seven folded rows. Past the ceiling the
    // panel is no longer readable as a whole and folding is the kinder default. Rows are counted
    // with the changed lines under each entity, because those are rows on this list like any other.
    const rows = tree.fileCount + countEntityRows(tree.root) + countLineRows(tree.root);
    const foldFiles = rows > 250;
    const list = el("div", "ctree");
    appendChangeRows(list, tree.root, 0, scope, foldFiles);
    section.appendChild(list);
  }

  function countEntityRows(node) {
    if (node.kind === "file") return node.children.length;
    let n = 0;
    for (const c of node.children || []) n += countEntityRows(c);
    return n;
  }

  // The changed lines an unfolded file would draw: each entity's own, capped, plus its "+N more".
  function countLineRows(node) {
    if (node.kind === "file") {
      let n = 0;
      for (const e of node.children || []) {
        const len = (e.lines || []).length;
        n += Math.min(len, CHANGED_LINES_CAP) + (len > CHANGED_LINES_CAP ? 1 : 0);
      }
      return n;
    }
    let n = 0;
    for (const c of node.children || []) n += countLineRows(c);
    return n;
  }

  function appendChangeRows(list, parent, depth, scope, foldFiles) {
    for (const node of parent.children || []) {
      const folded = changeFolded.has(node.path) || (node.kind === "file" && foldFiles
        && !changeUnfolded.has(node.path));
      list.appendChild(changeRow(node, depth, scope, folded));
      if (node.kind === "dir") {
        if (!folded) appendChangeRows(list, node, depth + 1, scope, foldFiles);
      } else if (!folded) {
        for (const entity of node.children) {
          list.appendChild(changeRow(entity, depth + 1, scope, false));
          appendChangedLines(list, entity, depth + 2);
        }
      }
    }
  }

  // The lines themselves, under the entity that holds them.
  //
  // This panel used to stop at counts: `render  +4 −2`, and the code was one click away in a diff
  // editor that opens as a separate tab. Reading a history is the task here, not editing it, and
  // "what did this change" was answered with a number and a way to go and find out. Somebody
  // looking for where a wrong figure comes from read the whole panel, found no code in it, and
  // concluded the tool would not show them the change.
  //
  // Capped, because an entity can be a whole rewritten file and this sits inside a scrolling
  // inspector. The cap says what it hid, and the row above still opens the full diff.
  const CHANGED_LINES_CAP = 10;

  function appendChangedLines(list, entity, depth) {
    const lines = entity.lines || [];
    if (!lines.length) return;
    const shown = lines.slice(0, CHANGED_LINES_CAP);
    for (const ln of shown) {
      const row = el("div", `cline cline-${ln.side === "+" ? "add" : "del"}`);
      row.style.paddingLeft = `${4 + depth * 12}px`;
      row.appendChild(el("span", "cline-no", String(ln.line)));
      row.appendChild(el("span", "cline-sign", ln.side));
      // Leading whitespace is meaning in Python, and a span collapses it. Kept verbatim in the
      // text node; `white-space: pre` on `.cline-text` is what preserves it on screen.
      const text = el("span", "cline-text", ln.text == null ? "" : ln.text);
      // The panel is narrow and a clipped line can hide the half that matters, so the whole line
      // is on the row itself rather than only in the diff a click away.
      text.title = ln.text == null ? "" : ln.text;
      row.appendChild(text);
      list.appendChild(row);
    }
    if (lines.length > shown.length) {
      const rest = el("div", "cline cline-more",
        `+${lines.length - shown.length} more changed line(s) — click the row above for the full diff`);
      rest.style.paddingLeft = `${4 + depth * 12}px`;
      list.appendChild(rest);
    }
  }

  function changeRow(node, depth, scope, folded) {
    const row = el("div", `ctree-row ctree-${node.kind}`);
    row.style.paddingLeft = `${4 + depth * 12}px`;
    const twisty = el("span", "ctree-twisty",
      node.kind === "entity" || !node.children.length ? "" : folded ? "▸" : "▾");
    row.appendChild(twisty);
    row.appendChild(el("span", "ctree-name", node.name));

    const meter = changeMeter(node.added, node.removed);
    const strip = el("span", "ctree-meter");
    strip.appendChild(el("span", "ctree-plus", meter.plus));
    strip.appendChild(el("span", "ctree-minus", meter.minus));
    strip.appendChild(el("span", "ctree-rest", meter.rest));
    row.appendChild(strip);
    row.appendChild(el("span", "ctree-count", `+${node.added} −${node.removed}`));

    if (node.kind === "dir") {
      row.title = `${node.path} — ${node.added} line(s) added, ${node.removed} removed by this `
        + `${scope.noun}`;
      row.addEventListener("click", () => toggleChangeFold(node.path, folded));
      return row;
    }
    // A file or an entity is a way into the diff; a file's twisty is the one part of its row that
    // folds instead, the same split the editor's own explorer uses.
    const what = node.kind === "file" ? node.path : node.symbol || node.path;
    row.title = `${what} — +${node.added} −${node.removed} · click to diff without ⇄ with `
      + `this ${scope.noun}`;
    row.addEventListener("click", () => vscode.postMessage({
      type: "openChangeDiff", verb: scope.verb, ref: scope.ref, label: scope.label,
      path: node.path, symbol: node.kind === "entity" ? node.symbol : undefined,
    }));
    if (node.kind === "file" && node.children.length) {
      twisty.classList.add("ctree-twisty-live");
      twisty.title = folded ? "Show the entities inside" : "Hide the entities inside";
      twisty.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleChangeFold(node.path, folded);
      });
    }
    return row;
  }

  // Folding is exploratory, not a preference: it lives beside the panel, not in `state`, so a
  // reopened workbench is not still hiding what the last selection folded. `changeUnfolded` is the
  // opposite set, so a file the long-tree rule folded stays open once the reader opens it.
  function toggleChangeFold(path, wasFolded) {
    if (wasFolded) {
      changeFolded.delete(path);
      changeUnfolded.add(path);
    } else {
      changeFolded.add(path);
      changeUnfolded.delete(path);
    }
    renderInspector();
  }

  function renderActionBar(id) {
    // A selected checkpoint narrows the bar to that chapter (see chapterScope) -- the fix for
    // "clicked a checkpoint, pressed Revert, lost the whole feature".
    const scope = state.selectedCheckpoint
      && chapterScope(checkpointsByFeature[id] || [], state.selectedCheckpoint);
    if (scope) return renderChapterActionBar(id, scope);

    const bar = document.createElement("div");
    bar.className = "action-bar";

    const btn = (label, verb, title) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.className = "action";
      if (title) b.title = title;
      b.addEventListener("mouseenter", () => onHoverIntent(() => previewAction(verb, id)));
      b.addEventListener("mouseleave", () => clearGhosts());
      b.addEventListener("click", () => triggerAction(verb, id));
      return b;
    };
    const segs = checkpointsByFeature[id] || [];
    const chapters = segs.length;
    const retired = retiredWork(segs);
    // Two verbs, and they are the two this surface is for: take this work out, put it back.
    //
    // Rename, Merge into…, Split and Move ops… used to sit ahead of them, in that order, so the
    // first four things offered about a piece of work were four ways to reorganise the RECORD of
    // it -- before either of the two ways to act on the code. They are real verbs and they are not
    // this panel's job: reading a history is what someone opens this for, and a reader who reaches
    // for the leftmost button to see what it does has restructured their own graph. Reorganising
    // the record is deliberate work and belongs where it is asked for by name, on the CLI.
    // The whole-feature blast radius, said out loud -- and pointing at the narrower tool, so the
    // person who meant one chapter learns the distinction BEFORE the click, not from the wreckage.
    bar.appendChild(btn("Revert", "revert", chapters > 1
      ? `Removes the whole feature — all ${chapters} checkpoints. To act on one, click a checkpoint below.`
      : "Removes this feature's edits."));
    // Restore only exists when something is actually retired. Offering it otherwise was a button
    // whose only possible outcome was a refusal -- and the refusal was the hex wall.
    if (retired.any) {
      const label = `Restore ${retired.edits} edit(s)`;
      const r = btn(label, "restore",
        `Brings back what was reverted here — ${retired.chapters} retired checkpoint(s)`
        + (retired.partial ? ` and part of ${retired.partial} more` : "")
        + ". To bring back one, click it below.");
      bar.appendChild(r);
    } else {
      const inert = document.createElement("button");
      inert.className = "action";
      inert.textContent = "Restore";
      inert.disabled = true;
      inert.title = "Nothing is retired in this feature — every chapter is already live.";
      bar.appendChild(inert);
    }
    return bar;
  }

  // ---- arm-preview
  // Why these three verbs need a preview of their own. Merge, move and rename each ask a question
  // before they do anything -- which lane? what label? -- so the hover that arms one has no second
  // operand and cannot round-trip to `sgt preview`, which is why all three previewed nothing. But the
  // half that does not depend on the answer is the half that decides *which verb to pick*: what
  // becomes of the lane under the cursor. Merge ends it. Move empties it -- and `computeLayout` drops
  // a lane with no ops (`if (!commits.length) continue`), so the feature survives in the tree while
  // vanishing from this view, a result visually identical to the merge the reader did not choose.
  // Feedback that confirms the wrong operation removes the reason to check it, so the difference has
  // to be legible before the click, not after. Rename changes no edits, symbols or lanes, and saying
  // so is what makes it usable as a first move rather than something as consequential as revert.
  function armPreviewText(verb, node, opCount) {
    const label = (node && node.label) || "this feature";
    const symbols = ((node && node.own_symbols) || []).length;
    const edits = `${opCount} edit${opCount === 1 ? "" : "s"}`;
    const syms = `${symbols} symbol${symbols === 1 ? "" : "s"}`;
    if (verb === "rename") {
      // `target`, not `blast`: blast means "this lane loses ops" everywhere else in this file, and a
      // rename loses nothing. The role is the reassurance -- the sentence only spells it out.
      return { role: "target",
               message: `Rename · label only — "${label}" keeps its ${edits} and ${syms}; nothing moves.` };
    }
    if (verb === "merge") {
      return {
        role: "blast",
        message: `Merge · pick a lane to fold "${label}" into — its ${edits} and ${syms} move there `
          + "and this lane stops existing.",
      };
    }
    return {
      role: "blast",
      message: `Move · pick a lane to take all ${edits} — "${label}" keeps its ${syms} but, with no `
        + "edits left, leaves the graph until it is edited again.",
    };
  }
  // ---- end-arm-preview

  function previewAction(verb, id) {
    // merge/move/rename have no target or label yet, so this is the pre-target half: paint the one
    // lane whose fate is already decided, and say it. `previewArmed` replaces this with the real
    // two-operand `sgt preview` round-trip once a candidate target is under the cursor.
    if (verb === "rename" || verb === "merge" || verb === "move") {
      const say = armPreviewText(verb, byId(id), opIdsFor(id).length);
      paintClosure(say.role === "target" ? { target: id, blast: [], foundation: [] }
                                        : { target: null, blast: [id], foundation: [] });
      // Merge/move relocate this lane's chapters; rename moves nothing, so nothing nudges.
      if (verb !== "rename") paintLaneRelocating(id);
      setPreviewContext(say.message);
      return;
    }
    if (verb === "revert" || verb === "restore") previewAndBlast(verb, [id]);
    // Split has no `sgt preview split` branch server-side by design (`sgt split <feature>` with
    // no `--apply` already *is* that preview -- a second path would duplicate it), so this can't
    // go through the generic `previewVerb` round-trip the other verbs share.
    if (verb === "split") previewSplit(id);
  }

  // ---- humanize (test slice boundary)
  // Refusals arrive as raw CLI/kernel strings, and one of them is a dumped op-id set: the kernel's
  // invalid-ideal ValueError used to print every id in the ideal, which reached the reader as a
  // full-pane wall of hex with the one useful sentence buried at the top. The producing message is
  // bounded at the source now, but this surface renders whatever any deployed CLI hands it, so the
  // guarantee has to hold here too: collapse id runs to a count, cap the length, keep the lead.
  const HEXISH = /\b[0-9a-f]{12,}\b/g;

  function humanizeRefusal(message) {
    let text = String(message || "").trim();
    if (!text) return "sgt refused this — no reason given.";
    // A bracketed list of long hex ids says only "there were N of them"; say that instead.
    text = text.replace(/\[[^\]]*\]/g, (list) => {
      const ids = list.match(HEXISH);
      return ids && ids.length ? `${ids.length} op(s)` : list;
    });
    text = text.replace(HEXISH, (id) => id.slice(0, 8) + "…"); // stray ids: short-form, still traceable
    text = text.replace(/\s+/g, " ");
    return text.length > 220 ? text.slice(0, 219) + "…" : text;
  }
  // ---- end-humanize

  // ---- split-preview
  // What a split preview says. Split exists for one situation: a lane is carrying two pieces of work
  // that only look like one, and the reader has to decide whether *this* cut is the right place to
  // separate them. That decision is entirely about which symbols end up on which side -- and the
  // groups that answer it were computed, returned, and thrown away. The preview painted one amber
  // row and nothing else, which told the reader what they already knew from having hovered it.
  //
  // Two further things it got wrong. `groups.length > 1` reads like a guard but is vacuous: split is
  // always binary (`lens/verbs.py plan_split` folds >2 communities into exactly 2), so an ok preview
  // always passes it. And the case it silently dropped -- a feature with no cut in it -- is the
  // interesting half of the feedforward, rendered identically to a preview still in flight.
  function splitPreviewText(res) {
    if (!res || !res.ok || !Array.isArray(res.groups) || res.groups.length !== 2) {
      return { kind: "refused", message: (res && res.message) || "Can't split this feature." };
    }
    const [keep, off] = res.groups;
    // Name a few whole and count the rest. Both sides arrive complete here, so unlike undo's
    // upstream-capped symbol list the remainder is a number this payload can be right about.
    const named = off.slice(0, 3).join(", ");
    const rest = off.length - 3;
    return {
      kind: "split",
      // Same frame the terminal's own split preview uses ("splits in two", keep / new), so the two
      // surfaces describe one operation in one vocabulary. Both counts, because a lopsided cut is
      // the thing a reader wants to catch; then the new side by name, because that is the proposal
      // under judgement -- what stays is the feature they already know.
      message: `splits in two · keeps ${keep.length}, new ${off.length}: ` +
               named + (rest > 0 ? `, +${rest} more` : ""),
    };
  }
  // ---- end-split-preview

  function previewSplit(id) {
    beginPreviewPending(id, "previewing split…");
    const seq = ++previewSeq;
    vscode.postMessage({ type: "previewSplit", featureId: id, seq });
    pendingPreview = {
      seq,
      onResult: (res) => {
        clearPreviewPending();
        const say = splitPreviewText(res);
        if (say.kind === "refused") {
          showRefusal(say.message);
          return;
        }
        // `ghost-target`, not `ghost-blast`. Split removes nothing, and `ghost-blast` is the channel
        // that means "this lane loses ops" everywhere else in this file -- the one visual with an
        // established meaning, used to say something it does not mean. A split has exactly one
        // participant, so the target role is the whole truth about which rows change.
        paintClosure({ target: id, blast: [], foundation: [] });
        setPreviewContext(say.message);
        paintSplitPreview(id, res); // the moving chapters + the ghost of the lane the graph gains
      },
    };
  }

  // The painter half of the chunk-grain grammar. Additive (does not clear first) so a chained
  // preview -- revert-to-here accumulating its cross-feature blast one chapter at a time -- can
  // layer impacts as they land. The dash is drawn in the car's own identity hue: SVG presentation
  // attributes can't resolve CSS custom properties and the hue is per-lane, the same reason the
  // hollow reverted car sets its stroke inline at render.
  function paintCarPreview(impacts, cls) {
    const byCp = new Map((impacts || []).map((im) => [im.checkpoint, im]));
    if (!byCp.size) return;
    rail.querySelectorAll(".gcar-wrap").forEach((wrap) => {
      const im = byCp.get(wrap.getAttribute("data-checkpoint"));
      if (!im) return;
      wrap.classList.add(cls || (im.dir === "in" ? "gcar-preview-in" : "gcar-preview-out"));
      if (im.coverage === "partial") wrap.classList.add("gcar-preview-partial");
      const rect = wrap.querySelector(".gcar");
      if (rect) rect.style.stroke = rect.getAttribute("fill");
    });
  }

  function clearCarPreview() {
    rail.querySelectorAll(".gcar-preview-in, .gcar-preview-out, .gcar-splitting").forEach((wrap) => {
      wrap.classList.remove("gcar-preview-in", "gcar-preview-out", "gcar-preview-partial", "gcar-splitting");
      const rect = wrap.querySelector(".gcar");
      // A hollow (reverted) car's inline identity stroke is part of its resting look -- keep it.
      if (rect && !rect.classList.contains("gcar-reverted")) rect.style.stroke = "";
    });
    rail.querySelectorAll(".split-ghost, .migrate-ghost").forEach((el) => el.remove());
  }

  // Where a row's car strip sits, read off the DOM rather than recomputed: the row's own hit
  // rect is drawn at absolute coordinates by renderLane, so this stays correct without depending
  // on `graphView.geom` (which only exists once the time axis has drawn).
  function laneBarY(row) {
    const hit = row.querySelector(".glane-hit");
    if (hit) return Number(hit.getAttribute("y")) + GANTT.rowH / 2 - GANTT.barH / 2;
    const car = row.querySelector(".gcar");
    return car ? Number(car.getAttribute("y")) : null;
  }

  // ─── Destination ghosts: the graph as it WILL BE ────────────────────────────────────────────
  // Merge and move RELOCATE recorded work, and a preview that only drains the source answers half
  // the question -- the reader is choosing a destination, and the destination showed nothing. Each
  // chunk that would re-home is drawn a second time, dashed, in the receiving lane at the same
  // point in time, in the SOURCE's identity hue: whose work is arriving, and where it lands. The
  // same in-situ move a plan ghost makes, run over recorded history instead of predicted work.
  function paintMigration(sourceId, targetId) {
    const src = findRow(sourceId);
    const dst = findRow(targetId);
    if (!src || !dst || typeof dst.appendChild !== "function") return;
    const barY = laneBarY(dst);
    if (barY == null) return;
    const cars = [...src.querySelectorAll(".gcar-wrap .gcar")];
    for (const car of cars) {
      const ghost = mk("rect", {
        x: car.getAttribute("x"), y: barY,
        width: car.getAttribute("width"), height: GANTT.barH, rx: 3,
        class: "migrate-ghost",
      });
      // Identity is the source's -- the point of the ghost is that THIS work goes THERE.
      ghost.style.stroke = car.getAttribute("fill");
      ghost.style.fill = car.getAttribute("fill");
      dst.appendChild(ghost);
    }
    paintLaneRelocating(sourceId); // ...and the same chunks read as leaving where they are now
  }

  // Split's destination is a lane that does not exist yet, so it is drawn in the row's own slack:
  // one ghost segment per moving chapter, on a dashed baseline directly under the cars they leave.
  // Per-car, not one merged bar -- the question a split answers is WHICH chapters go, and a single
  // span erases exactly that. Falls back to the sentence alone on a CLI without `moving_op_ids`.
  function paintSplitPreview(featureId, res) {
    const moving = (res && res.moving_op_ids) || [];
    if (!moving.length) return;
    const segs = segmentsOf(compose).filter((sg) => sg.feature_id === featureId);
    paintCarPreview(classifyCarImpact(moving, [], segs), "gcar-splitting");
    const row = findRow(featureId);
    if (!row || typeof row.appendChild !== "function") return; // the rail view has no car geometry
    const rects = [...row.querySelectorAll(".gcar-wrap.gcar-splitting .gcar")];
    if (!rects.length) return;
    const barY = laneBarY(row);
    if (barY == null) return;
    const stripY = barY + GANTT.barH + 2;
    const newCount = (res.groups && res.groups[1] && res.groups[1].length) || 0;
    const tip = `the new lane: ${newCount} symbol(s) split off here`
      + (res.new_id ? ` as ${String(res.new_id).slice(0, 10)}…` : "");
    let x0 = Infinity, x1 = -Infinity, color = "#888";
    for (const r of rects) {
      const x = Number(r.getAttribute("x"));
      const w = Number(r.getAttribute("width"));
      x0 = Math.min(x0, x);
      x1 = Math.max(x1, x + w);
      color = r.getAttribute("fill") || color;
      const seg = mk("rect", {
        x, y: stripY, width: w, height: 4, rx: 2, class: "split-ghost split-ghost-car",
      }, [mk("title", { text: tip })]);
      seg.style.fill = color;
      row.appendChild(seg);
    }
    // The baseline ties the segments into one lane rather than leaving three unrelated stubs.
    const base = mk("line", {
      x1: x0, x2: x1, y1: stripY + 2, y2: stripY + 2, class: "split-ghost split-ghost-base",
    }, [mk("title", { text: tip })]);
    base.style.stroke = color;
    row.appendChild(base);
  }

  // Merge/move/split RELOCATE recorded chunks rather than deleting them, so their preview keeps
  // each car's fill and nudges it out in dashed outline -- visually distinct from revert's
  // draining and restore's filling.
  function paintLaneRelocating(featureId) {
    paintCarPreview(
      segmentsOf(compose)
        .filter((sg) => sg.feature_id === featureId)
        .map((sg) => ({ checkpoint: sg.checkpoint, featureId, dir: "out", coverage: "full" })),
      "gcar-splitting");
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
  function enterPreviewMode(focus, targetId, verb) {
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
        // The row the user clicked, and until now the ONE row with no direction on it: `target`
        // short-circuited before either morph, so the lane being restored and the lane being
        // reverted were painted identically -- a neutral outline and an `N → M` count. It is the
        // row being looked at, so it is the row that most has to say which way this goes.
        row.classList.add("preview-target", verb === "restore" ? "preview-gaining" : "preview-losing");
        if (n.ops_after > n.ops_before) row.classList.add("preview-arriving");
        else if (n.ops_after === 0) row.classList.add("preview-leaving");
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
    if (focus.context_count > 0) setPreviewContext(`＋${focus.context_count} unchanged`);
  }

  function exitPreviewMode() {
    if (!previewActive) return;
    previewActive = false;
    const svg = rail.querySelector("svg");
    if (svg) svg.classList.remove("preview");
    rail.querySelectorAll(".preview-lit").forEach((el) =>
      el.classList.remove(
        "preview-lit", "preview-target", "preview-blast", "preview-foundation",
        "preview-leaving", "preview-arriving", "preview-gaining", "preview-losing",
      ));
    rail.querySelectorAll(".gbar-count.preview-delta").forEach((c) => {
      if (c.hasAttribute("data-orig")) {
        c.textContent = c.getAttribute("data-orig");
        c.removeAttribute("data-orig");
      }
      c.classList.remove("preview-delta", "losing", "gaining");
    });
    setPreviewContext(null);
    clearOffscreenPills();
    // Entering a preview drops the `focus` class (the two dims would compound), so leaving one has
    // to put it back -- otherwise previewing anything silently un-dimmed a pinned lane or a live
    // search, and the banner went on claiming the field was dimmed.
    applyLens();
  }

  // The one preview-scoped sentence, wherever it comes from. Two writers and one clearer of the same
  // element, and the clearer cannot live in `exitPreviewMode` alone -- that early-returns unless a
  // Focus & Morph overlay is live, so a pill set by the lighter ghost path would stay on screen.
  // `kind` marks who owns the pill, so a clear can be precise: `true`/"pending" is work in
  // flight, "identity" is the neutral "this is what you are pointing at" a chapter hover writes.
  // Without that, moving the cursor off a chapter would wipe an unrelated "applying merge…".
  function setPreviewContext(text, kind) {
    previewContext.hidden = text === null;
    previewContext.textContent = text || "";
    previewContext.classList.toggle("pending", kind === true || kind === "pending");
    previewContext.classList.toggle("identity", kind === "identity");
  }

  // A blocked-restore overlay: sgt refuses to restore a symbol that has a competing live version
  // (`ok:false, forked:true`). Show the two ways out as plain text -- swap (revert the live tip, then
  // restore) and reconcile (`sgt resolve <symbol>`) -- mirroring the CLI's own refusal, rather than
  // swallowing the preview. The symbol is the preview's `file::symbol` target, else its first
  // affected symbol. Hover-scoped; torn down by clearGhosts on the same mouseleave path as the ghosts.
  function showRestoreRefusal(res, fallbackId) {
    const sym = res.target && String(res.target).includes("::") ? res.target
      : (res.affected_symbols && res.affected_symbols[0]) || res.target || fallbackId;
    showRefusal(res.message || "Can't restore — this symbol has a competing live version.",
                ["swap · revert the live tip, then restore", `reconcile · sgt resolve ${sym}`]);
  }

  // The refusal card: a head, plus a remedy line per way out. A blocked restore has two; a feature
  // with no cut in it has none -- the refusal is the whole answer, and saying it is the point. It
  // used to say nothing at all for split, which reads as "still thinking" rather than "no".
  function showRefusal(message, remedies = []) {
    previewRefusal.innerHTML = "";
    const head = document.createElement("div");
    head.className = "refusal-head";
    head.textContent = humanizeRefusal(message);
    previewRefusal.appendChild(head);
    for (const text of remedies) {
      const line = document.createElement("div");
      line.className = "refusal-remedy";
      line.textContent = text;
      previewRefusal.appendChild(line);
    }
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

  /** The car wrap for a `<feature>@<n>` checkpoint. Attribute equality, not a template-literal
   * selector, for the same reason findRow uses it: a ref with a quote in it must not break the
   * query. */
  function findCar(checkpoint) {
    let found = null;
    rail.querySelectorAll(".gcar-wrap").forEach((el) => {
      if (el.getAttribute("data-checkpoint") === checkpoint) found = el;
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

  function renderArmedBanner(answer) {
    if (!armedBanner) return;
    const n = armedVerb && byId(armedVerb.feature);
    const text = armedBannerText(armedVerb, n && n.label);
    armedBanner.hidden = text === null;
    // Two elements, not one string with a newline: the standing question and the answer for *this*
    // candidate carry different weight, and the answer is the half the eye should land on. Same
    // primary/secondary pairing the refusal card uses for its head and remedies.
    armedBanner.textContent = "";
    if (text === null) return;
    armedBanner.appendChild(el("div", "armed-q", text));
    if (answer) armedBanner.appendChild(el("div", "armed-a", answer));
  }

  function triggerAction(verb, id) {
    if (verb === "rename") {
      vscode.postMessage({ type: "renamePrompt", feature: id });
      return;
    }
    if (verb === "merge" || verb === "move") {
      armedVerb = { verb, feature: id };
      rail.classList.add("arming");
      renderArmedBanner();
      return;
    }
    if (verb === "split") {
      // Staged like revert/restore: the cut is held on the graph (moving cars + the ghost of the
      // new lane) and the confirm bar takes the decision.
      const node = byId(id);
      stageAction({ verb: "split", ref: id, targetId: id, label: node && node.label, kind: "split" });
      return;
    }
    if (verb === "revert" || verb === "restore") {
      // Staged in the graph: the consequence preview is held on the field and the confirm bar
      // takes the decision -- no modal between the reader and the picture.
      const node = byId(id);
      stageAction({ verb, ref: id, targetId: id, label: node && node.label, kind: "feature" });
    }
  }

  function confirmArmed(targetId) {
    const { verb, feature } = armedVerb;
    armedVerb = null;
    rail.classList.remove("arming");
    renderArmedBanner();
    clearGhosts();
    if (targetId === feature) return;
    // The apply round-trips a subprocess + a full graph rebuild; say so where the eye already is.
    setPreviewContext(`applying ${verb}…`, true);
    if (verb === "merge") {
      vscode.postMessage({ type: "applyVerb", verb: "merge", args: [targetId, feature] });
    } else if (verb === "move") {
      vscode.postMessage({ type: "applyVerb", verb: "move", args: [...opIdsFor(feature), targetId] });
    }
  }

  // Minimize/restore the detail pane -- hands the full width to the timeline when docked narrow.
  inspectorToggle.addEventListener("click", () => {
    state.inspectorCollapsed = !state.inspectorCollapsed;
    saveState();
    render(); // re-measures the rail against the now-full width via the ResizeObserver too
  });

  const loopBtns = {
    save: document.getElementById("saveBtn"),
    undo: document.getElementById("undoBtn"),
  };
  let loopBusy = null; // the daily-loop verb running in the host, or null

  function renderLoopButtons() {
    for (const b of loopButtonState(loopBusy)) {
      loopBtns[b.verb].textContent = b.label;
      loopBtns[b.verb].disabled = b.disabled;
    }
  }

  for (const verb of Object.keys(loopBtns)) {
    loopBtns[verb].addEventListener("click", () => {
      // Set busy on the click, not on the host's answer: a round trip is long enough for a second
      // click to land, and that second click is the thing this state exists to stop.
      loopBusy = verb;
      renderLoopButtons();
      vscode.postMessage({ type: "dailyLoop", verb });
    });
  }

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (msg.type === "state") {
      // A fresh composition means the mutation landed (`store.invalidate()` pushes this), so the
      // buttons come back here as well as on the host's own end signal -- whichever arrives first.
      // The end signal is the backstop for the paths that never reach a mutation: a cancelled
      // dialog, "nothing to save", a failure.
      if (loopBusy !== null) {
        loopBusy = null;
        renderLoopButtons();
      }
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
      changeCache = {};
      pendingChange = null; // in flight against an ideal this push replaced
      playheadResultCache = {};
      playheadCommitIndex = null; // a new composition means a different commit-index axis
      // A held (not yet applying) staged confirm is stale the moment the composition changes under
      // it -- its consequence was computed against a world that no longer exists. Drop it. A BUSY
      // one stays: this push IS its own mutation landing, and the done phase closes the bar.
      if (stagedAction && !applyBusy) cancelStaged();
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
      if (!stagedAction) setPreviewContext(null); // an "applying…" note is answered by this push
      if (pendingReveal) revealFeature(pendingReveal); // deliver a reveal that arrived before its lane existed
      // The receipt: the lane a staged action just rewrote settles with a one-shot flash, so the
      // change is visible IN the graph -- the same place the preview promised it.
      if (pendingSettle.length) {
        for (const fid of pendingSettle) {
          const row = findRow(fid);
          if (row) {
            row.classList.add("settle-flash");
            setTimeout(() => row.classList.remove("settle-flash"), 1500);
          }
        }
        pendingSettle = [];
      }
    } else if (msg.type === "applyProgress") {
      onApplyProgress(msg);
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
    } else if (msg.type === "changeResult" && pendingChange && pendingChange.seq === msg.seq) {
      changeCache[pendingChange.key] = {
        ok: msg.ok !== false, message: msg.message, files: msg.files || {},
      };
      pendingChange = null;
      renderInspector();
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
      changeCache = {};
      pendingChange = null;
      renderTitlebar();
      renderInspector();
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
    } else if (msg.type === "askedResult" && pendingAsked && pendingAsked.seq === msg.seq) {
      const ref = pendingAsked.ref;
      pendingAsked = null;
      askedCache[ref] = msg.ok !== false && (msg.asks || []).length
        ? { asks: msg.asks }
        : { error: msg.message || "nothing was captured for it" };
      if (askedOpen === ref) renderInspector();
    } else if (msg.type === "findResult") {
      renderFindResults(msg);
    } else if (msg.type === "agentAction") {
      showAgentAction(msg);
    } else if (msg.type === "revealFeature") {
      revealFeature(msg.featureId);
    } else if (msg.type === "loopBusy") {
      loopBusy = msg.verb || null;
      renderLoopButtons();
    } else if (msg.type === "error") {
      inspector.innerHTML = "";
      inspector.appendChild(statusLine(msg.message, "error"));
    }
  });

  window.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    if (stagedAction && !applyBusy) {
      cancelStaged();
      return;
    }
    if (armedVerb) {
      armedVerb = null;
      rail.classList.remove("arming");
      renderArmedBanner();
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

  initFind();
  vscode.postMessage({ type: "ready" });
  render();
})();
