#!/usr/bin/env bash
# Run the seedbank demo with the provenance overlay on top.
#
#   scripts/demo/with-overlay.sh [<repo-dir>] [<port>]
#
# Hover the hairline rail on the left edge: each feature's regions light up in its identity hue
# and everything else dims. Hover the page itself: a chip names the symbol and the feature that
# last changed it. Backtick hides the instrument for a clean take.
#
# WHY IT RUNS FROM A SCRATCH COPY
#
# The overlay must not live in the demo repo -- anything under its `src/` or `tools/` is mined and
# would show up as a feature in the very tree the overlay is drawing. So the demo is copied to a
# scratch dir (node_modules symlinked, exactly as render-frontiers.sh does), the overlay config is
# written there, and the real repo is never touched. Blame is still read from the REAL repo, since
# that is where `.sgt` lives.
set -euo pipefail

repo="${1:-$HOME/repos/sgt-demo/seedbank-v3}"
port="${2:-5174}"
here="$(cd "$(dirname "$0")" && pwd)"
SGT="${SGT:-$here/../../.venv/bin/sgt}"
[ -x "$SGT" ] || SGT="sgt"
[ -d "$repo/.sgt" ] || { echo "no sgt repo at $repo" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
for f in index.html package.json tsconfig.json vite.config.ts src tools; do
    cp -R "$repo/$f" "$work/" 2>/dev/null || true
done
ln -s "$repo/node_modules" "$work/node_modules"

# The demo's own config already carries sgtLoc + react; this one re-declares them rather than
# importing a .ts config from a .mjs one, and adds the overlay. Kept in lockstep by hand -- if the
# demo's vite.config.ts grows a plugin, add it here too.
cat > "$work/vite.overlay.config.mjs" <<CONFIG
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import sgtLoc from "./tools/vite-plugin-sgt-loc.mjs";
import sgtOverlay from "$here/overlay/sgt-overlay.mjs";

export default defineConfig({
  plugins: [sgtLoc(), react(), sgtOverlay({ repo: "$repo", sgt: "$SGT" })],
});
CONFIG

echo "  demo      $repo"
echo "  overlay   $here/overlay"
echo "  scratch   $work"
echo
cd "$work"
exec npx vite --config vite.overlay.config.mjs --port "$port" --strictPort
