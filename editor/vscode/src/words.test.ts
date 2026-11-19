// What the take-out, bring-back and undo dialogs say. Run: npm test.

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { test } from "node:test";

import type { Plan, UndoPreview } from "./types.ts";
import { previewWords, resultWords, undoWords } from "./words.ts";

const fixture = (name: string): Plan => JSON.parse(fs.readFileSync(path.join("dev", "fixtures", name), "utf8"));

test("a take-out preview counts what goes, what is held and why, and offers Whole when it is larger", () => {
  const plan = fixture("revert-name.json");
  const say = previewWords(plan);
  assert.equal(say.message, "Take out hourly profile");
  assert.match(say.detail, new RegExp(`^${plan.undone.length} steps go$`, "m"));
  assert.match(say.detail, new RegExp(`^${plan.held.length} held: .*used`, "m"));
  assert.match(say.detail, new RegExp(`^Whole takes ${plan.removal_set!.steps} steps in ${plan.removal_set!.saves.length} saves$`, "m"));
  assert.match(say.detail, /HEAD not observed/);
  assert.ok(say.apply && say.whole);
});

test("a --whole preview holds nothing and offers no second Whole", () => {
  const say = previewWords(fixture("revert-whole.json"));
  assert.doesNotMatch(say.detail, /held|Whole/);
  assert.equal(say.whole, false);
});

test("a bring-back preview says what comes back", () => {
  const plan = fixture("restore-out.json");
  const say = previewWords(plan);
  assert.equal(say.message, `Bring back ${plan.target}`);
  assert.match(say.detail, new RegExp(`^${plan.undone.length} steps come back$`, "m"));
  assert.equal(say.whole, false);
});

test("an applied take-out says the steps, the check and what the check observed", () => {
  const plan: Plan = {
    ...fixture("revert-whole.json"),
    applied: true,
    check: { judge: "check", passed: true, rounds: 1, output: "" },
    observed: { head: { verdict: "pass", lines_recorded: true }, delta: { added: 0, removed: 3 },
      saves_delta: { added: 3, removed: 0 }, reverses: true },
  };
  assert.equal(resultWords(plan), `Took out ${plan.undone.length} steps · check passes · output 0 added, 3 removed, gone as the check sees it`);
});

test("an apply that changed nothing says why, in the planner's words when it has them", () => {
  const plan = fixture("revert-name.json");
  assert.equal(resultWords({ ...plan, check: { judge: "check", passed: false }, message: "" }), "Nothing applied: check fails");
  assert.equal(resultWords({ ...plan, message: "the check fails in code this plan does not undo; nothing applied" }),
    "The check fails in code this plan does not undo; nothing applied");
  assert.equal(resultWords({ ...plan, undone: [], message: "" }), `Nothing applied: ${plan.held.length} held`);
});

const undo = (over: Partial<UndoPreview>): UndoPreview => ({
  applied: false, kind: "commit", ok: true, message: "takes out the save add a csv download link",
  undone: 6, held: 0, steps: ["bikecount/server.py::daily_csv@a656515"], ...over,
});

test("the undo dialog names what it reverses and the steps that go", () => {
  const say = undoWords(undo({}));
  assert.equal(say.kind, "confirm");
  assert.match(say.detail, /^Takes out the save add a csv download link$/m);
  assert.match(say.detail, /^6 steps go$/m);
  assert.match(say.detail, /daily_csv@a656515/);
});

test("an undo of a name has no steps to list", () => {
  const say = undoWords(undo({ kind: "names", message: "restores the names from before the rename (no code changes)", undone: 0, steps: [] }));
  assert.equal(say.detail, "Restores the names from before the rename (no code changes)");
});

test("a refused undo offers no button, and an empty log is not a dialog", () => {
  assert.equal(undoWords(undo({ ok: false, message: "the check fails" })).kind, "refused");
  assert.equal(undoWords(undo({ kind: null })).kind, "nothing");
});
