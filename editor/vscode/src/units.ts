// From `ugh show <path> --json` to the units of an open document: which lines each unit spans in
// the text as it is now, which unit a line falls in, and the name a unit carries. Pure, so
// `units.test.ts` runs it under `node --test`.
//
// `show` gives each unit's lines at HEAD. A file with unsaved edits has moved since, so a unit is
// placed by its own text: where HEAD put it if that still matches, else wherever that text now
// is, else at its signature line when only the body changed. A unit found none of these ways is
// left out rather than placed on the wrong lines.

import type { DetailStep, ShowView } from "./types.ts";

export type UnitKind = "definition" | "head" | "file";

export interface Unit {
  /** `path::def`, or `path` for a whole file. File-head units have no address of their own. */
  unit: string;
  kind: UnitKind;
  /** The definition's own name, or the file's path. */
  label: string;
  /** Oldest save first, as `show` lists them. */
  steps: DetailStep[];
  /** 1-based, inclusive, in the document. */
  first: number;
  last: number;
}

export function kindOf(unit: string): UnitKind {
  const sep = unit.indexOf("::");
  if (sep < 0) return "file";
  const rest = unit.slice(sep + 2);
  return rest.startsWith("__import__") || rest.startsWith("__residue__") ? "head" : "definition";
}

/** The name on the newest step that carries one and is in the code. */
export function nameOf(steps: DetailStep[]): string | null {
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].name && !steps[i].out) return steps[i].name;
  }
  return null;
}

const lf = (s: string) => s.replace(/\r\n/g, "\n");

function lineCount(text: string): number {
  const n = text.split("\n").length;
  return text.endsWith("\n") ? n - 1 : n;
}

/** The first 1-based line where `text` starts at the start of a line of `doc`. */
function find(doc: string, text: string): number | undefined {
  for (let i = doc.indexOf(text); i >= 0; i = doc.indexOf(text, i + 1)) {
    if (i === 0 || doc[i - 1] === "\n") return doc.slice(0, i).split("\n").length;
  }
  return undefined;
}

/** The units of `show` that are in HEAD's tree, placed in `docText`. */
export function locate(show: ShowView, docText: string): Unit[] {
  if (!show.ok) return [];
  const doc = lf(docText);
  const docLines = doc.split("\n");
  const byUnit = new Map<string, DetailStep[]>();
  for (const st of show.steps) {
    if (!st.lines) continue;
    const list = byUnit.get(st.unit) ?? [];
    list.push(st);
    byUnit.set(st.unit, list);
  }

  const end = doc.endsWith("\n") ? docLines.length - 1 : docLines.length;
  // `length` is null for a unit found by its signature: its body changed, so it runs to the next.
  const placed: { unit: Unit; length: number | null }[] = [];
  for (const [unit, steps] of byUnit) {
    const headFirst = steps[0].lines![0];
    // HEAD's text is one of the steps' `after`: usually the newest, but after a sync that kept ours
    // the newest step is theirs.
    const texts = [...new Set(steps.map((s) => s.after).filter((t): t is string => !!t).reverse())].map(lf);
    let first: number | undefined;
    let length: number | null = null;
    for (const t of texts) {
      if (docLines.slice(headFirst - 1, headFirst - 1 + lineCount(t)).join("\n") + "\n" === t.replace(/\n?$/, "\n")) {
        [first, length] = [headFirst, lineCount(t)];
        break;
      }
    }
    for (const t of first === undefined ? texts : []) {
      const at = find(doc, t);
      if (at !== undefined) {
        [first, length] = [at, lineCount(t)];
        break;
      }
    }
    if (first === undefined && texts.length) {
      const lines = texts[0].split("\n");
      const lead = lines.findIndex((l) => l.trim());
      const hits = lead < 0 ? [] : docLines.flatMap((l, i) => (l === lines[lead] ? [i + 1 - lead] : []));
      if (hits.length) first = hits.reduce((a, b) => (Math.abs(b - headFirst) < Math.abs(a - headFirst) ? b : a));
    }
    if (first === undefined || first < 1) continue;
    const kind = kindOf(unit);
    const label = kind === "file" ? unit : unit.slice(unit.indexOf("::") + 2);
    placed.push({ unit: { unit, kind, label, steps, first, last: first }, length });
  }

  placed.sort((a, b) => a.unit.first - b.unit.first);
  return placed.map(({ unit, length }, i) => {
    const next = placed[i + 1]?.unit.first ?? end + 1;
    unit.last = Math.max(unit.first, Math.min(length === null ? next - 1 : unit.first + length - 1, next - 1, end));
    return unit;
  });
}

/** The unit a 1-based line falls in. */
export function unitAt(units: Unit[], line: number): Unit | undefined {
  return units.find((u) => u.first <= line && line <= u.last);
}

/** The definition's first line that is not blank: where its name is written. */
export function headLine(unit: Unit, docLines: string[]): number {
  for (let l = unit.first; l <= unit.last; l++) {
    if (docLines[l - 1]?.trim()) return l;
  }
  return unit.first;
}

/** "3 days ago", from an ISO date. */
export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "";
  const s = Math.max(0, (now - Date.parse(iso)) / 1000);
  const say = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  if (s < 60) return "just now";
  if (s < 3600) return say(Math.floor(s / 60), "minute");
  if (s < 86400) return say(Math.floor(s / 3600), "hour");
  const d = Math.floor(s / 86400);
  if (d < 2) return "yesterday";
  if (d < 14) return say(d, "day");
  if (d < 60) return say(Math.floor(d / 7), "week");
  if (d < 365) return say(Math.floor(d / 30), "month");
  return say(Math.floor(d / 365), "year");
}
