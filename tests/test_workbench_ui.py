"""The workbench webview's pure derivations (`editor/vscode/media/workbench.js`, the block between
the `// ---- model` markers), run under node against the real `ugh` output in
`editor/vscode/dev/fixtures/`."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_JS = _ROOT / "editor/vscode/media/workbench.js"
_FX = _ROOT / "editor/vscode/dev/fixtures"


def _fx(name: str):
    return json.loads((_FX / name).read_text(encoding="utf-8"))


def _run(body: str, **values):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    text = _JS.read_text(encoding="utf-8")
    model = text[text.index("// ---- model"):text.index("// ---- end model")]
    consts = "".join(f"const {k} = {json.dumps(v)};\n" for k, v in values.items())
    script = model + consts + f"console.log(JSON.stringify((() => {{\n{body}\n}})()));\n"
    res = subprocess.run([node, "-"], input=script, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _bike(body: str, **values):
    return _run(body, LOG=_fx("log.json"), STATUS=_fx("status.json"), **values)


# Helpers available to every snippet below.
_AT = """
const m = buildModel(LOG, STATUS, {});
const col = (sha) => colOf(m, sha);
const row = (key) => m.rows.find((r) => r.key === key);
const cell = (key, sha) => row(key).cells.get(col(sha));
"""


def test_saves_run_oldest_to_newest_and_stepless_saves_collapse_to_a_hairline():
    r = _bike(_AT + "return m.cols.map((c) => [c.short, c.x, c.w]);")
    shas = [c[0] for c in r]
    assert shas[0] == "e261747" and shas[-1] == "49bdf65"
    width = {sha: w for sha, _, w in r}
    assert width["e261747"] == 22 and width["c360a3d"] == 22
    assert width["1b37a42"] == 7, "an `sgt land` merge has no steps"
    assert width["49bdf65"] == 22, "the sync merge carries the pending choice, so it keeps a full column"
    assert all(r[i + 1][1] == r[i][1] + r[i][2] for i in range(len(r) - 1))


def test_rows_are_names_by_first_save_then_declared_names_then_unnamed():
    r = _bike(_AT + """
const open = buildModel(LOG, STATUS, { unnamedOpen: true });
return {
  rows: m.rows.map((x) => x.key),
  planCells: m.rows.filter((x) => x.kind === "plan").map((x) => x.cells.size),
  files: open.rows.filter((x) => x.kind === "file").map((x) => x.key),
  fileStepsNamed: open.rows.filter((x) => x.kind === "file").some((x) => x.steps.some((s) => s.name)),
  groupCellsOpen: open.rows.find((x) => x.kind === "group").cells.size,
};""")
    assert r["rows"] == [
        "name:hourly profile", "name:weekday split", "name:event days", "name:monthly trend",
        "name:side by side", "name:year table", "name:csv export", "name:date window",
        "plan:rider counts", "plan:weather overlay", "unnamed",
    ]
    assert r["planCells"] == [0, 0], "a declared name with no saves is an empty track"
    assert len(r["files"]) == 12 and not r["fileStepsNamed"]
    assert r["groupCellsOpen"] == 0, "open, the group's steps sit on its file rows instead"


def test_cell_tone_steps_with_the_count_and_out_steps_are_hollow():
    r = _bike(_AT + """
const st = (key, sha) => cellState(cell(key, sha).steps, null, null);
return {
  tones: [1, 2, 3, 4, 7, 8, 21].map(toneLevel),
  window: st("name:date window", "22496d8"),
  weekday: st("name:weekday split", "b9f66c7"),
  out: st("name:event days", "0c56354"),
};""")
    assert r["tones"] == [1, 2, 2, 3, 3, 4, 4]
    assert r["window"]["tone"] == 4 and r["window"]["a"] == 100 and not r["window"]["hollow"]
    assert r["weekday"]["tone"] == 2
    assert r["out"]["hollow"] and r["out"]["a"] == 0 and r["out"]["b"] == 0, "taken out by `ugh revert 0c56354`"


def test_a_pending_choice_marks_its_units_row_at_the_merge():
    r = _bike(_AT + """
const open = buildModel(LOG, STATUS, { unnamedOpen: true });
return { closed: m.choices[0], open: open.choices[0], merge: col("49bdf65") };""")
    assert r["closed"]["row"] == "unnamed" and r["closed"]["col"] == r["merge"]
    assert r["open"]["row"] == "file:bikecount/metrics.py"
    assert r["open"]["unit"] == "bikecount/metrics.py::yearly_summary"


def test_a_revert_preview_marks_what_goes_what_is_held_and_what_holds_it():
    r = _bike(_AT + """
const sel = stepsOf(m, "hourly profile").map((s) => s.key);
const marks = planMarks(m, PLAN, sel);
const st = (key, sha) => cellState(cell(key, sha).steps, marks, new Set(sel));
return {
  target: st("name:hourly profile", "196f7b6"),
  weekday: st("name:weekday split", "b9f66c7"),
  event: st("name:event days", "c360a3d"),
  window: st("name:date window", "22496d8"),
  monthly: st("name:monthly trend", "977e808"),
  words: previewWords(PLAN),
};""", PLAN=_fx("revert-name.json"))
    t = r["target"]
    assert t["gone"] and t["held"] and t["sel"]
    assert (t["a"], t["b"]) == (62.5, 100), "5 of 8 stay (held), 3 of 8 go"
    assert r["weekday"]["holder"] and r["event"]["holder"] and r["window"]["holder"]
    m = r["monthly"]
    assert not (m["gone"] or m["held"] or m["holder"] or m["sel"])
    assert r["words"] == {"go": "3 go", "held": "5 held", "whole": 13}


def test_the_whole_preview_takes_the_removal_set_across_saves():
    r = _bike(_AT + """
const sel = stepsOf(m, "hourly profile").map((s) => s.key);
const marks = planMarks(m, WHOLE, sel);
const gone = [...marks].filter(([, v]) => v.undone).map(([k]) => m.byKey.get(k).col);
return { n: gone.length, saves: [...new Set(gone)].map((c) => m.cols[c].short).sort(), words: previewWords(WHOLE) };""",
              WHOLE=_fx("revert-whole.json"))
    assert r["n"] == 13
    assert r["saves"] == ["196f7b6", "22496d8", "b9f66c7", "c360a3d"]
    assert r["words"]["whole"] == 0, "nothing larger to offer once the whole set is the preview"


def test_a_restore_maps_the_take_outs_steps_back_onto_the_hollow_cells():
    """`ugh restore 0c56354` answers with the steps of the take-out commit (43dafc6), which the log
    does not list, so the cells to mark are found by unit among the out steps."""
    r = _bike(_AT + """
const sel = stepsOf(m, "0c56354").map((s) => s.key);
const marks = planMarks(m, PLAN, sel);
return { verb: verbFor(m, sel), cell: cellState(cell("name:event days", "0c56354").steps, marks, null),
         marked: [...marks.keys()].every((k) => sel.includes(k)), words: previewWords(PLAN) };""",
              PLAN=_fx("restore-out.json"))
    assert r["verb"] == "restore"
    assert r["cell"]["gone"] and r["cell"]["b"] == 100 and not r["cell"]["hollow"]
    assert r["marked"]
    assert r["words"]["go"] == "6 come back"


def test_a_click_names_exactly_the_steps_it_shows():
    r = _bike(_AT + """
const open = buildModel(LOG, STATUS, { unnamedOpen: true });
const pick = (model, t) => {
  const a = addressFor(model, t);
  const named = new Set(a.addresses.flatMap((x) => stepsOf(model, x).map((s) => s.key)));
  return { addresses: a.addresses, exact: named.size === a.keys.length && a.keys.every((k) => named.has(k)), n: a.keys.length };
};
const last = m.cols.length - 1;
return {
  cell: pick(m, { kind: "cell", row: "name:event days", col: col("c360a3d") }),
  row: pick(m, { kind: "row", row: "name:event days" }),
  column: pick(m, { kind: "col", col: col("c360a3d") }),
  range: pick(m, { kind: "range", from: col("b9f66c7"), to: col("c360a3d") }),
  toHead: pick(m, { kind: "range", from: col("22496d8"), to: last }),
  firstSave: pick(m, { kind: "cell", row: "unnamed", col: col("e261747") }),
  fileCell: pick(open, { kind: "cell", row: "file:bikecount/metrics.py", col: col("e261747") }),
  hairline: pick(m, { kind: "col", col: col("1b37a42") }),
  before: m.cols[col("b9f66c7") - 1].short,
  before2: m.cols[col("22496d8") - 1].short,
};""")
    assert r["cell"]["addresses"] == ["event days@c360a3d"] and r["cell"]["n"] == 3
    assert r["row"]["addresses"] == ["event days"] and r["row"]["n"] == 13
    assert r["column"]["addresses"] == ["c360a3d"]
    assert r["range"]["addresses"] == [r["before"] + "..c360a3d"]
    assert r["toHead"]["addresses"] == [r["before2"] + ".."]
    assert r["firstSave"]["addresses"] == ["e261747"], "every step of the first save is unnamed"
    assert r["fileCell"]["addresses"] == ["bikecount/metrics.py@e261747"]
    assert r["hairline"]["addresses"] == []
    assert all(r[k]["exact"] for k in ("cell", "row", "column", "range", "toHead", "firstSave", "fileCell"))


def test_only_the_latest_reply_of_each_kind_is_read():
    r = _run("""
const q = requestLedger();
const a = q.next("preview"), b = q.next("preview"), s = q.next("show");
const out = [q.accept(a), q.accept(b), q.accept(b), q.accept(s)];
const c = q.next("preview"), ap = q.next("apply");
q.drop("preview");
out.push(q.accept(c), q.accept(ap));
return out;""")
    assert r == [None, "preview", None, "show", None, "apply"]


def test_arrow_keys_move_the_selection_from_cell_to_cell():
    r = _bike(_AT + """
const go = (cur, ...keys) => keys.reduce((c, k) => moveCursor(m, c, k), cur);
const at = (c) => c && [c.row, c.col == null ? null : m.cols[c.col].short];
const top = go(null, "ArrowDown");
return {
  start: at(top),
  right: at(go(top, "ArrowRight")),
  down: at(go(top, "ArrowRight", "ArrowDown")),
  back: at(go(top, "ArrowRight", "ArrowLeft")),
  up: at(go(top, "ArrowUp")),
  skipPlan: at(go({ row: "name:date window", col: col("22496d8") }, "ArrowDown")),
  along: at(go({ row: "name:event days", col: col("66ca5b9") }, "ArrowRight", "ArrowRight")),
  end: at(go({ row: "name:event days", col: col("66ca5b9") }, "ArrowRight", "ArrowRight", "ArrowRight")),
};""")
    assert r["start"] == ["name:hourly profile", None]
    assert r["right"] == ["name:hourly profile", "196f7b6"]
    assert r["down"] == ["name:weekday split", "b9f66c7"]
    assert r["back"] == ["name:hourly profile", None]
    assert r["up"] == ["name:hourly profile", None]
    assert r["skipPlan"][0] == "unnamed", "declared names have nothing to select"
    assert r["along"] == ["name:event days", "c360a3d"]
    assert r["end"] == ["name:event days", "c360a3d"]


def test_the_playhead_settles_column_by_column():
    r = _bike(_AT + """
const end = (k) => m.cols[k].x + m.cols[k].w;
const k = col("c360a3d");
const mid = (end(k) + end(k + 1)) / 2;
return [detent(m, mid + 1, k), detent(m, mid + 6, k), detent(m, end(k + 1), k), detent(m, -50, null)];""")
    assert r[0] == r[1] - 1, "just past halfway it holds"
    assert r[2] == r[1]
    assert r[3] == 0


def test_a_landed_take_out_and_a_refusal_each_read_as_one_line():
    applied = {
        "ok": True, "applied": True, "verb": "revert",
        "undone": [{}] * 3, "held": [{}] * 5,
        "check": {"judge": "check", "passed": True, "rounds": 1, "output": "ok\n"},
        "observed": {"head": {"verdict": "pass"}, "delta": {"added": 1, "removed": 1}, "saves_delta": None, "reverses": None},
    }
    r = _run("""
return {
  words: resultWords(APPLIED),
  gone: resultWords(Object.assign({}, APPLIED, { observed: { delta: { added: 0, removed: 2 }, reverses: true } })),
  dirty: refusalOf({ ok: false, error: "revert would overwrite uncommitted changes: ['a.py']" }),
  failed: refusalOf({ ok: true, applied: false, check: { judge: "check", passed: false, output: "\\nFAIL: test_totals\\n" } }),
  landed: refusalOf(APPLIED),
};""", APPLIED=applied)
    assert r["words"] == ["3 out, 5 held", "check passed", "output +1 −1"]
    assert r["gone"][-1] == "gone, as the check sees it"
    assert r["dirty"].startswith("revert would overwrite uncommitted changes")
    assert r["failed"] == "Check failed: FAIL: test_totals"
    assert r["landed"] is None


def test_a_landed_take_out_flashes_only_the_cells_whose_steps_changed():
    log = _fx("log.json")
    after = json.loads(json.dumps(log))
    for save in after["saves"]:
        for st in save["steps"]:
            if st["address"] == "bikecount/pages/hourly.py::ORDER@196f7b6":
                st["out"] = True
    r = _run("""
const a = buildModel(LOG, STATUS, {}), b = buildModel(AFTER, STATUS, {});
return outChanges(a, b).map((k) => b.byKey.get(k).address);""",
             LOG=log, AFTER=after, STATUS=_fx("status.json"))
    assert r == ["bikecount/pages/hourly.py::ORDER@196f7b6"]


def test_the_inline_diff_keeps_changed_lines_with_one_line_of_context():
    show = _fx("show-step.json")["steps"][0]
    r = _run("""
const ops = lineDiff(STEP.before, STEP.after);
const { lines, more } = hunks(ops, 30);
return { kinds: lines.map((l) => l.t), more, first: lines[0].s };""", STEP=show)
    assert "-" in r["kinds"] and "+" in r["kinds"]
    changed = [i for i, k in enumerate(r["kinds"]) if k in "+-"]
    assert all(r["kinds"][i] in "+- " for i in range(changed[0], changed[-1] + 1) if r["kinds"][i] != "gap")
    assert r["first"].startswith("def side_totals"), "leading blank lines of the unit are not shown"
    assert r["more"] == 0


def test_name_hue_is_the_function_the_blame_gutter_uses():
    r = _run('return ["event days", "hourly profile", "event days"].map((l) => Math.round(hueOf(l) * 10) / 10);')
    assert r[0] == r[2] == 353.3
    assert 0 <= r[1] < 360 and r[1] != r[0]
