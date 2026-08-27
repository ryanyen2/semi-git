// Dev-only Vite plugin: paint the running app by the feature that last changed each region.
//
// This lives in the *sgt* repo, not in the demo repo, and that is deliberate. Anything under the
// demo's `src/` or `tools/` is mined, clustered, and shows up as a feature -- an overlay shipped
// inside the demo would appear in its own feature tree and in its own blame output. So it is
// injected at dev time instead, and the demo repo stays exactly what it claims to be.
//
// The join it builds is the one the design doc calls Layer B:
//
//     DOM element -> data-sgt-loc="<file>:<line>" -> `sgt advanced blame` span -> feature
//
// `data-sgt-loc` comes from the demo's own `tools/vite-plugin-sgt-loc.mjs`, which stamps every
// host JSX element. React 19 discards `__source`, so there is no built-in to read instead.
//
// WHAT THIS CAN AND CANNOT SAY
//
// sgt attributes a *symbol*, and blame reports the feature of that symbol's tip op. A React
// component is one large symbol, so a 68-line `Card` touched most recently by the seed tray reads
// as the seed tray's -- for the whole card, not just the star the tray added. That is why every
// label here says "last changed by" and never "owned by": the claim the data supports is a
// recency claim, and overstating it would be the kind of confident-and-wrong the rest of this
// project keeps finding. Small single-purpose symbols (a Badge, a TrayButton) are exact.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

const GOLDEN = 0.618033988749895;

function hashId(s) {
  let h = 2166136261;
  for (const ch of s) {
    h ^= ch.codePointAt(0);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h;
}

// Port of sgt/tui/color.py's OKLCH identity color, at the *light* lightness/chroma -- the demo
// page is cream, and the terminal's dark-theme constants wash out on it.
function colorFor(id, L = 0.55, C = 0.15) {
  const h = (((hashId(id) * GOLDEN) % 1) * 360 * Math.PI) / 180;
  const a = C * Math.cos(h), b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
  const lr = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const lg = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const lb = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;
  const g = (x) => {
    const v = x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
    return Math.round(Math.max(0, Math.min(1, v)) * 255);
  };
  return `#${[g(lr), g(lg), g(lb)].map((n) => n.toString(16).padStart(2, "0")).join("")}`;
}

export default function sgtOverlay({ repo, sgt = "sgt" } = {}) {
  let payload = null;

  function build() {
    // One call for the whole repo -- `--all` exists precisely so this is not 24 subprocesses.
    const raw = execFileSync(sgt, ["advanced", "blame", "--all", "--json"], {
      cwd: repo, encoding: "utf8", maxBuffer: 64 * 1024 * 1024,
    });
    const blame = JSON.parse(raw);
    const features = {};
    for (const [fid, v] of Object.entries(blame.features || {})) {
      features[fid] = { label: v.label || fid.slice(0, 10), color: colorFor(fid) };
    }
    const files = {};
    for (const [path, v] of Object.entries(blame.files || {})) {
      const spans = (v.spans || [])
        .filter((s) => s.feature_id)
        .map((s) => [s.start_line, s.end_line, s.feature_id, s.symbol || ""]);
      if (spans.length) files[path] = spans;
    }
    return { features, files };
  }

  return {
    name: "sgt-overlay",
    apply: "serve",
    configureServer(server) {
      try {
        payload = build();
        const n = Object.keys(payload.features).length;
        server.config.logger.info(`  sgt overlay: ${n} features, ${Object.keys(payload.files).length} files blamed`);
      } catch (err) {
        // A failed blame must not take the app down -- the demo still has to render.
        server.config.logger.error(`  sgt overlay disabled: ${err.message.split("\n")[0]}`);
        payload = { features: {}, files: {} };
      }
    },
    transformIndexHtml(html) {
      if (!payload) return html;
      return {
        html,
        tags: [
          { tag: "script", attrs: { type: "application/json", id: "sgt-blame" },
            children: JSON.stringify(payload), injectTo: "body" },
          { tag: "script", attrs: { type: "module", src: "/@sgt-overlay/client.js" }, injectTo: "body" },
        ],
      };
    },
    resolveId(id) {
      return id === "/@sgt-overlay/client.js" ? "\0sgt-overlay-client" : null;
    },
    load(id) {
      if (id !== "\0sgt-overlay-client") return null;
      // Read on every request rather than caching: editing the overlay during a rehearsal should
      // show up on reload without restarting the dev server.
      return readFileSync(join(HERE, "client.js"), "utf8");
    },
  };
}
