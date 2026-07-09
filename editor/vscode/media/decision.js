// This file used to be the Decision Graph webview's client script. U13 retired the decision
// webview (and decisionView.ts/activity.ts/codelens.ts/hover.ts with it) in favor of the
// feature-map projection, but two node-driven test suites still slice pure functions out of this
// file, so the functions themselves survive here unmodified:
//   * `tests/test_color_parity.py` slices `GOLDEN` .. `colorFor` to check OKLCH parity against
//     `sgt/tui/color.py` and `editor/vscode/src/color.ts` (the three-impl color contract).
//   * `tests/test_decision_layout.py` slices `computeLayout` .. the `end-layout` marker to check
//     the git-log lane/row layout engine (a pure function with no DOM/webview dependency).
// Nothing here is loaded by the extension anymore; this file is a test fixture only.

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
  return oklchToHex(0.72, 0.13, ((hashId(id) * GOLDEN) % 1) * 360);
}

// ─── Layout engine ────────────────────────────────────────────────────────────────────────────
// Pure: takes the graph payload, returns row/lane assignments. No DOM, no color, no anime — so it
// can be sliced out and exercised under node (tests/test_decision_layout.py). The contract:
//   • one row per decision, newest landing on top; ties broken by a stable topological order over
//     the dependency edges (a node sits above the decisions it builds on), then feature, then id;
//   • each feature occupies exactly one lane (column) across the full row-span it touches, so its
//     decisions are a straight vertical line — and lanes are reused by features whose spans don't
//     overlap (greedy interval-graph coloring), giving a low column count with no overlap;
//   • a connector is NEVER hidden behind a dot. The overprint test (see wouldOverprint) rejects any
//     lane that would draw a connector's vertical run straight through an intervening dot, opening a
//     fresh column instead — so an integrator's fan always reads as visible branches, not a collapsed
//     trunk. This holds in BOTH packing modes; a hidden edge is never an acceptable tradeoff.
// `opts.avoidCrossings` (the "spread" toggle) only changes the choice *among* honest lanes: features
// are visited in dependency order and placed in the lane nearest the dependency they connect to, so
// connectors stay short and adjacent (the GitKraken-style tidy packing). With it off, the lowest free
// honest lane wins (a more compact, left-packed rail). Either way no connector is hidden.
function computeLayout(graph, opts) {
  const avoidCrossings = !!(opts && opts.avoidCrossings);
  const decisions = (graph.decisions || []).slice();
  const idSet = new Set(decisions.map((d) => d.id));
  // Transitive reduction over the builds-on DAG: drop A→C whenever a longer A→…→C builds-on path
  // already exists, so only the most direct dependency is ever drawn or routed. Those redundant
  // "skip" edges are exactly the long ones that spear intervening lanes, so dropping them is what
  // keeps the rail git-log clean. Computed once here so the layout (lane packing + spear test) and
  // the renderer share one edge set. revises/fork lineage is never reduced — it is the stored spine.
  const edges = reduceBuildsOn(graph.edges || [], idSet);

  // Topological rank within an equal-landing group: an edge src→dst means src depends on dst, so
  // dst should sit lower (older) than src. We give every node a depth = longest dependency chain
  // beneath it; deeper (more depended-upon) nodes get a higher rank so they sort toward the bottom.
  // This only ever discriminates between decisions that share a landing — landing dominates first.
  const deps = {}; // id -> [ids it depends on]
  for (const d of decisions) deps[d.id] = [];
  for (const e of edges) {
    if (idSet.has(e.src) && idSet.has(e.dst) && e.src !== e.dst) deps[e.src].push(e.dst);
  }
  const depthMemo = {};
  function depth(id, seen) {
    if (depthMemo[id] !== undefined) return depthMemo[id];
    if (seen.has(id)) return 0; // cycle guard (derived edges should be acyclic, but be safe)
    seen.add(id);
    let d = 0;
    for (const dn of deps[id]) d = Math.max(d, 1 + depth(dn, seen));
    seen.delete(id);
    return (depthMemo[id] = d);
  }
  for (const d of decisions) depth(d.id, new Set());

  // Row order. Default: newest landing on top, with dependency depth breaking ties so that within a
  // single landing the integrator (the most-depended-upon node — what a human reads as HEAD) floats
  // to the TOP, never sinking under the leaves it builds on. (Real planned decisions already carry
  // distinct dependency-topological landings, so the integrator is the newest; this tiebreak is what
  // keeps an equal-landing cohort right-side-up instead of upside-down.) Then feature, then id.
  // When the projection names a primary `head` (the in-force integrator) the order is instead rooted
  // there: HEAD first, then a DFS over the things it builds on (nearest/newest dependency just
  // beneath), then anything unreachable by landing — a fan becomes a rooted tree with HEAD on top,
  // while a spine graph is unchanged. See docs/design/2026-06-25-decision-graph-layout.md.
  const byLanding = (a, b) =>
    b.landing - a.landing ||
    depthMemo[b.id] - depthMemo[a.id] ||
    (a.feature < b.feature ? -1 : a.feature > b.feature ? 1 : 0) ||
    (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const head = graph.head;
  let ordered;
  if (head && idSet.has(head)) {
    const landingOf = {};
    for (const d of decisions) landingOf[d.id] = d.landing;
    const seen = new Set();
    const tree = [];
    const dfs = (id) => {
      if (seen.has(id)) return;
      seen.add(id);
      tree.push(id);
      for (const dn of deps[id].slice().sort((a, b) => landingOf[b] - landingOf[a])) dfs(dn);
    };
    dfs(head);
    // Decisions unreachable from HEAD's builds-on/revises tree. One that landed *after* HEAD is
    // fresh, unanchored work — a just-planned or just-checkpointed node nothing connects to yet.
    // Appending it below HEAD's whole subtree (the old order) buried the newest thing at the very
    // bottom, where the eye never looks and a new plan reads as "lost". Surface it directly under
    // HEAD instead; older disconnected lanes still trail the tree, keeping newest-on-top for them.
    const rest = decisions.slice().sort(byLanding).map((d) => d.id).filter((id) => !seen.has(id));
    const headLanding = landingOf[head];
    const fresh = rest.filter((id) => landingOf[id] > headLanding);
    const stale = rest.filter((id) => landingOf[id] <= headLanding);
    ordered = [head, ...fresh, ...tree.slice(1), ...stale];
  } else {
    ordered = decisions.slice().sort(byLanding).map((d) => d.id);
  }
  const rowOf = {};
  ordered.forEach((id, i) => (rowOf[id] = i));
  decisions.sort((a, b) => rowOf[a.id] - rowOf[b.id]);

  // Each feature's inclusive row-span (top = newest decision's row, bot = oldest's row). A feature
  // reserves its whole span even across rows owned by other features (the spine passes behind them).
  const span = {};
  for (const d of decisions) {
    const r = rowOf[d.id];
    const s = span[d.feature];
    if (!s) span[d.feature] = { top: r, bot: r };
    else { s.top = Math.min(s.top, r); s.bot = Math.max(s.bot, r); }
  }

  // Visitation order for lane packing. Baseline: features by top row (gives minimal columns).
  // avoidCrossings: a dependency-first DFS — emit each feature immediately before the features it
  // builds on, so a dependent and its dependency are packed into neighbouring lanes and their
  // connector spans no intervening lane. Roots and ties fall back to top-row order for determinism.
  const byTop = (a, b) => span[a].top - span[b].top || (a < b ? -1 : 1);
  let order = Object.keys(span).sort(byTop);
  const featureOf = {};
  for (const d of decisions) featureOf[d.id] = d.feature;
  // Island features: a *lone* decision (its feature's only one) that touches no builds-on/revises/
  // fork edge — it relates to nothing in the graph, so packing it inline lets a disconnected node
  // masquerade as a member of whatever lane it lands in (the `embedding_logic`-looks-like-`providers`
  // confusion). We defer these to a reserved gutter on the right of the connected rail (placed after
  // the loop). A multi-decision edge-less feature is NOT an island: its own revise spine is already a
  // distinct, self-evident column, so it stays in the rail.
  const incidentIds = new Set();
  for (const e of edges)
    if (e.type === "builds-on" || e.type === "revises" || e.type === "fork") {
      incidentIds.add(e.src);
      incidentIds.add(e.dst);
    }
  const featHasEdge = {}, featCount = {};
  for (const d of decisions) {
    featHasEdge[d.feature] ||= incidentIds.has(d.id);
    featCount[d.feature] = (featCount[d.feature] || 0) + 1;
  }
  const islandFeat = new Set(
    Object.keys(span).filter((f) => !featHasEdge[f] && featCount[f] === 1),
  );
  if (avoidCrossings) {
    const featDeps = {}; // feature -> features it builds on
    for (const f in span) featDeps[f] = new Set();
    for (const e of edges) {
      const af = featureOf[e.src], bf = featureOf[e.dst];
      if (af !== undefined && bf !== undefined && af !== bf) featDeps[af].add(bf);
    }
    const seen = new Set(), dfs = [];
    const visit = (f) => {
      if (seen.has(f)) return;
      seen.add(f); dfs.push(f);
      for (const d of [...featDeps[f]].sort(byTop)) visit(d);
    };
    for (const r of order) visit(r);
    order = dfs;
  }

  // Per-feature rows, and a growing record of placed dots + drawn connectors so the placement loop can
  // ask: would putting feature f at lane L hide a connector behind a dot?
  //
  // The overprint test mirrors what edgePath() actually draws. A cross-feature connector is rendered
  // as a VERTICAL run in its SOURCE's lane (`e.src`, the upper/child node) from the source row down to
  // the parent, then a short diagonal joint. So a connector is hidden exactly when a dot sits in the
  // SOURCE's lane strictly between the two endpoint rows — the geometry that lets an integrator's whole
  // fan collapse onto the trunk and draw straight through every dot beneath it (the "straight line, no
  // edges" failure). We reject any lane that would create such an overprint, in BOTH packing modes —
  // a hidden connector is never acceptable; only the *choice among* honest lanes is a mode preference.
  const rowsByFeature = {};
  for (const d of decisions) (rowsByFeature[d.feature] ||= []).push(rowOf[d.id]);
  const placedDots = [];   // { row, lane } for every dot already placed
  const placedEdges = [];  // { lane, r0, r1 } — a connector's vertical run, in its source's lane
  function wouldOverprint(lane, f) {
    // (a) one of f's dots, placed in `lane`, landing on an already-drawn connector's vertical run.
    for (const r of rowsByFeature[f])
      for (const E of placedEdges)
        if (E.lane === lane && E.r0 < r && r < E.r1) return true;
    // (b) one of f's own outgoing connectors (f is the source) running vertically in `lane` and
    //     sweeping over an already-placed dot between its endpoints' rows. (Connectors where f is the
    //     destination run in the *other* lane, so f's lane choice can't hide them — skipped here.)
    for (const e of edges) {
      if (featureOf[e.src] !== f || featureOf[e.dst] === f) continue;
      const r0 = Math.min(rowOf[e.src], rowOf[e.dst]), r1 = Math.max(rowOf[e.src], rowOf[e.dst]);
      for (const D of placedDots)
        if (D.lane === lane && r0 < D.row && D.row < r1) return true;
    }
    return false;
  }

  const laneBot = []; // laneBot[i] = bottom row currently occupying lane i
  const laneOf = {};
  // Record a feature's dots and the vertical runs of the connectors it sources, once its lane is set.
  function recordPlaced(f) {
    const L = laneOf[f];
    for (const r of rowsByFeature[f]) placedDots.push({ row: r, lane: L });
    for (const e of edges) {
      if (featureOf[e.src] !== f || featureOf[e.dst] === f) continue; // vertical lives in the source's lane
      placedEdges.push({ lane: L, r0: Math.min(rowOf[e.src], rowOf[e.dst]), r1: Math.max(rowOf[e.src], rowOf[e.dst]) });
    }
  }

  // Fan-bus collapse. A fan/star (many leaf capabilities feeding one integrator) otherwise gets
  // packed by interval-coloring into ONE column, where every feeder→HEAD edge hides as a vertical
  // behind the intervening dots (the "straight line, can't see edges" failure). When the projection
  // names a HEAD, pin its feature to lane 0 and gather its pure-leaf feeders — single-decision
  // features HEAD builds on, that nothing else builds on and that build on nothing — into ONE shared
  // adjacent "bus" lane, so each feeder→HEAD connector becomes a visible short curve and width stays
  // at O(spines)+1 regardless of how many feeders there are.
  if (head && idSet.has(head)) {
    const headFeat = featureOf[head];
    const out = {}, inc = {};
    for (const e of edges) {
      if (e.type !== "builds-on") continue;
      const af = featureOf[e.src], bf = featureOf[e.dst];
      if (af === undefined || bf === undefined || af === bf) continue;
      (out[af] ||= new Set()).add(bf);
      (inc[bf] ||= new Set()).add(af);
    }
    const feeders = [];
    for (const f of out[headFeat] || []) {
      if (f === headFeat || (rowsByFeature[f] || []).length !== 1) continue;
      if ((out[f] || new Set()).size !== 0) continue; // builds on nothing itself (a pure leaf)
      const fi = inc[f] || new Set();
      if (fi.size === 1 && fi.has(headFeat)) feeders.push(f); // only HEAD builds on it
    }
    if (feeders.length >= 2) {
      laneOf[headFeat] = 0;
      laneBot[0] = span[headFeat].bot;
      let busBot = -Infinity;
      for (const f of feeders) { laneOf[f] = 1; busBot = Math.max(busBot, span[f].bot); }
      laneBot[1] = busBot;
      for (const f of [headFeat, ...feeders]) recordPlaced(f);
    }
  }

  // Lane-adjacency: the mean lane of f's already-placed builds-on neighbours (either direction). When
  // several lanes are equally valid we pick the one closest to this target so a dependent sits next to
  // its dependency and the connector is a short curve — never opening a new column to do it (column
  // count is unchanged; only the choice *among* existing valid lanes changes).
  function neighborLane(f) {
    let sum = 0, n = 0;
    for (const e of edges) {
      if (e.type !== "builds-on" && e.type !== "revises") continue;
      const af = featureOf[e.src], bf = featureOf[e.dst];
      if (af === undefined || bf === undefined || af === bf) continue;
      const other = af === f ? bf : bf === f ? af : null;
      if (other !== null && laneOf[other] !== undefined) { sum += laneOf[other]; n++; }
    }
    return n ? sum / n : null;
  }

  for (const f of order) {
    if (laneOf[f] !== undefined) continue; // pinned by the fan-bus pass above
    if (islandFeat.has(f)) continue;       // deferred to the gutter pass below
    const s = span[f];
    const valid = [];
    for (let L = 0; L < laneBot.length; L++) {
      if (laneBot[L] >= s.top) continue;            // occupied
      if (wouldOverprint(L, f)) continue;           // would hide a connector — never allowed (both modes)
      valid.push(L);
    }
    const target = avoidCrossings ? neighborLane(f) : null;
    let lane;
    if (!valid.length) {
      lane = laneBot.length;                        // every existing lane is taken/spears — new column
    } else if (target !== null) {
      lane = valid.reduce((best, L) =>
        Math.abs(L - target) < Math.abs(best - target) ||
        (Math.abs(L - target) === Math.abs(best - target) && L < best) ? L : best, valid[0]);
    } else {
      lane = valid[0];                              // baseline: lowest free lane (minimal columns)
    }
    laneBot[lane] = s.bot;
    laneOf[f] = lane;
    recordPlaced(f);
  }

  // Gutter pass. Island features go into lanes starting just right of the connected rail, so a
  // disconnected node reads as detached rather than as part of an adjacent strand. Interval-colored
  // among themselves (disjoint islands still share a gutter lane), in row order for determinism.
  let gutterStart = null; // first gutter lane index — the renderer offsets these lanes by a gap
  if (islandFeat.size) {
    const gutterBase = laneBot.length;       // first lane past the connected lanes
    const gutterBot = [];                    // bottoms within the gutter, indexed from gutterBase
    for (const f of order) {
      if (!islandFeat.has(f)) continue;
      const s = span[f];
      let g = 0;
      while (g < gutterBot.length && gutterBot[g] >= s.top) g++;
      gutterBot[g] = s.bot;
      laneOf[f] = gutterBase + g;
    }
    // Only a real gutter (islands sitting beside connected lanes) gets the gap+divider; an
    // all-islands graph has nothing to separate from, so its dots stay flush against the edge.
    if (gutterBot.length) {
      laneBot.length = gutterBase + gutterBot.length;
      if (gutterBase > 0) gutterStart = gutterBase;
    }
  }

  const pos = {};
  for (const d of decisions) pos[d.id] = { row: rowOf[d.id], lane: laneOf[d.feature] };
  // Unanchored decisions: no incident builds-on/revises/fork edge in either direction — nothing in
  // the graph relates to them yet (a fresh plan whose `needs` matched no provider, a leaf utility
  // nothing calls). The renderer marks them so a disconnected node reads as "pending placement"
  // rather than a silently-floating dot. Computed over the lineage edge set (pre-reduction kinds).
  const unanchored = decisions.map((d) => d.id).filter((id) => !incidentIds.has(id));
  // `head` is the anchored top node; `edges` is the transitively-reduced set the renderer draws.
  return { decisions, rowOf, laneOf, span, pos, head, edges, unanchored, gutterStart,
           laneCount: Math.max(1, laneBot.length) };
}

// Transitive reduction of the builds-on DAG: keep an edge A→B only when B is NOT reachable from A
// through some other child of A (i.e. A→B is the direct link, not a shortcut implied by A→…→B).
// Only builds-on edges are reduced; revises/fork lineage passes through untouched. Returns a new
// array preserving the input edges minus the redundant builds-on shortcuts.
function reduceBuildsOn(all, idSet) {
  const adj = {}; // builds-on adjacency among present decisions
  for (const e of all)
    if (e.type === "builds-on" && idSet.has(e.src) && idSet.has(e.dst) && e.src !== e.dst)
      (adj[e.src] ||= []).push(e.dst);
  const memo = {};
  function reach(n) {
    if (memo[n]) return memo[n];
    memo[n] = new Set(); // placeholder breaks cycles (a back-edge contributes no extra reach)
    const r = new Set();
    for (const m of adj[n] || []) { r.add(m); for (const x of reach(m)) r.add(x); }
    return (memo[n] = r);
  }
  const redundant = new Set();
  for (const a in adj) {
    const kids = adj[a];
    for (const b of kids)
      for (const c of kids)
        if (c !== b && reach(c).has(b)) { redundant.add(a + "	" + b); break; }
  }
  return all.filter((e) => !(e.type === "builds-on" && redundant.has(e.src + "	" + e.dst)));
}
// ---- end-layout (test slice boundary) ----
