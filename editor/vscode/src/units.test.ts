// The line-to-step lookup the hover, the inlay hints, the blame tint and the diagnostics share.
// Run: npm test.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import type { DetailStep, ShowView } from "./types.ts";
import { ago, headLine, kindOf, locate, nameOf, unitAt } from "./units.ts";

function step(unit: string, sha: string, after: string | null, lines: [number, number] | null,
  over: Partial<DetailStep> = {}): DetailStep {
  const [file, def] = unit.split("::");
  const address = def && !def.startsWith("__") ? `${unit}@${sha}` : `${file}@${sha}`;
  return { address, kind: "change", name: null, out: false, unit, before: null, after, lines, ...over };
}

const HEAD_TEXT = '"""Doc."""\n\n\ndef a():\n    return 1\n\n\ndef b(x):\n    return x\n';
const SHOW: ShowView = {
  ok: true,
  address: "m.py",
  saves: [],
  plan: undefined as never,
  steps: [
    step("m.py::__residue__::\u0000HEAD\u0000", "1111111", '"""Doc."""\n', [1, 1]),
    step("m.py::a", "1111111", "\n\ndef a():\n    return 1\n", [2, 5], { name: "alpha" }),
    step("m.py::b", "1111111", "\n\ndef b(x):\n    return 0\n", [6, 9], { name: "beta" }),
    step("m.py::b", "2222222", "\n\ndef b(x):\n    return x\n", [6, 9]),
    step("m.py::gone", "1111111", null, null),
  ],
};

const at = (text: string, line: number) => unitAt(locate(SHOW, text), line)?.unit;

test("a document that is HEAD places every unit at HEAD's lines", () => {
  const units = locate(SHOW, HEAD_TEXT);
  assert.deepEqual(units.map((u) => [u.unit, u.first, u.last]), [
    ["m.py::__residue__::\u0000HEAD\u0000", 1, 1],
    ["m.py::a", 2, 5],
    ["m.py::b", 6, 9],
  ]);
});

test("a line maps to its unit, blank lines before a definition included", () => {
  assert.equal(at(HEAD_TEXT, 3), "m.py::a");
  assert.equal(at(HEAD_TEXT, 8), "m.py::b");
  assert.equal(at(HEAD_TEXT, 1), "m.py::__residue__::\u0000HEAD\u0000");
  assert.equal(at(HEAD_TEXT, 42), undefined);
});

test("unsaved lines above a definition move it, found by its own text", () => {
  const text = "import os\nimport re\n" + HEAD_TEXT;
  assert.equal(at(text, 6), "m.py::a");
  assert.equal(at(text, 10), "m.py::b");
  assert.deepEqual(locate(SHOW, text).find((u) => u.unit === "m.py::b")!.first, 8);
});

test("a definition whose body was edited is found by its signature line", () => {
  const text = HEAD_TEXT.replace("return x", "y = x * 2\n    return y");
  const b = locate(SHOW, text).find((u) => u.unit === "m.py::b")!;
  assert.equal(b.first, 6);
  assert.equal(b.last, 10, "a body that grew keeps its tail");
  assert.equal(at(text, 8), "m.py::b");
});

test("a definition rewritten past recognition is left out, not placed on the wrong lines", () => {
  const text = HEAD_TEXT.replace("def b(x):\n    return x", "def renamed(y):\n    pass");
  assert.equal(locate(SHOW, text).find((u) => u.unit === "m.py::b"), undefined);
});

test("HEAD's text may be an older step's: after a sync that kept ours, the newest step is theirs", () => {
  const theirs = step("m.py::b", "3333333", "\n\ndef b(x):\n    return -x\n", [6, 9]);
  const show = { ...SHOW, steps: [...SHOW.steps, theirs] } as ShowView;
  const text = "# a new first line\n" + HEAD_TEXT;
  assert.equal(locate(show, text).find((u) => u.unit === "m.py::b")!.first, 7);
});

test("a unit's name is its newest named step that is in the code", () => {
  const b = locate(SHOW, HEAD_TEXT).find((u) => u.unit === "m.py::b")!;
  assert.equal(nameOf(b.steps), "beta");
  const out = [step("m.py::c", "1", "x\n", [1, 1], { name: "old" }), step("m.py::c", "2", "y\n", [1, 1], { name: "new", out: true })];
  assert.equal(nameOf(out), "old");
  assert.equal(nameOf([step("m.py::c", "1", "x\n", [1, 1])]), null);
});

test("the head line is where the name is written, past the blank lines", () => {
  const a = locate(SHOW, HEAD_TEXT).find((u) => u.unit === "m.py::a")!;
  assert.equal(headLine(a, HEAD_TEXT.split("\n")), 4);
});

test("unit kinds: a definition, a file head, a whole file", () => {
  assert.equal(kindOf("m.py::a"), "definition");
  assert.equal(kindOf("m.py::__import__::os"), "head");
  assert.equal(kindOf("README.md"), "file");
});

test("real show output: every unit lands on HEAD's lines, and two lines inserted above move them all", () => {
  const show: ShowView = JSON.parse(fs.readFileSync(path.join("dev", "fixtures", "show-file.json"), "utf8"));
  assert.ok(show.ok);
  // The file at HEAD, rebuilt from the units in line order. Each unit's HEAD text is a step's
  // `after` with as many lines as its span; any of them gives a document in which it is exact.
  const spans = new Map<string, { lines: [number, number]; text: string }>();
  for (const st of show.steps) {
    const n = st.lines && st.after ? st.after.split("\n").length - (st.after.endsWith("\n") ? 1 : 0) : -1;
    if (st.lines && n === st.lines[1] - st.lines[0] + 1) spans.set(st.unit, { lines: st.lines, text: st.after! });
  }
  const ordered = [...spans.values()].sort((a, b) => a.lines[0] - b.lines[0]);
  const text = ordered.map((s) => s.text).join("");
  const units = locate(show, text);
  assert.equal(units.length, spans.size);
  for (const u of units) assert.deepEqual([u.first, u.last], spans.get(u.unit)!.lines, u.unit);
  const moved = locate(show, "import sys\nimport os\n" + text);
  for (const u of moved) assert.equal(u.first, spans.get(u.unit)!.lines[0] + 2, u.unit);
});

test("ago reads like a person", () => {
  const now = Date.parse("2026-11-19T12:00:00Z");
  assert.equal(ago("2026-11-19T11:59:30Z", now), "just now");
  assert.equal(ago("2026-11-19T11:00:00Z", now), "1 hour ago");
  assert.equal(ago("2026-11-18T10:00:00Z", now), "yesterday");
  assert.equal(ago("2026-11-12T12:00:00Z", now), "7 days ago");
  assert.equal(ago("2026-08-24T13:44:14+00:00", now), "2 months ago");
  assert.equal(ago(null, now), "");
});
