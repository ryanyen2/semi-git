"""Render the feature hierarchy + operation DAG as a decision-graph-style RAIL (git-log lanes).

This is the second view (the flat commit x lane grid stays in render_hierarchy_html.py). It mirrors
the editor's decision graph (editor/vscode/media/decision.js): a vertical rail where

  - each ROW is a feature lane (the versioning unit), ordered by recency — newest-touched at the
    TOP (the integrator/HEAD floats up), lanes from different subsystems INTERLEAVED by time;
  - each COLUMN is a subsystem, hue = identity, packed by greedy interval-coloring so subsystems
    whose (interleaved) lane row-spans don't overlap share a column — the low-column git-log look;
  - CONNECTORS are the directed ``depends`` edges (calls/imports), a curve from the dependent's dot
    to the dot of the lane it most depends on;
  - each lane carries a GLYPH STRIP of its typed operation history over time — born ◆, extended +,
    reworked ~, pruned −, split ⋔, merge ⋈, died ✝ — so the lifecycle reads at a glance.

A subsystem legend (hue chips) navigates + filters; the panel shows a lane's tree path, members,
full op history and dependencies. Reads ``out/operations.json`` (falls back to hierarchy.json).
Writes ``out/rail.html`` — one self-contained file.

    .venv/bin/python experiments/patch_clustering/render_rail_html.py
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
<title>sgt — feature rail (decision-graph layout over history)</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#262b36; --ink:#e6e9ef; --dim:#8b93a3; --accent:#7aa2f7; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:12px 16px; border-bottom:1px solid var(--line); position:sticky; top:0;
           background:var(--bg); z-index:20; }
  header h1 { margin:0 0 4px; font-size:15px; font-weight:600; }
  .stats { color:var(--dim); font-size:12px; } .stats b { color:var(--ink); }
  .howto { color:var(--dim); font-size:11.5px; margin-top:6px; max-width:1040px; }
  .howto b { color:var(--ink); }
  .oplegend { margin-top:6px; font-size:11.5px; color:var(--dim); display:flex; gap:15px; flex-wrap:wrap; }
  .oplegend b { color:var(--ink); }
  .controls { margin-top:8px; display:flex; gap:16px; align-items:center; font-size:12px; color:var(--dim); flex-wrap:wrap; }
  button { font:inherit; font-size:11px; background:#1e2430; color:var(--ink); border:1px solid var(--line);
           border-radius:4px; padding:3px 9px; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  #legend { margin-top:8px; display:flex; gap:5px 10px; flex-wrap:wrap; max-height:64px; overflow:auto; }
  .chip { font-size:11px; color:var(--dim); cursor:pointer; white-space:nowrap; user-select:none; }
  .chip.off { opacity:.32; text-decoration:line-through; }
  .chip .sw { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:4px; vertical-align:middle; }
  .chip:hover { color:var(--ink); }
  .wrap { display:flex; align-items:flex-start; }
  .rail-scroll { overflow:auto; max-height:calc(100vh - 236px); flex:1; }
  aside { width:360px; flex:none; border-left:1px solid var(--line); padding:14px 14px 40px;
          height:calc(100vh - 236px); overflow:auto; background:var(--panel); }
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
  .legend-hint { font-size:11px; color:var(--dim); margin-bottom:6px; }
  .flag { display:inline-block; font-size:10px; padding:1px 5px; border-radius:3px; margin:0 4px 4px 0; }
  .flag.dense { background:#3a1212; color:#f79b9b; }
  .empty { color:var(--dim); padding:20px; }
  svg text { font:11px ui-monospace,Menlo,monospace; }
  .lane-label { fill:var(--dim); cursor:pointer; }
  .lane-label:hover { fill:var(--accent); }
  .glyph { cursor:pointer; }
</style>
</head>
<body>
<header>
  <h1>sgt — feature rail · decision-graph layout over version history</h1>
  <div class="stats" id="stats"></div>
  <div class="howto">
    <b>Rows</b> = feature lanes, newest-touched on top (the integrator floats up), interleaved by
    time. <b>Columns</b> = subsystems (hue = identity), packed like git-log lanes. <b>Curves</b> =
    directed <b>depends</b> (calls/imports, dependent → dependency). Each lane's <b>glyph strip</b>
    is its typed operation history across the 54 commits. Click a legend chip to filter a subsystem;
    click a lane to inspect.
  </div>
  <div class="oplegend">
    <span><b>◆</b> born</span><span><b>+</b> extended</span><span><b>~</b> reworked</span>
    <span><b>−</b> pruned</span><span><b>⋔</b> split</span><span><b>⋈</b> merge</span>
    <span><b>✝</b> died</span><span><b>↺</b> reverted</span>
  </div>
  <div class="controls">
    <button id="allOn">show all</button>
    <button id="allOff">hide all</button>
    <label><input type="checkbox" id="showDeps" checked> depends connectors</label>
    <span id="shown"></span>
  </div>
  <div id="legend"></div>
</header>
<div class="wrap">
  <div class="rail-scroll"><svg id="rail"></svg></div>
  <aside id="panel"><div class="empty">Click a lane to inspect its members, history and dependencies.</div></aside>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const lanes = D.lanes, supers = D.supers, commits = D.commits, nodes = D.nodes || {};
const nC = commits.length, nLanes = Object.keys(lanes).length;
const SVGNS = 'http://www.w3.org/2000/svg';
const ROWH = 22, COLW = 20, LEFT = 16, TOP = 14, LABELGAP = 18, GLYPH_W = 9, LABEL_W = 296;

const supById = {}; supers.forEach(s => supById[s.id] = s);
const hueOf = {};
supers.slice().sort((a,b)=>b.size-a.size).forEach((s,i)=> hueOf[s.id] = Math.round((i*137.508)%360));
const superColor = s => `hsl(${hueOf[s]} 55% 58%)`;
const laneColor = l => `hsl(${hueOf[lanes[l].super]} 48% 60%)`;

const GLYPH = { born:'◆', extended:'+', reworked:'~', pruned:'−', split:'⋔', merge:'⋈', died:'✝', reverted:'↺', touched:'·' };
const el = id => document.getElementById(id);
const esc = s => (s||'').replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));

// lanes ordered by recency (newest-touched first); interleaving across subsystems is what gives the
// rail real columns (a subsystem's scattered lanes make its span overlap other subsystems' spans).
const ALL = Object.keys(lanes);
const last = l => (lanes[l].last ?? -1);
ALL.sort((a,b)=> last(b)-last(a) || lanes[b].size-lanes[a].size || (a<b?-1:1));

const hidden = new Set();  // subsystems toggled off in the legend

// greedy interval-coloring of subsystems over the CURRENT visible row order -> one column each
function layout(visible){
  const rowOf = {}; visible.forEach((l,i)=> rowOf[l]=i);
  const span = {};
  for (const l of visible){ const s=lanes[l].super, r=rowOf[l];
    (span[s] ??= {top:r,bot:r}); span[s].top=Math.min(span[s].top,r); span[s].bot=Math.max(span[s].bot,r); }
  const order = Object.keys(span).sort((a,b)=> span[a].top-span[b].top || (a<b?-1:1));
  const colBot=[], colOf={};
  for (const s of order){ let c=0; while(c<colBot.length && colBot[c]>=span[s].top) c++; colBot[c]=span[s].bot; colOf[s]=c; }
  return { rowOf, span, colOf, ncols: Math.max(1,colBot.length) };
}

const covered = commits.filter(c => (D.commit_lanes[c.order]||[]).length).length;
const lc = D.lifecycle || {};
el('stats').innerHTML = `<b>${supers.length}</b> subsystems · <b>${nLanes}</b> feature lanes · `
  + `depth ≤<b>${D.max_depth||4}</b> · <b>${(D.op_types&&Object.values(D.op_types).reduce((a,b)=>a+b,0))||'?'}</b> typed ops · `
  + `<b>${lc.deaths||0}</b> deaths · <b>${lc.splits||0}</b> splits · <b>${D.dep_edges||0}</b> deps · `
  + `coverage <b>${covered}/${nC}</b> · ${D.cost||''}`;

function renderLegend(){
  const box = el('legend'); box.innerHTML='';
  for (const s of supers.slice().sort((a,b)=>b.size-a.size)){
    const c = document.createElement('span');
    c.className = 'chip' + (hidden.has(s.id)?' off':'');
    c.innerHTML = `<span class="sw" style="background:${superColor(s.id)}"></span>${esc(s.label)} <span style="opacity:.6">${s.children.length}</span>`;
    c.title = `${s.label} — ${s.size} entities, ${s.children.length} lanes (click: toggle · dbl: inspect)`;
    c.onclick = ()=>{ hidden.has(s.id)?hidden.delete(s.id):hidden.add(s.id); render(); };
    c.ondblclick = ()=> showSuper(s);
    box.appendChild(c);
  }
}

const mk = (tag, attrs) => { const e=document.createElementNS(SVGNS,tag); for(const k in attrs) e.setAttribute(k,attrs[k]); return e; };

function render(){
  const visible = ALL.filter(l => !hidden.has(lanes[l].super));
  const { rowOf, span, colOf, ncols } = layout(visible);
  const svg = el('rail'); svg.innerHTML='';
  const railRight = LEFT + ncols*COLW;
  const glyphMax = Math.max(0, ...visible.map(l => (lanes[l].ops||[]).length));
  const W = Math.max(920, railRight + LABELGAP + LABEL_W + glyphMax*GLYPH_W + 40);
  svg.setAttribute('height', TOP + visible.length*ROWH + 20);
  svg.setAttribute('width', W);
  renderLegend();
  el('shown').textContent = `${visible.length} lanes · ${ncols} columns`
    + (hidden.size?` · ${hidden.size} subsystems hidden`:'');

  const yOf = i => TOP + i*ROWH + ROWH/2;
  const xOf = c => LEFT + c*COLW + COLW/2;
  const laneX = l => xOf(colOf[lanes[l].super]);
  const laneY = l => yOf(rowOf[l]);

  // subsystem trunk guides
  for (const s in span){
    const y0=yOf(span[s].top), y1=yOf(span[s].bot);
    svg.appendChild(mk('line',{x1:xOf(colOf[s]),y1:y0,x2:xOf(colOf[s]),y2:y1,stroke:superColor(s),'stroke-width':2,opacity:.26}));
  }
  // depends connectors (strongest cross-lane dep per lane)
  if (el('showDeps').checked){
    for (const l of visible){
      const d = (lanes[l].depends||[]).find(x => rowOf[x.lane]!==undefined);
      if (!d) continue;
      const x1=laneX(l), y1=laneY(l), x2=laneX(d.lane), y2=laneY(d.lane);
      if (x1===x2 && Math.abs(y1-y2)<=ROWH) continue;
      const mx=(x1+x2)/2;
      svg.appendChild(mk('path',{d:`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`,
        fill:'none',stroke:laneColor(l),'stroke-width':1,opacity:.28}));
    }
  }
  // lane rows: dot + label + op glyph strip
  for (const l of visible){
    const y=laneY(l), x=laneX(l), L=lanes[l], isHead = last(l)===nC-1;
    svg.appendChild(mk('circle',{cx:x,cy:y,r:4,fill:laneColor(l),
      stroke:(isHead?'#fff':'none'),'stroke-width':(isHead?1.5:0)}));
    const lx = railRight + LABELGAP;
    const t = mk('text',{x:lx,y:y+4,class:'lane-label'});
    t.textContent = `${L.label}${L.size>24?'  ◼':''} · ${L.size}`;
    t.onclick = ()=> showLane(l);
    svg.appendChild(t);
    const gx0 = lx + LABEL_W;
    (L.ops||[]).forEach((op,k)=>{
      const g = mk('text',{x:gx0+k*GLYPH_W, y:y+4, class:'glyph', fill:laneColor(l),
        opacity:(op.type==='reworked'?0.6:1)});
      g.textContent = GLYPH[op.type]||'·';
      const ti=mk('title'); ti.textContent=`commit ${op.order}: ${op.type} (+${op.added} ~${op.modified} −${op.removed}${op.deaths?' ✝'+op.deaths:''})`;
      g.appendChild(ti); g.onclick=()=>showLane(l);
      svg.appendChild(g);
    });
  }
}

function depRows(list){
  return (list||[]).map(d=>{
    const L = lanes[d.lane]; if (!L) return '';
    return `<div class="row" onclick="showLane('${d.lane}')"><span class="sw" style="background:${laneColor(d.lane)}"></span>`
      + `→ ${esc(L.label)} <span style="color:#5b6272">· ${d.w} refs</span></div>`;
  }).join('') || '<div class="kv">— none</div>';
}

function pathOf(l){
  if (!nodes[l]) return '';
  const chain=[]; let cur=l, n=0;
  while (cur!=null && nodes[cur] && n++<10){ chain.unshift(nodes[cur].label||cur); cur=nodes[cur].parent; }
  return chain.map(esc).join(' <b>›</b> ');
}

function showSuper(s){
  const kids = s.children.map(c=>
    `<div class="row" onclick="showLane('${c}')"><span class="sw" style="background:${laneColor(c)}"></span>`
    + `${esc(lanes[c]?lanes[c].label:c)} <span style="color:#5b6272">· ${lanes[c]?lanes[c].size:''} ent</span></div>`).join('');
  el('panel').innerHTML =
    `<h2><span class="sw" style="background:${superColor(s.id)}"></span>${esc(s.label)} <span style="color:#5b6272">· subsystem</span></h2>`
    + `<p class="why">${esc(s.why||'')}</p>`
    + `<div class="kv">folder: <b>${esc(s.dir||'')}</b> · <b>${s.size}</b> entities · <b>${s.children.length}</b> lanes</div>`
    + `<div class="kv">active: commits <b>${s.birth}</b> → <b>${s.last}</b></div>`
    + `<div class="sec"><div class="legend-hint">feature lanes (click to inspect):</div>${kids}</div>`;
}

function showLane(l){
  const L = lanes[l];
  const members = (L.members||[]).map(m=>{const p=m.split('::');return `<span class="fn">${esc(p[1]||m)}</span>  ${esc(p[0])}`;}).join('\\n');
  const hist = (L.ops||[]).map(o=>{
    const col = laneColor(l);
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
  el('panel').innerHTML =
    `<h2><span class="sw" style="background:${laneColor(l)}"></span>${esc(L.label)}</h2>`
    + `<p class="why">${esc(L.why||'')}</p>`
    + (pathOf(l)?`<div class="path">${pathOf(l)}</div>`:'')
    + (L.size>24?`<span class="flag dense">◼ dense — ${L.size} entities (cohesive blob, e.g. a facade)</span>`:'')
    + `<div class="kv">folder: <b>${esc(L.dir)}</b> · <b>${L.size}</b> entities · active <b>${L.birth}</b> → <b>${L.last}</b></div>`
    + (reshapes?`<div class="sec"><div class="legend-hint">lifecycle reshapes:</div>${reshapes}</div>`:'')
    + `<div class="sec"><div class="legend-hint">operation history (${(L.ops||[]).length} ops, oldest→newest):</div><div class="hist">${hist||'—'}</div></div>`
    + `<div class="sec"><div class="legend-hint">depends on (calls/imports →):</div>${depRows(L.depends)}</div>`
    + `<div class="sec"><div class="legend-hint">members (${(L.members||[]).length}):</div><div class="members">${members}</div></div>`;
}

el('allOn').onclick = ()=>{ hidden.clear(); render(); };
el('allOff').onclick = ()=>{ supers.forEach(s=>hidden.add(s.id)); render(); };
el('showDeps').onchange = render;
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
    dest = _OUT / "rail.html"
    dest.write_text(_TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
    print(f"wrote {dest}  (from {src.name}: {len(data['supers'])} subsystems, "
          f"{len(data['lanes'])} lanes, {len(data['commits'])} commits)")


if __name__ == "__main__":
    main()
