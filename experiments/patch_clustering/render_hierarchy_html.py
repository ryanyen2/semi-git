"""Render the subsystem -> feature hierarchy AND the typed-operation DAG as one HTML view.

Reads ``out/operations.json`` if present (hierarchy enriched with typed ops + directed
dependencies by operations.py), else falls back to ``out/hierarchy.json``. Writes
``out/hierarchy.html`` — one self-contained file, no dependencies.

Two axes in one picture:
  - hierarchy: bold columns = subsystems, collapsible into their feature lanes (same hue).
  - operations: a filled child cell is now styled by *operation type* (born / extended /
    reworked / pruned / reverted), and the panel shows each lane's typed history and the
    lanes it depends on (directed calls/imports).

    .venv/bin/python experiments/patch_clustering/render_hierarchy_html.py
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
<title>sgt — feature hierarchy + operation DAG over history</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#262b36; --ink:#e6e9ef; --dim:#8b93a3; --accent:#7aa2f7; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:12px 16px; border-bottom:1px solid var(--line); position:sticky; top:0;
           background:var(--bg); z-index:20; }
  header h1 { margin:0 0 4px; font-size:15px; font-weight:600; }
  .stats { color:var(--dim); font-size:12px; } .stats b { color:var(--ink); }
  .howto { color:var(--dim); font-size:11.5px; margin-top:6px; max-width:960px; }
  .howto b { color:var(--ink); }
  .oplegend { margin-top:6px; font-size:11px; color:var(--dim); display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  .oplegend .ic { display:inline-block; width:13px; height:13px; border-radius:3px; margin-right:4px; vertical-align:-2px; background:#7aa2f7; }
  .controls { margin-top:8px; display:flex; gap:16px; align-items:center; font-size:12px; color:var(--dim); flex-wrap:wrap; }
  button { font:inherit; font-size:11px; background:#1e2430; color:var(--ink); border:1px solid var(--line);
           border-radius:4px; padding:3px 9px; cursor:pointer; }
  button:hover { border-color:var(--accent); }
  .wrap { display:flex; align-items:flex-start; }
  .grid-scroll { overflow:auto; max-height:calc(100vh - 208px); flex:1; }
  table { border-collapse:collapse; }
  th.corner { position:sticky; left:0; top:0; z-index:16; background:var(--bg); text-align:left;
              padding:0 8px; vertical-align:bottom; color:var(--dim); font-weight:500; }
  .colhead { position:sticky; top:0; z-index:10; background:var(--bg); writing-mode:vertical-rl;
             transform:rotate(180deg); white-space:nowrap; font-size:10.5px; padding:6px 0; height:172px;
             cursor:pointer; border-left:1px solid var(--line); }
  .colhead.super { font-weight:600; color:var(--ink); }
  .colhead.child { font-weight:400; color:var(--dim); border-left:1px dotted #333a48; }
  .colhead .sw { display:inline-block; width:9px; height:9px; border-radius:2px; margin-bottom:4px; }
  .colhead .caret { font-size:9px; opacity:.8; }
  .colhead:hover { background:#1e2430; }
  .colhead .badge { font-size:9px; }
  tbody td.meta { position:sticky; left:0; background:var(--bg); white-space:nowrap; padding:2px 8px;
                  border-right:1px solid var(--line); z-index:5; }
  tbody td.meta .sha { color:var(--dim); } tbody td.meta .subj { color:var(--ink); }
  tbody tr.dead td.meta .subj { color:#5b6272; font-style:italic; }
  tbody tr:hover td.meta { background:#1e2430; }
  tbody tr:hover td { background-color:rgba(122,162,247,.06); }
  td.cell { width:16px; height:15px; border-left:1px solid #1b1f28; border-top:1px solid #1b1f28; padding:1px; }
  td.cell.superc { border-left:1px solid #2c3342; }
  .dot { width:100%; height:100%; border-radius:2px; }
  aside { width:342px; flex:none; border-left:1px solid var(--line); padding:14px 14px 40px;
          height:calc(100vh - 208px); overflow:auto; background:var(--panel); }
  aside h2 { font-size:13px; margin:0 0 2px; }
  aside .why { color:var(--dim); font-style:italic; margin:0 0 10px; }
  aside .kv { color:var(--dim); font-size:12px; margin:2px 0; } aside .kv b { color:var(--ink); }
  aside .sec { margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }
  aside .row { font-size:12px; margin:3px 0; cursor:pointer; } aside .row:hover { color:var(--accent); }
  aside .sw { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; vertical-align:middle; }
  aside .members { font-size:11px; color:var(--dim); white-space:pre-wrap; word-break:break-all; }
  aside .members .fn { color:var(--ink); }
  aside .hist { font-size:12px; word-break:break-word; line-height:1.9; }
  aside .hist span { padding:1px 3px; border-radius:3px; }
  .legend-hint { font-size:11px; color:var(--dim); margin-bottom:6px; }
  .flag { display:inline-block; font-size:10px; padding:1px 5px; border-radius:3px; margin:0 4px 4px 0; }
  .flag.hub { background:#3a2a12; color:#f7c66b; } .flag.dup { background:#2a1230; color:#e58bff; }
  .empty { color:var(--dim); padding:20px; }
</style>
</head>
<body>
<header>
  <h1>sgt — feature hierarchy + operation DAG over version history</h1>
  <div class="stats" id="stats"></div>
  <div class="howto">
    <b>Rows</b> = commits (oldest at top, HEAD at bottom). <b>Bold columns</b> = subsystems;
    click one to <b>expand</b> into its feature lanes (thin dotted columns). A filled child cell
    is a <b>typed operation</b> on that lane in that commit. Click a header or cell to inspect —
    a lane shows its <b>typed history</b> and the lanes it <b>depends on</b> (directed calls/imports).
    <span class="flag hub">⚠ hub</span> = god-cluster; <span class="flag dup">⧉ dup</span> = repeated
    label (likely over-split).
  </div>
  <div class="oplegend" id="oplegend">
    <span>operation:</span>
    <span><i class="ic" style="box-shadow:inset 0 0 0 2px rgba(255,255,255,.85)"></i>born</span>
    <span><i class="ic"></i>extended</span>
    <span><i class="ic" style="opacity:.5"></i>reworked</span>
    <span><i class="ic" style="background:transparent;box-shadow:inset 0 0 0 1.5px #7aa2f7"></i>pruned</span>
    <span><i class="ic" style="box-shadow:0 0 0 1.5px #ff6b6b"></i>reverted</span>
  </div>
  <div class="controls">
    <button id="expandAll">expand all</button>
    <button id="collapseAll">collapse all</button>
    <label><input type="checkbox" id="showTail"> show small subsystems (&lt;15 entities)</label>
    <label>min lane size: <input type="range" id="sizeSlider" min="4" max="40" value="4">
      <span id="sizeVal">4</span></label>
    <span id="shownCount"></span>
  </div>
</header>
<div class="wrap">
  <div class="grid-scroll"><table id="grid"></table></div>
  <aside id="panel"><div class="empty">Click a subsystem to expand it, or click any header / cell to inspect.</div></aside>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const commits = D.commits, lanes = D.lanes, nC = commits.length;
const commitLanes = {};
for (const [o, ls] of Object.entries(D.commit_lanes)) commitLanes[+o] = new Set(ls);
const supers = D.supers.slice().sort((a,b)=> b.size - a.size);
const TAIL = 15, HUB_FRAC = 0.40;
const GLYPH = {born:'◆', extended:'+', reworked:'~', pruned:'−', reverted:'↺', touched:'·'};

// per-lane per-commit operation type
const laneOp = {};
for (const l in lanes){ laneOp[l] = {}; for (const o of (lanes[l].ops||[])) laneOp[l][o.order]=o; }

const hueOf = {};
supers.forEach((s,i)=> hueOf[s.id] = Math.round((i*137.508)%360));
function superColor(s){ return `hsl(${hueOf[s.id]} 55% 53%)`; }
function laneColor(l){ const L=lanes[l]; const idx=D.supers.find(s=>s.id===L.super).children.indexOf(l);
  return `hsl(${hueOf[L.super]} 48% ${Math.max(45, 66 - idx*4)}%)`; }

const labelCount = {};
for (const l in lanes) labelCount[lanes[l].label] = (labelCount[lanes[l].label]||0)+1;
const isDup = l => labelCount[lanes[l].label] > 1;
const isHubSet = set => set.size >= HUB_FRAC*nC;
const superTouch = {}; supers.forEach(s=> superTouch[s.id]=new Set(s.commits));
const laneTouch = {}; for (const l in lanes) laneTouch[l]=new Set(lanes[l].commits||[]);

const expanded = new Set();
const el = id => document.getElementById(id);

const covered = commits.filter(c=> (commitLanes[c.order]||new Set()).size).length;
const nOps = Object.values(D.op_types||{}).reduce((a,b)=>a+b,0);
el('stats').innerHTML = `<b>${supers.length}</b> subsystems · <b>${Object.keys(lanes).length}</b> lanes · `
  + `<b>${nOps||'?'}</b> typed ops · <b>${D.dep_edges||0}</b> dep edges · coverage <b>${covered}/${nC}</b> · `
  + `γ ${D.gamma_coarse}/${D.gamma_fine} · ${D.cost}`;

function visibleSupers(){
  const showTail = el('showTail').checked;
  return supers.filter(s=> showTail || s.size >= TAIL);
}
function visibleChildren(s){
  const min = +el('sizeSlider').value;
  return s.children.filter(c=> lanes[c].size >= min).sort((a,b)=> lanes[b].size - lanes[a].size);
}
function columns(){
  const cols=[];
  for (const s of visibleSupers()){
    cols.push({t:'s', id:s.id, s});
    if (expanded.has(s.id)) for (const c of visibleChildren(s)) cols.push({t:'l', id:c, parent:s.id});
  }
  return cols;
}

function mkdot(color){ const d=document.createElement('div'); d.className='dot'; d.style.background=color; return d; }
function mkop(color, type){
  const d=document.createElement('div'); d.className='dot';
  if (type==='pruned'){ d.style.background='transparent'; d.style.boxShadow='inset 0 0 0 1.5px '+color; }
  else { d.style.background=color; }
  if (type==='born') d.style.boxShadow='inset 0 0 0 2px rgba(255,255,255,.85)';
  else if (type==='reverted') d.style.boxShadow='0 0 0 1.5px #ff6b6b';
  else if (type==='reworked') d.style.opacity='.5';
  d.title = type;
  return d;
}
function esc(s){ return s.replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }

function render(){
  const cols = columns();
  el('shownCount').textContent = `${cols.length} columns (${visibleSupers().length} subsystems`
    + `${expanded.size?`, ${expanded.size} expanded`:''})`;
  const tbl = el('grid'); tbl.innerHTML='';

  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  const corner = document.createElement('th'); corner.className='corner';
  corner.textContent='commit ↓ / subsystem · lane →'; hr.appendChild(corner);
  for (const col of cols){
    const th = document.createElement('th');
    if (col.t==='s'){
      const s=col.s, hub=isHubSet(superTouch[s.id]);
      th.className='colhead super';
      th.innerHTML = `<span class="caret">${expanded.has(s.id)?'▾':'▸'}</span> `
        + `<span class="sw" style="background:${superColor(s)}"></span>${s.label}`
        + `<span class="badge">${hub?' ⚠':''}</span>`;
      th.title = `${s.label} — ${s.dir} · ${s.size} entities · ${s.children.length} lanes`;
      th.onclick = ()=>{ expanded.has(s.id)?expanded.delete(s.id):expanded.add(s.id); showSuper(s); render(); };
    } else {
      const L=lanes[col.id], hub=isHubSet(laneTouch[col.id]);
      th.className='colhead child';
      th.innerHTML = `<span class="sw" style="background:${laneColor(col.id)}"></span>${L.label}`
        + `<span class="badge">${hub?' ⚠':''}${isDup(col.id)?' ⧉':''}</span>`;
      th.title = `${L.label} — ${L.dir} · ${L.size} entities`;
      th.onclick = ()=>{ showLane(col.id); };
    }
    hr.appendChild(th);
  }
  thead.appendChild(hr); tbl.appendChild(thead);

  const tb = document.createElement('tbody');
  for (const c of commits){
    const fine = commitLanes[c.order]||new Set();
    const tr = document.createElement('tr');
    if (!fine.size) tr.className='dead';
    const meta=document.createElement('td'); meta.className='meta';
    const head = c.order===nC-1 ? ' <b style="color:#7aa2f7">HEAD</b>' : '';
    meta.innerHTML = `<span class="sha">${String(c.order).padStart(2,' ')} ${c.short}</span> `
      + `<span class="subj">${esc(c.subject).slice(0,50)}</span>${head}`;
    meta.onmouseenter = ()=> showCommit(c);
    tr.appendChild(meta);
    for (const col of cols){
      const td=document.createElement('td');
      if (col.t==='s'){
        td.className='cell superc';
        if (superTouch[col.id].has(c.order)) td.appendChild(mkdot(superColor(col.s)));
        td.onclick=()=>showSuper(col.s);
      } else {
        td.className='cell';
        if (fine.has(col.id)){ const op=laneOp[col.id][c.order]; td.appendChild(mkop(laneColor(col.id), op?op.type:'touched')); }
        td.onclick=()=>showLane(col.id);
      }
      tr.appendChild(td);
    }
    tb.appendChild(tr);
  }
  tbl.appendChild(tb);
}

function depRows(list, key){
  return (list||[]).map(d=>{
    const id=d[key]; const label = key==='lane'?lanes[id].label:(D.supers.find(s=>s.id===id)||{}).label||id;
    const fn = key==='lane'?`showLane('${id}')`:`showSuper(D.supers.find(s=>s.id==='${id}'))`;
    return `<div class="row" onclick="${fn}">→ ${esc(label)} <span style="color:#5b6272">· ${d.w} refs</span></div>`;
  }).join('') || '<div class="kv">— none</div>';
}

function showSuper(s){
  const hub = isHubSet(superTouch[s.id]);
  const kids = s.children.map(c=>
    `<div class="row" onclick="showLane('${c}')"><span class="sw" style="background:${laneColor(c)}"></span>`
    + `${esc(lanes[c].label)} <span style="color:#5b6272">· ${lanes[c].size} ent${isDup(c)?' ⧉':''}</span></div>`
  ).join('');
  el('panel').innerHTML =
    `<h2><span class="sw" style="background:${superColor(s)}"></span>${esc(s.label)} <span style="color:#5b6272">· subsystem</span></h2>`
    + `<p class="why">${esc(s.why||'')}</p>`
    + (hub?`<span class="flag hub">⚠ hub — touches ${superTouch[s.id].size}/${nC} commits (god-cluster)</span>`:'')
    + `<div class="kv">folder: <b>${esc(s.dir)}</b></div>`
    + `<div class="kv">entities: <b>${s.size}</b> across <b>${s.children.length}</b> lanes</div>`
    + `<div class="kv">active: commits <b>${s.birth}</b> → <b>${s.last}</b></div>`
    + `<div class="sec"><div class="legend-hint">depends on (subsystems):</div>${depRows(s.depends,'super')}</div>`
    + `<div class="sec"><div class="legend-hint">feature lanes (click to inspect):</div>${kids}</div>`;
}

function showLane(l){
  const L=lanes[l], hub=isHubSet(laneTouch[l]);
  const members = (L.members||[]).map(m=>{const p=m.split('::');return `<span class="fn">${esc(p[1]||m)}</span>  ${esc(p[0])}`;}).join('\\n');
  const hist = (L.ops||[]).map(o=>{
    const col = laneColor(l);
    const style = o.type==='pruned' ? `border:1px solid ${col};color:${col}`
      : o.type==='reworked' ? `background:${col};opacity:.55`
      : o.type==='reverted' ? `box-shadow:0 0 0 1px #ff6b6b;color:#ff9b9b`
      : `background:${col};color:#0f1115`;
    return `<span style="${style}" title="commit ${o.order}: +${o.added} ~${o.modified} -${o.removed}">${GLYPH[o.type]}${o.order}</span>`;
  }).join(' ');
  el('panel').innerHTML =
    `<h2><span class="sw" style="background:${laneColor(l)}"></span>${esc(L.label)}</h2>`
    + `<p class="why">${esc(L.why||'')}</p>`
    + (hub?`<span class="flag hub">⚠ hub — ${laneTouch[l].size}/${nC} commits</span>`:'')
    + (isDup(l)?`<span class="flag dup">⧉ label appears ${labelCount[L.label]}× — possible over-split</span>`:'')
    + `<div class="kv">subsystem: <b>${esc(L.super)}</b> · folder: <b>${esc(L.dir)}</b> · <b>${L.size}</b> entities</div>`
    + `<div class="sec"><div class="legend-hint">operation history (${(L.ops||[]).length} ops, oldest→newest):</div><div class="hist">${hist||'—'}</div></div>`
    + `<div class="sec"><div class="legend-hint">depends on (calls/imports →):</div>${depRows(L.depends,'lane')}</div>`
    + `<div class="sec"><div class="legend-hint">members (${(L.members||[]).length}) — judge coherence:</div><div class="members">${members}</div></div>`;
}

function showCommit(c){
  const ls=[...(commitLanes[c.order]||new Set())];
  const list = ls.length
    ? ls.map(l=>{const op=laneOp[l][c.order];return `<div class="row" onclick="showLane('${l}')"><span class="sw" style="background:${laneColor(l)}"></span>${esc(lanes[l].label)} <span style="color:#5b6272">· ${op?op.type:'touched'}</span></div>`;}).join('')
    : '<div class="kv">— no significant lane (churn not surviving to HEAD, or docs)</div>';
  el('panel').innerHTML = `<h2>commit ${c.order} · ${c.short}</h2><p class="why">${esc(c.subject)}</p>`
    + `<div class="sec"><div class="legend-hint">${ls.length} operation(s) in this commit:</div>${list}</div>`;
}

el('expandAll').onclick = ()=>{ visibleSupers().forEach(s=>expanded.add(s.id)); render(); };
el('collapseAll').onclick = ()=>{ expanded.clear(); render(); };
el('showTail').onchange = render;
el('sizeSlider').oninput = e=>{ el('sizeVal').textContent=e.target.value; render(); };
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
    dest = _OUT / "hierarchy.html"
    dest.write_text(_TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
    print(f"wrote {dest}  (from {src.name}: {len(data['supers'])} subsystems, "
          f"{len(data['lanes'])} lanes × {len(data['commits'])} commits)")


if __name__ == "__main__":
    main()
