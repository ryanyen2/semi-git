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
  // The future band: a reserved strip past the real commit-index axis where a pending plan step
  // renders as a hypothetical next op on its predicted feature's own row -- real estate that is
  // always empty of real ops, so a prediction never competes with (or hides among) real history.
  const FUTURE_W = 44;
  function futureX(slot) {
    return AXIS_X + AXIS_W + 14 + slot * 10;
  }
  // A leaf row's real ops render as discrete GLYPH marks only while they're legible at this
  // width; past this many ops (or once any pixel column holds more than one), the marks would
  // overlap into a smear regardless of glyph choice -- e.g. a 1287-op feature in this repo's own
  // self-hosted store -- so the row falls back to an opacity-graded density heatstrip instead.
  const DENSE_OP_THRESHOLD = 36;

  const state = vscode.getState() || {
    collapsed: [], selected: null, compositionLabel: "HEAD", compositionRef: "HEAD",
  };
  if (state.selectedStep === undefined) state.selectedStep = null;
  if (state.selectedPlanSession === undefined) state.selectedPlanSession = null;
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

  // Composition-picker hover-preview: while the titlebar's composition QuickPick is open, arrowing
  // over a session/branch item folds it live and takes over the code(I) slot -- "what would
  // switching to this show," seen before committing to the real `sgt switch`.
  let compositionPreviewActive = null; // the ref currently previewed, or null when the picker is closed
  let compositionPreviewCache = {}; // ref -> {files, oracle_verdict, forked, error}
  let latestCompositionPreviewSeq = 0; // discards a stale reply that lands after a newer hover

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

  // Plan marks (Phase 6): predicted steps render as open marks in the "future zone" of their
  // predicted feature's own row rather than a separate ghost subtree (see collectPlanMarks below).
  // `knownPlanSteps`/`pendingPlanSteps` snapshot the *previous* render's step ids/pending-ness so a
  // fade-in or landing keyframe fires only on a genuine transition -- never replayed by an
  // unrelated re-render (select, collapse, an unrelated .py-save refresh). The `prev*` pair is a
  // snapshot taken at the top of render(), before `knownPlanSteps`/`pendingPlanSteps` are rebuilt.
  let planMarks = { steps: [], byFeature: {}, floating: [], sessions: [] };
  let knownPlanSteps = new Set();
  let pendingPlanSteps = new Set();
  let prevKnownPlanSteps = new Set();
  let prevPendingPlanSteps = new Set();
  let planStepEnterStagger = {}; // step id -> 0-based order among this render's newly-entering steps
  let justLandedByPredicted = {}; // predicted feature id -> just-matched steps (same-row landing)
  let justLandedByMatched = {}; // matched feature id -> just-matched, diverged steps (comet arrival)

  // Drift marks: same snapshot-diff discipline as plan marks (see field comments above) so a
  // drift ring's one-shot pulse fires only the render a drift op is first seen -- never replayed
  // by an unrelated re-render (select, collapse, an .sgt/-triggered refresh).
  let driftMarks = { ids: new Set(), unplaced: [] };
  let knownDriftIds = new Set();
  let prevKnownDriftIds = new Set();

  let forkMarks = { byFeature: {}, unplaced: [] };

  // FLIP morph: `prevLayoutRowOf` is the row-index map the layout had immediately before this
  // state push, captured in the message handler before `recompute()` overwrites `layout`. Row
  // position is a pure function of layout (`row * ROWH + 20`), so no DOM measurement is needed --
  // see renderRow's per-row `element.animate()` call below.
  let prevLayoutRowOf = null;
  let hasRenderedOnce = false;
  const FLIP_MAX_ROWS = 300; // skip the morph on huge trees rather than risk jank

  const rail = document.getElementById("rail");
  const inspector = document.getElementById("inspector");
  const compositionBtn = document.getElementById("compositionBtn");
  const oracleChip = document.getElementById("oracleChip");
  const offscreenAbove = document.getElementById("offscreenAbove");
  const offscreenBelow = document.getElementById("offscreenBelow");
  let planChipsEl = null; // created lazily on first renderTitlebar(), inserted after oracleChip

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
    layout = computeLayout(map, history, { collapsed: state.collapsed });
  }

  function renderTitlebar() {
    compositionBtn.textContent = state.compositionLabel || "HEAD";
    const oracle = (compose.status && compose.status.oracle) || { configured: false, status: "pending" };
    const st = oracle.configured ? oracle.status : "unconfigured";
    oracleChip.dataset.state = st;
    oracleChip.textContent = `oracle: ${st}`;

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

    // Plan-mark transition bookkeeping: snapshot what a *prior* render already knew about before
    // rebuilding from the current `planMarks` -- see the field comments above for why. A step seen
    // pending last render and matched now just landed (this render only); a step never seen before
    // is entering (fresh `sgt plan intake`).
    prevKnownPlanSteps = knownPlanSteps;
    prevPendingPlanSteps = pendingPlanSteps;
    prevKnownDriftIds = knownDriftIds;
    planStepEnterStagger = {};
    justLandedByPredicted = {};
    justLandedByMatched = {};
    let enterN = 0;
    for (const step of planMarks.steps) {
      if (step.matched) {
        if (!prevPendingPlanSteps.has(step.id)) continue; // already resolved as of last render
        if (step.predictedFeature) {
          (justLandedByPredicted[step.predictedFeature] || (justLandedByPredicted[step.predictedFeature] = [])).push(step);
        }
        if (step.matchedFeature && step.matchedFeature !== step.predictedFeature) {
          (justLandedByMatched[step.matchedFeature] || (justLandedByMatched[step.matchedFeature] = [])).push(step);
        }
      } else if (!prevKnownPlanSteps.has(step.id)) {
        planStepEnterStagger[step.id] = enterN++;
      }
    }

    const prevScrollTop = rail.scrollTop; // rebuilt below via innerHTML="" -- restore after
    rail.innerHTML = "";
    const height = Math.max(1, layout.rows.length) * ROWH + 20;
    const svg = mk("svg", { width: AXIS_X + AXIS_W + FUTURE_W + 40, height, class: "railsvg" });
    const edgeLayer = mk("g", { class: "edges" });
    const rowLayer = mk("g", { class: "rows" });
    svg.appendChild(mk("line", { x1: AXIS_X - 14, x2: AXIS_X - 14, y1: 0, y2: height, class: "rail-divider" }));
    if (layout.commitCount > 1) {
      // The anticipation boundary: real history ends here, everything right of it is hypothetical.
      svg.appendChild(mk("line", {
        x1: AXIS_X + AXIS_W + 4, x2: AXIS_X + AXIS_W + 4, y1: 0, y2: height, class: "future-boundary",
      }));
    }
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
    renderLandingComets(edgeLayer);

    const flipEligible = prevLayoutRowOf && !prefersReducedMotion() && layout.rows.length <= FLIP_MAX_ROWS;
    layout.rows.forEach((id, row) => {
      const g = renderRow(id, row);
      rowLayer.appendChild(g);
      if (flipEligible) flipRow(g, id, row);
    });

    // Rebuild the "known"/"pending" plan-step sets fresh from the current data -- next render's
    // transition classes (entering / landing / comet) compare against this snapshot.
    const nextKnown = new Set();
    const nextPending = new Set();
    for (const step of planMarks.steps) {
      nextKnown.add(step.id);
      if (!step.matched) nextPending.add(step.id);
    }
    knownPlanSteps = nextKnown;
    pendingPlanSteps = nextPending;
    knownDriftIds = new Set(driftMarks.ids);

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
    rail.scrollTop = prevScrollTop;
    renderInspector();
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // FLIP without measuring the DOM: `rail.innerHTML=""` above already destroyed the prior row
  // elements, but row Y is a pure function of layout (`row * ROWH + 20`), so `prevLayoutRowOf`
  // (captured in the message handler before `recompute()` ran) is all "First"/"Last" needs.
  // `element.animate()` rather than a CSS transition or inline `style` attribute: the webview's
  // CSP (`style-src`, no `'unsafe-inline'`) would silently drop an injected style, and WAAPI's
  // default `fill: "none"` cleanly reverts to the `<g>`'s own `transform` attribute (already set
  // to the final position by `mk()`) the instant the animation ends -- no transform to strip
  // before the next render's `innerHTML=""`.
  function flipRow(g, id, row) {
    const prevRow = prevLayoutRowOf[id];
    if (prevRow == null || prevRow === row) return; // new row, or didn't move -- nothing to morph
    const oldY = prevRow * ROWH + 20;
    const newY = row * ROWH + 20;
    g.animate(
      [{ transform: `translate(0px, ${oldY}px)` }, { transform: `translate(0px, ${newY}px)` }],
      { duration: 280, easing: "cubic-bezier(.16, 1, .3, 1)" }
    );
  }

  // A step that matched in a *different* feature than predicted (matching is footprint-overlap,
  // not feature-id, so this is a real divergence): a short comet-trail traces from the predicted
  // row's future-band position to the real landing spot, then fades over ~1s and is gone -- a
  // transient miss-marker, never a permanent one. The same-feature case needs no such pass: its
  // marker slides in place inside `renderRow` and simply becomes the row's real glyph.
  function renderLandingComets(edgeLayer) {
    for (const fid in justLandedByMatched) {
      if (!(fid in layout.rowOf)) continue;
      for (const step of justLandedByMatched[fid]) {
        if (!(step.predictedFeature in layout.rowOf)) continue;
        const op = findMatchedOp(step);
        if (!op) continue;
        const fromRow = layout.rowOf[step.predictedFeature];
        const toRow = layout.rowOf[fid];
        const fromX = futureX((planMarks.byFeature[step.predictedFeature] || []).length);
        const toX = commitIndexToX(op.commit_index);
        const y1 = fromRow * ROWH + 20, y2 = toRow * ROWH + 20;
        const mid = (y1 + y2) / 2;
        const path = mk("path", {
          d: `M ${fromX} ${y1} C ${fromX + 20} ${mid}, ${toX - 20} ${mid}, ${toX} ${y2}`,
          class: "plan-comet",
        });
        edgeLayer.appendChild(path);
      }
    }
  }

  function findMatchedOp(step) {
    for (const op of layout.opsByFeature[step.matchedFeature] || []) {
      if (op.id === step.matchedOpId) return op;
    }
    return null;
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

  // Discrete glyphs below the density threshold (today's behavior, unchanged); above it, an
  // opacity-graded heatstrip bucketed by rendered pixel column -- fill stays the feature's own
  // identity color (never a second hue), opacity carries local density (magnitude = status, per
  // the rail's color=identity / glyph-or-opacity=status contract). `driftIds`: ops mined but
  // unpredicted by any active plan session -- flagged with a ring around the SAME glyph (never a
  // second mark; see collectDriftMarks), solid stroke + the row's own identity color to keep it
  // out of the --accent "anticipated" channel that pending plan-marks use.
  function renderOpsForRow(g, ops, color, driftIds) {
    if (!ops.length) return;
    const byCol = new Map();
    for (const op of ops) {
      const col = Math.round(commitIndexToX(op.commit_index));
      byCol.set(col, (byCol.get(col) || 0) + 1);
    }
    const crowded = [...byCol.values()].some((n) => n > 1);
    if (ops.length <= DENSE_OP_THRESHOLD && !crowded) {
      for (const op of ops) {
        const x = commitIndexToX(op.commit_index);
        g.appendChild(mk("text", { x, y: 4, class: `glyph glyph-${op.kind}`, text: GLYPH[op.kind] || "·" }));
        if (driftIds && driftIds.has(op.id)) {
          const entering = !prevKnownDriftIds.has(op.id);
          g.appendChild(mk("circle", {
            cx: x, cy: 0, r: 5, class: `drift-ring${entering ? " entering" : ""}`, stroke: color || "#888",
          }));
        }
      }
      return;
    }
    const maxCount = Math.max(...byCol.values());
    const driftCols = new Set();
    if (driftIds && driftIds.size) {
      for (const op of ops) {
        if (driftIds.has(op.id)) driftCols.add(Math.round(commitIndexToX(op.commit_index)));
      }
    }
    for (const [col, count] of byCol) {
      const opacity = (0.18 + 0.7 * (count / maxCount)).toFixed(2);
      g.appendChild(mk("rect", {
        x: col - 3, y: -4, width: 6, height: 8, rx: 1, class: "heatcell", fill: color || "#888", opacity,
      }));
      // Too dense for a per-op ring -- a small solid tick above the column stands in for it,
      // rather than silently dropping the drift signal in dense rows.
      if (driftCols.has(col)) {
        g.appendChild(mk("rect", { x: col - 1, y: -8, width: 2, height: 3, class: "drift-tick", fill: color || "#888" }));
      }
    }
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
      renderOpsForRow(g, layout.opsByFeature[id] || [], node.color, driftMarks.ids);
      renderPlanMarksForRow(g, id, node.color);
      renderForkMarksForRow(g, id);
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

  // A feature row's plan marks: pending steps predicted here render as open dashed rings in the
  // future band (a hypothetical next op); a step that just matched *in this same feature* slides
  // from its future-band position into the real op's column and solidifies to that feature's own
  // identity color -- by the next render it's simply one of the row's ordinary glyphs, so no
  // ongoing state is kept for it. Cross-feature landings (divergence) are handled by the separate
  // `renderLandingComets` edge-layer pass, since they span two rows.
  function renderPlanMarksForRow(g, id, color) {
    const pending = planMarks.byFeature[id] || [];
    pending.forEach((step, slot) => {
      const x = futureX(slot);
      const entering = !prevKnownPlanSteps.has(step.id);
      const mark = mk("circle", { cx: x, cy: 0, r: 4, class: "plan-mark" });
      if (entering) {
        mark.classList.add("entering");
        // A CSSOM setter, not `setAttribute("style", ...)` -- the webview's CSP (`style-src`, no
        // `'unsafe-inline'`) silently drops an injected `style=""` attribute; `.style.x =` is
        // exempt (it isn't parsed as inline HTML, so no CSP check applies).
        mark.style.animationDelay = `${(planStepEnterStagger[step.id] || 0) * 45}ms`;
      }
      if (step.checkpointMatch) {
        // A visible hint that a confirmable footprint-overlap candidate already exists for this
        // pending step, before opening its card -- the ring's own center dot, never a new mark.
        mark.classList.add("has-checkpoint-match");
      }
      mark.addEventListener("click", (ev) => {
        ev.stopPropagation();
        selectPlanStep(step.id);
      });
      g.appendChild(mark);
    });

    for (const step of justLandedByPredicted[id] || []) {
      const fromX = futureX(pending.length);
      const sameRow = step.matchedFeature === id;
      if (sameRow) {
        // Landed exactly where predicted: the open ring solidifies and slides onto the real op's
        // column -- by the next render it's indistinguishable from any other real glyph there.
        const op = findMatchedOp(step);
        const toX = op ? commitIndexToX(op.commit_index) : fromX;
        const mark = mk("circle", { cx: fromX, cy: 0, r: 4, class: "plan-mark landing", fill: color || "var(--accent)" });
        g.appendChild(mark);
        requestAnimationFrame(() => {
          mark.style.transition = "cx 340ms cubic-bezier(.16,1,.3,1)";
          mark.setAttribute("cx", toX);
        });
      } else {
        // Diverged: the real landing is carried by the comet-trail on the matched row instead --
        // this side just fades out in place rather than leaving a stray permanent dot behind.
        g.appendChild(mk("circle", { cx: fromX, cy: 0, r: 4, class: "plan-mark departing" }));
      }
    }
  }

  // An open fork just past this feature's lifebar end -- never on the real commit axis (fork
  // tips are excluded from every ideal, so there's no column for them) and never in the future
  // band (that's the anticipated/--accent zone; a fork is a present, unresolved conflict). Shape
  // (a small branch mark, not GLYPH.move's ⋔) plus the --blast channel carry the state; click
  // opens the same N-column resolution wizard the sgtForks tree already uses.
  function renderForkMarksForRow(g, id) {
    const forks = forkMarks.byFeature[id];
    if (!forks || !forks.length) return;
    const bar = layout.lifebars[id];
    const baseX = bar ? commitIndexToX(bar.end) + 14 : AXIS_X + 14;
    forks.forEach((fork, slot) => {
      const x = baseX + slot * 12;
      const mark = mk("path", {
        d: `M ${x} -6 L ${x} -1 M ${x} -1 L ${x - 4} 4 M ${x} -1 L ${x + 4} 4`,
        class: "fork-mark",
      });
      mark.setAttribute("title", fork.remedy);
      mark.addEventListener("click", (ev) => {
        ev.stopPropagation();
        vscode.postMessage({ type: "resolveFork", symbol: fork.symbol });
      });
      g.appendChild(mark);
    });
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
    state.selectedStep = null;
    state.selectedPlanSession = null;
    saveState();
    render();
    const node = state.selected && byId(state.selected);
    if (node && node.kind === "feature") requestFold(state.selected);
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
    }
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
    return bar;
  }

  function previewAction(verb, id) {
    if (verb === "rename" || verb === "merge" || verb === "move") return; // needs a target/label first
    if (verb === "revert") previewAndBlast(verb, [id]);
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
      // Snapshot the row positions the CURRENT (about-to-be-stale) layout assigned, keyed by
      // node id -- a pure function of the tree, so this is all `render()` needs to FLIP-morph
      // rows that moved (a merge/split/rename/move, or a composition switch that re-folds a
      // different tree) from where they were to where they are now. `null` on the very first
      // render (nothing to morph from).
      prevLayoutRowOf = hasRenderedOnce ? layout.rowOf : null;
      compose = msg.compose || compose;
      history = compose.history || { commits: [], ops: [] };
      map = compose.map || { nodes: [], roots: [], edges: [] };
      planMarks = collectPlanMarks(compose.plan, history);
      driftMarks = collectDriftMarks(compose.drift, history);
      forkMarks = collectForkMarks(compose.forks, map.nodes);
      foldResultCache = {};
      playheadResultCache = {};
      playheadCommitIndex = null; // a new composition's commit-index axis means different columns
      recompute();
      render();
      hasRenderedOnce = true;
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

  vscode.postMessage({ type: "ready" });
  render();
})();
