// What the undo confirm dialog says (`undoConfirm.ts`). Run: npm test.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { UndoPreview } from "./types.ts";
import { undoConfirmText } from "./undoConfirm.ts";

function pv(over: Partial<UndoPreview>): UndoPreview {
  return {
    applied: false, ok: true, kind: "ideal_edit", message: "re-materializes the ideal from before the last edit",
    restored: [], dropped: [], symbols: [], ...over,
  };
}

test("the dialog names the operation, not just the fact that there is one", () => {
  // The whole of this dialog used to be "Undo the last operation?", which asks for consent to
  // reverse something without saying what -- so the answer was a guess about what you last did.
  const t = undoConfirmText(pv({ restored: ["o1", "o2"], symbols: ["a.py::bar"] }));
  assert.equal(t.kind, "confirm");
  assert.match(t.message, /re-materializes the ideal from before the last edit/);
  assert.match(t.detail, /Brings back 2 edits\./);
  assert.match(t.detail, /Touches a\.py::bar/);
});

test("one edit is not '1 edits'", () => {
  const t = undoConfirmText(pv({ restored: ["o1"], dropped: ["o2"] }));
  assert.match(t.detail, /Brings back 1 edit\./);
  assert.match(t.detail, /Drops 1 edit made since\./);
});

test("a line with nothing to say is not drawn", () => {
  // Every clause is conditional: an undo that drops nothing must not print "Drops 0 edits".
  const t = undoConfirmText(pv({ restored: ["o1"] }));
  assert.doesNotMatch(t.detail, /Drops/);
  assert.doesNotMatch(t.detail, /Touches/);
});

test("a long symbol list ends in an ellipsis, not a count it cannot know", () => {
  // `symbols` arrives capped upstream (12), so "+N more" would be a number this payload has no
  // way to be right about.
  const many = Array.from({ length: 9 }, (_, i) => `a.py::f${i}`);
  const t = undoConfirmText(pv({ symbols: many }));
  assert.match(t.detail, /Touches a\.py::f0, a\.py::f1, a\.py::f2, a\.py::f3, a\.py::f4, a\.py::f5 …$/);
  assert.doesNotMatch(t.detail, /more/);
});

test("a refusal the guard already decided is stated, and offers no button that would hit it", () => {
  const t = undoConfirmText(pv({ ok: false, message: "would drop work committed after that edit: a.py::baz" }));
  assert.equal(t.kind, "refused");
  assert.match(t.message, /a\.py::baz/);
});

test("an empty log is not a dialog at all", () => {
  const t = undoConfirmText(pv({ kind: null, message: "nothing to undo" }));
  assert.equal(t.kind, "nothing");
  assert.equal(t.message, "nothing to undo");
});
