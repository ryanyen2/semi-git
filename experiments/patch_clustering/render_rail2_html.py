"""Render the feature hierarchy + operation DAG as a COMPOSITION-first grouped rail (rail2).

Three things this view answers that the old rail.html did not:

  1. STATE = the composition of the codebase at HEAD. The left side is the full hierarchy tree
     (up to 4 levels: subsystem ▸ group ▸ … ▸ feature lane), rendered as contiguous collapsible
     blocks, ordered integrators-on-top -> foundations-below by dependency rank. Size bars make
     "what it's composed of" quantitative.
  2. HISTORY = a Gantt. The right side is one shared commit axis. Each row draws a lifespan bar
     from its birth commit to its last, with typed-op glyphs (born / extended / reworked / pruned /
     split / merge / died / reverted) as markers. A lane whose bar reaches the tip is HEAD-fresh.
  3. CONSEQUENCE = a stepped ripple. Full transitive up/down-sets on this graph cover ~93% of the
     codebase (it is cyclic + foundation-heavy), so a static closure would light everything. Instead
     clicking a feature tints its DIRECT dependents amber (blast radius) and dependencies blue
     (foundation), dims the rest, and a hop stepper grows the BFS one layer at a time — so you watch
     the blast radius spread and see when a load-bearing foundation saturates the graph. Clicking a
     commit tick shows "what reverting commit N would ripple into". No long crossing arcs.

Reads out/operations.json (falls back to hierarchy.json). Writes out/rail2.html (self-contained).
Leaves rail.html untouched for side-by-side comparison.

    .venv/bin/python experiments/patch_clustering/render_rail2_html.py
"""

from __future__ import annotations

import json
from pathlib import Path

_OUT = Path(__file__).resolve().parent / "out"

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>sgt — feature rail 2 (composition + gantt + ripple)</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#262b36; --ink:#e6e9ef; --dim:#8b93a3; --accent:#7aa2f7;
          --blast:#e8a13a; --found:#5aa0e8; --both:#b083e0; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:12px 16px; border-bottom:1px solid var(--line); position:sticky; top:0;
           background:var(--bg); z-index:20; }
  header h1 { margin:0 0 4px; font-size:15px; font-weight:600; }
  .stats { color:var(--dim); font-size:12px; } .stats b { color:var(--ink); }
  .howto { color:var(--dim); font-size:11.5px; margin-top:6px; max-width:1120px; }
  .howto b { color:var(--ink); }
  .howto .blast { color:var(--blast); } .howto .found { color:var(--found); }
  .oplegend { margin-top:6px; font-size:11.5px; color:var(--dim); display:flex; gap:15px; flex-wrap:wrap; }
  .oplegend b { color:var(--ink); }
  .controls { margin-top:8px; display:flex; gap:14px; align-items:center; font-size:12px; color:var(--dim); flex-wrap:wrap; }
  button { font:inherit; font-size:11px; background:#1e2430; color:var(--ink); border:1px solid var(--line);
           border-radius:4px; padding:3px 9px; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  button.hopb { padding:2px 8px; }
  select { font:inherit; font-size:11px; background:#1e2430; color:var(--ink); border:1px solid var(--line);
           border-radius:4px; padding:3px 6px; }
  #conseq { color:var(--dim); font-size:11.5px; min-height:15px; }
  #conseq b { color:var(--ink); } #conseq .blast { color:var(--blast); } #conseq .found { color:var(--found); }
  #legend { margin-top:8px; display:flex; gap:5px 10px; flex-wrap:wrap; max-height:64px; overflow:auto; }
  .chip { font-size:11px; color:var(--dim); cursor:pointer; white-space:nowrap; user-select:none; }
  .chip.off { opacity:.32; text-decoration:line-through; }
  .chip .sw { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:4px; vertical-align:middle; }
  .chip:hover { color:var(--ink); }
  .wrap { display:flex; align-items:flex-start; }
  .rail-scroll { overflow:auto; max-height:calc(100vh - 250px); flex:1; }
  aside { width:360px; flex:none; border-left:1px solid var(--line); padding:14px 14px 40px;
          height:calc(100vh - 250px); overflow:auto; background:var(--panel); }
  aside h2 { font-size:13px; margin:0 0 2px; word-break:break-word; }
  aside .why { color:var(--dim); font-style:italic; margin:0 0 10px; }
  aside .kv { color:var(--dim); font-size:12px; margin:2px 0; } aside .kv b { color:var(--ink); }
  aside .path { color:var(--dim); font-size:11.5px; margin:0 0 8px; }
  aside .path b { color:var(--ink); }
  aside .sec { margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }
  aside .row { font-size:12px; margin:3px 0; cursor:pointer; } aside .row:hover { color:var(--accent); }
  aside .sw { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; vertical-align:middle; }
  aside .members { font-size:11px; color:var(--dim); white-space:pre-wrap; word-break:break-all; }
  aside .members .fn { color:var(--ink); }
  aside .hist { font-size:13px; line-height:1.9; word-break:break-word; }
  aside .hist span { padding:1px 4px; border-radius:3px; margin-right:1px; }
  .blastbar { height:7px; border-radius:2px; margin:4px 0 2px; background:linear-gradient(90deg,var(--blast),#3a2a12); }
  .legend-hint { font-size:11px; color:var(--dim); margin-bottom:6px; }
  .flag { display:inline-block; font-size:10px; padding:1px 5px; border-radius:3px; margin:0 4px 4px 0; }
  .flag.dense { background:#3a1212; color:#f79b9b; }
  .empty { color:var(--dim); padding:20px; }
  svg text { font:11px ui-monospace,Menlo,monospace; }
  .lane-label, .sub-label { cursor:pointer; }
  .lane-label { fill:var(--ink); }
  .sub-label { fill:var(--ink); font-weight:600; }
  .lane-label:hover, .sub-label:hover { fill:var(--accent); }
  .glyph { cursor:pointer; }
  .tick { fill:#4a5160; }
  .gridline { stroke:#1b2029; stroke-width:1; }
  .lifebar { pointer-events:none; }
  .band { pointer-events:none; }
  /* consequence focus: selected + ripple neighbours stay lit, everything else dims.
     order matters — .lit is last so it wins when a row is both an ancestor (.ctx) and lit */
  .rowg { transition:opacity .12s; }
  svg.focus .rowg { opacity:.2; }
  svg.focus .rowg.ctx { opacity:.5; }
  svg.focus .rowg.lit { opacity:1; }
  .caret { fill:var(--dim); cursor:pointer; user-select:none; }
  .cticks rect { cursor:pointer; }
</style>
</head>
<body>
<header>
  <h1>sgt — feature rail 2 · composition + gantt + ripple</h1>
  <div class="stats" id="stats"></div>
  <div class="howto">
    <b>Left</b> = the codebase at HEAD as a hierarchy (subsystem ▸ … ▸ feature), integrators on top →
    foundations below; bars = entity count. <b>Right</b> = one commit axis; each row's <b>bar</b> is its
    lifespan (birth→last) and glyphs are typed ops. <b>Click a feature</b> to trace consequence:
    <span class="blast">amber = blast radius (what depends on it)</span>,
    <span class="found">blue = foundation (what it depends on)</span> — then step <b>hops</b> to grow the
    ripple. <b>Click a commit tick</b> (top) to see what reverting it would touch.
  </div>
  <div class="oplegend">
    <span><b>◆</b> born</span><span><b>+</b> extended</span><span><b>~</b> reworked</span>
    <span><b>−</b> pruned</span><span><b>⋔</b> split</span><span><b>⋈</b> merge</span>
    <span><b>✝</b> died</span><span><b>↺</b> reverted</span>
  </div>
  <div class="controls">
    <label>order <select id="order">
      <option value="rank">dependency rank</option>
      <option value="recency">recency</option>
      <option value="size">size</option>
    </select></label>
    <label>ripple <button class="hopb" id="hopMinus">−</button>
      <span id="hopN" style="color:var(--ink)">1</span> hop<button class="hopb" id="hopPlus">+</button></label>
    <button id="expandAll">expand all</button>
    <button id="collapseAll">collapse all</button>
    <button id="clearSel">clear selection</button>
    <span id="shown"></span>
  </div>
  <div id="conseq">Click a feature to trace its dependency consequence.</div>
  <div id="legend"></div>
</header>
<div class="wrap">
  <div class="rail-scroll"><svg id="rail"></svg></div>
  <aside id="panel"><div class="empty">Click a feature to inspect its members, history, blast radius and dependencies.</div></aside>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const lanes = D.lanes, supers = D.supers, commits = D.commits, nodes = D.nodes || {}, roots = D.roots || [];
const commitLanes = D.commit_lanes || {};
const nC = commits.length, nLanes = Object.keys(lanes).length;
const SVGNS = 'http://www.w3.org/2000/svg';

// geometry ----------------------------------------------------------------------------------------
const ROWH = 20, TOP = 30;
const PAD = 10, INDENT = 13;
const NAME_W = 286;                         // hierarchy name column (indent lives inside it)
const BAR_X = PAD + NAME_W, BAR_W = 42;     // entity-count size bar
const GX0 = BAR_X + BAR_W + 16;             // commit axis / gantt start
const GLYPH_W = 9;
const dotX   = d => PAD + d*INDENT + 6;
const labelX = d => PAD + d*INDENT + 15;
const timeX  = o => GX0 + o*GLYPH_W + GLYPH_W/2;

const supById = {}; supers.forEach(s => supById[s.id] = s);
const GLYPH = { born:'◆', extended:'+', reworked:'~', pruned:'−', split:'⋔', merge:'⋈', died:'✝', reverted:'↺', touched:'·' };
const el = id => document.getElementById(id);
const esc = s => (s||'').replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
const mk = (tag, attrs) => { const e=document.createElementNS(SVGNS,tag); for(const k in attrs) e.setAttribute(k,attrs[k]); return e; };
const fit = (s, px) => { const max=Math.max(3, Math.floor(px/6.3)); return s.length>max? s.slice(0,max-1)+'…' : s; };

// hue is per subsystem (root); every descendant inherits it ---------------------------------------
const hueOf = {};
supers.slice().sort((a,b)=>b.size-a.size).forEach((s,i)=> hueOf[s.id] = Math.round((i*137.508)%360));
const rootMemo = {};
function rootOf(id){
  if (rootMemo[id]!=null) return rootMemo[id];
  let c=id, g=0; while (nodes[c] && nodes[c].parent!=null && g++<12) c=nodes[c].parent;
  return rootMemo[id]=c;
}
const hcol = (id, leaf) => `hsl(${hueOf[rootOf(id)]} ${leaf?48:56}% ${leaf?62:56}%)`;

// tree helpers ------------------------------------------------------------------------------------
const isLeaf = nid => !(nodes[nid] && nodes[nid].children && nodes[nid].children.length);
const leafCache = {};
function leavesOf(nid){
  if (leafCache[nid]) return leafCache[nid];
  if (isLeaf(nid)) return leafCache[nid]=[nid];
  let a=[]; for (const c of nodes[nid].children) a = a.concat(leavesOf(c));
  return leafCache[nid]=a;
}
function childOrder(nid){
  return (nodes[nid].children||[]).slice().sort((a,b)=> (nodes[b].size||0)-(nodes[a].size||0) || (a<b?-1:1));
}
// lifespan (birth,last) for any node — leaves carry it; internals roll up from their leaves
const spanCache = {};
function spanOf(nid){
  if (spanCache[nid]) return spanCache[nid];
  if (lanes[nid]) return spanCache[nid]=[lanes[nid].birth, lanes[nid].last];
  let b=Infinity, e=-Infinity;
  for (const l of leavesOf(nid)){ const L=lanes[l]; if(!L) continue; b=Math.min(b,L.birth); e=Math.max(e,L.last); }
  return spanCache[nid]=[b===Infinity?0:b, e<0?0:e];
}
function sizeOf(nid){ return (nodes[nid] && nodes[nid].size) || (lanes[nid] && lanes[nid].size) || 0; }
function labelOf(nid){ return (nodes[nid] && nodes[nid].label) || (lanes[nid] && lanes[nid].label) || nid; }

// lane-level dependency adjacency (outgoing stored; invert for incoming) --------------------------
const outAdj = {}, inAdj = {};
for (const l in lanes){ outAdj[l]=[]; inAdj[l]=[]; }
for (const l in lanes)
  for (const d of (lanes[l].depends||[]))
    if (lanes[d.lane]){ outAdj[l].push(d.lane); inAdj[d.lane].push(l); }

// subsystem dependency rank (deepest chain beneath, cycle-guarded) -> integrators float to the top
const sdeps = {}; supers.forEach(s => sdeps[s.id] = (s.depends||[]).map(d=>d.super).filter(x=>supById[x]));
const rankMemo = {}, onStack = {};
function subRank(s){
  if (rankMemo[s]!=null) return rankMemo[s];
  if (onStack[s]) return 0;
  onStack[s]=1; let r=0; for (const d of sdeps[s]) r=Math.max(r, 1+subRank(d)); onStack[s]=0;
  return rankMemo[s]=r;
}
supers.forEach(s => subRank(s.id));

const hidden = new Set();       // subsystems toggled off in the legend
const collapsed = new Set();    // internal nodes whose subtree is folded away
let hop = 1;                    // ripple depth
let sticky = null;              // {kind:'node'|'commit', id}

function orderedRoots(){
  const mode = el('order').value;
  const vis = roots.filter(r => !hidden.has(r));
  const cmp = {
    rank:    (a,b)=> (rankMemo[b]||0)-(rankMemo[a]||0) || sizeOf(b)-sizeOf(a) || (a<b?-1:1),
    recency: (a,b)=> spanOf(b)[1]-spanOf(a)[1] || sizeOf(b)-sizeOf(a) || (a<b?-1:1),
    size:    (a,b)=> sizeOf(b)-sizeOf(a) || (a<b?-1:1),
  }[mode];
  return vis.sort(cmp);
}

// stats --------------------------------------------------------------------------------------------
const covered = commits.filter(c => (commitLanes[c.order]||[]).length).length;
const lc = D.lifecycle || {};
el('stats').innerHTML = `<b>${supers.length}</b> subsystems · <b>${nLanes}</b> feature lanes · `
  + `depth ≤<b>${(D.max_depth||4)}</b> · <b>${(D.op_types&&Object.values(D.op_types).reduce((a,b)=>a+b,0))||'?'}</b> typed ops · `
  + `<b>${lc.deaths||0}</b> deaths · <b>${lc.splits||0}</b> splits · <b>${D.dep_edges||0}</b> deps · `
  + `coverage <b>${covered}/${nC}</b> · ${D.cost||''}`;

function renderLegend(){
  const box = el('legend'); box.innerHTML='';
  for (const s of supers.slice().sort((a,b)=>b.size-a.size)){
    const c = document.createElement('span');
    c.className = 'chip' + (hidden.has(s.id)?' off':'');
    c.innerHTML = `<span class="sw" style="background:${hcol(s.id,false)}"></span>${esc(s.label)} <span style="opacity:.6">${s.children.length}</span>`;
    c.title = `${s.label} — ${s.size} entities, ${s.children.length} lanes (click: toggle · dbl: select · hover: highlight)`;
    c.onclick = ()=>{ hidden.has(s.id)?hidden.delete(s.id):hidden.add(s.id); render(); };
    c.ondblclick = ()=> selectNode(s.id);
    c.onmouseenter = ()=> previewNode(s.id);
    c.onmouseleave = ()=> restoreSticky();
    box.appendChild(c);
  }
}

// row model ---------------------------------------------------------------------------------------
let ROWS = [];            // [{id, depth, leaf, y}]
let rowIndex = {};        // id -> row (only for rows actually drawn)
function buildRows(){
  ROWS = []; rowIndex = {}; let i = 0;
  const push = (nid, depth) => {
    const r = {id:nid, depth, leaf:isLeaf(nid), row:i++};
    ROWS.push(r);
    if (!r.leaf && !collapsed.has(nid)) for (const c of childOrder(nid)) push(c, depth+1);
  };
  for (const r of orderedRoots()) push(r, 0);
  ROWS.forEach(r => { r.y = TOP + r.row*ROWH + ROWH/2; rowIndex[r.id] = r; });
}
// nearest drawn row for an id (its own, or the closest visible ancestor if it is collapsed away)
function visibleRow(id){
  let c=id, g=0; while (c!=null && g++<12){ if (rowIndex[c]) return rowIndex[c]; c=nodes[c]?nodes[c].parent:null; }
  return null;
}

const svg = el('rail');
let chartH = 0, chartW = 0;
function render(){
  buildRows();
  svg.innerHTML=''; svg.className.baseVal='';
  chartW = GX0 + nC*GLYPH_W + 40;
  chartH = TOP + ROWS.length*ROWH + 20;
  svg.setAttribute('width', chartW); svg.setAttribute('height', chartH);
  renderLegend();
  const nRoots = orderedRoots().length;
  el('shown').textContent = `${nRoots} subsystems · ${ROWS.filter(r=>r.leaf).length} leaves shown`
    + (hidden.size?` · ${hidden.size} hidden`:'') + (collapsed.size?` · ${collapsed.size} collapsed`:'');

  // commit-axis gridlines + ticks (every 5th) so the gantt reads as one timeline
  for (let o=0; o<nC; o+=5){
    const x = timeX(o);
    svg.appendChild(mk('line',{class:'gridline', x1:x, y1:TOP-8, x2:x, y2:chartH-16}));
    const t = mk('text',{class:'tick', x:x-3, y:TOP-12}); t.textContent=o; svg.appendChild(t);
  }
  svg.appendChild(mk('g',{id:'bands'}));   // consequence heat bands (drawn under rows)
  for (const r of ROWS) drawRow(r);
  drawCommitTicks();                        // clickable commit columns on top
  restoreSticky();
}

function drawRow(r){
  const nid=r.id, y=r.y, d=r.depth;
  const g = mk('g',{class:'rowg', 'data-id':nid});
  const col = hcol(nid, r.leaf);
  const [b,e] = spanOf(nid);
  const headFresh = (e === nC-1);

  if (!r.leaf){                                       // internal / subsystem header
    const caret = mk('text',{class:'caret', x:dotX(d)-9, y:y+4});
    caret.textContent = collapsed.has(nid)?'▸':'▾';
    caret.onclick = ev=>{ ev.stopPropagation(); toggleCollapse(nid); };
    g.appendChild(caret);
    g.appendChild(mk('rect',{x:dotX(d)-4, y:y-4, width:8, height:8, rx:2, fill:col}));
  } else {                                            // leaf feature lane (or solo subsystem at d0)
    const L=lanes[nid]; const rad = 3 + 2.4*Math.sqrt((L.size)/maxLeaf);
    g.appendChild(mk('circle',{cx:dotX(d), cy:y, r:(d===0?rad+0.5:rad), fill:col,
      stroke:(headFresh?'#fff':'none'), 'stroke-width':(headFresh?1.3:0)}));
  }

  // size bar
  const mx = r.leaf ? maxLeaf : maxHeader;
  const bw = 2 + (BAR_W-2)*(sizeOf(nid)/mx);
  g.appendChild(mk('rect',{x:BAR_X, y:y-3.5, width:bw, height:7, rx:1, fill:col, opacity:r.leaf?0.4:0.6}));

  // label
  const t = mk('text',{x:labelX(d), y:y+4, class:(r.leaf&&d>0)?'lane-label':'sub-label'});
  const dense = r.leaf && lanes[nid].size>24 ? '  ◼' : '';
  t.textContent = fit(labelOf(nid)+dense, NAME_W - d*INDENT - 18) + ` · ${sizeOf(nid)}`;
  t.onclick = ev=>{ ev.stopPropagation(); selectNode(nid); };
  g.appendChild(t);

  // gantt lifespan bar (birth -> last) on the shared commit axis
  const x1 = timeX(b), x2 = timeX(e);
  g.appendChild(mk('rect',{class:'lifebar', x:x1-2, y:y-2, width:Math.max(2,(x2-x1)+4), height:4, rx:2,
    fill:col, opacity:r.leaf?0.22:0.16}));
  if (headFresh)                                      // reaches the tip => currently active in HEAD state
    g.appendChild(mk('circle',{class:'lifebar', cx:timeX(nC-1), cy:y, r:2.6, fill:col, opacity:.9}));

  if (r.leaf){                                        // typed-op glyphs
    (lanes[nid].ops||[]).forEach(op=>{
      const gl = mk('text',{x:timeX(op.order)-4, y:y+4, class:'glyph', fill:col,
        opacity:(op.type==='reworked'?0.6:1)});
      gl.textContent = GLYPH[op.type]||'·';
      const ti=mk('title'); ti.textContent=`commit ${op.order}: ${op.type} (+${op.added} ~${op.modified} −${op.removed}${op.deaths?' ✝'+op.deaths:''})`;
      gl.appendChild(ti); gl.onclick=ev=>{ ev.stopPropagation(); selectNode(nid); };
      g.appendChild(gl);
    });
  } else {                                            // header: faint activity ticks where descendants moved
    const active = new Set(); for (const l of leavesOf(nid)) (lanes[l].ops||[]).forEach(o=>active.add(o.order));
    active.forEach(o => g.appendChild(mk('rect',{class:'lifebar', x:timeX(o)-1.5, y:y-3, width:3, height:6, rx:1, fill:col, opacity:.3})));
  }

  g.onmouseenter = ()=> previewNode(nid);
  g.onmouseleave = ()=> restoreSticky();
  svg.appendChild(g);
}

function drawCommitTicks(){
  const grp = mk('g',{class:'cticks'});
  for (let o=0;o<nC;o++){
    const x = timeX(o);
    const rect = mk('rect',{x:x-GLYPH_W/2, y:TOP-14, width:GLYPH_W, height:chartH-TOP-4, fill:'transparent'});
    const c = commits[o];
    const ti=mk('title'); ti.textContent=`commit ${o}: ${(c&&c.subject)||''}  —  click: what reverting this touches`;
    rect.appendChild(ti);
    rect.onclick = ev=>{ ev.stopPropagation(); selectCommit(o); };
    grp.appendChild(rect);
  }
  svg.appendChild(grp);
}

// consequence: stepped ripple ---------------------------------------------------------------------
// layers[k-1] = lanes first reached at hop k along `adj`, seeded by `startLanes`
function rippleLayers(startLanes, adj, H){
  const seen=new Set(startLanes); let frontier=[...startLanes]; const layers=[];
  for (let k=1;k<=H;k++){
    const nx=[]; for (const x of frontier) for (const y of (adj[x]||[])) if(!seen.has(y)){ seen.add(y); nx.push(y); }
    layers.push(nx); frontier=nx; if(!nx.length) break;
  }
  return layers;
}
// full blast profile (cumulative dependents after each hop) — cheap on <=90 lanes, tells the story
function blastProfile(startLanes){
  const seen=new Set(startLanes); let f=[...startLanes]; const prof=[]; let k=0;
  while (f.length && k++<14){ const nx=[]; for (const x of f) for (const y of inAdj[x]) if(!seen.has(y)){ seen.add(y); nx.push(y); } prof.push(seen.size-startLanes.length); f=nx; }
  return prof;
}

function clearFocus(){
  el('bands') && (el('bands').innerHTML='');
  svg.querySelectorAll('.rowg.lit,.rowg.ctx').forEach(n=>n.classList.remove('lit','ctx'));
  svg.classList.remove('focus');
}
function band(y, color, alpha){
  el('bands').appendChild(mk('rect',{class:'band', x:0, y:y-ROWH/2+1, width:chartW, height:ROWH-2, fill:color, opacity:alpha}));
}
function ctxAncestors(id){ let c=nodes[id]?nodes[id].parent:null, g=0; while(c!=null && g++<12){ const el2=svg.querySelector(`.rowg[data-id="${CSS.escape(c)}"]`); if(el2 && !el2.classList.contains('lit')) el2.classList.add('ctx'); c=nodes[c]?nodes[c].parent:null; } }

// paint a consequence set: seed lanes white, up-closure amber (blast), down-closure blue (foundation)
function paintConsequence(startLanes){
  clearFocus(); svg.classList.add('focus');
  const seed = new Set(startLanes);
  const up = rippleLayers(startLanes, inAdj, hop);      // dependents -> blast
  const down = rippleLayers(startLanes, outAdj, hop);   // dependencies -> foundation
  const litId = id => { const g=svg.querySelector(`.rowg[data-id="${CSS.escape(id)}"]`); if(g){ g.classList.add('lit'); g.classList.remove('ctx'); } };
  // seed
  startLanes.forEach(l=>{ const r=visibleRow(l); if(r){ litId(r.id); band(r.y,'#ffffff',.14); } ctxAncestors(l); });
  const seenRow = new Set(startLanes.map(l=>{const r=visibleRow(l);return r&&r.id;}));
  const paint = (layers, color, baseA) => layers.forEach((arr,i)=>arr.forEach(l=>{
    if (seed.has(l)) return;
    const r=visibleRow(l); if(!r) return;
    litId(r.id);
    if (!seenRow.has(r.id)){ band(r.y, color, Math.max(.08, baseA - i*0.03)); seenRow.add(r.id); }
    ctxAncestors(l);
  }));
  paint(up, 'var(--blast)', .24);
  paint(down, 'var(--found)', .20);
  return {up, down};
}

function selectNode(nid){ sticky={kind:'node', id:nid}; applySticky(); (lanes[nid]?showLane:showNode)(nid); }
function selectCommit(o){ sticky={kind:'commit', id:o}; applySticky(); showCommit(o); }
function previewNode(nid){                              // transient hover highlight (does not stick)
  const lv = leavesOf(nid); paintConsequence(lv);
}
function restoreSticky(){ if (sticky) applySticky(); else clearFocus(); }
function applySticky(){
  if (!sticky){ clearFocus(); return; }
  if (sticky.kind==='commit'){ paintCommit(sticky.id); }
  else { const lv=leavesOf(sticky.id); const {up,down}=paintConsequence(lv);
    const prof = blastProfile(lv);
    const total = prof.length?prof[prof.length-1]:0, pct = Math.round(100*total/nLanes);
    el('conseq').innerHTML = `<b>${esc(labelOf(sticky.id))}</b> — `
      + `<span class="blast">blast +${up.flat().length} at ≤${hop} hop${hop>1?'s':''}</span> · `
      + `<span class="found">foundation ${down.flat().length}</span> · `
      + `full blast radius <b>${total}</b>/${nLanes} (<b>${pct}%</b>) over ${prof.length} hops`
      + (pct>=80?` — <b>load-bearing</b>`:'');
  }
}
function paintCommit(o){
  clearFocus(); svg.classList.add('focus');
  const touched = (commitLanes[o]||[]).filter(l=>lanes[l]);
  // column highlight
  el('bands').appendChild(mk('rect',{class:'band', x:timeX(o)-GLYPH_W/2, y:TOP-10, width:GLYPH_W, height:chartH-TOP-6, fill:'var(--accent)', opacity:.14}));
  const {up} = paintConsequence(touched);
  el('conseq').innerHTML = `<b>commit ${o}</b>: touched <b>${touched.length}</b> feature${touched.length===1?'':'s'} · `
    + `<span class="blast">reverting ripples to +${up.flat().length} at ≤${hop} hop${hop>1?'s':''}</span>`;
}

function toggleCollapse(nid){ collapsed.has(nid)?collapsed.delete(nid):collapsed.add(nid); render(); }

// detail panel ------------------------------------------------------------------------------------
function depRows(list){
  return (list||[]).map(d=>{
    const L = lanes[d.lane]; if (!L) return '';
    return `<div class="row" onclick="selectNode('${d.lane}')"><span class="sw" style="background:${hcol(d.lane,true)}"></span>`
      + `→ ${esc(L.label)} <span style="color:#5b6272">· ${d.w} refs</span></div>`;
  }).join('') || '<div class="kv">— none</div>';
}
function inRows(l){
  const src = inAdj[l]; if (!src.length) return '<div class="kv">— none</div>';
  return src.map(s=>`<div class="row" onclick="selectNode('${s}')"><span class="sw" style="background:${hcol(s,true)}"></span>`
    + `← ${esc(lanes[s].label)}</div>`).join('');
}
function pathOf(id){
  const chain=[]; let cur=id, n=0;
  while (cur!=null && nodes[cur] && n++<10){ chain.unshift(labelOf(cur)); cur=nodes[cur].parent; }
  return chain.map(esc).join(' <b>›</b> ');
}
function showNode(nid){                                 // internal / subsystem node
  const s = supById[nid];                               // present only for roots
  const kids = childOrder(nid).map(c=>
    `<div class="row" onclick="selectNode('${c}')"><span class="sw" style="background:${hcol(c,isLeaf(c))}"></span>`
    + `${esc(labelOf(c))} <span style="color:#5b6272">· ${sizeOf(c)} ${isLeaf(c)?'ent':'▸'}</span></div>`).join('');
  const [b,e] = spanOf(nid);
  const lv = leavesOf(nid);
  const prof = blastProfile(lv); const total=prof.length?prof[prof.length-1]:0;
  const deps = s ? ((s.depends||[]).map(d=>`<div class="row" onclick="selectNode('${d.super}')"><span class="sw" style="background:${hcol(d.super,false)}"></span>`
    + `→ ${esc(labelOf(d.super))} <span style="color:#5b6272">· ${d.w}</span></div>`).join('') || '<div class="kv">— none</div>') : '';
  el('panel').innerHTML =
    `<h2><span class="sw" style="background:${hcol(nid,false)}"></span>${esc(labelOf(nid))} <span style="color:#5b6272">· ${s?'subsystem · rank '+rankMemo[nid]:'group · depth '+nodes[nid].depth}</span></h2>`
    + (s&&s.why?`<p class="why">${esc(s.why)}</p>`:'')
    + `<div class="path">${pathOf(nid)}</div>`
    + `<div class="kv">folder: <b>${esc((nodes[nid]&&nodes[nid].dir)||(s&&s.dir)||'')}</b> · <b>${sizeOf(nid)}</b> entities · <b>${lv.length}</b> leaves</div>`
    + `<div class="kv">active: commits <b>${b}</b> → <b>${e}</b></div>`
    + `<div class="sec"><div class="legend-hint">blast radius (features that (transitively) depend on this):</div>`
    +   `<div class="blastbar"></div><div class="kv"><b>${total}</b>/${nLanes} features over ${prof.length} hops. Click the header, then step hops to trace it.</div></div>`
    + (s?`<div class="sec"><div class="legend-hint">depends on subsystems (→):</div>${deps}</div>`:'')
    + `<div class="sec"><div class="legend-hint">composed of:</div>${kids}</div>`;
}
function showLane(l){
  const L = lanes[l];
  const members = (L.members||[]).map(m=>{const p=m.split('::');return `<span class="fn">${esc(p[1]||m)}</span>  ${esc(p[0])}`;}).join('\\n');
  const hist = (L.ops||[]).map(o=>{
    const col = hcol(l,true);
    const style = o.type==='pruned' ? `border:1px solid ${col};color:${col}`
      : o.type==='died' ? `box-shadow:0 0 0 1px #f79b9b;color:#f79b9b`
      : (o.type==='split'||o.type==='merge') ? `box-shadow:0 0 0 1px ${col};color:${col}`
      : o.type==='reworked' ? `background:${col};opacity:.55;color:#0f1115`
      : `background:${col};color:#0f1115`;
    const extra = o.reshape ? ` ${o.reshape.type}` : (o.deaths?` ✝${o.deaths}`:'');
    return `<span style="${style}" title="commit ${o.order}: ${o.type} +${o.added} ~${o.modified} −${o.removed}${extra}">${GLYPH[o.type]}${o.order}</span>`;
  }).join(' ');
  const reshapes = (L.ops||[]).filter(o=>o.reshape).map(o=>{
    const R=o.reshape, nm=x=>esc((x||'').split('::')[1]||x);
    const body = R.type==='split' ? `${nm(R.from)} → ${R.to.map(nm).join(', ')}` : `${R.from.map(nm).join(', ')} → ${nm(R.to)}`;
    return `<div class="kv">c${o.order} <b>${R.type}</b>: ${body}</div>`;
  }).join('');
  const prof = blastProfile([l]); const total=prof.length?prof[prof.length-1]:0, pct=Math.round(100*total/nLanes);
  const profStr = prof.map((n,i)=>`h${i+1}:+${i?n-prof[i-1]:n}`).slice(0,6).join('  ');
  el('panel').innerHTML =
    `<h2><span class="sw" style="background:${hcol(l,true)}"></span>${esc(L.label)}</h2>`
    + `<p class="why">${esc(L.why||'')}</p>`
    + (pathOf(l)?`<div class="path">${pathOf(l)}</div>`:'')
    + (L.size>24?`<span class="flag dense">◼ dense — ${L.size} entities (cohesive blob, e.g. a facade)</span>`:'')
    + `<div class="kv">folder: <b>${esc(L.dir)}</b> · <b>${L.size}</b> entities · active <b>${L.birth}</b> → <b>${L.last}</b>${L.last===nC-1?' <b style="color:#8fd67a">(HEAD-fresh)</b>':''}</div>`
    + `<div class="sec"><div class="legend-hint">blast radius — reverting this ripples to:</div>`
    +   `<div class="blastbar" style="width:${Math.max(8,pct)}%"></div>`
    +   `<div class="kv"><b>${total}</b>/${nLanes} features (<b>${pct}%</b>) over ${prof.length} hops &nbsp; ${profStr}</div></div>`
    + (reshapes?`<div class="sec"><div class="legend-hint">lifecycle reshapes:</div>${reshapes}</div>`:'')
    + `<div class="sec"><div class="legend-hint">operation history (${(L.ops||[]).length} ops, oldest→newest):</div><div class="hist">${hist||'—'}</div></div>`
    + `<div class="sec"><div class="legend-hint">depends on (calls/imports →):</div>${depRows(L.depends)}</div>`
    + `<div class="sec"><div class="legend-hint">depended on by (←):</div>${inRows(l)}</div>`
    + `<div class="sec"><div class="legend-hint">members (${(L.members||[]).length}):</div><div class="members">${members}</div></div>`;
}
function showCommit(o){
  const c = commits[o];
  const touched = (commitLanes[o]||[]).filter(l=>lanes[l]);
  const rows = touched.map(l=>`<div class="row" onclick="selectNode('${l}')"><span class="sw" style="background:${hcol(l,true)}"></span>`
    + `${esc(lanes[l].label)}</div>`).join('') || '<div class="kv">— none</div>';
  const prof = blastProfile(touched); const total=prof.length?prof[prof.length-1]:0, pct=Math.round(100*total/nLanes);
  el('panel').innerHTML =
    `<h2>commit ${o}</h2>`
    + `<p class="why">${esc((c&&c.subject)||'')}</p>`
    + `<div class="sec"><div class="legend-hint">reverting this un-does ops in these <b>${touched.length}</b> feature(s):</div>${rows}</div>`
    + `<div class="sec"><div class="legend-hint">downstream that could be affected:</div>`
    +   `<div class="blastbar" style="width:${Math.max(8,pct)}%"></div>`
    +   `<div class="kv"><b>${total}</b>/${nLanes} features (<b>${pct}%</b>). Step hops to grow the ripple.</div></div>`;
}

// controls ----------------------------------------------------------------------------------------
el('order').onchange = render;
el('hopPlus').onclick = ()=>{ hop=Math.min(12,hop+1); el('hopN').textContent=hop; restoreSticky(); };
el('hopMinus').onclick = ()=>{ hop=Math.max(1,hop-1); el('hopN').textContent=hop; restoreSticky(); };
el('expandAll').onclick = ()=>{ collapsed.clear(); render(); };
el('collapseAll').onclick = ()=>{ for (const nid in nodes) if(!isLeaf(nid)&&nodes[nid].depth>0) collapsed.add(nid); render(); };
el('clearSel').onclick = ()=>{ sticky=null; clearFocus(); el('conseq').textContent='Click a feature to trace its dependency consequence.';
  el('panel').innerHTML='<div class="empty">Click a feature to inspect its members, history, blast radius and dependencies.</div>'; };
document.addEventListener('keydown', e=>{ if(e.key==='Escape') el('clearSel').click(); });

// size scales (headers vs leaves scaled independently) --------------------------------------------
const maxHeader = Math.max(1, ...Object.keys(nodes).filter(n=>!isLeaf(n)).map(sizeOf));
const maxLeaf = Math.max(1, ...Object.values(lanes).map(L=>L.size));
render();
</script>
</body>
</html>
"""


def main() -> None:
    src = _OUT / "operations.json"
    if not src.exists():
        src = _OUT / "hierarchy.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    dest = _OUT / "rail2.html"
    dest.write_text(_TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
    print(f"wrote {dest}  (from {src.name}: {len(data['supers'])} subsystems, "
          f"{len(data['lanes'])} lanes, {len(data['commits'])} commits)")


if __name__ == "__main__":
    main()
