// The render panel's CSP is the part that fails invisibly. A policy missing `frame-src` does not
// raise anything the extension can catch -- the iframe is simply blocked, and the panel shows an
// empty box that reads exactly like a dev server that has not finished booting. These pin the
// policy to the URL actually being loaded, so the two cannot drift.
//
// Run: npm test  (node's built-in runner and type-stripping -- no dependencies. Needs Node >= 22.6.)

import assert from "node:assert/strict";
import { test } from "node:test";

import { bootHtml, devCommand, errorHtml, frameHtml, plain } from "./renderSeam.ts";

const CSP_SOURCE = "vscode-resource://test";

test("devCommand substitutes every ${port}, not just the first", () => {
  assert.equal(
    devCommand("serve --port ${port} --hmr-port ${port}", 5173),
    "serve --port 5173 --hmr-port 5173",
  );
});

test("devCommand leaves a template with no placeholder alone", () => {
  assert.equal(devCommand("npm run dev", 5173), "npm run dev");
});

test("frame-src names the origin of the URL actually loaded", () => {
  const html = frameHtml(CSP_SOURCE, "http://127.0.0.1:5191/", "now");
  assert.match(html, /frame-src http:\/\/127\.0\.0\.1:5191;/);
  assert.match(html, /<iframe id="frame" src="http:\/\/127\.0\.0\.1:5191\/"/);
});

test("a remote-forwarded URL carries its own origin into the policy", () => {
  // Over Remote/Codespaces `asExternalUri` hands back a tunnel URL on a different host and
  // scheme. Hard-coding localhost here would blank the panel for every remote user.
  const url = "https://abc-5191.euw.devtunnels.ms/";
  const html = frameHtml(CSP_SOURCE, url, "12");
  assert.match(html, /frame-src https:\/\/abc-5191\.euw\.devtunnels\.ms;/);
  assert.doesNotMatch(html, /frame-src http:\/\/127/);
});

test("the policy still denies everything it did not name", () => {
  const html = frameHtml(CSP_SOURCE, "http://127.0.0.1:5191/", "now");
  assert.match(html, /default-src 'none'/);
});

test("an empty label renders as `now` rather than an empty span", () => {
  assert.match(frameHtml(CSP_SOURCE, "http://127.0.0.1:1/", ""), /id="label">now</);
});

test("plain strips the characters that could close a tag", () => {
  assert.equal(plain('</script><img src=x>'), "/scriptimg src=x");
});

test("an error message cannot inject markup into the panel", () => {
  // The message is whatever the dev command wrote to stderr -- untrusted enough to matter.
  const html = errorHtml(CSP_SOURCE, '</code></p><script>alert(1)</script>');
  assert.doesNotMatch(html, /<script>alert/);
});

test("boot and error documents carry no frame-src at all", () => {
  // Nothing is embedded yet, so nothing should be permitted to embed.
  assert.doesNotMatch(bootHtml(CSP_SOURCE, "starting"), /frame-src/);
  assert.doesNotMatch(errorHtml(CSP_SOURCE, "boom"), /frame-src/);
});
