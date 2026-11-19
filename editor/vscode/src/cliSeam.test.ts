// The argv each verb is run with, and what a failed run is reported as. Run: npm test (node's own
// runner and type stripping, Node >= 22.6).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ExecFailure } from "./cliSeam.ts";
import { args, failureDetail, isSpawnFailure } from "./cliSeam.ts";

test("a preview is --emit, an apply is --yes, and the addresses come after --", () => {
  assert.deepEqual(args.preview("revert", ["event days"]), ["revert", "--emit", "--json", "--", "event days"]);
  assert.deepEqual(args.apply("revert", ["a.py::f", "3f2a1c0"]), ["revert", "--yes", "--json", "--", "a.py::f", "3f2a1c0"]);
  assert.ok(!args.apply("restore", ["x"]).includes("--emit"));
});

test("--whole goes to revert only, before the addresses", () => {
  assert.deepEqual(args.preview("revert", ["x"], true), ["revert", "--emit", "--json", "--whole", "--", "x"]);
  assert.deepEqual(args.apply("restore", ["x"], true), ["restore", "--yes", "--json", "--", "x"]);
});

test("an address or a label that starts with a dash is never read as a flag", () => {
  assert.deepEqual(args.show("-x"), ["show", "--json", "--", "-x"]);
  assert.deepEqual(args.name("a.py::f", "--whole"), ["name", "--json", "--", "a.py::f", "--whole"]);
  assert.deepEqual(args.rename("-old", "new name"), ["rename", "--json", "--", "-old", "new name"]);
  assert.deepEqual(args.find("-v"), ["find", "--json", "--", "-v"]);
});

test("save carries -m and --as only when given", () => {
  assert.deepEqual(args.save(), ["save", "--json"]);
  assert.deepEqual(args.save("add input validation", "checkout"),
    ["save", "--json", "-m", "add input validation", "--as", "checkout"]);
  assert.deepEqual(args.save(undefined, "checkout"), ["save", "--json", "--as", "checkout"]);
});

test("resolve shows both versions without a flag and settles with one", () => {
  assert.deepEqual(args.versions("m.py::f"), ["resolve", "--json", "--", "m.py::f"]);
  assert.deepEqual(args.resolve("m.py::f", "theirs"), ["resolve", "--theirs", "--json", "--", "m.py::f"]);
  assert.deepEqual(args.resolve("m.py::f", "apply"), ["resolve", "--apply", "--json", "--", "m.py::f"]);
});

test("sync and push name a remote and branch only when given", () => {
  assert.deepEqual(args.sync(), ["sync", "--json"]);
  assert.deepEqual(args.sync("origin", "main"), ["sync", "--json", "--", "origin", "main"]);
  assert.deepEqual(args.push("origin"), ["push", "--json", "--", "origin"]);
});

test("the render panel's write previews onto a directory", () => {
  assert.deepEqual(args.emitTo("revert", "22496d8..", "/tmp/t"),
    ["revert", "--emit", "--json", "--out", "/tmp/t", "--", "22496d8.."]);
});

const BIN = "/usr/local/bin/ugh";
const detail = (err: ExecFailure) => failureDetail(err, BIN, 120_000);

test("a JSON refusal reports its one line, not the JSON", () => {
  assert.equal(detail({ code: 1, stdout: JSON.stringify({ ok: false, error: "working tree has uncommitted changes" }) }),
    "working tree has uncommitted changes");
  assert.equal(detail({ code: 2, stdout: JSON.stringify({ ok: false, message: "no such address: 'nope'", hits: [] }) }),
    "no such address: 'nope'");
});

test("a dispatch error comes from stderr", () => {
  assert.match(detail({ code: 2, stdout: "", stderr: "ugh: unknown verb `bogus`.\n" }), /unknown verb/);
});

test("a spawn failure names the binary and is told apart from a refusal", () => {
  const missing: ExecFailure = { code: "ENOENT", stdout: "", stderr: "", message: "spawn ENOENT" };
  assert.ok(isSpawnFailure(missing));
  assert.match(detail(missing), /could not run '\/usr\/local\/bin\/ugh'/);
  assert.equal(isSpawnFailure({ code: 1, stdout: "{}", stderr: "" }), false);
});

test("a timeout says so, whatever partial output arrived", () => {
  assert.match(detail({ killed: true, code: null, stdout: "{\"half\":" }), /did not answer within 120s/);
});

test("a failure with nothing on either stream still says something", () => {
  assert.equal(detail({ code: 1, stdout: "", stderr: "", message: "" }), "failed with no output");
});
