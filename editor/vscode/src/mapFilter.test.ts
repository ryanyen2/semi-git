// The listing drop rule (`mapFilter.ts`), which the sidebar tree and the feature quick-pick share
// with the terminal map and the workbench timeline. Run: npm test.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { MapNode } from "./types.ts";
import { hasOwnSymbols, isVisibleNode } from "./mapFilter.ts";

function node(id: string, children: string[], own?: string[]): MapNode {
  return {
    id, label: id, kind: children.length ? "subsystem" : "feature", parent: null, children,
    size: 1, op_count: 1, dir: "", members: [], own_symbols: own, why: "", split_reason: null,
  };
}

test("a husk leaf is dropped and a real one is kept", () => {
  assert.equal(hasOwnSymbols(node("husk", [], [])), false);
  assert.equal(hasOwnSymbols(node("real", [], ["a.py::f"])), true);
});

test("a node with no own_symbols field is unknown, not empty", () => {
  // An older projection did not carry the field; dropping every node would empty the whole tree.
  assert.equal(hasOwnSymbols(node("old", [])), true);
});

test("a subsystem is visible only while a visible descendant remains", () => {
  const nodes = [node("S", ["deep"]), node("deep", ["husk", "real"]),
                 node("husk", [], []), node("real", [], ["a.py::f"])];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const nodeOf = (id: string) => byId.get(id);
  assert.equal(isVisibleNode(byId.get("S")!, nodeOf), true);   // reaches `real` two levels down
  byId.set("real", node("real", [], []));                      // now both leaves are husks
  assert.equal(isVisibleNode(byId.get("S")!, nodeOf), false);
});

test("a dangling child id does not make a subsystem visible", () => {
  const s = node("S", ["gone"]);
  assert.equal(isVisibleNode(s, () => undefined), false);
});
