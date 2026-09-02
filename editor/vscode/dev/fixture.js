// The compose payload the dev surfaces render, built from `fixture-compose.json`.
//
// Shared by `smoke.js` (node) and `preview.html` (browser) so the two cannot disagree about what
// they are showing. They did: preview.html carried its own three-node hand-written map with no
// `grid` at all, so the page it drew had an EMPTY plot -- which is the one thing a preview exists
// to rule out, and it looked like a rendering bug every time anyone opened it.
//
// The raw file is a `compose_view` capture. Three things the real host adds before posting are
// added here for the same reason it adds them.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.buildComposeFixture = factory();
})(typeof self !== "undefined" ? self : this, function () {
  return function buildComposeFixture(raw) {
    const compose = JSON.parse(JSON.stringify(raw));

    // 1. Identity hues. The real host enriches EVERY node with a concrete hex (see `colorForNode`
    // in workbench.ts) -- subsystems included, which is what lets the folded tree be the default
    // view without opening as a grey wall. Fed raw, every lane comes back grey and a regression
    // where lanes lose their hue could not be caught. The exact hue is
    // `tests/test_color_parity.py`'s business; what matters here is that every lane has one.
    compose.map = {
      ...compose.map,
      nodes: (compose.map.nodes || []).map((n, i) => ({
        ...n,
        color: `#${(0x334455 + i * 0x010203).toString(16).padStart(6, "0")}`,
      })),
    };

    // 2. The cell table. The host injects `grid_view`'s cells into the state message alongside the
    // compose_view aggregate; the fixture is a raw capture, so derive the same op -> (feature,
    // commit) grouping here.
    const history = compose.history || {};
    const commits = history.commits || [];
    const byCell = new Map();
    for (const op of history.ops || []) {
      if (op.feature_id == null) continue;
      const key = op.feature_id + "|" + op.commit_index;
      let c = byCell.get(key);
      if (!c) {
        byCell.set(key, (c = {
          feature_id: op.feature_id, commit_index: op.commit_index, op_ids: [], kinds: {},
        }));
      }
      c.op_ids.push(op.id);
      c.kinds[op.kind] = (c.kinds[op.kind] || 0) + 1;
    }
    compose.grid = {
      commits,
      cells: [...byCell.values()].map((c) => ({
        feature_id: c.feature_id, commit_index: c.commit_index, op_ids: c.op_ids.slice().sort(),
        op_count: c.op_ids.length, kinds: c.kinds, fidelity: "full",
      })),
      commit_count: commits.length,
    };

    // 3. One cross-feature theme, so the TableLens focus path renders. The fixture predates themes;
    // membership arrives as commit shas exactly like the real payload's, so `resolveThemeMarks`'
    // sha -> commit-index join is the code under test.
    const byFeature = new Map();
    for (const c of compose.grid.cells) {
      if (!byFeature.has(c.feature_id)) byFeature.set(c.feature_id, []);
      byFeature.get(c.feature_id).push(c.commit_index);
    }
    const feats = [...byFeature.keys()].slice(0, 2);
    if (feats.length >= 2) {
      const idx = new Set(feats.flatMap((f) => byFeature.get(f).slice(0, 2)));
      const shas = compose.grid.commits.filter((c) => idx.has(c.index)).map((c) => c.sha);
      compose.intent = compose.intent || {};
      compose.intent.themes = [{
        theme_id: "theme-smoke", label: "Smoke Theme", rationale: "", source: "fallback",
        atom_shas: shas, stale_shas: [], op_ids: [], feature_span: feats, tier: "co-changed",
      }];
    }
    return compose;
  };
});
