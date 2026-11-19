"use strict";
// The workbench: saves as columns and names as rows (the timeline), or one row per save (the
// ledger). The block between the model markers is pure; tests/test_workbench_ui.py runs it in node.

// ---- model
const ROW_H = 22;
const COL_W = 22;
const HAIR_W = 7;
const LABEL_W = 184;
const META_W = 220;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function shortSha(sha) {
  return String(sha || "").slice(0, 7);
}

function unitOf(address) {
  const s = String(address);
  const at = s.lastIndexOf("@");
  return at > 0 ? s.slice(0, at) : s;
}

function pathOf(address) {
  const u = unitOf(address);
  const i = u.indexOf("::");
  return i < 0 ? u : u.slice(0, i);
}

function defOf(address) {
  const u = unitOf(address);
  const i = u.indexOf("::");
  return i < 0 ? "" : u.slice(i + 2);
}

// Shared with the host's blame gutter: FNV-1a, spread by the golden ratio.
function hueOf(label) {
  let h = 2166136261;
  for (let i = 0; i < label.length; i++) {
    h ^= label.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) * 0.618033988749895 % 1) * 360;
}

function toneLevel(n) {
  return n >= 8 ? 4 : n >= 4 ? 3 : n >= 2 ? 2 : 1;
}

// Every save gets the same column width, sized to the panel between a floor and a ceiling.
function fitColumn(log, status, avail) {
  const saves = (log && log.saves) || [];
  const merges = new Set(((status && status.choices) || []).map((c) => c.merge));
  const full = saves.filter((s) => (s.steps || []).length || merges.has(s.sha)).length;
  if (!full) return COL_W;
  const w = Math.floor((avail - (saves.length - full) * HAIR_W) / full);
  return Math.max(18, Math.min(44, w));
}

function buildModel(log, status, opts) {
  const open = !!(opts && opts.unnamedOpen);
  const colW = (opts && opts.colW) || COL_W;
  const saves = ((log && log.saves) || []).slice().reverse();
  const choices = (status && status.choices) || [];
  const isMerge = (sha) => choices.some((c) => c.merge && (c.merge === sha || sha.startsWith(c.merge)));
  const cols = [];
  const steps = [];
  let x = 0;
  saves.forEach((s, i) => {
    const own = (s.steps || []).map((st, j) => ({
      key: shortSha(s.sha) + "#" + j,
      address: st.address,
      kind: st.kind,
      name: st.name || null,
      out: !!st.out,
      col: i,
      unit: unitOf(st.address),
      path: pathOf(st.address),
    }));
    const w = own.length || isMerge(s.sha) ? colW : HAIR_W;
    cols.push({ i, sha: s.sha, short: shortSha(s.sha), subject: s.subject || "", date: s.date, x, w, steps: own, empty: !own.length });
    x += w;
    steps.push(...own);
  });
  const byKey = new Map(steps.map((s) => [s.key, s]));
  const byAddress = new Map();
  for (const s of steps) {
    if (!byAddress.has(s.address)) byAddress.set(s.address, []);
    byAddress.get(s.address).push(s);
  }
  const named = new Map();
  const files = new Map();
  for (const s of steps) {
    const into = s.name ? named : files;
    const k = s.name || s.path;
    if (!into.has(k)) into.set(k, []);
    into.get(k).push(s);
  }
  const rows = [];
  const rowOf = new Map();
  const addRow = (key, kind, label, own, extra) => {
    const cells = new Map();
    for (const s of own) {
      if (!cells.has(s.col)) cells.set(s.col, { row: key, col: s.col, steps: [] });
      cells.get(s.col).steps.push(s);
      rowOf.set(s.key, key);
    }
    const r = Object.assign({ key, kind, label, steps: own, total: own.length, cells }, extra);
    rows.push(r);
    return r;
  };
  for (const [label, own] of named) addRow("name:" + label, "name", label, own, { hue: hueOf(label) });
  for (const p of (status && status.plan && status.plan.names) || []) {
    if (!named.has(p.name)) addRow("plan:" + p.name, "plan", p.name, [], { hue: hueOf(p.name) });
  }
  const unnamed = [].concat(...files.values());
  if (unnamed.length) {
    addRow("unnamed", "group", "Unnamed", open ? [] : unnamed, { open, total: unnamed.length });
    if (open) for (const [path, own] of files) addRow("file:" + path, "file", path, own, {});
  }
  const marks = [];
  for (const c of choices) {
    const col = cols.find((k) => c.merge && (k.sha === c.merge || k.sha.startsWith(c.merge)));
    if (!col) continue;
    const unit = unitOf(c.unit || c.address);
    const last = steps.filter((s) => s.unit === unit).pop();
    const row = (last && rowOf.get(last.key)) || (rows.find((r) => r.kind === "group" || r.key === "file:" + pathOf(unit)) || {}).key;
    if (row) marks.push({ row, col: col.i, address: c.address, unit });
  }
  return { cols, steps, rows, byKey, byAddress, rowOf, labels: new Set(named.keys()), choices: marks, width: x };
}

function colOf(model, ref) {
  if (!ref || ref.length < 4) return null;
  const c = model.cols.find((k) => k.sha.startsWith(ref));
  return c ? c.i : null;
}

function colAt(model, gx) {
  for (const c of model.cols) if (gx >= c.x && gx < c.x + c.w) return c.i;
  return null;
}

// The steps an address names, read off the log the way the core reads it.
function stepsOf(model, address) {
  const a = String(address || "").trim();
  const range = a.match(/^([^.\s]*)\.\.([^.\s]*)$/);
  if (range) {
    const lo = range[1] ? colOf(model, range[1]) : -1;
    const hi = range[2] ? colOf(model, range[2]) : model.cols.length - 1;
    if (lo == null || hi == null) return [];
    return model.steps.filter((s) => s.col > lo && s.col <= hi);
  }
  const at = a.lastIndexOf("@");
  let base = a;
  let pool = model.steps;
  if (at > 0) {
    const c = colOf(model, a.slice(at + 1));
    if (c == null) return [];
    base = a.slice(0, at);
    pool = pool.filter((s) => s.col === c);
  } else if (!model.labels.has(a) && /^[0-9a-f]{4,40}$/.test(a)) {
    const c = colOf(model, a);
    if (c != null) return pool.filter((s) => s.col === c);
  }
  if (model.labels.has(base)) return pool.filter((s) => s.name === base);
  if (base.includes("::")) return pool.filter((s) => s.unit === base);
  return pool.filter((s) => s.path === base);
}

// The fewest addresses that name exactly these steps: each pick is the name, save, file or file in
// a save that covers the most of what is left without naming anything outside; the rest are steps.
function compress(model, keys) {
  const want = new Set(keys);
  const steps = [...want].map((k) => model.byKey.get(k));
  const cands = [];
  const add = (address, group) => {
    if (group.length && group.every((s) => want.has(s.key))) cands.push({ address, keys: group.map((s) => s.key) });
  };
  for (const label of new Set(steps.map((s) => s.name).filter(Boolean))) add(label, model.steps.filter((s) => s.name === label));
  for (const c of new Set(steps.map((s) => s.col))) add(model.cols[c].short, model.cols[c].steps);
  for (const p of new Set(steps.map((s) => s.path))) add(p, model.steps.filter((s) => s.path === p));
  const inSave = new Map();
  for (const s of steps) inSave.set(s.path + "\n" + s.col, s);
  for (const s of inSave.values()) add(s.path + "@" + model.cols[s.col].short, model.cols[s.col].steps.filter((t) => t.path === s.path));
  const left = new Set(want);
  const out = [];
  for (;;) {
    let best = null, gain = 0;
    for (const c of cands) {
      const g = c.keys.filter((k) => left.has(k)).length;
      if (g > gain) {
        best = c;
        gain = g;
      }
    }
    if (!best) break;
    out.push(best.address);
    for (const k of best.keys) left.delete(k);
  }
  for (const k of left) {
    const a = model.byKey.get(k).address;
    if (!out.includes(a)) out.push(a);
  }
  return out;
}

// What a click names: a cell, a row label, a column, or a range of columns.
function addressFor(model, t) {
  const row = t.row ? model.rows.find((r) => r.key === t.row) : null;
  const done = (addresses, label) => {
    const keys = new Set();
    for (const a of addresses) for (const s of stepsOf(model, a)) keys.add(s.key);
    return { addresses, keys: [...keys], label: label || null };
  };
  if (t.kind === "cell" && row) {
    const cell = row.cells.get(t.col);
    if (!cell) return done([]);
    if (row.kind === "name") return done([row.label + "@" + model.cols[t.col].short]);
    return done(compress(model, cell.steps.map((s) => s.key)));
  }
  if (t.kind === "row" && row) {
    if (row.kind === "name") return done([row.label], row.label);
    if (row.kind === "plan") return done([]);
    const own = row.kind === "group" ? model.steps.filter((s) => !s.name) : row.steps;
    return done(compress(model, own.map((s) => s.key)));
  }
  if (t.kind === "col") {
    const c = model.cols[t.col];
    return done(c && !c.empty ? [c.short] : []);
  }
  if (t.kind === "range") {
    const lo = Math.min(t.from, t.to);
    const hi = Math.max(t.from, t.to);
    if (lo === hi) return addressFor(model, { kind: "col", col: lo });
    if (lo > 0) return done([model.cols[lo - 1].short + ".." + (hi === model.cols.length - 1 ? "" : model.cols[hi].short)]);
    return done(model.cols.slice(lo, hi + 1).filter((c) => !c.empty).map((c) => c.short));
  }
  return done([]);
}

function verbFor(model, keys) {
  const steps = keys.map((k) => model.byKey.get(k)).filter(Boolean);
  return steps.length && steps.every((s) => s.out) ? "restore" : "revert";
}

// The planner's answer, step by step. A restore names the take-out's steps, which the log does not
// list, so those map back by unit onto the out steps (preferring the selection's own).
function planMarks(model, plan, selKeys) {
  const marks = new Map();
  if (!plan) return marks;
  const sel = new Set(selKeys || []);
  const find = (address) => {
    const hit = model.byAddress.get(address);
    if (hit) return hit;
    const unit = unitOf(address);
    const out = model.steps.filter((s) => s.out && s.unit === unit);
    const mine = out.filter((s) => sel.has(s.key));
    return mine.length ? mine : out;
  };
  const get = (s) => {
    if (!marks.has(s.key)) marks.set(s.key, { undone: false, held: [], holder: false, ran: null });
    return marks.get(s.key);
  };
  for (const e of plan.undone || []) {
    for (const s of find(e.address)) {
      const m = get(s);
      m.undone = true;
      m.ran = e.ran;
    }
  }
  for (const e of plan.held || []) {
    const holders = (e.holders || []).map((h) => h.address);
    for (const s of find(e.address)) {
      const m = get(s);
      m.held.push({ reason: e.reason, holders });
      m.ran = e.ran;
    }
    for (const h of holders) for (const s of find(h)) get(s).holder = true;
  }
  return marks;
}

// How a cell reads under a plan: the share still in, the share going, the share already out.
function cellState(steps, marks, sel) {
  let a = 0, u = 0, o = 0;
  const st = { held: false, holder: false, norun: false, sel: false };
  for (const s of steps) {
    const m = marks && marks.get(s.key);
    if (m && m.undone) u++;
    else if (s.out) o++;
    else a++;
    if (m && m.held.length) st.held = true;
    if (m && m.holder) st.holder = true;
    if (m && m.ran === false) st.norun = true;
    if (sel && sel.has(s.key)) st.sel = true;
  }
  const n = steps.length || 1;
  return Object.assign(st, { tone: toneLevel(steps.length), a: (a / n) * 100, b: ((a + u) / n) * 100, gone: u > 0, hollow: o > 0 });
}

function outChanges(before, after) {
  if (!before) return [];
  const id = (s, i) => s.address + "|" + s.kind + "|" + i;
  const seen = new Map();
  const old = new Map();
  for (const s of before.steps) {
    const n = seen.get(s.address + s.kind) || 0;
    seen.set(s.address + s.kind, n + 1);
    old.set(id(s, n), s.out);
  }
  seen.clear();
  const changed = [];
  for (const s of after.steps) {
    const n = seen.get(s.address + s.kind) || 0;
    seen.set(s.address + s.kind, n + 1);
    const was = old.get(id(s, n));
    if (was != null && was !== s.out) changed.push(s.key);
  }
  return changed;
}

// The lower level: the selection's definitions, grouped by file, each with its steps by save.
function lowerRows(model, keys) {
  const steps = [...keys].map((k) => model.byKey.get(k)).filter(Boolean).sort((p, q) => p.col - q.col);
  const files = new Map();
  for (const s of steps) {
    if (!files.has(s.path)) files.set(s.path, new Map());
    const units = files.get(s.path);
    if (!units.has(s.unit)) units.set(s.unit, []);
    units.get(s.unit).push(s);
  }
  const rows = [];
  for (const [path, units] of files) {
    rows.push({ kind: "file", key: "f:" + path, path, steps: units.get(path) || [] });
    for (const [unit, own] of units) if (unit !== path) rows.push({ kind: "def", key: "d:" + unit, path, def: defOf(unit), steps: own });
  }
  return rows;
}

const REASONS = {
  uses: ["still used", "used by"],
  changed_later: ["changed later", "changed later by"],
  same_lines: ["its lines changed later", "same lines as"],
  check: ["the check needs it", "the check needs it"],
};

function shortAddress(address) {
  const def = defOf(address);
  const at = String(address).lastIndexOf("@");
  const sha = at > 0 ? String(address).slice(at) : "";
  return (def || pathOf(address).split("/").pop()) + sha;
}

function answerOf(step, mark, verb) {
  if (mark && mark.undone) return { text: verb === "restore" ? "comes back" : "goes", tone: "gone" };
  if (mark && mark.held.length) {
    const h = mark.held.find((x) => x.holders.length) || mark.held[0];
    const words = REASONS[h.reason] || [h.reason, h.reason];
    const text = h.holders.length && h.reason !== "check" ? words[1] + " " + h.holders.map(shortAddress).join(", ") : words[0];
    return { text: "held, " + text, tone: "held" };
  }
  if (step.out) return { text: "out", tone: "out" };
  return { text: "", tone: "" };
}

// A directory that does not fit keeps its last segments: "…/pages" rather than "bikecou…".
function tailDir(dir, room) {
  if (dir.length <= room) return dir;
  const parts = dir.split("/");
  let out = "";
  for (let i = parts.length - 1; i >= 0; i--) {
    const next = parts[i] + (out ? "/" + out : "");
    if (next.length + 2 > room) break;
    out = next;
  }
  return out ? "\u2026/" + out : dir;
}

function plural(n, one, many) {
  return n + " " + (n === 1 ? one : many);
}

function previewWords(plan) {
  const n = (plan.undone || []).length;
  const held = (plan.held || []).length;
  const set = plan.removal_set && plan.removal_set.steps;
  return {
    go: plan.verb === "restore" ? n + " come back" : n + (n === 1 ? " goes" : " go"),
    held: held ? held + " held" : "",
    whole: set && set > n ? set : 0,
  };
}

function firstLine(text) {
  return String(text || "").split("\n").map((l) => l.trim()).filter(Boolean)[0] || "";
}

function refusalOf(plan) {
  if (!plan) return "No answer";
  if (plan.ok === false) return firstLine(plan.error || plan.message) || "Refused";
  if (plan.applied === false) {
    const check = plan.check && typeof plan.check === "object" ? plan.check : null;
    if (check && check.passed === false) return "Check failed" + (firstLine(check.output) ? ": " + firstLine(check.output) : "");
    return firstLine(plan.message) || "Not applied";
  }
  return null;
}

function resultWords(plan) {
  const n = (plan.undone || []).length;
  const held = (plan.held || []).length;
  const words = [n + (plan.verb === "restore" ? " back" : " out") + (held ? ", " + held + " held" : "")];
  const check = plan.check && typeof plan.check === "object" ? plan.check : null;
  if (check) words.push((check.judge === "parser" ? "parser " : "check ") + (check.passed ? "passed" : "failed"));
  const o = plan.observed || {};
  if (o.reverses) words.push("gone, as the check sees it");
  else if (o.delta) words.push(o.delta.added || o.delta.removed ? "output +" + o.delta.added + " −" + o.delta.removed : "output unchanged");
  return words;
}

function checkWords(observed) {
  const head = observed && observed.head;
  if (!head) return "HEAD not observed";
  if (head.verdict === "pass") return "check passed";
  if (head.verdict === "fail") return "check failed";
  return "check " + head.verdict;
}

function ago(iso, now) {
  const t = Date.parse(iso);
  if (!isFinite(t)) return "";
  const s = Math.max(0, (now - t) / 1000);
  if (s < 60) return "now";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  if (s < 86400 * 7) return Math.floor(s / 86400) + "d";
  const d = new Date(t);
  const md = MONTHS[d.getMonth()] + " " + d.getDate();
  return d.getFullYear() === new Date(now).getFullYear() ? md : md + " " + d.getFullYear();
}

function dateLong(iso, now) {
  const d = new Date(Date.parse(iso));
  if (isNaN(d)) return "";
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const year = d.getFullYear() === new Date(now == null ? Date.now() : now).getFullYear() ? "" : " " + d.getFullYear();
  return MONTHS[d.getMonth()] + " " + d.getDate() + year + ", " + hh + ":" + mm;
}

// A date under the first save of each day, unless it would crowd the one before.
function axisTicks(model) {
  const ticks = [];
  let day = "";
  for (const c of model.cols) {
    const d = new Date(Date.parse(c.date));
    if (isNaN(d)) continue;
    const key = d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate();
    if (key === day) continue;
    day = key;
    const prev = ticks[ticks.length - 1];
    if (prev && c.x - prev.x < 52) continue;
    ticks.push({ col: c.i, x: c.x, text: MONTHS[d.getMonth()] + " " + d.getDate() });
  }
  return ticks;
}

// The playhead sits after a save. It moves to the nearest boundary only once the hand is clearly
// nearer to it, so it settles column by column instead of jittering between two.
function detent(model, gx, cur) {
  const ends = model.cols.map((c) => c.x + c.w);
  let best = 0;
  for (let k = 1; k < ends.length; k++) if (Math.abs(ends[k] - gx) < Math.abs(ends[best] - gx)) best = k;
  if (cur == null || best === cur) return best;
  return Math.abs(gx - ends[best]) + 3 < Math.abs(gx - ends[cur]) ? best : cur;
}

function moveCursor(model, cur, key) {
  const rows = model.rows.filter((r) => r.cells.size);
  if (!rows.length) return null;
  const ri = cur ? rows.findIndex((r) => r.key === cur.row) : -1;
  if (ri < 0) return { row: rows[0].key, col: null };
  const r = rows[ri];
  const cols = (row) => [...row.cells.keys()].sort((p, q) => p - q);
  const cs = cols(r);
  const col = cur.col;
  if (key === "ArrowRight") {
    const next = col == null ? cs[0] : cs.find((c) => c > col);
    return { row: r.key, col: next == null ? col : next };
  }
  if (key === "ArrowLeft") {
    if (col == null) return cur;
    const prev = cs.filter((c) => c < col).pop();
    return { row: r.key, col: prev == null ? null : prev };
  }
  if (key === "ArrowDown" || key === "ArrowUp") {
    const nr = rows[Math.min(rows.length - 1, Math.max(0, ri + (key === "ArrowDown" ? 1 : -1)))];
    if (col == null) return { row: nr.key, col: null };
    const near = cols(nr).reduce((b, c) => (b == null || Math.abs(c - col) < Math.abs(b - col) ? c : b), null);
    return { row: nr.key, col: near };
  }
  if (key === "Home") return { row: r.key, col: cs[0] };
  if (key === "End") return { row: r.key, col: cs[cs.length - 1] };
  return cur;
}

function moveSave(model, col, key) {
  const live = model.cols.filter((c) => !c.empty).map((c) => c.i);
  if (!live.length) return null;
  if (col == null) return live[live.length - 1];
  if (key === "ArrowDown") {
    const p = live.filter((c) => c < col).pop();
    return p == null ? col : p;
  }
  if (key === "ArrowUp") {
    const n = live.find((c) => c > col);
    return n == null ? col : n;
  }
  return col;
}

// Replies carry the id of the request they answer; only the latest of each kind is read.
function requestLedger() {
  let n = 0;
  const latest = {};
  const kindOf = new Map();
  return {
    next(kind) {
      n += 1;
      latest[kind] = n;
      kindOf.set(n, kind);
      return n;
    },
    drop(kind) {
      latest[kind] = 0;
    },
    accept(id) {
      const kind = kindOf.get(id);
      kindOf.delete(id);
      return kind && latest[kind] === id ? kind : null;
    },
  };
}

function trimLines(text) {
  const lines = String(text || "").split("\n");
  while (lines.length && !lines[0].trim()) lines.shift();
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return lines;
}

function lineDiff(before, after) {
  const a = trimLines(before);
  const b = trimLines(after);
  const n = a.length, m = b.length;
  if (n * m > 250000) return a.map((s) => ({ t: "-", s })).concat(b.map((s) => ({ t: "+", s })));
  const L = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ t: " ", s: a[i] });
      i++;
      j++;
    } else if (L[i + 1][j] >= L[i][j + 1]) ops.push({ t: "-", s: a[i++] });
    else ops.push({ t: "+", s: b[j++] });
  }
  while (i < n) ops.push({ t: "-", s: a[i++] });
  while (j < m) ops.push({ t: "+", s: b[j++] });
  return ops;
}

// The changed lines with one line of context, at most `cap` lines; a skipped stretch is a gap.
function hunks(ops, cap) {
  const near = ops.map((o, i) => o.t !== " " || (ops[i - 1] && ops[i - 1].t !== " ") || (ops[i + 1] && ops[i + 1].t !== " "));
  const lines = [];
  let more = 0;
  ops.forEach((o, i) => {
    if (!near[i]) return;
    if (lines.length >= cap) {
      if (o.t !== " ") more++;
      return;
    }
    if (i > 0 && !near[i - 1] && lines.length) lines.push({ t: "gap", s: "" });
    lines.push(o);
  });
  return { lines, more };
}
// ---- end model

(function () {
  if (typeof acquireVsCodeApi !== "function" || typeof document === "undefined") return;
  const vscode = acquireVsCodeApi();
  const kept = vscode.getState() || {};
  const req = requestLedger();
  const S = {
    mode: kept.mode === "ledger" ? "ledger" : "timeline",
    unnamedOpen: !!kept.unnamedOpen,
    phase: "loading",
    error: "",
    log: null,
    status: null,
    model: null,
    sel: null,
    cursor: null,
    anchor: null,
    preview: null,
    marks: null,
    showMarks: new Map(),
    whole: false,
    detail: new Map(),
    queue: [],
    showOut: false,
    open: new Set(),
    result: null,
    busy: null,
    ph: null,
    hover: null,
    hits: null,
    hitAt: 0,
    flash: null,
    arrived: new Set(),
    pendingFocus: null,
    scrolled: false,
  };
  const post = (m) => vscode.postMessage(m);
  const keep = () => vscode.setState({ mode: S.mode, unnamedOpen: S.unnamedOpen });

  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function button(label, cls, onClick) {
    const b = h("button", "btn" + (cls ? " " + cls : ""), label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }
  function chevron(open) {
    const ns = "http://www.w3.org/2000/svg";
    const s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 16 16");
    s.setAttribute("class", "chev" + (open ? " open" : ""));
    s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", "M10.072 8.024L5.715 3.667l.618-.62L11 7.716v.618L6.333 13l-.618-.619 4.357-4.357z");
    p.setAttribute("fill", "currentColor");
    s.append(p);
    return s;
  }
  function pathLabel(path) {
    const span = h("span", "path");
    const cut = path.lastIndexOf("/");
    const base = path.slice(cut + 1);
    span.append(h("span", null, base));
    if (cut > 0) span.append(h("span", "dir", " " + tailDir(path.slice(0, cut), Math.floor((LABEL_W - 40) / 7.2) - base.length - 1)));
    span.title = path;
    return span;
  }
  function unitLabel(unit) {
    const path = pathOf(unit);
    const def = defOf(unit);
    const span = h("span", "path");
    const cut = path.lastIndexOf("/");
    span.append(h("span", def ? "dir" : null, path.slice(cut + 1)));
    if (def) span.append(h("span", "dir", "::"), h("span", null, def));
    span.title = unit;
    return span;
  }

  // ---- skeleton
  const hd = h("header", "hd");
  const tabs = h("div", "tabs");
  tabs.setAttribute("role", "tablist");
  const tab = (label, mode) => {
    const b = h("button", "tab", label);
    b.type = "button";
    b.setAttribute("role", "tab");
    b.addEventListener("click", () => setMode(mode));
    return b;
  };
  const tabT = tab("Timeline", "timeline");
  const tabL = tab("Ledger", "ledger");
  tabs.append(tabT, tabL);
  const find = h("div", "find");
  const findIn = h("input", "find-in");
  findIn.type = "text";
  findIn.placeholder = "Find";
  findIn.spellcheck = false;
  findIn.setAttribute("aria-label", "Find");
  const findKey = h("span", "find-key", "/");
  const hitsEl = h("div", "hits");
  hitsEl.setAttribute("role", "listbox");
  hitsEl.hidden = true;
  find.append(findIn, findKey, hitsEl);
  const stat = h("div", "st");
  hd.append(tabs, find, stat);
  const progress = h("div", "progress");
  const main = h("main", "main");
  const tl = h("section", "tl");
  const canvas = h("div", "canvas");
  tl.append(canvas);
  const ledger = h("section", "ledger");
  const lower = h("section", "lower");
  const note = h("div", "note");
  main.append(tl, ledger, lower, note);
  const bar = h("footer", "bar");
  document.body.append(hd, progress, main, bar);

  let cells = [];
  let rowEls = new Map();
  let refs = {};

  // ---- render
  function render() {
    tabT.setAttribute("aria-selected", String(S.mode === "timeline"));
    tabL.setAttribute("aria-selected", String(S.mode === "ledger"));
    renderStatus();
    const ready = S.phase === "ready";
    tl.hidden = !(S.mode === "timeline" && (ready || S.phase === "loading"));
    ledger.hidden = !(S.mode === "ledger" && (ready || S.phase === "loading"));
    note.hidden = ready || S.phase === "loading";
    find.hidden = !(ready || S.phase === "loading");
    if (S.phase === "loading") renderSkeleton(S.mode === "timeline" ? canvas : ledger);
    else if (!ready) renderNote();
    else if (S.mode === "timeline") renderTimeline();
    else renderLedger();
    renderLower();
    renderBar();
    paint();
  }

  function renderSkeleton(into) {
    into.textContent = "";
    const widths = [96, 120, 72, 108, 84, 64];
    widths.forEach((w, i) => {
      const r = h("div", "row skel");
      const lab = h("div", "lab");
      const b = h("span", "sk");
      b.style.width = w + "px";
      lab.append(b);
      r.append(lab);
      for (let k = 0; k < 3; k++) {
        const c = h("span", "sk cellsk");
        c.style.left = LABEL_W + 3 + ((i * 3 + k * 5) % 14) * COL_W + "px";
        r.append(c);
      }
      into.append(r);
    });
  }

  function renderNote() {
    note.textContent = "";
    note.className = "note";
    if (S.phase === "uninit") {
      note.append(h("span", null, "No ugh history in this repository"), button("Init", "primary", () => post({ type: "init" })));
    } else if (S.phase === "empty") {
      note.append(h("span", null, "No saves yet"));
    } else if (S.phase === "error") {
      note.classList.add("err");
      const t = h("span", "line", S.error);
      t.title = S.error;
      note.append(t);
    }
  }

  function canvasWidth() {
    return LABEL_W + S.model.width + META_W;
  }

  function renderTimeline() {
    const m = S.model;
    const W = canvasWidth();
    const rowsH = m.rows.length * ROW_H;
    const top = tl.scrollTop;
    const left = tl.scrollLeft;
    canvas.textContent = "";
    canvas.style.width = W + "px";
    cells = [];
    rowEls = new Map();

    const back = h("div", "back");
    back.style.height = rowsH + "px";
    for (const c of m.cols) {
      if (!c.empty) continue;
      const l = h("div", "hair");
      l.style.left = LABEL_W + c.x + Math.floor(c.w / 2) + "px";
      l.dataset.col = c.i;
      back.append(l);
    }
    const band = h("div", "band");
    band.hidden = true;
    back.append(band);
    canvas.append(back);

    for (const r of m.rows) {
      const row = h("div", "row k-" + r.kind);
      row.dataset.row = r.key;
      if (r.hue != null) row.style.setProperty("--h", r.hue.toFixed(1));
      const lab = h("div", "lab");
      if (r.kind === "group") {
        lab.append(chevron(r.open), h("span", "t", "Unnamed"));
        lab.setAttribute("role", "button");
        lab.setAttribute("aria-expanded", String(!!r.open));
      } else if (r.kind === "file") {
        const t = pathLabel(r.label);
        t.classList.add("t");
        lab.append(t);
      } else {
        const t = h("span", "t", r.label);
        t.title = r.label;
        lab.append(t);
      }
      row.append(lab);
      row.append(rowMeta(r));
      if (r.kind === "plan") {
        const t = h("div", "track");
        t.style.left = LABEL_W + 2 + "px";
        t.style.width = Math.max(0, m.width - 4) + "px";
        row.append(t);
      }
      for (const cell of r.cells.values()) {
        const c = m.cols[cell.col];
        const e = h("div", "cell");
        e.dataset.col = cell.col;
        e.style.left = LABEL_W + c.x + 3 + "px";
        e.style.width = c.w - 6 + "px";
        if (S.arrived.has(c.sha)) e.classList.add("arrive");
        row.append(addCell({ el: e, steps: cell.steps, col: cell.col }));
      }
      for (const ch of m.choices) {
        if (ch.row !== r.key) continue;
        const c = m.cols[ch.col];
        const e = h("div", "choice");
        e.style.left = LABEL_W + c.x + 3 + "px";
        e.style.width = c.w - 6 + "px";
        e.title = "Two versions of " + ch.unit;
        e.dataset.choice = ch.address;
        row.append(e);
      }
      canvas.append(row);
      rowEls.set(r.key, row);
    }

    const over = h("div", "over");
    over.style.height = rowsH + "px";
    const gv = h("div", "gv");
    const gh = h("div", "gh");
    const phl = h("div", "phl");
    phl.style.height = rowsH + "px";
    over.append(gv, gh, phl);
    canvas.append(over);

    const axis = h("div", "axis");
    const alab = h("div", "alab");
    const ticks = h("div", "ticks");
    for (const t of axisTicks(m)) {
      const s = h("span", "tick", t.text);
      s.style.left = LABEL_W + t.x + "px";
      ticks.append(s);
    }
    const amark = h("div", "amark");
    const rset = h("div", "rset");
    const read = h("div", "read");
    const ph = h("div", "ph");
    ph.append(h("span", "now", "now"));
    ph.tabIndex = 0;
    ph.setAttribute("role", "slider");
    ph.setAttribute("aria-label", "Playhead");
    axis.append(alab, ticks, rset, amark, read, ph);
    canvas.append(axis);

    refs = { back, band, gv, gh, phl, ticks, amark, rset, read, ph, axis };
    bindPlayhead(ph);
    placePlayhead();
    drawHover();
    tl.scrollTop = top;
    tl.scrollLeft = S.scrolled ? left : tl.scrollWidth;
    S.scrolled = true;
  }

  function rowMeta(r) {
    const meta = h("span", "meta");
    if (r.kind === "plan") {
      meta.append(h("span", null, "declared"));
      return meta;
    }
    const steps = r.kind === "group" ? S.model.steps.filter((s) => !s.name) : r.steps;
    const out = steps.filter((s) => s.out).length;
    let words = plural(steps.length, "step", "steps") + (out ? ", " + out + " out" : "");
    if (r.kind === "group") words += " in " + plural(new Set(steps.map((s) => s.path)).size, "file", "files");
    meta.append(h("span", null, words));
    const last = S.model.cols[Math.max(...steps.map((s) => s.col))];
    const d = h("span", "ago", ago(last.date, Date.now()));
    d.title = dateLong(last.date);
    meta.append(d);
    return meta;
  }

  function renderLedger() {
    const m = S.model;
    const top = ledger.scrollTop;
    ledger.textContent = "";
    cells = [];
    rowEls = new Map();
    const lanes = m.rows.filter((r) => r.kind === "name").map((r) => ({
      label: r.label,
      hue: r.hue,
      first: Math.min(...r.steps.map((s) => s.col)),
      last: Math.max(...r.steps.map((s) => s.col)),
    }));
    const gutW = 10 + lanes.length * 7;
    const gutter = (col, touched) => {
      const g = h("div", "gut");
      g.style.width = gutW + "px";
      lanes.forEach((l, i) => {
        if (col < l.first || col > l.last) return;
        const ln = h("span", "ln" + (touched && touched.has(l.label) ? " on" : ""));
        ln.style.left = 8 + i * 7 + "px";
        ln.style.setProperty("--h", l.hue.toFixed(1));
        g.append(ln);
      });
      return g;
    };
    const selCols = new Set(S.sel ? [...S.sel.keys].map((k) => m.byKey.get(k).col) : []);
    const now = Date.now();
    for (const c of m.cols.slice().reverse()) {
      const names = new Set(c.steps.filter((s) => s.name).map((s) => s.name));
      const quiet = c.empty && !m.choices.some((ch) => ch.col === c.i);
      const row = h("div", "lrow" + (quiet ? " empty" : ""));
      row.dataset.col = c.i;
      row.append(gutter(c.i, names));
      if (quiet) {
        row.title = c.short + "  " + c.subject;
        ledger.append(row);
        continue;
      }
      const subj = h("span", "subj", c.subject);
      subj.title = c.subject;
      row.append(subj);
      for (const l of lanes) {
        if (!names.has(l.label)) continue;
        const nm = h("span", "nm", l.label);
        nm.style.setProperty("--h", l.hue.toFixed(1));
        row.append(nm);
      }
      for (const ch of m.choices) {
        if (ch.col !== c.i) continue;
        const e = h("span", "lch", shortAddress(ch.unit));
        e.title = "Two versions of " + ch.unit;
        e.dataset.choice = ch.address;
        row.append(e);
      }
      const meta = h("span", "lmeta");
      if (c.steps.length) {
        const out = c.steps.filter((s) => s.out).length;
        meta.append(h("span", null, plural(c.steps.length, "step", "steps") + (out ? ", " + out + " out" : "")));
      }
      meta.append(h("span", "sha", c.short));
      const d = h("span", "ago", ago(c.date, now));
      d.title = dateLong(c.date);
      meta.append(d);
      row.append(meta);
      if (S.arrived.has(c.sha)) row.classList.add("arrive");
      ledger.append(row);
      rowEls.set("col:" + c.i, row);
      if (!selCols.has(c.i)) continue;
      row.classList.add("open");
      const steps = c.steps.slice().sort((p, q) => (p.path === q.path ? 0 : p.path < q.path ? -1 : 1));
      for (const s of steps) {
        const sub = h("div", "sub");
        sub.dataset.step = s.key;
        sub.append(gutter(c.i, null));
        if (s.name) sub.style.setProperty("--h", hueOf(s.name).toFixed(1));
        else sub.classList.add("un");
        sub.append(addCell({ el: h("div", "cell"), steps: [s], col: c.i, sub: true, flat: true }));
        const lab = unitLabel(s.unit);
        lab.classList.add("ulab");
        sub.append(lab);
        if (s.name) sub.append(h("span", "nm", s.name));
        sub.append(stepMeta(s));
        sub.classList.toggle("other", !S.sel.keys.has(s.key));
        ledger.append(sub);
        if (S.open.has(s.key)) {
          const blk = stepBlock(s, 0);
          blk.style.paddingLeft = gutW + 32 + "px";
          ledger.append(blk);
        }
      }
    }
    ledger.scrollTop = top;
  }

  function currentMarks() {
    return S.marks || S.showMarks;
  }

  function stepMeta(s) {
    const meta = h("span", "smeta");
    const verb = S.preview ? S.preview.verb : "revert";
    const ans = answerOf(s, currentMarks().get(s.key), verb);
    const m = currentMarks().get(s.key);
    meta.append(h("span", "kind", s.kind));
    if (m && m.ran === false) meta.append(h("span", "norun", "not run by the check"));
    const a = h("span", "ans " + ans.tone, ans.text);
    a.title = ans.text;
    meta.append(a);
    return meta;
  }

  function renderLower() {
    const top = lower.scrollTop;
    lower.textContent = "";
    const on = S.phase === "ready" && !!S.sel && S.mode === "timeline";
    lower.hidden = !on;
    main.classList.toggle("split", on);
    cells = cells.filter((c) => !c.lower);
    if (!on) return;
    const m = S.model;
    const W = canvasWidth();
    const box = h("div", "canvas");
    box.style.width = W + "px";
    const vw = lower.clientWidth || tl.clientWidth;
    for (const r of lowerRows(m, S.sel.keys)) {
      const row = h("div", "row lrw k-" + r.kind);
      const lab = h("div", "lab");
      if (r.kind === "file") lab.append(pathLabel(r.path));
      else lab.append(h("span", "def", r.def));
      lab.title = r.kind === "file" ? r.path : r.path + "::" + r.def;
      lab.dataset.unit = r.key;
      row.append(lab);
      const byCol = new Map();
      for (const s of r.steps) {
        if (!byCol.has(s.col)) byCol.set(s.col, []);
        byCol.get(s.col).push(s);
      }
      for (const [col, own] of byCol) {
        const c = m.cols[col];
        const e = h("div", "cell");
        e.style.left = LABEL_W + c.x + 3 + "px";
        e.style.width = c.w - 6 + "px";
        e.dataset.step = own[0].key;
        const hue = own[0].name;
        if (hue) {
          e.classList.add("own");
          e.style.setProperty("--h", hueOf(hue).toFixed(1));
        } else e.classList.add("un");
        row.append(addCell({ el: e, steps: own, col, lower: true, flat: true }));
      }
      for (const ch of m.choices) {
        if (r.kind !== "def" || ch.unit !== r.path + "::" + r.def) continue;
        const c = m.cols[ch.col];
        const e = h("div", "choice");
        e.style.left = LABEL_W + c.x + 3 + "px";
        e.style.width = c.w - 6 + "px";
        e.title = "Two versions of " + ch.unit;
        row.append(e);
      }
      if (r.steps.length) {
        const last = r.steps[r.steps.length - 1];
        const meta = stepMeta(last);
        meta.classList.add("meta");
        row.append(meta);
        row.dataset.step = last.key;
      }
      box.append(row);
      for (const s of r.steps) if (S.open.has(s.key)) box.append(stepBlock(s, vw));
    }
    lower.append(box);
    lower.scrollTop = top;
    lower.scrollLeft = tl.scrollLeft;
  }

  function stepBlock(s, vw) {
    const b = h("div", "step");
    if (vw) b.style.width = vw + "px";
    const head = h("div", "shead");
    const addr = h("span", "addr", s.address);
    addr.title = s.address;
    head.append(addr, stepMeta(s));
    const acts = h("span", "acts");
    acts.append(button("Diff", "", () => post({ type: "openStep", address: s.address })));
    const d = S.detail.get(s.key);
    if (d && d.lines) acts.append(button("File", "", () => post({ type: "openFile", path: s.path, line: d.lines[0] })));
    head.append(acts);
    b.append(head);
    const body = h("div", "diff");
    if (!d) {
      for (const w of [220, 160]) {
        const sk = h("div", "sk dsk");
        sk.style.width = w + "px";
        body.append(sk);
      }
    } else {
      const { lines, more } = hunks(lineDiff(d.before, d.after), 30);
      for (const l of lines) {
        const line = h("div", "dl " + (l.t === "+" ? "add" : l.t === "-" ? "del" : l.t === "gap" ? "gap" : "ctx"));
        if (l.t !== "gap") line.append(h("span", "sign", l.t === " " ? "" : l.t === "-" ? "−" : "+"), h("span", null, l.s || " "));
        body.append(line);
      }
      if (more) body.append(h("div", "dl more", "+" + more + " more lines"));
    }
    b.append(body);
    return b;
  }

  function renderStatus() {
    stat.textContent = "";
    if (S.busy) stat.append(h("span", "busy", busyWords(S.busy)));
    progress.classList.toggle("on", !!S.busy);
    const st = S.status;
    if (!st || !S.model || !(S.phase === "ready" || S.phase === "empty")) return;
    const unsaved = ((st.drift && st.drift.paths) || []).length;
    if (unsaved) {
      const u = h("span", null, plural(unsaved, "unsaved file", "unsaved files"));
      u.title = st.drift.paths.join("\n");
      stat.append(u);
    }
    const n = S.model.choices.length;
    if (n) stat.append(button(plural(n, "choice", "choices"), "stc", () => selectChoice(S.model.choices[0])));
    stat.append(h("span", null, checkWords(st.observed)));
    if (st.sync_status && st.sync_status.complete === false) stat.append(h("span", null, "sync incomplete"));
  }

  function busyWords(verb) {
    const words = {
      save: "Saving", revert: "Taking out", restore: "Bringing back", name: "Naming", rename: "Renaming",
      resolve: "Resolving", sync: "Syncing", push: "Pushing", undo: "Undoing", init: "Initializing",
    };
    return (words[verb] || verb) + "…";
  }

  function renderBar() {
    bar.textContent = "";
    bar.className = "bar";
    const show = S.phase === "ready" && (S.sel || S.result);
    bar.hidden = !show;
    if (!show) return;
    const acts = h("span", "acts");
    if (S.result && !S.sel) {
      if (S.result.error) bar.append(errLine(S.result.error));
      else S.result.words.forEach((w, i) => bar.append(h("span", i ? "w" : "cnt", w)));
      acts.append(button("Close", "", closeResult));
      bar.append(acts);
      return;
    }
    const sel = S.sel;
    const addr = h("button", "baddr", sel.addresses.join("  "));
    addr.type = "button";
    addr.title = sel.addresses.join("\n");
    addr.addEventListener("click", () => {
      post({ type: "copy", text: sel.addresses.join(" ") });
      addr.classList.remove("copied");
      void addr.offsetWidth;
      addr.classList.add("copied");
    });
    bar.append(addr);
    const busy = !!S.busy || !!S.applying;
    if (sel.kind === "choice") {
      bar.append(h("span", "held", "two versions"));
      const r = button("Resolve", "primary", () => post({ type: "resolve", address: sel.choice }));
      r.disabled = busy;
      acts.append(r, button("Cancel", "", clearSelection));
      bar.append(acts);
      return;
    }
    const p = S.preview;
    if (p && p.error) bar.append(errLine(p.error));
    else if (p && p.plan) {
      const w = previewWords(p.plan);
      bar.append(h("span", "cnt", w.go));
      if (w.held) bar.append(h("span", "held", w.held));
    } else if (p && p.pending) bar.append(h("span", "sk bsk"));
    const name = button("Name", "", () => post({ type: "name", addresses: sel.addresses }));
    name.disabled = busy;
    acts.append(name);
    if (sel.label) {
      const rn = button("Rename", "", () => post({ type: "rename", label: sel.label }));
      rn.disabled = busy;
      acts.append(rn);
    }
    if (!p) {
      acts.append(button("Preview", "primary", previewNow));
    } else {
      const verb = p.verb === "restore" ? "Bring back" : "Take out";
      const go = button(verb, "primary", apply);
      go.disabled = busy || !p.plan;
      acts.append(go);
      const whole = p.plan ? previewWords(p.plan).whole : 0;
      if (p.verb === "revert" && (whole || S.whole)) {
        const wb = button("Whole", "sec", toggleWhole);
        wb.setAttribute("aria-pressed", String(S.whole));
        if (whole) wb.append(h("span", "num", String(whole)));
        wb.title = "Take out everything that stands on it too";
        wb.disabled = busy;
        acts.append(wb);
      }
    }
    acts.append(button("Cancel", "", clearSelection));
    bar.append(acts);
  }

  function errLine(text) {
    const e = h("span", "err", text);
    e.title = text;
    return e;
  }

  // ---- paint: classes only, so a preview arriving never moves anything
  function paintCtx() {
    const last = S.model.cols.length - 1;
    return {
      marks: S.marks,
      sel: S.sel ? S.sel.keys : null,
      ring: !!S.sel && !["row", "col", "range", "choice"].includes(S.sel.kind),
      ph: S.ph == null ? last : S.ph,
      fl: S.flash && performance.now() - S.flash.t0 < 1400 ? S.flash : null,
    };
  }

  function paintCell(c, x) {
    const st = cellState(c.steps, x.marks, x.sel);
    const e = c.el;
    e.classList.remove("t1", "t2", "t3", "t4");
    e.classList.add("t" + (c.flat ? 3 : st.tone));
    e.style.setProperty("--a", st.a + "%");
    e.style.setProperty("--b", st.b + "%");
    e.classList.toggle("hollow", st.hollow);
    e.classList.toggle("gone", st.gone);
    e.classList.toggle("held", st.held);
    e.classList.toggle("holder", st.holder && !st.held);
    e.classList.toggle("norun", st.norun && !st.holder);
    e.classList.toggle("sel", x.ring && st.sel && !c.lower && !c.sub);
    e.classList.toggle("inv", st.sel || st.gone || st.held || st.holder);
    e.classList.toggle("ahead", S.mode === "timeline" && c.col > x.ph);
    const flash = !!x.fl && c.steps.some((s) => x.fl.keys.has(s.key));
    e.classList.toggle("flash", flash);
    if (flash) e.style.setProperty("--fd", -(performance.now() - x.fl.t0) + "ms");
  }

  // A cell is painted before it enters the page, so a rebuild never animates.
  function addCell(c) {
    paintCell(c, paintCtx());
    cells.push(c);
    return c.el;
  }

  // ---- paint: classes only, so a preview arriving never moves anything
  function paint() {
    if (S.phase !== "ready") return;
    const x = paintCtx();
    canvas.classList.toggle("previewing", !!x.marks);
    ledger.classList.toggle("previewing", !!x.marks);
    const ph = x.ph;
    const metaW = Math.max(META_W, (tl.clientWidth || lower.clientWidth) - LABEL_W - S.model.width - 24);
    document.body.style.setProperty("--meta-w", metaW + "px");
    for (const c of cells) paintCell(c, x);
    if (S.mode === "timeline" && refs.back) {
      for (const [key, row] of rowEls) row.classList.toggle("sel", !!S.sel && S.sel.kind === "row" && S.sel.row === key);
      for (const l of refs.back.querySelectorAll(".hair")) l.classList.toggle("ahead", +l.dataset.col > ph);
      for (const e of canvas.querySelectorAll(".choice")) e.classList.toggle("on", !!S.sel && S.sel.choice === e.dataset.choice);
      refs.rset.textContent = "";
      const set = S.preview && S.preview.whole && S.preview.plan && S.preview.plan.removal_set;
      for (const r of (set && set.saves) || []) {
        const col = colOf(S.model, r.sha);
        if (col == null) continue;
        const c = S.model.cols[col];
        const e = h("span", "rm");
        e.style.left = LABEL_W + c.x + 2 + "px";
        e.style.width = c.w - 4 + "px";
        refs.rset.append(e);
      }
      const band = refs.band;
      const span = S.sel && (S.sel.kind === "col" ? [S.sel.col, S.sel.col] : S.sel.kind === "range" ? [S.sel.from, S.sel.to] : null);
      band.hidden = !span;
      if (span) {
        const a = S.model.cols[Math.min(...span)];
        const b = S.model.cols[Math.max(...span)];
        band.style.left = LABEL_W + a.x + "px";
        band.style.width = b.x + b.w - a.x + "px";
      }
    }
    if (S.mode === "ledger") {
      const selCols = new Set(S.sel ? [...S.sel.keys].map((k) => S.model.byKey.get(k).col) : []);
      for (const [key, row] of rowEls) row.classList.toggle("sel", selCols.has(+key.slice(4)) && S.sel.kind === "col");
    }
  }

  // ---- hover: two guides and the axis reading, nothing floats over the cells
  function drawHover() {
    if (S.mode !== "timeline" || !S.model || !refs.gv) return;
    const m = S.model;
    const { gv, gh, amark, read, ticks } = refs;
    for (const row of rowEls.values()) row.classList.remove("hov");
    const last = m.cols.length - 1;
    const hv = S.hover;
    const col = hv && hv.col != null ? hv.col : S.ph != null && S.ph !== last ? S.ph : null;
    if (col == null) {
      gv.hidden = gh.hidden = amark.hidden = read.hidden = true;
      ticks.classList.remove("away");
      return;
    }
    const c = m.cols[col];
    const rowsH = m.rows.length * ROW_H;
    const axisTop = Math.min(rowsH, tl.scrollTop + tl.clientHeight - 24);
    const ri = hv && hv.row ? m.rows.findIndex((r) => r.key === hv.row) : -1;
    const onCell = hv && hv.col === col && ri >= 0 && m.rows[ri].cells.has(col);
    if (hv && hv.col === col) {
      const y0 = onCell ? ri * ROW_H + 17 : 0;
      gv.hidden = false;
      gv.style.transform = "translate(" + (LABEL_W + c.x + Math.floor(c.w / 2)) + "px," + y0 + "px)";
      gv.style.height = Math.max(0, axisTop - y0) + "px";
    } else gv.hidden = true;
    if (onCell) {
      const x0 = tl.scrollLeft + LABEL_W;
      const x1 = LABEL_W + c.x + 3;
      gh.hidden = x1 <= x0;
      gh.style.transform = "translate(" + x0 + "px," + (ri * ROW_H + 11) + "px)";
      gh.style.width = Math.max(0, x1 - x0 - 2) + "px";
      const row = rowEls.get(m.rows[ri].key);
      if (row) row.classList.add("hov");
    } else gh.hidden = true;
    amark.hidden = false;
    amark.style.left = LABEL_W + c.x + 2 + "px";
    amark.style.width = c.w - 4 + "px";
    read.textContent = "";
    read.append(h("span", "sha", c.short), h("span", "subj", c.subject), h("span", "date", dateLong(c.date)));
    read.hidden = false;
    read.style.maxWidth = Math.max(160, tl.clientWidth - LABEL_W - 24) + "px";
    const rw = read.offsetWidth;
    const atHead = !(hv && hv.col === col);
    let left = LABEL_W + c.x + (atHead ? c.w + 10 : 0);
    const right = tl.scrollLeft + tl.clientWidth - 12;
    if (left + rw > right) left = Math.max(tl.scrollLeft + LABEL_W, LABEL_W + c.x + (atHead ? c.w - 10 : c.w) - rw);
    read.style.left = left + "px";
    ticks.classList.add("away");
  }

  function hit(ev) {
    const m = S.model;
    const r = canvas.getBoundingClientRect();
    const x = ev.clientX - r.left;
    const y = ev.clientY - r.top;
    const axis = !!ev.target.closest(".axis");
    const label = ev.clientX - tl.getBoundingClientRect().left < LABEL_W && !axis;
    const ri = Math.floor(y / ROW_H);
    const row = !axis && ri >= 0 && ri < m.rows.length ? m.rows[ri] : null;
    const col = label ? null : colAt(m, x - LABEL_W);
    return { row, col, axis, label };
  }

  canvas.addEventListener("mousemove", (ev) => {
    if (S.phase !== "ready" || S.mode !== "timeline") return;
    const t = hit(ev);
    S.hover = { row: t.row ? t.row.key : null, col: t.col };
    drawHover();
  });
  canvas.addEventListener("mouseleave", () => {
    S.hover = null;
    drawHover();
  });
  canvas.addEventListener("click", (ev) => {
    if (S.phase !== "ready" || ev.target.closest(".ph")) return;
    const ch = ev.target.closest(".choice");
    if (ch) {
      selectChoice(S.model.choices.find((c) => c.address === ch.dataset.choice));
      return;
    }
    const t = hit(ev);
    if (t.label && t.row) {
      if (t.row.kind === "group") toggleUnnamed();
      else select({ kind: "row", row: t.row.key });
      return;
    }
    if (t.col == null) return;
    if (t.row && t.row.cells.has(t.col)) select({ kind: "cell", row: t.row.key, col: t.col });
    else if (ev.shiftKey && S.anchor != null) select({ kind: "range", from: Math.min(S.anchor, t.col), to: Math.max(S.anchor, t.col) }, { anchor: false });
    else select({ kind: "col", col: t.col });
  });

  lower.addEventListener("click", (ev) => {
    const e = ev.target.closest("[data-step]");
    if (!e || ev.target.closest(".btn")) return;
    toggleStep(e.dataset.step);
  });
  ledger.addEventListener("click", (ev) => {
    if (S.phase !== "ready" || ev.target.closest(".btn")) return;
    const ch = ev.target.closest("[data-choice]");
    if (ch) {
      selectChoice(S.model.choices.find((c) => c.address === ch.dataset.choice));
      return;
    }
    const sub = ev.target.closest(".sub");
    if (sub) {
      toggleStep(sub.dataset.step);
      return;
    }
    const row = ev.target.closest(".lrow");
    if (row && !row.classList.contains("empty") && S.model.cols[+row.dataset.col].steps.length) select({ kind: "col", col: +row.dataset.col });
  });

  let syncing = false;
  tl.addEventListener("scroll", () => {
    if (!syncing) {
      syncing = true;
      lower.scrollLeft = tl.scrollLeft;
      syncing = false;
    }
    drawHover();
  });
  lower.addEventListener("scroll", () => {
    if (syncing) return;
    syncing = true;
    tl.scrollLeft = lower.scrollLeft;
    syncing = false;
  });
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (S.phase !== "ready") return;
      rebuild();
      render();
    }, 80);
  });

  // ---- playhead
  let renderTimer = 0;
  function placePlayhead() {
    if (!refs.ph || !S.model.cols.length) return;
    const m = S.model;
    const last = m.cols.length - 1;
    const k = S.ph == null ? last : S.ph;
    const c = m.cols[k];
    const x = LABEL_W + c.x + c.w;
    refs.ph.style.transform = "translateX(" + x + "px)";
    refs.ph.classList.toggle("now", k === last);
    refs.ph.setAttribute("aria-valuemin", "0");
    refs.ph.setAttribute("aria-valuemax", String(last));
    refs.ph.setAttribute("aria-valuenow", String(k));
    refs.ph.setAttribute("aria-valuetext", k === last ? "now" : c.short + " " + c.subject);
    refs.phl.hidden = k === last;
    refs.phl.style.transform = "translateX(" + x + "px)";
  }
  function setPlayhead(k) {
    const last = S.model.cols.length - 1;
    const next = k == null || k >= last ? null : Math.max(0, k);
    if (next === S.ph) return;
    S.ph = next;
    placePlayhead();
    paint();
    drawHover();
    clearTimeout(renderTimer);
    renderTimer = setTimeout(() => post({ type: "render", address: S.ph == null ? null : S.model.cols[S.ph].short + ".." }), 200);
  }
  function bindPlayhead(ph) {
    let drag = false;
    ph.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      drag = true;
      ph.setPointerCapture(ev.pointerId);
      ph.classList.add("drag");
      ph.focus();
    });
    ph.addEventListener("pointermove", (ev) => {
      if (!drag) return;
      const gx = ev.clientX - canvas.getBoundingClientRect().left - LABEL_W;
      const last = S.model.cols.length - 1;
      setPlayhead(detent(S.model, gx, S.ph == null ? last : S.ph));
    });
    const end = () => {
      drag = false;
      ph.classList.remove("drag");
    };
    ph.addEventListener("pointerup", end);
    ph.addEventListener("pointercancel", end);
    ph.addEventListener("keydown", (ev) => {
      const last = S.model.cols.length - 1;
      const k = S.ph == null ? last : S.ph;
      const to = { ArrowLeft: k - 1, ArrowRight: k + 1, Home: 0, End: last, Escape: last }[ev.key];
      if (to == null) return;
      ev.preventDefault();
      ev.stopPropagation();
      setPlayhead(Math.min(last, Math.max(0, to)));
    });
  }

  // ---- selection, preview, apply
  function select(t, opts) {
    const a = addressFor(S.model, t);
    if (!a.addresses.length || !a.keys.length) return;
    setSelection(Object.assign({}, t, { addresses: a.addresses, keys: new Set(a.keys), label: a.label }), opts);
  }

  function setSelection(sel, opts) {
    const o = opts || {};
    S.sel = sel;
    S.result = null;
    S.whole = false;
    S.preview = null;
    S.marks = null;
    S.showMarks = new Map();
    S.open = new Set();
    S.applying = null;
    req.drop("preview");
    if (sel.kind === "col" && o.anchor !== false) S.anchor = sel.col;
    if (sel.kind === "cell") S.anchor = sel.col;
    if (sel.kind === "cell" || sel.kind === "row") S.cursor = { row: sel.row, col: sel.kind === "cell" ? sel.col : null };
    S.queue = sel.addresses.filter((a) => stepsOf(S.model, a).some((s) => !S.detail.has(s.key)));
    S.showOut = false;
    req.drop("show");
    pumpShow();
    if (o.preview !== false) previewNow();
    if (S.mode === "ledger") renderLedger();
    renderLower();
    renderBar();
    paint();
    if (o.reveal) reveal();
  }

  function selectChoice(ch) {
    if (!ch) return;
    const keys = stepsOf(S.model, ch.unit).map((s) => s.key);
    setSelection({ kind: "choice", choice: ch.address, addresses: [ch.unit], keys: new Set(keys), label: null, row: ch.row, col: ch.col }, { preview: false, reveal: true });
  }

  function focusAddress(address) {
    const keys = stepsOf(S.model, address).map((s) => s.key);
    if (!keys.length) {
      S.sel = null;
      S.result = { error: "No steps at " + address };
      renderLower();
      renderBar();
      paint();
      return;
    }
    const label = S.model.labels.has(address) ? address : null;
    const sel = { kind: label ? "row" : "addr", addresses: [address], keys: new Set(keys), label };
    if (label) sel.row = "name:" + label;
    setSelection(sel, { reveal: true });
  }

  function clearSelection() {
    S.sel = null;
    S.preview = null;
    S.marks = null;
    S.whole = false;
    S.open = new Set();
    S.queue = [];
    S.applying = null;
    req.drop("preview");
    req.drop("show");
    if (S.mode === "ledger") renderLedger();
    renderLower();
    renderBar();
    paint();
  }

  function closeResult() {
    S.result = null;
    renderBar();
  }

  function previewNow() {
    if (!S.sel || S.sel.kind === "choice") return;
    const verb = verbFor(S.model, [...S.sel.keys]);
    const id = req.next("preview");
    S.preview = { id, verb, whole: S.whole, pending: true };
    post({ type: "preview", id, verb, addresses: S.sel.addresses, whole: S.whole });
    renderBar();
  }

  function toggleWhole() {
    S.whole = !S.whole;
    previewNow();
  }

  function apply() {
    const p = S.preview;
    if (!p || !p.plan || S.busy || S.applying) return;
    const id = req.next("apply");
    req.drop("preview");
    S.applying = { id };
    post({ type: "apply", id, verb: p.verb, addresses: S.sel.addresses, whole: p.whole });
    renderBar();
  }

  function onPlan(msg) {
    const kind = req.accept(msg.id);
    if (kind === "preview" && S.sel && S.preview) {
      const err = msg.error ? firstLine(msg.error) : msg.plan && msg.plan.ok === false ? refusalOf(msg.plan) : null;
      S.preview = Object.assign({}, S.preview, { pending: false, plan: err ? null : msg.plan, error: err });
      S.marks = err ? null : planMarks(S.model, msg.plan, [...S.sel.keys]);
      if (S.mode === "ledger") renderLedger();
      renderLower();
      renderBar();
      paint();
    } else if (kind === "apply") {
      S.applying = null;
      const err = msg.error ? firstLine(msg.error) : refusalOf(msg.plan);
      if (err && S.sel && S.preview) {
        S.preview = Object.assign({}, S.preview, { error: err });
        renderBar();
        return;
      }
      S.result = err ? { error: err } : { words: resultWords(msg.plan) };
      S.sel = null;
      S.preview = null;
      S.marks = null;
      S.whole = false;
      S.open = new Set();
      if (S.mode === "ledger") renderLedger();
      renderLower();
      renderBar();
      paint();
    }
  }

  function pumpShow() {
    if (S.showOut) return;
    const a = S.queue.shift();
    if (a == null) return;
    S.showOut = true;
    post({ type: "show", id: req.next("show"), address: a });
  }

  function onDetail(msg) {
    if (req.accept(msg.id) !== "show") return;
    S.showOut = false;
    const show = msg.show;
    if (show && show.steps) {
      const seen = new Map();
      for (const st of show.steps) {
        const k = st.address + "|" + st.kind;
        const i = seen.get(k) || 0;
        seen.set(k, i + 1);
        const cands = (S.model.byAddress.get(st.address) || []).filter((s) => s.kind === st.kind);
        const s = cands[i] || cands[0];
        if (s) S.detail.set(s.key, { before: st.before, after: st.after, lines: st.lines });
      }
      if (show.plan && show.plan.ok !== false && S.sel) {
        for (const [k, v] of planMarks(S.model, show.plan, [...S.sel.keys])) S.showMarks.set(k, v);
      }
    }
    pumpShow();
    if (S.mode === "ledger") renderLedger();
    renderLower();
    paint();
  }

  function toggleStep(key) {
    if (!key) return;
    if (S.open.has(key)) S.open.delete(key);
    else {
      S.open.add(key);
      const s = S.model.byKey.get(key);
      if (s && !S.detail.has(key) && !S.queue.includes(s.address)) {
        S.queue.push(s.address);
        pumpShow();
      }
    }
    if (S.mode === "ledger") renderLedger();
    renderLower();
    paint();
  }

  function toggleUnnamed() {
    S.unnamedOpen = !S.unnamedOpen;
    keep();
    rebuild();
    render();
  }

  function setMode(mode) {
    if (mode === S.mode) return;
    S.mode = mode;
    S.hover = null;
    keep();
    render();
    if (S.sel) reveal();
  }

  function rebuild() {
    const avail = (tl.clientWidth || main.clientWidth || window.innerWidth) - LABEL_W - META_W;
    S.model = buildModel(S.log, S.status, { unnamedOpen: S.unnamedOpen, colW: fitColumn(S.log, S.status, avail) });
    if (S.sel) {
      const keys = new Set();
      for (const a of S.sel.addresses) for (const s of stepsOf(S.model, a)) keys.add(s.key);
      if (!keys.size) S.sel = null;
      else S.sel.keys = keys;
    }
    if (S.ph != null && S.ph >= S.model.cols.length - 1) S.ph = null;
  }

  function reveal() {
    if (!S.sel) return;
    const m = S.model;
    const cols = [...S.sel.keys].map((k) => m.byKey.get(k).col);
    if (S.mode === "timeline") {
      const c = m.cols[Math.max(...cols)];
      const x = LABEL_W + c.x;
      if (x < tl.scrollLeft + LABEL_W || x + c.w > tl.scrollLeft + tl.clientWidth) tl.scrollLeft = x - tl.clientWidth / 2;
      const ri = m.rows.findIndex((r) => r.key === (S.sel.row || m.rowOf.get([...S.sel.keys][0])));
      if (ri >= 0) {
        const y = ri * ROW_H;
        if (y < tl.scrollTop || y + ROW_H > tl.scrollTop + tl.clientHeight - 24) tl.scrollTop = y - ROW_H * 2;
      }
    } else {
      const row = rowEls.get("col:" + Math.max(...cols));
      if (row) row.scrollIntoView({ block: "nearest" });
    }
  }

  // ---- keyboard
  function move(key) {
    const m = S.model;
    if (S.mode === "timeline") {
      const cur = moveCursor(m, S.cursor, key);
      if (!cur) return;
      S.cursor = cur;
      if (cur.col == null) select({ kind: "row", row: cur.row }, { preview: false, reveal: true });
      else select({ kind: "cell", row: cur.row, col: cur.col }, { preview: false, reveal: true });
      S.cursor = cur;
    } else if (key === "ArrowUp" || key === "ArrowDown") {
      const cur = S.sel && S.sel.kind === "col" ? S.sel.col : null;
      const col = moveSave(m, cur, key);
      if (col != null) select({ kind: "col", col }, { preview: false, reveal: true });
    }
  }

  document.addEventListener("keydown", (ev) => {
    const t = ev.target;
    if (t === findIn) return;
    if (ev.key === "/" && !ev.metaKey && !ev.ctrlKey) {
      ev.preventDefault();
      findIn.focus();
      findIn.select();
      return;
    }
    if (ev.key === "Escape") {
      if (S.result && !S.sel) closeResult();
      else if (S.sel) clearSelection();
      else if (S.ph != null) setPlayhead(null);
      return;
    }
    if (S.phase !== "ready" || (t.closest && t.closest(".ph"))) return;
    if (t.tagName === "BUTTON" && (ev.key === "Enter" || ev.key === " ")) return;
    if (ev.key === "Enter") {
      if (S.sel && !S.preview) {
        ev.preventDefault();
        previewNow();
      }
      return;
    }
    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) {
      ev.preventDefault();
      move(ev.key);
    }
  });

  // ---- find
  let findTimer = 0;
  findIn.addEventListener("input", () => {
    clearTimeout(findTimer);
    findKey.hidden = !!findIn.value;
    findTimer = setTimeout(() => {
      const q = findIn.value.trim();
      if (!q) {
        S.hits = null;
        req.drop("find");
        renderHits();
        return;
      }
      post({ type: "find", id: req.next("find"), query: q });
    }, 160);
  });
  findIn.addEventListener("focus", renderHits);
  findIn.addEventListener("blur", () => {
    hitsEl.hidden = true;
  });
  findIn.addEventListener("keydown", (ev) => {
    const hits = S.hits || [];
    if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
      ev.preventDefault();
      if (!hits.length) return;
      S.hitAt = (S.hitAt + (ev.key === "ArrowDown" ? 1 : hits.length - 1)) % hits.length;
      renderHits();
    } else if (ev.key === "Enter") {
      ev.preventDefault();
      if (hits[S.hitAt]) pick(hits[S.hitAt]);
    } else if (ev.key === "Escape") {
      ev.preventDefault();
      findIn.value = "";
      findKey.hidden = false;
      S.hits = null;
      req.drop("find");
      findIn.blur();
    }
  });
  hitsEl.addEventListener("mousedown", (ev) => {
    ev.preventDefault();
    const e = ev.target.closest(".hit");
    if (e && S.hits) pick(S.hits[+e.dataset.i]);
  });

  function pick(hit) {
    findIn.blur();
    if (S.phase === "ready") focusAddress(hit.address);
  }

  function renderHits() {
    hitsEl.textContent = "";
    const q = findIn.value.trim();
    hitsEl.hidden = !(document.activeElement === findIn && q && S.hits);
    if (hitsEl.hidden) return;
    if (!S.hits.length) {
      hitsEl.append(h("div", "hit none", "No matches"));
      return;
    }
    S.hits.forEach((hit, i) => {
      const r = h("div", "hit" + (i === S.hitAt ? " on" : ""));
      r.dataset.i = i;
      r.setAttribute("role", "option");
      r.setAttribute("aria-selected", String(i === S.hitAt));
      const a = h("span", "a", hit.address);
      a.title = hit.address;
      r.append(a);
      const text = hit.text && hit.text !== hit.address ? hit.text : "";
      const x = h("span", "x", text);
      x.title = text;
      r.append(x, h("span", "k", hit.matched || ""));
      hitsEl.append(r);
    });
  }

  // ---- host messages
  function onState(msg) {
    if (msg.uninitialized) {
      S.phase = "uninit";
      S.sel = null;
    } else if (msg.error && !msg.log) {
      S.phase = "error";
      S.error = firstLine(msg.error);
    } else {
      const before = S.model;
      const prevShas = before ? new Set(before.cols.map((c) => c.sha)) : null;
      S.log = msg.log;
      S.status = msg.status;
      rebuild();
      const m = S.model;
      S.phase = m.cols.length ? "ready" : "empty";
      const changed = outChanges(before, m);
      if (changed.length) S.flash = { keys: new Set(changed), t0: performance.now() };
      S.arrived = new Set(prevShas ? m.cols.filter((c) => !prevShas.has(c.sha)).map((c) => c.sha) : []);
      if (S.arrived.size) {
        if (tl.scrollLeft + tl.clientWidth >= tl.scrollWidth - 4) S.scrolled = false;
        setTimeout(() => S.arrived.clear(), 400);
      }
      if (S.sel && S.preview && S.arrived.size) previewNow();
    }
    render();
    if (S.flash) setTimeout(paint, 1450);
    if (S.pendingFocus && S.phase === "ready") {
      const a = S.pendingFocus;
      S.pendingFocus = null;
      focusAddress(a);
    }
  }

  window.addEventListener("message", (ev) => {
    const msg = ev.data || {};
    switch (msg.type) {
      case "state":
        onState(msg);
        break;
      case "plan":
        onPlan(msg);
        break;
      case "detail":
        onDetail(msg);
        break;
      case "found":
        if (req.accept(msg.id) === "find") {
          S.hits = msg.hits || [];
          S.hitAt = 0;
          renderHits();
        }
        break;
      case "busy":
        S.busy = msg.verb || null;
        renderStatus();
        renderBar();
        break;
      case "focus":
        if (S.phase === "ready") focusAddress(msg.address);
        else S.pendingFocus = msg.address;
        break;
    }
  });

  render();
  post({ type: "ready" });
})();
