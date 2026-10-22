// The ugh ledger webview (src/ledger.ts): the history as a document of what was asked, newest
// first, with the feature lanes in the gutter -- docs/design/2026-10-18-workbench-redesign.md §3.
// Reads the same `compose_view` payload the workbench reads (the per-checkpoint `symbols` and
// `state` the kernel now carries, so no dry run is needed to name what a checkpoint touched) and
// drives the same host verbs. Every animation stands for a real ugh event, and is chosen by
// diffing the previous state against the next one (§7.2), never by a timeline.
(function () {
  "use strict";
  const vscode = acquireVsCodeApi();
  const M = window.Motion;

  // ── identity colour (fallback for a node the host did not colour; port of src/color.ts) ──────
  const GOLDEN = 0.618033988749895;
  function hashId(id) { let h = 2166136261; for (let i = 0; i < id.length; i++) { h ^= id.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }
  function oklchToRgb(L, C, hDeg) {
    const h = (hDeg * Math.PI) / 180, a = C * Math.cos(h), b = C * Math.sin(h);
    const l_ = L + 0.3963377774 * a + 0.2158037573 * b, m_ = L - 0.1055613458 * a - 0.0638541728 * b, s_ = L - 0.0894841775 * a - 1.291485548 * b;
    const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
    const lr = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s, lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s, lb = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;
    const g = (x) => { const c = x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055; return Math.round(Math.max(0, Math.min(1, c)) * 255); };
    return [g(lr), g(lg), g(lb)];
  }
  function fallbackColor(id) { const [r, g, b] = oklchToRgb(0.7, 0.13, ((hashId(id) * GOLDEN) % 1) * 360); return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join(""); }

  // ── the payload, joined ──────────────────────────────────────────────────────────────────────
  // Rebuilt on every `state` push. `D` is the current reading; nothing else holds payload data.
  let D = null;
  const SENTINEL = /^(__residue__|__anchor__|__import__|__export__|__decorator__)$/;
  const isUserSymbol = (sym) => !sym.split("::").some((p) => SENTINEL.test(p));
  const KIND = { added: { g: "+", cls: "add", word: "added" }, changed: { g: "~", cls: "chg", word: "changed" }, removed: { g: "−", cls: "del", word: "removed" }, moved: { g: "→", cls: "mov", word: "moved" } };
  const kindOf = (k) => KIND[k] || KIND.changed;
  const plural = (n, w, ws) => `${n} ${n === 1 ? w : ws || w + "s"}`;
  const shortSha = (sha) => String(sha).slice(0, 7);
  const shortFile = (f) => f.split("/").slice(-1)[0];
  const truncate = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

  function load(compose) {
    const map = compose.map || { nodes: [] }, history = compose.history || { commits: [], ops: [] }, intent = compose.intent || { segments: [], themes: [] };
    const nodeById = new Map(map.nodes.map((n) => [n.id, n]));
    const commits = (history.commits || []).slice().sort((a, b) => a.index - b.index);
    const commitBySha = new Map(commits.map((c) => [c.sha, c]));
    const segs = (intent.segments || []).map((s) => ({ ...s, ref: s.checkpoint }));
    const segByRef = new Map(segs.map((s) => [s.ref, s]));
    const segsByFeature = new Map();
    for (const s of segs) { if (!segsByFeature.has(s.feature_id)) segsByFeature.set(s.feature_id, []); segsByFeature.get(s.feature_id).push(s); }
    for (const l of segsByFeature.values()) l.sort((a, b) => a.seg_index - b.seg_index);
    const themes = (intent.themes || []).map((t) => ({ ...t, commitIdx: new Set((t.atom_shas || []).map((sha) => commitBySha.get(sha)).filter(Boolean).map((c) => c.index)), span: new Set(t.feature_span || []) })).sort((a, b) => b.span.size - a.span.size);
    const laneIds = map.nodes.filter((n) => n.kind === "feature" && segsByFeature.has(n.id)).map((n) => n.id);
    const now = Math.max(0, ...commits.map((c) => c.ts || 0)) + 3600;
    D = { compose, map, history, intent, nodeById, commits, commitBySha, segs, segByRef, segsByFeature, themes, themeById: new Map(themes.map((t) => [t.theme_id, t])), laneIds, now };
    D.allYou = segs.every((s) => whoOf(s) === "you");
  }
  // Who took a checkpoint's work out, when it was not the reader: the kernel names the revert
  // that spliced each symbol (`taken_out_by`, the tag of the revert), and a tag is a checkpoint
  // ref or a feature id, both of which this payload can name.
  // A row's name as the ledger prints it: a run of one save's rows is named by feature after its
  // first row, and a title repeated elsewhere carries its feature, so a note, a diff tab or the
  // status line naming a row has to use the same words the row shows.
  function rowName(ref) {
    const r = model && model.rows.find((x) => x.ref === ref); const s = D.segByRef.get(ref);
    if (!r) return s ? s.intent : String(ref);
    return r.dis ? `${r.title} (${r.dis})` : r.title;
  }
  function takenOutBy(seg) {
    const tags = seg.taken_out_by || []; if (!tags.length) return "";
    const name = (tag) => { const s = D.segByRef.get(tag); if (s && s.ref !== seg.ref) return rowName(s.ref); const n = D.nodeById.get(tag); if (n) return n.label; const fid = D.laneIds.find((id) => tag.startsWith(id)); return fid ? labelOf(fid) : null; };
    const names = [...new Set(tags.map(name).filter(Boolean))];
    return names.length ? ` with ${names.slice(0, 2).join(", ")}` : "";
  }
  const colorFor = (id) => { const n = D.nodeById.get(id); return (n && n.color) || fallbackColor(id || "?"); };
  const labelOf = (fid) => { const n = D.nodeById.get(fid); return (n && n.label) || fid; };
  // "Taken out" is the kernel's per-symbol `state`; an older payload keeps the count rule.
  const stateOf = (seg) => seg.state || (seg.present_op_count === 0 ? "reverted" : seg.present_op_count == null ? null : "live");
  const isLive = (seg) => stateOf(seg) !== "reverted";
  function symbolsOf(seg) {
    return (seg.symbols || []).filter((s) => isUserSymbol(s.symbol)).map((s) => { const p = s.symbol.split("::"); return { ...s, name: p.length > 1 ? p.slice(1).join("::") : null }; });
  }
  function counts(seg) {
    const syms = symbolsOf(seg); const fns = syms.filter((s) => s.name).length, whole = syms.length - fns;
    const files = new Set(syms.map((s) => s.file)).size; const saves = (seg.commit_shas || []).length;
    const changes = seg.change_count != null ? seg.change_count : syms.reduce((a, s) => a + Math.max(1, (s.saves || []).length), 0);
    let what = fns ? plural(fns, "function") : ""; if (whole) what += (what ? " and " : "") + plural(whole, "whole file"); if (!what) what = "no function";
    return { syms, fns, whole, files, saves, changes, layoutOnly: syms.length === 0 && (seg.symbol_count || 0) === 0, what, filesWord: plural(files, "file"), live: isLive(seg) ? changes : 0 };
  }
  const savesOf = (seg) => (seg.commit_shas || []).map((sha) => D.commitBySha.get(sha)).filter(Boolean).sort((a, b) => a.index - b.index);
  const inTheme = (seg, t) => (seg.commit_shas || []).some((sha) => { const c = D.commitBySha.get(sha); return c && t.commitIdx.has(c.index); });
  function relTime(ts) {
    if (!ts) return ""; const d = D.now - ts;
    if (d < 90) return "now"; if (d < 3600) return Math.round(d / 60) + "m"; if (d < 86400) return Math.round(d / 3600) + "h";
    if (d < 7 * 86400) return Math.round(d / 86400) + "d"; return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  const whoOf = (seg) => { const a = (seg.asks || [])[0]; return a ? (a.actor === "agent" ? "agent" : "you") : "you"; };
  const whenOf = (seg) => { const a = (seg.asks || [])[0]; const sv = savesOf(seg); return relTime((a && a.ts) || (sv.length ? sv[sv.length - 1].ts : 0)); };

  // ── state ────────────────────────────────────────────────────────────────────────────────────
  const saved = vscode.getState() || {};
  // `picked`: checkpoints chosen with a modifier click, acted on together; `chain`: the steps of a
  // gesture that is several verbs (a swap, a pick of several), run one after another.
  const state = { open: saved.open || null, follow: saved.follow || null, theme: null, lens: "", at: null, staged: null, hover: null, laneHover: null, picked: new Set(), chain: null };
  const persist = () => vscode.setState({ open: state.open, follow: state.follow });
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const svg = (tag, attrs) => { const e = document.createElementNS(SVGNS, tag); for (const k in attrs || {}) { if (k === "text") e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]); } return e; };
  const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); };
  const rowsEl = $("rows"), noteEl = $("note"), stripEl = $("strip"), statusL = $("statusLeft"), statusR = $("statusRight"), mainEl = $("main");
  const setStatus = (t, cls) => { statusL.textContent = t || ""; statusL.className = "s-left" + (cls ? " " + cls : ""); };

  // ── the dry runs: one per (verb, ref), asked of the host, answered as `changeResult` ─────────
  const emits = new Map(); // key -> view | "pending"
  let emitSeq = 0; const emitWaiters = new Map();
  function requestEmit(verb, ref) {
    const key = verb + "\u001f" + ref;
    if (emits.has(key)) return;
    emits.set(key, "pending");
    const seq = ++emitSeq; emitWaiters.set(seq, key);
    vscode.postMessage({ type: "requestChange", verb, ref, seq });
  }
  const emitFor = (verb, ref) => { const v = emits.get(verb + "\u001f" + ref); return v && v !== "pending" ? v : null; };

  // ── rows and lanes ───────────────────────────────────────────────────────────────────────────
  function pendingRows() {
    const out = [];
    const plan = (D.compose.plan && D.compose.plan.sessions) || [];
    for (const sess of plan) for (const step of sess.steps || []) {
      if (step.status !== "pending" || !step.predicted_feature || !D.segsByFeature.has(step.predicted_feature)) continue;
      out.push({ kind: "pending", ref: "plan:" + step.hollow_id, featureId: step.predicted_feature, title: step.title, sub: "an agent, planned", explain: step.rationale || "Planned by the agent; not built yet." });
    }
    const sessions = (D.compose.sessions && D.compose.sessions.sessions) || [];
    for (const s of sessions) {
      const fid = D.laneIds.find((id) => ((D.nodeById.get(id) || {}).sessions || []).includes(s.name));
      if (!fid) continue;
      out.push({ kind: "session", ref: "session:" + s.name, featureId: fid, title: `session ${s.name}`, session: s.name, sub: `${plural(s.new_op_count || 0, "change")} not landed · ${s.alive ? "alive" : "idle"}`, explain: `A parallel session on its own scratch tree (${s.branch}).` });
    }
    return out;
  }
  function buildRows() {
    const rows = [];
    for (const x of pendingRows()) rows.push({ ...x, seg: null, idx: Infinity });
    const cps = D.segs.slice().sort((a, b) => b.last_index - a.last_index || (labelOf(a.feature_id) < labelOf(b.feature_id) ? -1 : 1) || b.seg_index - a.seg_index);
    for (const s of cps) rows.push({ kind: "cp", ref: s.ref, featureId: s.feature_id, seg: s, title: s.intent, idx: s.last_index });
    rows.forEach((r, i) => { r.i = i; });
    // a save shared with the row above is not news: the sha prints once, on the first row of it
    let prevSha = null; rows.forEach((r) => { const sv = r.seg ? savesOf(r.seg) : []; const sha = sv.length ? sv[sv.length - 1].sha : null; r.sameSave = !!(sha && sha === prevSha); prevSha = sha; });
    // One save split across several features is one subject over several rows. The subject prints
    // on the first row of the run; the rest are named by their feature, which is what tells them
    // apart (a ten-feature commit used to print its long subject ten times). A title repeated
    // anywhere else says which feature each row belongs to.
    rows.forEach((r, i) => { const p = rows[i - 1]; r.sib = r.kind === "cp" && !!p && p.kind === "cp" && r.sameSave && (p.sib ? p.subject : p.title) === r.title; if (r.sib) { r.subject = r.title; r.title = labelOf(r.featureId); } });
    const titleCount = new Map(); rows.forEach((r) => titleCount.set(r.title, (titleCount.get(r.title) || 0) + 1));
    rows.forEach((r) => { r.dis = !r.sib && titleCount.get(r.title) > 1 && r.kind === "cp" ? labelOf(r.featureId) : null; });
    const range = new Map();
    rows.forEach((r) => { const g = range.get(r.featureId) || { min: Infinity, max: -Infinity }; g.min = Math.min(g.min, r.i); g.max = Math.max(g.max, r.i); range.set(r.featureId, g); });
    const feats = [...range.entries()].sort((a, b) => a[1].min - b[1].min || a[1].max - b[1].max);
    const busy = []; const laneOf = new Map();
    for (const [fid, g] of feats) { let k = busy.findIndex((b) => b < g.min - 1); if (k < 0) { k = busy.length; busy.push(-Infinity); } busy[k] = g.max; laneOf.set(fid, k); }
    return { rows, range, laneOf, nLanes: busy.length };
  }
  let model = null;
  const rowEls = new Map();
  // Lanes are drawn from the text outward: the first-assigned lane (the newest feature's) sits
  // nearest the titles, so the common case reads node → title without crossing other lanes.
  const LANE_X0 = 12, LANE_DX = 10, NODE_Y = 18;
  const laneX = (k) => LANE_X0 + (model.nLanes - 1 - k) * LANE_DX;

  function rowEl(r) {
    let row = rowEls.get(r.ref); if (row) return row;
    row = el("div", "row"); row.dataset.ref = r.ref;
    const lanes = el("div", "lanes"); lanes.appendChild(svg("svg", {})); row.appendChild(lanes);
    const body = el("div", "body"); const line = el("div", "line");
    // the state right after the title, so "layout only" / "taken out" line up whatever the pill does
    line.append(el("span", "title", r.title), el("span", "state"), el("span", "pill"), el("span", "meta")); body.appendChild(line);
    const detail = el("div", "detail"); detail.appendChild(el("div")); body.appendChild(detail);
    row.appendChild(body);
    line.addEventListener("click", (ev) => {
      if (ev.target.closest(".sha") || ev.target.closest(".pill")) return;
      // a modifier click picks the row into a set to act on together, instead of opening it
      if ((ev.metaKey || ev.ctrlKey || ev.shiftKey) && r.kind === "cp") { togglePick(r.ref); return; }
      toggleOpen(r.ref);
    });
    row.addEventListener("mouseenter", () => { state.hover = r.ref; });
    rowEls.set(r.ref, row); return row;
  }
  function paintLanes(row, r) {
    const s = row.querySelector(".lanes svg"); clear(s);
    const { range, laneOf } = model; const mine = laneOf.get(r.featureId);
    for (const [fid, g] of range) {
      if (r.i < g.min || r.i > g.max) continue;
      const k = laneOf.get(fid), x = laneX(k), c = colorFor(fid);
      const pending = fid === r.featureId && r.kind !== "cp";
      if (!(r.i === g.min && r.i === g.max)) {
        s.appendChild(svg("line", { x1: x, x2: x, y1: r.i === g.min ? NODE_Y : 0, y2: r.i === g.max ? NODE_Y : "100%", class: "lane" + (fid === r.featureId ? " mine" : "") + (pending ? " pending" : "") + (state.laneHover === fid ? " lit" : ""), stroke: c, "data-fid": fid }));
      }
      const hit = svg("rect", { x: x - 5, y: 0, width: 10, height: "100%", class: "lane-hit" });
      hit.addEventListener("mouseenter", () => { state.laneHover = fid; rowsEl.classList.add("lanes-hover"); document.querySelectorAll(`.lane[data-fid="${CSS.escape(fid)}"]`).forEach((l) => l.classList.add("lit")); setStatus(`${labelOf(fid)} · ${plural((D.segsByFeature.get(fid) || []).length, "checkpoint")} · click to follow it`); });
      hit.addEventListener("mouseleave", () => { state.laneHover = null; rowsEl.classList.remove("lanes-hover"); document.querySelectorAll(".lane.lit").forEach((l) => l.classList.remove("lit")); setStatus(""); });
      hit.addEventListener("click", (ev) => { ev.stopPropagation(); follow(fid); });
      s.appendChild(hit);
    }
    const x = laneX(mine);
    s.appendChild(svg("circle", { cx: x, cy: NODE_Y, r: 4.5, class: "n-halo" }));
    const node = svg("circle", { cx: x, cy: NODE_Y, r: r.kind === "cp" && counts(r.seg).layoutOnly ? 3 : 4.5, class: "node" }); s.appendChild(node);
    s.appendChild(svg("text", { x, y: NODE_Y + 3.2, "text-anchor": "middle", class: "n-glyph", text: "" }));
    setNodeEl(row, nodeStateOf(r));
  }
  function nodeStateOf(r) {
    if (r.kind === "pending") return "pending";
    if (r.kind === "session") return "working";
    const st = state.staged;
    if (st && st.ref === r.ref) { if (st.phase === "refused" || st.phase === "failed") return "warn"; if (st.phase.startsWith("applying")) return "working"; if (st.phase === "receipt") return st.verb === "restore" ? "terminal" : "gone"; return "pending"; }
    if (counts(r.seg).layoutOnly) return "hair";
    const s = stateOf(r.seg); return s === "reverted" ? "gone" : s === "partial" ? "partial" : "solid";
  }
  function setNodeEl(row, st, halo) { const n = row.querySelector(".node"); if (!n) return; M.nodeState(n, st, { halo }); const g = row.querySelector(".n-glyph"); if (g) g.textContent = st === "warn" ? "!" : ""; }
  const setNode = (ref, st, halo) => { const row = rowEls.get(ref); if (row) setNodeEl(row, st, halo); };

  function paintRow(row, r) {
    row.style.setProperty("--c", colorFor(r.featureId));
    const live = r.kind === "cp" ? isLive(r.seg) : true;
    const c = r.kind === "cp" ? counts(r.seg) : null;
    const st = r.kind === "cp" ? stateOf(r.seg) : null;
    row.classList.toggle("gone", r.kind === "cp" && !live);
    row.classList.toggle("hair", !!(c && c.layoutOnly));
    row.classList.toggle("pending", r.kind !== "cp");
    row.classList.toggle("open", state.open === r.ref);
    row.classList.toggle("picked", state.picked.has(r.ref));
    row.classList.toggle("recede", !rowVisible(r));
    row.classList.toggle("veiled", state.at != null && r.kind === "cp" && r.seg.first_index > state.at);
    const sg = state.staged;
    row.classList.toggle("affected", !!(sg && sg.affectedRefs && sg.affectedRefs.has(r.ref) && sg.phase !== "receipt"));
    const pill = row.querySelector(".pill"); const top = model.range.get(r.featureId).min === r.i || r.kind === "session";
    pill.textContent = r.kind === "session" ? r.session : labelOf(r.featureId);
    // the feature's name where its lane starts, unless the row already says it
    const echoes = pill.textContent.trim().toLowerCase() === String(r.title || "").trim().toLowerCase();
    const pillShown = top && !echoes;
    pill.className = "pill" + (pillShown ? "" : echoes ? " echo" : " hover-only");
    row.classList.toggle("sib", !!r.sib);
    const titleEl = row.querySelector(".title"); clear(titleEl); titleEl.appendChild(document.createTextNode(r.title));
    if (r.dis && !pillShown) titleEl.appendChild(el("span", "dis", `  ${r.dis}`));
    pill.title = top ? "this feature starts here · click to follow it" : "click to follow this feature";
    pill.onclick = (ev) => { ev.stopPropagation(); follow(r.featureId); };
    const stEl = row.querySelector(".state");
    const by = r.kind === "cp" ? takenOutBy(r.seg) : "";
    stEl.textContent = r.kind === "pending" ? "planned" : r.kind === "session" ? "" : st === "reverted" ? ("taken out" + by) : st === "partial" ? ("partly taken out" + by) : c.layoutOnly ? "layout only" : "";
    stEl.className = "state" + (st === "reverted" ? " gone" : "");
    const meta = row.querySelector(".meta"); clear(meta);
    if (r.kind === "cp") {
      const sv = savesOf(r.seg); const newest = sv[sv.length - 1];
      if (newest && !r.sameSave) { const sha = el("span", "sha", shortSha(newest.sha)); sha.title = `${newest.subject} · click to look at the history as of this save`; sha.addEventListener("click", (ev) => { ev.stopPropagation(); lookBack(newest.index); }); meta.appendChild(sha); }
      else if (newest) meta.appendChild(el("span", "sha same", "〃"));
      if (sv.length > 1 && !r.sameSave) meta.appendChild(el("span", "more", `+${sv.length - 1}`));
      // the author only when authors differ: fifteen rows of "you" said nothing
      meta.appendChild(el("span", "", `${D.allYou ? "" : whoOf(r.seg) + " · "}${whenOf(r.seg)}`));
    } else if (r.sub) meta.appendChild(el("span", "", r.sub));
    paintLanes(row, r);
    paintDetail(row, r);
  }
  function rowVisible(r) {
    if (state.follow && r.featureId !== state.follow) return false;
    if (state.theme) { const t = D.themeById.get(state.theme); return r.kind === "cp" && inTheme(r.seg, t); }
    if (state.lens) { const q = state.lens.toLowerCase(); return r.kind === "cp" && segMatches(r.seg, q); }
    return true;
  }
  function segMatches(s, q) { return (s.intent || "").toLowerCase().includes(q) || labelOf(s.feature_id).toLowerCase().includes(q) || (s.asks || []).some((a) => (a.gist || "").toLowerCase().includes(q)) || symbolsOf(s).some((x) => x.symbol.toLowerCase().includes(q)); }

  // ── the opened row ───────────────────────────────────────────────────────────────────────────
  const detailBuilt = new Set();
  function paintDetail(row, r) {
    const host = row.querySelector(".detail > div");
    if (state.open !== r.ref) { if (detailBuilt.has(r.ref)) { setTimeout(() => { if (state.open !== r.ref) { clear(host); detailBuilt.delete(r.ref); } }, M.T.base + 20); } return; }
    if (detailBuilt.has(r.ref)) { refreshDetail(host, r); return; }
    detailBuilt.add(r.ref); clear(host);
    const inner = el("div", "detail-inner"); host.appendChild(inner);
    if (r.kind !== "cp") { inner.appendChild(el("div", "ask", r.explain || "")); return; }
    const seg = r.seg, c = counts(seg), live = isLive(seg);
    const a = (seg.asks || [])[0];
    if (a) { const ask = el("div", "ask"); ask.appendChild(document.createTextNode(`“${a.gist}”`)); ask.appendChild(el("span", "who", ` — ${a.source || a.actor}${a.ts ? ", " + relTime(a.ts) : ""}`)); inner.appendChild(ask); }
    else if (seg.rationale && seg.source !== "fallback") inner.appendChild(el("div", "ask", seg.rationale));
    else inner.appendChild(el("div", "ask dim", "No request was recorded for this, so it is named after its commit."));
    const cnt = el("div", "counts");
    cnt.appendChild(el("span", "", c.layoutOnly ? `moved lines around · changed no function · ${plural(c.saves, "save")}` : `${c.what} in ${c.filesWord} · ${plural(c.saves, "save")} · ${plural(c.changes, "change")}`));
    cnt.appendChild(el("span", "act")); inner.appendChild(cnt);
    if (!c.layoutOnly) {
      const grid = el("div", "syms");
      for (const s of c.syms) { const k = kindOf(s.kind); grid.appendChild(el("span", "k " + k.cls, k.g)); const sp = el("span", "s" + (s.live === false ? " gone" : "")); sp.appendChild(el("span", "f", shortFile(s.file) + "  ")); sp.appendChild(document.createTextNode(s.name || "(whole file)")); sp.title = `${k.word}: ${s.symbol}${s.live === false ? " · no longer in the code" : ""} · click to open it in the editor`; sp.addEventListener("click", (ev) => { ev.stopPropagation(); vscode.postMessage({ type: "openFile", path: s.file, symbol: s.symbol }); }); grid.appendChild(sp); }
      if (seg.symbols_truncated) grid.appendChild(el("span", "k", "")), grid.appendChild(el("span", "s", `+${seg.symbols_truncated} more`));
      inner.appendChild(grid);
    }
    const saves = savesOf(seg);
    if (saves.length) {
      const sv = el("div", "saves"); sv.appendChild(el("span", "", "from"));
      for (const s of saves) { const sh = el("span", "sha", shortSha(s.sha)); sh.title = s.subject; sv.appendChild(sh); sv.appendChild(el("span", "", truncate(s.subject, 44))); }
      inner.appendChild(sv);
    }
    refreshDetail(host, r);
    if (!c.layoutOnly) {
      const sec = el("div", "sec"); const lab = el("span", "", live ? "what it added and replaced" : "what putting it back restores"); lab.dataset.role = "hunks-label"; sec.appendChild(lab); inner.appendChild(sec);
      const hunkHost = el("div"); hunkHost.dataset.role = "hunks"; hunkHost.style.display = "flex"; hunkHost.style.flexDirection = "column"; hunkHost.style.gap = "8px"; inner.appendChild(hunkHost);
      paintHunks(row, r);
    }
  }
  function refreshDetail(host, r) {
    const act = host.querySelector(".act"); if (!act) return; clear(act);
    const seg = r.seg, live = isLive(seg);
    // one action here; each hunk below opens its own function in the editor, and the note opens
    // the whole diff once a decision is staged
    const b = el("button", "abtn " + (live ? "out" : "in"), live ? "Take out" : "Put back"); b.title = live ? "stage removing this checkpoint's changes; you confirm after reading the consequence" : "stage bringing this checkpoint's changes back";
    b.addEventListener("click", (ev) => { ev.stopPropagation(); stage(live ? "revert" : "restore", seg.ref); });
    act.append(b);
  }
  // Pass two of the opened row: the exact lines, once the dry run answers. Headed differently
  // from the function list above, because the closure can be wider than the checkpoint's own
  // symbols and the two must never read as one list morphing into another.
  function paintHunks(row, r) {
    const seg = r.seg, live = isLive(seg), verb = live ? "revert" : "restore";
    const lab = row.querySelector('[data-role="hunks-label"]'), host = row.querySelector('[data-role="hunks"]'); if (!lab || !host) return;
    const view = emitFor(verb, seg.ref);
    if (!view) { M.shimmer(lab, true); lab.classList.add("m-progress"); M.progress(lab, 0.4); requestEmit(verb, seg.ref); return; }
    M.shimmer(lab, false); M.progress(lab, 1); lab.classList.remove("m-progress"); clear(host);
    if (view.ok === false) { lab.textContent = humanize(view.message); return; }
    const d = hunksFor(seg);
    if (!d.files.length) { lab.textContent = "no line changes today"; return; }
    lab.textContent = `${live ? "what it added and replaced" : "what putting it back restores"} · +${d.added} −${d.removed}`;
    let i = 0; for (const f of d.files) for (const hk of f.hunks) { const h = hunkEl(f, hk, seg, verb); host.appendChild(h); M.blurFadeIn(h, i++ * 20); }
  }

  // ── hunks ────────────────────────────────────────────────────────────────────────────────────
  const diffCache = new Map();
  function lineDiff(a, b) {
    let p = 0; while (p < a.length && p < b.length && a[p] === b[p]) p++;
    let s = 0; while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
    const A = a.slice(p, a.length - s), B = b.slice(p, b.length - s); const n = A.length, m = B.length; const ops = [];
    for (let i = 0; i < p; i++) ops.push({ t: "=", i, j: i });
    if (n * m > 1200000) { for (let i = 0; i < n; i++) ops.push({ t: "-", i: p + i }); for (let j = 0; j < m; j++) ops.push({ t: "+", j: p + j }); }
    else {
      const W = m + 1, dp = new Uint16Array((n + 1) * W);
      for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i * W + j] = A[i] === B[j] ? dp[(i + 1) * W + j + 1] + 1 : Math.max(dp[(i + 1) * W + j], dp[i * W + j + 1]);
      let i = 0, j = 0;
      while (i < n && j < m) { if (A[i] === B[j]) { ops.push({ t: "=", i: p + i, j: p + j }); i++; j++; } else if (dp[(i + 1) * W + j] >= dp[i * W + j + 1]) { ops.push({ t: "-", i: p + i }); i++; } else { ops.push({ t: "+", j: p + j }); j++; } }
      while (i < n) { ops.push({ t: "-", i: p + i }); i++; } while (j < m) { ops.push({ t: "+", j: p + j }); j++; }
    }
    for (let k = 0; k < s; k++) ops.push({ t: "=", i: a.length - s + k, j: b.length - s + k });
    return ops;
  }
  // The checkpoint's own contribution: `+` is what it added to the code (present with it, absent
  // without), `−` what it replaced. For a put-back the sides swap, so the reading stays the same.
  function hunksFor(seg) {
    const live = isLive(seg); const view = emitFor(live ? "revert" : "restore", seg.ref);
    const key = seg.ref + (live ? "|r" : "|s");
    if (diffCache.has(key)) return diffCache.get(key);
    const out = { files: [], added: 0, removed: 0 };
    if (!view || !view.files) return out;
    const syms = symbolsOf(seg); const kindBySym = new Map(syms.map((s) => [s.symbol, s.kind]));
    for (const [path, pair] of Object.entries(view.files)) {
      const withText = live ? pair.before : pair.after, withoutText = live ? pair.after : pair.before;
      const withSpans = live ? pair.before_spans : pair.after_spans;
      const withL = withText ? String(withText).split("\n") : [], withoutL = withoutText ? String(withoutText).split("\n") : [];
      const ops = lineDiff(withoutL, withL);
      const spans = (withSpans || []).slice().sort((a, b) => a.start_line - b.start_line);
      const entityAt = (line) => { for (const sp of spans) if (line >= sp.start_line && line <= sp.end_line) return isUserSymbol(sp.symbol) ? sp.symbol : (sp.symbol.includes("__import__") ? "imports" : null); return null; };
      const lines = ops.map((o) => ({ t: o.t, text: o.t === "-" ? withoutL[o.i] : withL[o.j], withLine: o.t === "-" ? null : o.j + 1, withoutLine: o.t === "+" ? null : o.i + 1 }));
      let add = 0, del = 0; lines.forEach((l) => { if (l.t === "+") add++; if (l.t === "-") del++; });
      if (!add && !del) continue; out.added += add; out.removed += del;
      const hunks = []; let cur = null; let lastChange = -10;
      lines.forEach((l, idx) => {
        if (l.t !== "=") lastChange = idx;
        const near = l.t !== "=" || lines.slice(idx + 1, idx + 3).some((x) => x.t !== "=") || idx - lastChange <= 2;
        if (!near) { if (cur) { hunks.push(cur); cur = null; } return; }
        let wl = l.withLine; if (!wl) { let best = 0; for (const x of lines) { if (x.withLine) best = x.withLine; if (x.withoutLine === l.withoutLine) break; } wl = best; }
        const ent = entityAt(wl);
        if (!cur || (cur.entity !== ent && l.t !== "=")) { if (cur && cur.lines.some((x) => x.t !== "=")) hunks.push(cur); cur = { entity: ent, lines: [], file: path }; }
        cur.lines.push(l);
      });
      if (cur && cur.lines.some((x) => x.t !== "=")) hunks.push(cur);
      for (const h of hunks) { if (h.entity === "imports") { h.label = "imports"; h.entity = null; } h.add = h.lines.filter((l) => l.t === "+").length; h.del = h.lines.filter((l) => l.t === "-").length; h.kind = h.entity ? (kindBySym.get(h.entity) || "changed") : null; h.saves = h.entity ? ((syms.find((s) => s.symbol === h.entity) || {}).saves || []) : savesOf(seg).map((c) => c.index); }
      out.files.push({ path, add, del, hunks });
    }
    out.files.sort((a, b) => (b.add + b.del) - (a.add + a.del)); diffCache.set(key, out); return out;
  }
  function hunkEl(f, hk, seg, verb) {
    const wrap = el("div", "hunk"); const head = el("div", "h-head");
    const s = el("span", "s"); s.appendChild(el("span", "f", shortFile(f.path) + "  ")); s.appendChild(document.createTextNode(hk.entity ? hk.entity.split("::").slice(1).join("::") : (hk.label || ""))); head.appendChild(s);
    head.appendChild(el("span", "d", `+${hk.add} −${hk.del}`));
    const sv = (hk.saves || []).map((ci) => D.commits[ci] ? shortSha(D.commits[ci].sha) : "").filter(Boolean); if (sv.length) head.appendChild(el("span", "", "in " + sv.slice(0, 3).join(", ")));
    const open = el("button", "h-open", "open in editor"); open.addEventListener("click", () => vscode.postMessage({ type: "openChangeDiff", verb, ref: seg.ref, label: rowName(seg.ref), path: f.path, symbol: hk.entity || undefined })); head.appendChild(open);
    wrap.appendChild(head);
    const pre = el("pre"); const CAP = 12;
    const draw = (all) => { clear(pre); hk.lines.forEach((l, k) => { if (!all && k >= CAP) return; const ln = el("div", "ln " + (l.t === "+" ? "add" : l.t === "-" ? "del" : "")); ln.append(el("span", "no", String(l.withLine || l.withoutLine || "")), el("span", "mk", l.t === "=" ? " " : l.t), el("span", "tx", l.text)); pre.appendChild(ln); }); if (!all && hk.lines.length > CAP) { const m = el("button", "more", `${hk.lines.length - CAP} more lines`); m.addEventListener("click", () => draw(true)); pre.appendChild(m); } };
    draw(false); wrap.appendChild(pre); return wrap;
  }
  const HEXISH = /\b[0-9a-f]{12,}\b/g;
  function humanize(msg) {
    let t = String(msg || "").trim(); if (!t) return "ugh refused this and gave no reason.";
    t = t.replace(/\[[^\]]*\]/g, (list) => { const ids = list.match(HEXISH); return ids && ids.length ? `${ids.length} recorded edit(s)` : list; });
    t = t.replace(/\bf-[0-9a-f]{12,}(@\d+)?/g, "this checkpoint").replace(HEXISH, (id) => id.slice(0, 7)).replace(/none of its ops are in the current ideal; no change/, "none of its changes are in the code right now, so there is nothing to take out.");
    return t.length > 260 ? t.slice(0, 259) + "…" : t;
  }

  // ── the margin note: stage · consequence · apply · receipt · refusal ─────────────────────────
  let stageSeq = 0;
  function stage(verb, ref, chain) {
    const seg = D.segByRef.get(ref); if (!seg) return;
    state.staged = { verb, ref, label: rowName(ref), featureId: seg.feature_id, phase: "reading", cons: null, affectedRefs: new Set(), seq: ++stageSeq, chain: chain || null };
    state.open = ref; persist(); render();
    const row = rowEls.get(ref); if (row) { row.scrollIntoView({ block: "nearest", behavior: "smooth" }); setTimeout(renderNote, 260); }
    settleStage();
  }
  // A gesture that is several verbs -- a swap (take the newer out, put this back) or a pick of
  // several checkpoints -- runs as a chain: each step is staged, its consequence read, applied,
  // and the next begins; the note counts the steps and the final receipt lists every file. The
  // first step waits for the reader's click; the rest run on, since the gesture was one decision.
  function startChain(steps, label) {
    if (!steps.length) return;
    state.chain = { steps, i: 0, label, files: new Map(), details: [] };
    stage(steps[0].verb, steps[0].ref, state.chain);
  }
  function chainNext() {
    const ch = state.chain; if (!ch) return;
    ch.i += 1;
    if (ch.i >= ch.steps.length) { finishChain(); return; }
    const step = ch.steps[ch.i];
    stage(step.verb, step.ref, ch);
    // auto-apply once the consequence is read; a refusal stops the chain where it is
    const seq = state.staged.seq;
    const tick = setInterval(() => {
      const st = state.staged; if (!st || st.seq !== seq || st.chain !== ch) { clearInterval(tick); return; }
      if (st.phase === "confirmed") { clearInterval(tick); setTimeout(applyStaged, 350); }
      if (st.phase === "refused" || st.phase === "failed") clearInterval(tick);
    }, 80);
  }
  function finishChain() {
    const ch = state.chain; if (!ch) return;
    const st = state.staged;
    if (st) { st.receipt = { files: [...ch.files.values()], detail: ch.details.filter(Boolean).slice(-1)[0] || "" }; st.label = ch.label; st.chainDone = ch.steps.length; st.phase = "receipt"; }
    state.chain = null; state.picked.clear(); render();
  }
  function togglePick(ref) { if (state.picked.has(ref)) state.picked.delete(ref); else state.picked.add(ref); render(); }
  function takeOutPicked() {
    const refs = [...state.picked].filter((r) => D.segByRef.has(r) && isLive(D.segByRef.get(r)));
    // newest first: each take-out peels the latest work, never digs under later work
    refs.sort((a, b) => D.segByRef.get(b).last_index - D.segByRef.get(a).last_index);
    startChain(refs.map((ref) => ({ verb: "revert", ref })), `${plural(refs.length, "checkpoint")} you picked`);
  }
  // The checkpoint that owns the live version of `symbol` right now: the latest live checkpoint
  // on any feature whose symbols include it. Used for Swap until the kernel names the live op
  // itself (K7); said as "likely" in the note until then.
  function ownerOfLive(symbol, except) {
    let best = null;
    for (const s of D.segs) { if (s.ref === except || !isLive(s)) continue; if (symbolsOf(s).some((x) => x.symbol === symbol)) if (!best || s.last_index > best.last_index) best = s; }
    return best;
  }
  // When the dry run is here, read it; a refusal is a different note, with the ways out.
  function settleStage() {
    const st = state.staged; if (!st || st.phase !== "reading") return;
    const view = emitFor(st.verb, st.ref);
    if (!view) { requestEmit(st.verb, st.ref); return; }
    if (view.ok === false) {
      st.phase = "refused"; st.message = view.message; st.forked = !!view.forked;
      // the forking symbol and who owns its live version: from the kernel's `conflicts` when it
      // carries them (K7), else the best guess from the symbols the payload names
      const k = (view.conflicts || [])[0];
      st.symbol = (k && k.symbol) || (view.affected_symbols || []).find(isUserSymbol) || null;
      st.liveOwner = k && k.live_op ? D.segs.find((s) => (s.op_ids || []).includes(k.live_op)) || null : (st.symbol ? ownerOfLive(st.symbol, st.ref) : null);
      st.ownerKnown = !!(k && k.live_op);
      render(); const row = rowEls.get(st.ref); if (row) M.highlight(row, "warn"); return;
    }
    st.cons = consequence(st, view); st.affectedRefs = st.cons.refs; st.phase = "confirmed"; render();
    for (const r of st.affectedRefs) { const re = rowEls.get(r); if (re) M.highlight(re, "affected", 600); }
  }
  function consequence(st, view) {
    const laneOfSym = (sym) => { for (const n of D.map.nodes) if (n.kind === "feature" && (n.own_symbols || []).includes(sym)) return n.id; return null; };
    const list = (k) => [...new Set((view[k] || []).filter(isUserSymbol))].sort();
    const pruned = list("pruned_symbols"), subtracted = list("subtracted_symbols"), conflicts = list("kept_conflicts"), broken = list("broken_references");
    const other = new Map();
    // A feature in the focus subgraph counts as touched only when work of its own comes out
    // (its count drops). A count that rises is the minted prune and repair ops being filed by
    // plurality -- bookkeeping, not a rewrite, and it used to print as "12 → 13 recorded changes".
    for (const nd of (view.focus && view.focus.nodes) || []) { if (nd.feature_id === st.featureId || !D.nodeById.has(nd.feature_id) || !(nd.ops_after < nd.ops_before)) continue; other.set(nd.feature_id, { label: nd.label }); }
    const lanes = new Set(other.keys()); const touched = new Set([...pruned, ...subtracted, ...conflicts, ...broken]);
    for (const s of touched) { const l = laneOfSym(s); if (l && l !== st.featureId) lanes.add(l); }
    const refs = new Set();
    // The rows that change are the live rows, in any lane, whose named functions are among the
    // ones going (a later checkpoint that edited a function this one created is partly taken out
    // with it); a feature that comes out wholesale names its latest row.
    for (const [fid, segs] of D.segsByFeature) for (const s of segs) if (s.ref !== st.ref && isLive(s) && symbolsOf(s).some((x) => touched.has(x.symbol))) { refs.add(s.ref); lanes.add(fid); }
    for (const fid of other.keys()) { const l = (D.segsByFeature.get(fid) || []).filter(isLive); if (l.length && !l.some((s) => refs.has(s.ref))) refs.add(l[l.length - 1].ref); }
    return { pruned, subtracted, conflicts, broken, other, lanes, refs, so_what: view.so_what || "" };
  }
  const symName = (s) => { const p = s.split("::"); return p.length > 1 ? shortFile(p[0]) + "::" + p.slice(1).join("::") : shortFile(s); };
  const listNames = (arr, cap = 3) => arr.slice(0, cap).map(symName).join(", ") + (arr.length > cap ? ` +${arr.length - cap} more` : "");
  function renderNote() {
    const st = state.staged; clear(noteEl); noteEl.className = "note"; noteEl.hidden = !st; if (!st) return;
    const btn = (t, cls, fn) => { const b = el("button", "abtn " + (cls || ""), t); if (fn) b.addEventListener("click", fn); return b; };
    const seg = D.segByRef.get(st.ref); const c = seg ? counts(seg) : null;
    const out = st.verb === "revert";
    const head = el("div", "n-head");
    const verbWord = st.phase === "receipt" ? (out ? "Taken out" : "Put back") : st.phase === "refused" ? (out ? "Can't take out" : "Can't put back") : st.phase === "failed" ? (out ? "Take out failed" : "Put back failed") : (out ? "Take out" : "Put back");
    head.append(el("span", "n-verb " + (out ? "out" : "in"), verbWord), el("span", "n-target", st.label)); noteEl.appendChild(head);
    const actions = el("div", "n-actions");
    if (st.phase === "refused" || st.phase === "failed") {
      noteEl.classList.add("refused");
      const p = el("p");
      if (st.forked && st.symbol) { p.appendChild(el("span", "sym", symName(st.symbol))); p.appendChild(document.createTextNode(" already has a different live version. One version of a function can be live at a time, so this one cannot come back over it.")); }
      else p.textContent = humanize(st.message);
      noteEl.appendChild(p);
      if (st.forked && st.symbol) {
        const ways = el("div", "ways");
        if (st.liveOwner) {
          const owner = st.liveOwner;
          const sw = btn("", "", () => startChain([{ verb: "revert", ref: owner.ref }, { verb: "restore", ref: st.ref }], `swap: ${st.label} back in place of ${rowName(owner.ref)}`));
          sw.appendChild(document.createTextNode(`Swap: take out “${rowName(owner.ref)}” first, then put this back`));
          sw.appendChild(el("small", "", (st.ownerKnown ? "" : "likely the owner of the live version · ") + "two steps, both previewed, both undoable"));
          ways.appendChild(sw);
        }
        const rec = btn("", "", () => vscode.postMessage({ type: "resolveFork", symbol: st.symbol })); rec.appendChild(document.createTextNode("Reconcile the two versions in the editor")); rec.appendChild(el("small", "", "keeps both; you merge them by hand"));
        ways.appendChild(rec); noteEl.appendChild(ways);
      }
      if (st.chain) noteEl.appendChild(el("p", "dim", `step ${st.chain.i + 1} of ${st.chain.steps.length} stopped here; the earlier steps stand (Undo reverses them one at a time).`));
      actions.appendChild(btn("Copy command", "quiet", () => copyText(`ugh ${st.verb} ${st.ref}`)));
      actions.appendChild(btn("Close", "", cancelStaged)); noteEl.appendChild(actions); placeNote(); return;
    }
    if (st.phase === "receipt") {
      noteEl.classList.add("receipt"); const rec = st.receipt || { files: [], detail: "" };
      const p = el("p"); const chk = svg("svg", { class: "check", viewBox: "0 0 16 16" }); const path = svg("path", { d: "M3 8.5l3.2 3L13 4.5" }); chk.appendChild(path); p.appendChild(chk); p.appendChild(document.createTextNode(rec.files.length ? `${plural(rec.files.length, "file")} rewritten` : "applied")); noteEl.appendChild(p); M.checkDraw(path, 60);
      if (rec.files.length) { const fl = el("div", "files"); for (const f of rec.files) { const row = el("div", "rf"); const d = el("span", "d"); d.append(el("span", "minus", `−${f.del}`), document.createTextNode(" "), el("span", "plus", `+${f.add}`)); row.append(el("span", "f", shortFile(f.path)), d); fl.appendChild(row); } noteEl.appendChild(fl); }
      if (rec.detail) { const sp = el("p", "dim"); const t = el("span", ""); sp.appendChild(t); noteEl.appendChild(sp); M.decodeText(t, rec.detail, 500); }
      const left = el("span", "left"); left.appendChild(btn("Open diff", "quiet", () => { const d = seg ? hunksFor(seg) : { files: [] }; const first = d.files[0]; if (first) vscode.postMessage({ type: "openChangeDiff", verb: st.verb, ref: st.ref, label: st.label, path: first.path }); })); actions.appendChild(left);
      const n = st.chainDone || 1;
      actions.appendChild(btn(n > 1 ? `Undo all ${n}` : "Undo", "", () => { undoTimes(n); state.staged = null; render(); }));
      actions.appendChild(btn("Done", "", () => { state.staged = null; render(); }));
      noteEl.appendChild(actions); placeNote(); return;
    }
    if (st.phase.startsWith("applying")) {
      if (st.chain) noteEl.appendChild(el("p", "dim", `step ${st.chain.i + 1} of ${st.chain.steps.length}`));
      const steps = el("div", "steps"); const at = ["checking", "applying", "refreshing"].indexOf(st.phase.split(":")[1]);
      ["checking", "rewriting files", "refreshing"].forEach((t, i) => steps.appendChild(el("span", i < at ? "done" : i === at ? "live m-shimmer" : "", t))); noteEl.appendChild(steps); placeNote(); return;
    }
    if (st.chain) noteEl.appendChild(el("p", "dim", `step ${st.chain.i + 1} of ${st.chain.steps.length} · ${st.chain.label}`));
    const what = out ? "Removes" : "Brings back";
    const nSyms = c ? c.syms.length : 0, fnWord = c && c.syms.some((s) => s.name) ? "function" : "file";
    noteEl.appendChild(el("p", "", c && !c.layoutOnly ? `${what} ${plural(c.changes, "change")} in ${plural(nSyms, fnWord)} (${c.filesWord}).` : `${what} a step that only moved lines around.`));
    if (st.phase === "reading") noteEl.appendChild(el("p", "dim m-shimmer", "reading the exact consequence"));
    else {
      const k = st.cons; const amber = [];
      if (out) { if (k.pruned.length) noteEl.appendChild(el("p", "dim", `Gone entirely: ${listNames(k.pruned)}.`)); if (k.subtracted.length) noteEl.appendChild(el("p", "dim", `Rewritten in place, later work kept: ${listNames(k.subtracted)}.`)); }
      const outNames = [...k.other.values()].map((o) => o.label);
      if (outNames.length) amber.push(`Also comes out: ${outNames.slice(0, 4).join(", ")}${outNames.length > 4 ? ` +${outNames.length - 4} more` : ""} — ${outNames.length > 1 ? "their" : "its"} work depends on this.`);
      // the rows that go warm on the ledger, named here so the band and the sentence are one thing
      const rowsTouched = [...k.refs].map((r) => D.segByRef.get(r)).filter((s) => s && !k.other.has(s.feature_id)).map((s) => rowName(s.ref));
      if (rowsTouched.length) amber.push(`Rows that change with it: ${rowsTouched.slice(0, 3).join(", ")}${rowsTouched.length > 3 ? ` +${rowsTouched.length - 3} more` : ""}.`);
      if (k.broken.length) amber.push(`Still names removed code, may break: ${listNames(k.broken)}.`);
      if (k.conflicts.length) amber.push(`Needs a hand edit, two versions overlap: ${listNames(k.conflicts)}.`);
      if (amber.length) for (const t of amber) noteEl.appendChild(el("p", "warnline", t)); else noteEl.appendChild(el("p", "dim", "Nothing else depends on it."));
      noteEl.appendChild(el("p", "dim", out ? "The changes stay recorded. Undoable." : "Undoable."));
    }
    const left = el("span", "left"); left.appendChild(btn("Open diff", "quiet", () => { const d = seg ? hunksFor(seg) : { files: [] }; const first = d.files[0]; if (first) vscode.postMessage({ type: "openChangeDiff", verb: st.verb, ref: st.ref, label: st.label, path: first.path }); else setStatus("the dry run has not answered yet"); })); actions.appendChild(left);
    actions.appendChild(btn("Cancel", "", cancelStaged));
    const go = btn(out ? "Take out" : "Put back", out ? "out" : "in", applyStaged); go.disabled = st.phase !== "confirmed"; actions.appendChild(go);
    noteEl.appendChild(actions); placeNote();
  }
  // The note sits beside its row in the margin when the pane is wide enough for the page and the
  // note side by side (940 + 300 + gaps, the same width as the stylesheet's breakpoint), and inside
  // the opened row otherwise. In the margin it is clamped to the visible pane and re-clamped on
  // every scroll: placed once, the smooth scroll that brings a low row into view used to carry the
  // note off the top of a short panel. Inside the row it is scrolled into view once per phase. On a
  // narrow pane whose staged row is not the open one (a receipt kept while reading another row) it
  // docks to the bottom edge of the pane instead of trailing the last row.
  const NOTE_BESIDE = 1270;
  function placeNote() {
    const st = state.staged; if (!st || noteEl.hidden) return;
    const row = rowEls.get(st.ref);
    const inner = row && state.open === st.ref ? row.querySelector(".detail-inner") : null;
    if (mainEl.clientWidth < NOTE_BESIDE) {
      noteEl.style.top = noteEl.style.left = "";
      if (!inner) { if (noteEl.parentNode !== mainEl) mainEl.appendChild(noteEl); noteEl.classList.add("docked"); return; }
      noteEl.classList.remove("docked");
      if (noteEl.parentNode !== inner) inner.insertBefore(noteEl, inner.firstChild);
      // once per phase: each one follows something the reader just did (staged, confirmed, applied)
      if (st.noteShown !== st.phase) { st.noteShown = st.phase; setTimeout(() => { if (state.staged === st && noteEl.isConnected) noteEl.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, 60); }
      return;
    }
    if (noteEl.parentNode !== mainEl) mainEl.appendChild(noteEl);
    const page = document.querySelector(".page");
    // measured against the scrolling pane, not the rows box the row is laid out in
    const want = row ? row.getBoundingClientRect().top - mainEl.getBoundingClientRect().top + mainEl.scrollTop + 2 : 60;
    const maxTop = mainEl.scrollTop + mainEl.clientHeight - noteEl.offsetHeight - 12;
    noteEl.style.top = Math.max(mainEl.scrollTop + 8, Math.min(want, maxTop)) + "px";
    noteEl.style.left = Math.min(mainEl.clientWidth - 316, page.offsetLeft + page.offsetWidth + 8) + "px";
  }
  let notePlacing = false;
  const replaceNote = () => {
    if (!state.staged || notePlacing) return;
    notePlacing = true; requestAnimationFrame(() => { notePlacing = false; placeNote(); });
  };
  mainEl.addEventListener("scroll", replaceNote, { passive: true });
  // the note grows after it is placed (the consequence arrives, the receipt's id decodes in)
  if (typeof ResizeObserver === "function") new ResizeObserver(replaceNote).observe(noteEl);
  function cancelStaged() { const st = state.staged; state.staged = null; render(); if (st) { const r = model.rows.find((x) => x.ref === st.ref); if (r) setNode(st.ref, nodeStateOf(r)); } }
  function applyStaged() {
    const st = state.staged; if (!st || st.phase !== "confirmed") return;
    st.phase = "applying:checking"; render(); setNode(st.ref, "working");
    vscode.postMessage({ type: "applyStaged", verb: st.verb, ref: st.ref });
  }
  function onApplyProgress(msg) {
    const st = state.staged; if (!st || st.ref !== msg.ref) return;
    if (msg.phase === "checking" || msg.phase === "applying" || msg.phase === "refreshing") { st.phase = "applying:" + msg.phase; renderNote(); return; }
    if (msg.phase === "cancelled") { state.staged = null; render(); return; }
    if (msg.phase === "failed") { st.phase = "failed"; st.message = msg.detail || "the apply failed"; render(); const row = rowEls.get(st.ref); if (row) M.highlight(row, "warn"); return; }
    if (msg.phase === "done") {
      const seg = D.segByRef.get(st.ref); const d = seg ? hunksFor(seg) : { files: [] };
      const files = d.files.map((f) => ({ path: f.path, add: st.verb === "revert" ? f.del : f.add, del: st.verb === "revert" ? f.add : f.del }));
      setNode(st.ref, st.verb === "revert" ? "gone" : "terminal", true);
      const row = rowEls.get(st.ref); if (row) { M.highlight(row, "yours"); if (st.verb === "restore") { const ln = row.querySelector(".lane.mine"); if (ln) M.laneDraw(ln); } }
      if (st.chain) {
        // one step of several: fold its files into the chain's receipt and move on
        for (const f of files) { const e = st.chain.files.get(f.path) || { path: f.path, add: 0, del: 0 }; e.add += f.add; e.del += f.del; st.chain.files.set(f.path, e); }
        st.chain.details.push(msg.detail || "");
        st.phase = "applying:refreshing"; renderNote();
        setTimeout(chainNext, 400);
        return;
      }
      st.receipt = { files, detail: msg.detail || "" };
      st.phase = "receipt"; render();
    }
  }
  // Undo a chain: one `ugh undo` per step, each after the host says the previous one is done.
  let undoQueue = 0;
  function undoTimes(n) { undoQueue = Math.max(0, n - 1); vscode.postMessage({ type: "dailyLoop", verb: "undo" }); }
  function copyText(t) { try { navigator.clipboard.writeText(t).then(() => setStatus("copied: " + t), () => setStatus(t)); } catch { setStatus(t); } }

  // ── lines above the rows, the strip, the foot ────────────────────────────────────────────────
  function renderThemes() {
    const th = $("themesLine"); clear(th);
    const named = D.themes.filter((t) => t.span.size >= 2 && t.label !== "(unwitnessed)").slice(0, 6);
    th.hidden = !named.length; if (!named.length) return;
    th.appendChild(el("span", "lab", "across features"));
    for (const t of named) { const b = el("button", "t" + (state.theme === t.theme_id ? " on" : "")); b.append(document.createTextNode(truncate(t.label, 34)), el("span", "n", String(t.span.size))); b.title = (t.rationale || "") + " · click to see its rows"; b.addEventListener("click", () => { state.theme = state.theme === t.theme_id ? null : t.theme_id; state.follow = null; persist(); render(); }); th.appendChild(b); }
  }
  function renderStrip() {
    clear(stripEl); let text = null;
    if (state.theme) { const t = D.themeById.get(state.theme); text = ["showing ", el("b", "", t.label), ` · one request across ${plural(t.span.size, "feature")} · ${plural(D.segs.filter((s) => inTheme(s, t)).length, "checkpoint")}`]; }
    else if (state.follow) { text = ["following ", el("b", "", labelOf(state.follow)), ` · ${plural((D.segsByFeature.get(state.follow) || []).length, "checkpoint")}`]; }
    else if (state.lens) { const n = D.segs.filter((s) => segMatches(s, state.lens.toLowerCase())).length; text = ["matching ", el("b", "", `“${state.lens}”`), ` · ${plural(n, "checkpoint")}`]; }
    else if (state.at != null && D.commits[state.at]) { text = ["as of save ", el("b", "", shortSha(D.commits[state.at].sha)), ` · ${D.commits[state.at].subject} · newer rows veiled`]; }
    else if (state.picked.size) { text = [el("b", "", plural(state.picked.size, "checkpoint")), " picked · ⌘-click picks more"]; }
    stripEl.hidden = !text; if (!text) return;
    for (const t of text) stripEl.appendChild(typeof t === "string" ? document.createTextNode(t) : t);
    if (state.picked.size && !state.theme && !state.follow && !state.lens && state.at == null) {
      const live = [...state.picked].filter((r) => D.segByRef.has(r) && isLive(D.segByRef.get(r))).length;
      if (live) { const go = el("button", "abtn out", `Take out ${plural(live, "checkpoint")}`); go.title = "one gesture, one step per checkpoint, newest first; each step is previewed"; go.addEventListener("click", takeOutPicked); stripEl.appendChild(go); }
    }
    const c = el("button", "clear"); c.append(el("kbd", "", "Esc"), document.createTextNode(" clears")); c.addEventListener("click", popState); stripEl.appendChild(c);
  }
  function renderFoot(animate) {
    const live = D.segs.reduce((a, s) => a + counts(s).live, 0); const total = D.segs.reduce((a, s) => a + counts(s).changes, 0);
    const text = `${plural(D.commits.length, "save")} · ${plural(D.laneIds.length, "feature")} · ${plural(D.segs.length, "checkpoint")} · ${live === total ? plural(live, "change") : `${live} live of ${total} changes`}`;
    const f = $("foot"); if (animate && f.textContent && f.textContent !== text) M.counterSwap(f, text); else f.textContent = text;
    const open = state.open && D.segByRef.get(state.open);
    statusR.textContent = open ? rowName(open.ref) : "";
    // Save is the loud button only while there is unsaved work to record
    const dirty = !!(D.compose.status && D.compose.status.drift && D.compose.status.drift.any);
    $("saveBtn").classList.toggle("primary", dirty); $("saveBtn").title = dirty ? "record your edits as a save" : "nothing to save: everything is recorded";
  }

  // ── render ───────────────────────────────────────────────────────────────────────────────────
  let firstRender = true;
  function render() {
    if (!D) return;
    model = buildRows();
    const gutter = Math.max(48, LANE_X0 + model.nLanes * LANE_DX + 14) + "px";
    document.querySelector(".page").style.setProperty("--gutter", gutter);
    const seen = new Set();
    model.rows.forEach((r, i) => { const row = rowEl(r); seen.add(r.ref); if (rowsEl.children[i] !== row) rowsEl.insertBefore(row, rowsEl.children[i] || null); paintRow(row, r); });
    for (const [ref, row] of rowEls) if (!seen.has(ref)) { row.remove(); rowEls.delete(ref); detailBuilt.delete(ref); }
    renderThemes(); renderStrip(); renderNote(); renderFind(); renderFoot(!firstRender);
    if (firstRender) { firstRender = false; reveal(); }
  }
  async function reveal() {
    if (M.reduced()) return;
    const rows = [...rowsEl.children].reverse(); const stag = Math.min(M.T.stagger, Math.max(18, 1100 / Math.max(1, rows.length)));
    rows.forEach((row) => { row.style.opacity = "0"; row.style.filter = "blur(4px)"; });
    rows.forEach((row, i) => setTimeout(() => { M.blurFadeIn(row); row.querySelectorAll(".lane").forEach((l) => M.laneDraw(l, 260)); }, i * stag));
  }
  // What changed between two states decides what moves: a new checkpoint arrives (gap, decode,
  // lane draw, a tint for whose it was); a checkpoint whose state changed gets its node tweened and
  // a tint; nothing else animates on a push.
  let prevStates = null;
  function choreograph() {
    const next = new Map(D.segs.map((s) => [s.ref, stateOf(s)]));
    if (prevStates) {
      for (const s of D.segs) {
        if (!prevStates.has(s.ref)) {
          const row = rowEls.get(s.ref); if (!row) continue;
          const title = row.querySelector(".title"), lane = row.querySelector(".lane.mine"), node = row.querySelector(".node");
          M.run([
            async () => { row.style.opacity = "0"; await M.openGap(row); },
            async () => { await M.blurFadeIn(row); M.nodeState(node, "working"); if (lane) M.laneDraw(lane); await M.decodeText(title, rowName(s.ref)); },
            async () => { M.nodeState(node, "solid", { halo: true }); M.highlight(row, whoOf(s) === "agent" ? "incoming" : "yours"); },
          ]);
        } else if (prevStates.get(s.ref) !== next.get(s.ref)) {
          const row = rowEls.get(s.ref); if (!row) continue;
          const r = model.rows.find((x) => x.ref === s.ref); if (r) setNodeEl(row, nodeStateOf(r), true);
          M.highlight(row, next.get(s.ref) === "reverted" ? "affected" : "yours");
        }
      }
    }
    prevStates = next;
  }

  // ── state transitions ────────────────────────────────────────────────────────────────────────
  function toggleOpen(ref) { state.open = state.open === ref ? null : ref; persist(); render(); if (state.open) scrollRowIntoView(ref); }
  function follow(fid) { state.follow = state.follow === fid ? null : fid; state.theme = null; persist(); render(); }
  function lookBack(ci) { state.at = state.at === ci ? null : ci; render(); setStatus(state.at != null ? `as of save ${shortSha(D.commits[ci].sha)} · rows above are veiled: they had not happened yet` : ""); }
  function scrollRowIntoView(ref) { requestAnimationFrame(() => { const r = rowEls.get(ref); if (r) r.scrollIntoView({ block: "nearest", behavior: "smooth" }); }); }
  function popState() {
    if (state.staged && state.staged.phase !== "receipt" && !state.staged.phase.startsWith("applying")) { cancelStaged(); return; }
    if (state.staged && state.staged.phase === "receipt") state.staged = null;
    else if (state.picked.size) state.picked.clear();
    else if (state.lens) { state.lens = ""; $("find").value = ""; }
    else if (state.theme) state.theme = null;
    else if (state.follow) state.follow = null;
    else if (state.open) state.open = null;
    else if (state.at != null) state.at = null;
    persist(); render();
  }
  function findResults(q) {
    q = q.toLowerCase(); const hits = [];
    for (const fid of D.laneIds) if (labelOf(fid).toLowerCase().includes(q)) hits.push({ kind: "feature", id: fid, label: labelOf(fid), color: colorFor(fid) });
    for (const s of D.segs) if (segMatches(s, q)) hits.push({ kind: "checkpoint", id: s.ref, label: s.intent, sub: labelOf(s.feature_id), color: colorFor(s.feature_id) });
    return hits.slice(0, 9);
  }
  function renderFind() {
    const box = $("findResults"); clear(box); const q = state.lens.trim(); box.hidden = !q; if (!q) return;
    const hits = findResults(q);
    if (!hits.length) { box.appendChild(el("div", "empty", "nothing matches: try a word from a prompt, a function name, or a feature")); return; }
    hits.forEach((h, i) => { const b = el("button", i === 0 ? "active" : ""); const d = el("span", "dot"); d.style.background = h.color; b.append(d, el("span", "r-name", h.label)); if (h.sub) b.appendChild(el("span", "r-sub", h.sub)); b.addEventListener("click", () => goHit(h)); box.appendChild(b); });
  }
  function goHit(h) { state.lens = ""; $("find").value = ""; if (h.kind === "feature") follow(h.id); else { state.open = h.id; persist(); render(); scrollRowIntoView(h.id); } }
  $("find").addEventListener("input", (ev) => { state.lens = ev.target.value; render(); });
  $("find").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { const h = findResults(state.lens.trim())[0]; if (h) goHit(h); } if (ev.key === "Escape") { ev.stopPropagation(); state.lens = ""; ev.target.value = ""; ev.target.blur(); render(); } });
  document.addEventListener("keydown", (ev) => {
    const typing = ev.target && (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA");
    if (ev.key === "Escape") { if (!typing) popState(); return; }
    if (typing || !model) return;
    if (ev.key === "/") { ev.preventDefault(); $("find").focus(); return; }
    const order = model.rows.map((r) => r.ref);
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") { ev.preventDefault(); const dir = ev.key === "ArrowDown" ? 1 : -1; const cur = state.open || state.hover; const i = order.indexOf(cur); const next = order[Math.max(0, Math.min(order.length - 1, (i < 0 ? (dir > 0 ? -1 : order.length) : i) + dir))]; if (state.open) { state.open = next; persist(); render(); scrollRowIntoView(next); } else { state.hover = next; const re = rowEls.get(next); if (re) { re.scrollIntoView({ block: "nearest" }); document.querySelectorAll(".row.kb").forEach((x) => x.classList.remove("kb")); re.classList.add("kb"); } } return; }
    if (ev.key === "ArrowRight" || ev.key === "Enter") { ev.preventDefault(); const t = state.hover || order[0]; if (t && !state.open) { state.open = t; persist(); render(); } return; }
    if (ev.key === "ArrowLeft") { ev.preventDefault(); if (state.open) { state.open = null; persist(); render(); } }
  });
  $("workbenchBtn").addEventListener("click", () => vscode.postMessage({ type: "openWorkbench" }));
  $("saveBtn").addEventListener("click", () => { $("saveBtn").disabled = true; $("undoBtn").disabled = true; vscode.postMessage({ type: "dailyLoop", verb: "save" }); });
  $("undoBtn").addEventListener("click", () => { $("saveBtn").disabled = true; $("undoBtn").disabled = true; vscode.postMessage({ type: "dailyLoop", verb: "undo" }); });
  window.addEventListener("resize", () => { if (state.staged) renderNote(); });
  mainEl.addEventListener("scroll", () => { if (state.staged && noteEl.parentNode === mainEl) renderNote(); }, { passive: true });
  // the one-line how-to in the status line goes once the reader has touched the ledger
  rowsEl.addEventListener("pointerdown", () => document.body.classList.add("touched"), { once: true });

  // ── the host ─────────────────────────────────────────────────────────────────────────────────
  window.addEventListener("message", (ev) => {
    const msg = ev.data || {};
    if (msg.type === "state") {
      load(msg.compose); emits.clear(); emitWaiters.clear(); diffCache.clear();
      if (state.open && !D.segByRef.has(state.open) && !model) state.open = null;
      render(); choreograph();
      // a push answers an apply too: the staged note's receipt stays until dismissed; the rows now
      // carry the kernel's own states, and the opened row re-reads its hunks when asked
      if (state.open) { const row = rowEls.get(state.open); const r = model.rows.find((x) => x.ref === state.open); if (row && r && r.kind === "cp" && detailBuilt.has(r.ref)) paintHunks(row, r); }
      $("saveBtn").disabled = false; $("undoBtn").disabled = false;
      return;
    }
    if (msg.type === "changeResult") {
      const key = msg.verb + "\u001f" + msg.ref; emits.set(key, msg.view || { ok: false, message: "no answer" });
      diffCache.delete(msg.ref + "|r"); diffCache.delete(msg.ref + "|s");
      const row = rowEls.get(msg.ref); const r = model && model.rows.find((x) => x.ref === msg.ref);
      if (row && r && state.open === msg.ref) paintHunks(row, r);
      settleStage();
      return;
    }
    if (msg.type === "applyProgress") { onApplyProgress(msg); return; }
    if (msg.type === "loopBusy") {
      $("saveBtn").disabled = false; $("undoBtn").disabled = false;
      if (undoQueue > 0) { undoQueue -= 1; vscode.postMessage({ type: "dailyLoop", verb: "undo" }); }
      return;
    }
    if (msg.type === "error") { setStatus(msg.message, "err"); }
    // the editor's gutter hover ("Open Workbench") names a feature: follow its lane
    if (msg.type === "revealFeature" && D && D.nodeById.has(msg.featureId)) { state.follow = msg.featureId; state.theme = null; persist(); render(); }
  });
  window.__wb = { state, render, stage, toggleOpen, follow, get D() { return D; } };
  vscode.postMessage({ type: "ready" });
})();
