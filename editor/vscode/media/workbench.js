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
function computeGraphLayout(map, history, opts) {
  opts = opts || {};
  const collapsed = new Set(opts.collapsed || []);
  const frontier = opts.frontier == null ? Infinity : opts.frontier;
  const topK = opts.topK || 4;

  const byId = {};
  for (const n of map.nodes || []) byId[n.id] = n;

  // Ops per feature, filtered to the frontier -- the magnitude + temporal signal for each leaf.
  const opsByFeature = {};
  for (const op of (history && history.ops) || []) {
    if (op.feature_id == null || op.commit_index > frontier) continue;
    (opsByFeature[op.feature_id] || (opsByFeature[op.feature_id] = [])).push(op);
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
  function visit(id) {
    const node = byId[id];
    if (!node) return;
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

  // Group lanes into swimlanes and order by first appearance. A group is either an expanded
  // subsystem (a header row + its feature lanes) or a "solo" row (a meta-lane, or a feature with no
  // subsystem). Groups sort by their earliest firstCommit; lanes within a group likewise. Rows are
  // assigned top-to-bottom, counting each subsystem header as its own row.
  const headerGroups = {};
  const groups = [];
  for (const l of lanes) {
    if (l.isMeta || l.subsystem == null) {
      groups.push({ key: l.id, isHeader: false, laneIds: [l.id], firstCommit: l.firstCommit });
      continue;
    }
    let g = headerGroups[l.subsystem];
    if (!g) {
      const sub = byId[l.subsystem];
      g = headerGroups[l.subsystem] = {
        key: l.subsystem, isHeader: true, label: (sub && sub.label) || l.subsystem,
        collapsedId: l.subsystem, laneIds: [], firstCommit: Infinity,
      };
      groups.push(g);
    }
    g.laneIds.push(l.id);
    g.firstCommit = Math.min(g.firstCommit, l.firstCommit);
  }
  groups.sort((a, b) => a.firstCommit - b.firstCommit || (a.key < b.key ? -1 : 1));

  let row = 0;
  const headers = [];
  for (const g of groups) {
    const laneObjs = g.laneIds.map((id) => laneById[id])
      .sort((a, b) => a.firstCommit - b.firstCommit || (a.id < b.id ? -1 : 1));
    if (g.isHeader) {
      headers.push({
        key: g.key, label: g.label, collapsedId: g.collapsedId, row,
        firstCommit: g.firstCommit, lastCommit: Math.max(...laneObjs.map((l) => l.lastCommit)),
        opCount: laneObjs.reduce((s, l) => s + l.opCount, 0), laneCount: laneObjs.length,
      });
      row++; // the header occupies its own row
    }
    for (const l of laneObjs) {
      l.row = row++;
      l.groupKey = g.key;
    }
  }
  const rowCount = Math.max(1, row);

  return { lanes, headers, edges, overflow, laneById, opsByFeature, rowCount,
    commitCount: ((history && history.commits) || []).length };
}
// ---- end-graph-layout (test slice boundary) ----

// The episodic projection (Stage C): roll the flat op stream into EPISODES -- one per commit that
// carried ops -- and group episodes by their dominant feature into collapsible episode-groups (the
// "co-commit cluster" a developer rewinds as a unit). Sessions are empty on mined history (only
// sgt's own land/checkpoint stamp them), so the episode axis is projected from provenance: an op's
// commit_index identifies its earliest provenance commit, so ops sharing a commit_index were
// advanced in the same commit = one episode -- exactly the co-commit signal Stage B clusters on.
// Real sgt sessions supersede this going forward; the shape is identical. Pure (no DOM); the Python
// counterpart is `episodes()` in sgt/tui/graph.py, kept behaviour-parallel.
function rollupEpisodes(map, history) {
  const labels = {};
  for (const n of (map && map.nodes) || []) labels[n.id] = n.label || n.id;
  const subjectOf = {}, shaOf = {};
  for (const c of (history && history.commits) || []) {
    subjectOf[c.index] = c.subject || "";
    shaOf[c.index] = c.sha;
  }
  const byIndex = new Map();
  for (const op of (history && history.ops) || []) {
    const idx = op.commit_index;
    let ep = byIndex.get(idx);
    if (!ep) {
      ep = { index: idx, sha: shaOf[idx], subject: subjectOf[idx] || "", opIds: [], features: {}, kinds: {} };
      byIndex.set(idx, ep);
    }
    ep.opIds.push(op.id);
    if (op.feature_id != null) ep.features[op.feature_id] = (ep.features[op.feature_id] || 0) + 1;
    if (op.kind) ep.kinds[op.kind] = (ep.kinds[op.kind] || 0) + 1;
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
    status: { oracle: { configured: false, status: "pending" } }, sessions: { sessions: [] }, proposals: [],
  };
  let map = compose.map;
  let history = compose.history;
  let layout = computeGraphLayout(map, history, { collapsed: state.collapsed });
  let armedVerb = null; // {verb, feature} while "Merge into..."/"Move ops..." is picking a target
  let previewSeq = 0;
  let pendingPreview = null;
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

  // Plan marks: predicted steps render as a dashed accent ring + count badge on their predicted
  // feature's node (see collectPlanMarks / renderNodeBadges). `knownPlanSteps`/`prevKnownPlanSteps`
  // snapshot ids across renders so an "entering" pulse fires only on a genuine transition.
  let planMarks = { steps: [], byFeature: {}, floating: [], sessions: [] };
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

  const rail = document.getElementById("rail");
  const inspector = document.getElementById("inspector");
  const compositionBtn = document.getElementById("compositionBtn");
  const oracleChip = document.getElementById("oracleChip");
  const offscreenAbove = document.getElementById("offscreenAbove");
  const offscreenBelow = document.getElementById("offscreenBelow");
  let planChipsEl = null; // created lazily on first renderTitlebar(), inserted after oracleChip
  let viewToggleEl = null; // Gantt <-> Rail view switch, created lazily on first renderTitlebar()

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

  function saveState() {
    vscode.setState(state);
  }

  function recompute() {
    // Full history: the frontier scrubber dims nodes past its point (see applyFrontier) rather than
    // re-laying-out on every drag, so the layout stays stable while scrubbing.
    layout = computeGraphLayout(map, history, { collapsed: state.collapsed });
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
    compositionBtn.textContent = state.compositionLabel || "HEAD";
    const oracle = (compose.status && compose.status.oracle) || { configured: false, status: "pending" };
    const st = oracle.configured ? oracle.status : "unconfigured";
    oracleChip.dataset.state = st;
    oracleChip.textContent = `oracle: ${st}`;

    if (!viewToggleEl) {
      viewToggleEl = document.createElement("button");
      viewToggleEl.className = "view-toggle";
      viewToggleEl.addEventListener("click", () => {
        state.view = state.view === "rail" ? "gantt" : "rail";
        saveState();
        render();
      });
      compositionBtn.insertAdjacentElement("beforebegin", viewToggleEl);
    }
    viewToggleEl.textContent = state.view === "rail" ? "◫ Rail" : "▤ Timeline";
    viewToggleEl.title = state.view === "rail"
      ? "Showing the episode rail (what I did, in order) — click for the feature timeline"
      : "Showing the feature timeline (Gantt) — click for the episode rail";

    if (!planChipsEl) {
      planChipsEl = document.createElement("div");
      planChipsEl.className = "plan-chips";
      oracleChip.insertAdjacentElement("afterend", planChipsEl);
    }
    planChipsEl.innerHTML = "";
    for (const session of planMarks.sessions) {
      planChipsEl.appendChild(renderPlanChip(session));
    }
    // Drift/forks with no matching row have nowhere on the rail to attach -- surfaced the same
    // way an unplaced plan step is, rather than dropping the signal silently.
    if (driftMarks.unplaced.length) {
      const chip = document.createElement("span");
      chip.className = "plan-chip";
      chip.textContent = `⚠ ${driftMarks.unplaced.length} unplaced drift`;
      chip.title = driftMarks.unplaced.map((e) => e.footprint.join(", ")).join("\n");
      planChipsEl.appendChild(chip);
    }
    if (forkMarks.unplaced.length) {
      const chip = document.createElement("span");
      chip.className = "plan-chip";
      chip.textContent = `⑂ ${forkMarks.unplaced.length} unplaced fork(s)`;
      chip.title = forkMarks.unplaced.map((f) => f.symbol).join("\n");
      chip.addEventListener("click", () => vscode.postMessage({ type: "resolveFork", symbol: forkMarks.unplaced[0].symbol }));
      planChipsEl.appendChild(chip);
    }
  }

  // One compact "Plan · text" chip per active session with an inline progress ring -- replaces
  // the old ghost-root row's ring now that predictions live on the rail itself, not in a subtree.
  function renderPlanChip(session) {
    const chip = document.createElement("span");
    chip.className = "plan-chip";
    if (session.stepCount > 0 && session.matchedCount === session.stepCount) chip.classList.add("complete");
    chip.title = session.planText;
    const ring = mk("svg", { width: 14, height: 14, viewBox: "-7 -7 14 14" });
    ring.appendChild(renderPlanRing(0, session.matchedCount, session.stepCount));
    chip.appendChild(ring);
    const label = document.createElement("span");
    const floatingForSession = planMarks.floating.filter((s) => s.sessionId === session.sessionId);
    label.textContent = `Plan · ${session.matchedCount}/${session.stepCount}` +
      (floatingForSession.length ? ` · ${floatingForSession.length} unplaced` : "");
    chip.appendChild(label);
    chip.addEventListener("click", () => selectPlanSession(session.sessionId));
    return chip;
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
      const clo = view && view.ok ? ` → ${view.closure_op_count} op` : "";
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
  // One lane per feature: a left gutter (identity swatch + label) and a bar over a shared time
  // axis, spanning [firstCommit, lastCommit] with ops binned along it as a density heatstrip. Rows
  // are grouped into subsystem swimlanes. The width tracks the pane (time compresses to fit, no
  // horizontal scroll); it scrolls vertically only when there are more lanes than fit. A resize
  // re-runs render() via the ResizeObserver, so the axis reflows continuously.
  const GANTT = { padT: 14, rowH: 26, barH: 12, axisH: 34, minBarW: 6, gutterPad: 8, cellGap: 0.5 };
  let graphView = null; // { geom, handleEl, frontierEl, veilEl } -- set each render for the scrubber

  function ganttGeom() {
    const paneW = Math.max(rail.clientWidth || 0, 320);
    const labelW = Math.round(Math.max(92, Math.min(200, paneW * 0.36)));
    const plotX0 = labelW + GANTT.gutterPad;
    const plotW = Math.max(60, paneW - plotX0 - 16);
    const w = paneW;
    const rowsH = layout.rowCount * GANTT.rowH;
    const axisY = GANTT.padT + rowsH + 12;
    const h = axisY + GANTT.axisH;
    const maxCommit = Math.max(1, layout.commitCount - 1);
    const xOf = (ci) => plotX0 + (Math.max(0, Math.min(maxCommit, ci)) / maxCommit) * plotW;
    return {
      labelW, plotX0, plotW, w, h, axisY, maxCommit,
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

  // Density buckets: bin every lane's ops into the same set of time columns (so a column means the
  // same commit-range across lanes -- vertical alignment reads as "worked on together"). The global
  // max bucket normalizes intensity, sqrt-scaled so a 2000-op column still leaves a 1-op one visible.
  function densityBuckets(geom) {
    const bucketCount = Math.max(1, Math.min(160, Math.round(geom.plotW / 5)));
    const bucketOf = (ci) => Math.max(0, Math.min(bucketCount - 1,
      Math.floor((Math.max(0, Math.min(geom.maxCommit, ci)) / geom.maxCommit) * bucketCount)));
    const byLane = {};
    let gmax = 1;
    for (const l of layout.lanes) {
      const b = new Array(bucketCount).fill(0);
      for (const ci of l.commits) b[bucketOf(ci)]++;
      byLane[l.id] = b;
      for (const v of b) if (v > gmax) gmax = v;
    }
    return { byLane, gmax, bucketCount, bucketW: geom.plotW / bucketCount };
  }

  function renderGraph() {
    if (state.view === "rail") { renderRail(); return; }
    const prevScroll = rail.scrollTop;
    rail.innerHTML = "";
    const geom = ganttGeom();
    const density = densityBuckets(geom);
    const svg = mk("svg", { width: geom.w, height: geom.h, class: "railsvg gantt" });
    const bandLayer = mk("g", { class: "swimlanes" });
    const laneLayer = mk("g", { class: "glanes" });
    svg.appendChild(bandLayer);
    svg.appendChild(laneLayer);

    for (const hd of layout.headers) bandLayer.appendChild(renderSwimlaneHeader(hd, geom));
    for (const l of layout.lanes) laneLayer.appendChild(renderLane(l, geom, density));
    renderTimeAxis(svg, geom);

    rail.appendChild(svg);
    rail.scrollTop = prevScroll;
  }

  // ─── The episode rail (vertical git-log) ────────────────────────────────────────────────────
  // "What I did, in order": newest commit-episode on top, each feature a lane column (its episodes
  // a straight vertical spine), lanes reused across non-overlapping spans (episodeRailLayout's
  // interval coloring). Clicking a row selects that episode's feature -- the same select path the
  // Gantt uses, so revert/preview/multi-select all work identically from here.
  const RAIL = { rowH: 22, laneW: 16, padT: 10, dotR: 4, padL: 12, shaW: 58 };

  function renderRail() {
    const prevScroll = rail.scrollTop;
    rail.innerHTML = "";
    graphView = null; // no frontier scrubber in rail mode; drop the stale Gantt handle
    const rlayout = episodeRailLayout(rollupEpisodes(map, history));
    const rows = rlayout.rows;
    const paneW = Math.max(rail.clientWidth || 0, 320);
    const gutterW = RAIL.padL + rlayout.laneCount * RAIL.laneW;
    const h = RAIL.padT * 2 + rows.length * RAIL.rowH;
    const svg = mk("svg", { width: paneW, height: Math.max(h, 40), class: "railsvg rail" });
    const yOf = (row) => RAIL.padT + row * RAIL.rowH + RAIL.rowH / 2;
    const xOf = (lane) => RAIL.padL + lane * RAIL.laneW + RAIL.laneW / 2;

    if (!rows.length) {
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
        class: "rail-spine", stroke: laneColor(fid || ""),
      }));
    }
    svg.appendChild(spineLayer);

    const textX = gutterW + 8;
    const subjChars = Math.max(8, Math.floor((paneW - textX - RAIL.shaW - 12) / 6.2));
    for (const r of rows) {
      const inSel = r.feature === state.selected || (state.multi || []).includes(r.feature);
      const g = mk("g", { class: "rail-row" + (inSel ? " selected" : ""), "data-id": r.feature || "" });
      g.appendChild(mk("rect", {
        x: 0, y: RAIL.padT + r.row * RAIL.rowH, width: paneW, height: RAIL.rowH, class: "rail-hit",
      }));
      g.appendChild(mk("circle", {
        cx: xOf(r.lane), cy: yOf(r.row), r: RAIL.dotR, class: "rail-dot", fill: laneColor(r.feature || ""),
      }));
      g.appendChild(mk("text", { x: textX, y: yOf(r.row) + 4, class: "rail-sha", text: (r.sha || "").slice(0, 7) }));
      const subj = mk("text", { x: textX + RAIL.shaW, y: yOf(r.row) + 4, class: "rail-subject" });
      subj.textContent = truncate((r.subject || "").replace(/\n/g, " "), subjChars);
      g.appendChild(subj);
      if (r.feature) {
        g.addEventListener("click", (ev) => selectRow(r.feature, ev.metaKey || ev.ctrlKey || ev.shiftKey));
      }
      svg.appendChild(g);
    }
    rail.appendChild(svg);
    rail.scrollTop = prevScroll;
  }

  // A subsystem swimlane header: a faint full-width band with a ▾ caret + label + "(N features · M
  // ops)", and the group's [first,last] span drawn faintly in the plot. Clicking collapses the
  // subsystem back to a single meta-lane (toggleCollapse), so it's the "fold this cluster" affordance.
  function renderSwimlaneHeader(hd, geom) {
    const y = geom.rowY(hd.row);
    const g = mk("g", { class: "swimlane", "data-id": hd.collapsedId, "data-first": hd.firstCommit });
    g.appendChild(mk("rect", { x: 0, y, width: geom.w, height: GANTT.rowH, class: "swimlane-band" }));
    g.appendChild(mk("text", { x: 8, y: y + GANTT.rowH / 2 + 4, class: "swimlane-caret", text: "▾" }));
    const label = mk("text", { x: 22, y: y + GANTT.rowH / 2 + 4, class: "swimlane-label" });
    label.textContent = truncate(hd.label, Math.floor((geom.labelW - 30) / 6.5));
    g.appendChild(label);
    // The subsystem's own activity envelope in the plot, so the header still shows "when".
    const bx = geom.xOf(hd.firstCommit), bx2 = geom.xOf(hd.lastCommit);
    g.appendChild(mk("rect", {
      x: bx, y: y + GANTT.rowH / 2 - 2, width: Math.max(GANTT.minBarW, bx2 - bx), height: 4,
      rx: 2, class: "swimlane-span", "data-first": hd.firstCommit,
    }));
    const meta = mk("text", { x: geom.plotX0 - 8, y: y + GANTT.rowH / 2 + 4, class: "swimlane-meta" });
    meta.textContent = `${hd.laneCount} feat · ${hd.opCount}`;
    g.appendChild(meta);
    g.addEventListener("click", () => toggleCollapse(hd.collapsedId)); // fold cluster -> meta-lane
    return g;
  }

  function renderLane(l, geom, density) {
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

    // Left gutter: identity swatch (▸ caret for a folded subsystem), then the label.
    const gx = GANTT.gutterPad;
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
    g.appendChild(label);

    // Base track: the feature's lifetime [first,last], so even a sparse feature shows its span.
    const x1 = geom.xOf(l.firstCommit), x2 = geom.xOf(l.lastCommit);
    g.appendChild(mk("rect", {
      x: x1, y: barY, width: Math.max(GANTT.minBarW, x2 - x1), height: GANTT.barH, rx: 3,
      class: "gbar-track", fill: color,
    }));
    // Density heatstrip: one cell per non-empty time bucket, opacity by sqrt(count / global max).
    const buckets = density.byLane[l.id] || [];
    for (let b = 0; b < buckets.length; b++) {
      if (!buckets[b]) continue;
      const cell = mk("rect", {
        x: geom.plotX0 + b * density.bucketW, y: barY,
        width: density.bucketW + GANTT.cellGap, height: GANTT.barH, class: "gbar-cell", fill: color,
      });
      cell.setAttribute("fill-opacity", (0.28 + 0.72 * Math.sqrt(buckets[b] / density.gmax)).toFixed(3));
      g.appendChild(cell);
    }
    // Op count just past the bar (clamped so it stays on-screen).
    const cx = Math.min(x2 + 6, geom.w - 30);
    g.appendChild(mk("text", { x: cx, y: midY + 4, class: "gbar-count", text: String(l.opCount) }));

    renderLaneBadges(g, l, geom, color, barY, midY);

    g.addEventListener("mouseenter", () => onHover(l.id));
    g.addEventListener("mouseleave", () => onHover(null));
    g.addEventListener("click", (ev) => {
      if (l.isMeta) { toggleCollapse(l.id); return; } // expand the subsystem into its features
      if (armedVerb) { confirmArmed(l.id); return; }
      selectRow(l.id, ev.metaKey || ev.ctrlKey || ev.shiftKey);
    });
    return g;
  }

  // Plan / drift / fork are decorations ON the lane (never separate marks): a pending-plan lane gets
  // a dashed accent underline + count; a lane carrying a drift op gets a solid identity outline; a
  // forked lane gets a ⋔ badge in the gutter. None introduces a second hue competing with identity.
  function renderLaneBadges(g, l, geom, color, barY, midY) {
    const x1 = geom.xOf(l.firstCommit), x2 = geom.xOf(l.lastCommit);
    const barW = Math.max(GANTT.minBarW, x2 - x1);
    const hasDrift = (layout.opsByFeature[l.id] || []).some((op) => driftMarks.ids.has(op.id));
    if (hasDrift) {
      g.appendChild(mk("rect", {
        x: x1 - 1.5, y: barY - 1.5, width: barW + 3, height: GANTT.barH + 3, rx: 4,
        class: "gbar-drift", stroke: color,
      }));
    }
    const pending = (planMarks.byFeature[l.id] || []).length;
    if (pending) {
      g.appendChild(mk("line", {
        x1, x2: x1 + barW, y1: barY + GANTT.barH + 3, y2: barY + GANTT.barH + 3, class: "gbar-plan",
      }));
      g.appendChild(mk("text", { x: x1 + barW + 5, y: barY + GANTT.barH + 6, class: "gbar-plan-count", text: `+${pending}` }));
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
      svg.appendChild(mk("text", {
        x: tx, y: y + 16, class: "axis-tick" + (i === 0 ? " start" : i === 4 ? " end" : ""), text: `c${ci}`,
      }));
    }
    svg.appendChild(mk("text", { x: geom.plotX0, y: GANTT.padT - 3, class: "axis-title", text: "time →" }));

    const frontier = playheadCommitIndex == null ? geom.maxCommit : playheadCommitIndex;
    const fx = geom.scrubX(frontier);
    const veil = mk("rect", {
      x: fx, y: GANTT.padT, width: Math.max(0, geom.plotX0 + geom.plotW - fx), height: y - GANTT.padT,
      class: "future-veil" + (playheadCommitIndex == null ? " at-head" : ""),
    });
    const line = mk("line", { x1: fx, x2: fx, y1: GANTT.padT - 2, y2: y, class: "frontier-line" + (playheadCommitIndex == null ? " at-head" : "") });
    const handle = mk("path", { d: `M ${fx - 5} ${y + 3} L ${fx + 5} ${y + 3} L ${fx} ${y - 4} Z`, class: "frontier-handle", "data-cx": fx });
    svg.appendChild(veil);
    svg.appendChild(line);
    svg.appendChild(handle);
    graphView = { geom, handleEl: handle, frontierEl: line, veilEl: veil };
    handle.addEventListener("pointerdown", onScrubPointerDown);
    // click anywhere in the plot (on the svg background, not a lane) jumps the frontier there
    svg.addEventListener("pointerdown", (ev) => {
      if (ev.target !== svg) return;
      const lx = svgLocalX(svg, ev.clientX);
      if (lx < geom.plotX0 - 4) return; // gutter clicks aren't scrubs
      setPlayhead(geom.xToCommit(lx));
    });
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
    // Dim the gutter (label + swatch) of lanes/swimlanes not yet born; the veil handles the plot.
    for (const el of svg.querySelectorAll(".glane, .swimlane")) {
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
    const svg = rail.querySelector("svg");
    if (!svg || !graphView) return;
    playheadDragging = true;
    if (svg.setPointerCapture) svg.setPointerCapture(ev.pointerId);
    setPlayhead(graphView.geom.xToCommit(svgLocalX(svg, ev.clientX)));
    window.addEventListener("pointermove", onScrubPointerMove);
    window.addEventListener("pointerup", onScrubPointerUp);
  }

  function onScrubPointerMove(ev) {
    if (!playheadDragging) return;
    const svg = rail.querySelector("svg");
    if (!svg || !graphView) return;
    setPlayhead(graphView.geom.xToCommit(svgLocalX(svg, ev.clientX)));
  }

  function onScrubPointerUp() {
    playheadDragging = false;
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
        rail.querySelectorAll(".glane").forEach((el) => {
          if (el.getAttribute("data-id") === id) el.classList.add("ghost-target");
        });
        previewArmed(id);
      }
      return;
    }
    // Focus the hovered lane + its co-change neighbors; dim the rest (the "what changes with this").
    // This is the only place co-change is shown -- kept off the default view to avoid a hairball.
    svg.classList.add("focus");
    const neighbors = neighborsOf(id);
    svg.querySelectorAll(".glane").forEach((el) => {
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
    rail.querySelectorAll(".glane.ghost-blast, .glane.ghost-target, .glane.ghost-foundation").forEach((el) => {
      el.classList.remove("ghost-blast", "ghost-target", "ghost-foundation");
    });
    clearOffscreenPills();
  }

  function requestPreview(verb, args, onResult) {
    const seq = ++previewSeq;
    vscode.postMessage({ type: "previewVerb", verb, args, seq });
    pendingPreview = { seq, onResult };
  }

  // Every hover-preview site wants the same thing: paint the revert closure if the preview came
  // back ok, do nothing otherwise. The target is args[0] (revert/restore take one feature).
  function previewAndBlast(verb, args) {
    requestPreview(verb, args, (res) => {
      if (res && res.ok) paintClosure(classifyAffected(res, args[0]));
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
      meta.textContent = `${node.id} · ${node.size} member(s) · ${node.op_count} op(s)`;
      inspector.appendChild(meta);

      if (node.kind === "feature") inspector.appendChild(renderActionBar(id));
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
        `${view.direct_op_count} direct op(s) · ${view.closure_op_count} in closure · ${(view.files || []).length} file(s)`;
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
        wrap.appendChild(statusLine(`⚠ hub ${view.hub.symbol} pulls ${view.hub.pulled_op_count} op(s)`, ""));
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
        btn.textContent = `Confirm match (${group.op_ids.length} op(s))`;
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
    const files = featureNode
      ? Object.fromEntries(Object.entries(allPaths).filter(([p]) => p.startsWith(featureNode.dir)))
      : allPaths;
    renderCachedFrontierBody(section, cached, files);
    inspector.appendChild(section);
  }

  // Composition-picker hover-preview panel: same shape as the playhead panel (unfiltered fold,
  // filtered to the selected feature's `dir` client-side), fed by `compositionPreviewResult`
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
    rail.querySelectorAll(".glane.ghost-blast").forEach((el) => el.classList.remove("ghost-blast"));
    rail.querySelectorAll(".glane").forEach((el) => {
      if (featureIds.includes(el.getAttribute("data-id"))) el.classList.add("ghost-blast");
    });
    renderOffscreenPills(featureIds);
  }

  // Paint a revert closure (classifyAffected) with the three distinct roles, so a hover reads as
  // "this one (target), these lose ops (blast), these get re-drafted (foundation)" -- not one
  // undifferentiated amber blob. Off-screen pills cover every role so nothing affected hides
  // outside the scroll window.
  function paintClosure(closure) {
    rail.querySelectorAll(".glane").forEach((el) => {
      el.classList.remove("ghost-target", "ghost-blast", "ghost-foundation");
      const id = el.getAttribute("data-id");
      if (id === closure.target) el.classList.add("ghost-target");
      else if (closure.blast.includes(id)) el.classList.add("ghost-blast");
      else if (closure.foundation.includes(id)) el.classList.add("ghost-foundation");
    });
    renderOffscreenPills([closure.target, ...closure.blast, ...closure.foundation]);
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
    rail.querySelectorAll(".glane").forEach((el) => {
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
    oracleChip.textContent = "oracle: running…";
    vscode.postMessage({ type: "runOracle" });
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
      map = compose.map || { nodes: [], roots: [], edges: [] };
      planMarks = collectPlanMarks(compose.plan, history);
      driftMarks = collectDriftMarks(compose.drift, history);
      forkMarks = collectForkMarks(compose.forks, map.nodes);
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

  // Continuous reflow: the workbench is a WebviewView the user resizes freely left<->right, and
  // the axis width is derived from the pane width (measureAxis). Re-render on width change so the
  // ops re-spread and the graph re-flows -- debounced, and gated on a real width delta so the
  // scrollbar appearing/disappearing mid-render can't feed a render back into itself.
  let lastRailWidth = 0;
  let resizeTimer = null;
  new ResizeObserver(() => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const w = rail.clientWidth;
      if (Math.abs(w - lastRailWidth) < 4) return;
      lastRailWidth = w;
      render();
    }, 80);
  }).observe(rail);

  vscode.postMessage({ type: "ready" });
  render();
})();
