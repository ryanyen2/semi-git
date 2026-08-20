// The extension had no tests at all, which is how F125 survived eight months in six call sites and
// how F126 survived in the one place every failure message passes through. These cover the two pure
// decisions in `cliSeam.ts`; anything needing the extension host is still uncovered.
//
// Run: npm test  (node's built-in runner and type-stripping -- no dependencies. Needs Node >= 22.6.)

import assert from "node:assert/strict";
import { test } from "node:test";

// `ExecFailure` is imported separately because node strips types without resolving them: a type
// in a value import survives into the runtime lookup and fails as a missing export.
import type { ExecFailure } from "./cliSeam.ts";
import { failureDetail, isSpawnFailure, mutationArgs } from "./cliSeam.ts";

const BIN = "/usr/local/bin/sgt";
const detail = (err: ExecFailure) => failureDetail(err, BIN, 30_000);

test("revert and restore carry the --yes the tty gate needs", () => {
  assert.deepEqual(mutationArgs(["revert", "f-1234"]), ["revert", "f-1234", "--yes"]);
  assert.deepEqual(mutationArgs(["restore", "op-9"]), ["restore", "op-9", "--yes"]);
  // Through the shape `applyMutation` builds, which is how two of the six call sites arrive.
  assert.deepEqual(mutationArgs(["revert", "f-1", "--keep", "op-2,op-3"]),
                   ["revert", "f-1", "--keep", "op-2,op-3", "--yes"]);
});

test("verbs that would reject --yes do not get it", () => {
  // A parse error, not a refusal: only the two ideal-edit verbs accept the flag.
  for (const argv of [["init"], ["feature", "rename", "f-1", "x"], ["save", "-m", "x"], ["fulfill"]]) {
    assert.deepEqual(mutationArgs(argv), argv, argv.join(" "));
  }
});

test("--yes is not doubled when a caller already passed it", () => {
  assert.deepEqual(mutationArgs(["revert", "f-1", "--yes"]), ["revert", "f-1", "--yes"]);
});

test("a semantic refusal is read off stdout, not lost to err.message", () => {
  // F126, measured against the real CLI: exit 1, the whole explanation on stdout, stderr empty.
  const d = detail({
    code: 1,
    stdout: "✗ [revert] nope::nothing — symbol 'nope::nothing' is not live in the ideal\n",
    stderr: "",
    message: "Command failed: /usr/local/bin/sgt revert nope::nothing\n",
  });
  assert.match(d, /not live in the ideal/);
  assert.doesNotMatch(d, /Command failed/);
});

test("a dispatch error still comes from stderr, which is where the CLI puts it", () => {
  const d = detail({
    code: 2,
    stdout: "",
    stderr: "sgt: unknown verb `bogusverb`.\n  run `sgt help` for the verb surface.\n",
    message: "Command failed",
  });
  assert.match(d, /unknown verb/);
});

test("a failing --json read reports its field, not the tail of the JSON object", () => {
  // The regression the first version of this fix shipped: every read passes --json, so tailing
  // stdout would have shown the user the closing lines of an object.
  const payload = JSON.stringify(
    { applied: false, ok: false, verb: "revert", removed: [], added: [],
      message: "symbol 'nope::nothing' is not live in the ideal" }, null, 2);
  const d = detail({ code: 1, stdout: payload, stderr: "", message: "Command failed" });
  assert.equal(d, "symbol 'nope::nothing' is not live in the ideal");
});

test("a _fail_json envelope reports its error field", () => {
  const payload = JSON.stringify({ ok: false, error: "cannot overwrite uncommitted changes in m.py" });
  const d = detail({ code: 1, stdout: payload, stderr: "", message: "Command failed" });
  assert.match(d, /uncommitted changes/);
});

test("a long prose refusal is capped but keeps its last lines", () => {
  const stdout = [...Array(40).keys()].map((i) => `preview line ${i}`).join("\n") +
    "\n  not applied — this was the preview. re-run with --yes to apply.\n";
  const d = detail({ code: 2, stdout, stderr: "", message: "Command failed" });
  assert.match(d, /not applied/);
  assert.ok(d.split("\n").length <= 12, `${d.split("\n").length} lines survived the cap`);
  assert.doesNotMatch(d, /preview line 0\b/);
});

test("a spawn failure is classified apart from a CLI that ran and refused", () => {
  const missing: ExecFailure = { code: "ENOENT", stdout: "", stderr: "", message: "spawn ENOENT" };
  assert.ok(isSpawnFailure(missing));
  assert.match(detail(missing), /could not run the sgt CLI at '\/usr\/local\/bin\/sgt'/);
  // A refusal carries output, so it must never be mistaken for a bad binary path -- the two need
  // completely different fixes and the wrong one sends the user to their settings.
  assert.equal(isSpawnFailure({ code: 1, stdout: "✗ nope", stderr: "", message: "Command failed" }), false);
});

test("a timeout says so rather than reporting whatever partial output arrived", () => {
  const d = detail({ killed: true, code: null as unknown as number, stdout: "half a tree", stderr: "", message: "killed" });
  assert.match(d, /timed out after 30000ms/);
  assert.ok(!isSpawnFailure({ killed: true, code: "ENOENT" }));
});

test("a failure with nothing on either stream still says something", () => {
  assert.equal(detail({ code: 1, stdout: "", stderr: "", message: "" }), "failed with no output");
});
