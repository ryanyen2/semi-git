// Vite plugin: stamp every HOST jsx element with data-sgt-loc="<relpath>:<line>".
// Runs `pre`, so it rewrites source before the JSX transform. Parses with the TypeScript
// compiler API rather than a regex -- JSX nesting, generics and string attrs all defeat regex.
import ts from "typescript";
import path from "node:path";

export default function sgtLoc({ root = process.cwd() } = {}) {
  return {
    name: "sgt-loc",
    enforce: "pre",
    transform(code, id) {
      const [file] = id.split("?");
      if (!/\.[jt]sx$/.test(file)) return null;
      const src = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const rel = path.relative(root, file);
      const edits = [];
      const visit = (node) => {
        if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
          const name = node.tagName.getText(src);
          // Host elements only: a component's attributes are props, not DOM attributes.
          if (/^[a-z]/.test(name)) {
            const line = src.getLineAndCharacterOfPosition(node.tagName.getStart(src)).line + 1;
            edits.push({ pos: node.tagName.getEnd(), text: ` data-sgt-loc="${rel}:${line}"` });
          }
        }
        ts.forEachChild(node, visit);
      };
      visit(src);
      if (!edits.length) return null;
      let out = code;
      for (const e of edits.sort((a, b) => b.pos - a.pos)) {
        out = out.slice(0, e.pos) + e.text + out.slice(e.pos);
      }
      return { code: out, map: null };
    },
  };
}

// Verified 2026-08-26 against React 19.2 / Vite 8.2 / @vitejs/plugin-react 6.0.5.
// Rendered output: <article data-sgt-loc="src/Card.tsx:3" class="card">…
//
// Why this exists rather than React's own `__source`: the Vite transform still emits
// {fileName, lineNumber} at each jsxDEV call site, but React 19 DISCARDS it -- `jsxDEVImpl`'s
// 5th/6th parameters are now (debugStack, debugTask), and `_debugSource` no longer exists on a
// fiber (0 occurrences in react-dom 19's development build). So the location has to be put
// somewhere React will carry: a data-* attribute, which React always passes through to the DOM.
//
// Why the TypeScript compiler API rather than a babel plugin: @vitejs/plugin-react 6 dropped
// @babel/core for oxc, so `react({ babel: { plugins: [...] } })` is silently ignored.
// `typescript` is already a dependency of any React+TS project, so this adds nothing (CLAUDE.md §8).
