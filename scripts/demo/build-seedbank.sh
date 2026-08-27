#!/usr/bin/env bash
# Build the seedbank demo repo -- the paper's hero demo for the live render
# timeline (docs/design/2026-08-26-live-render-timeline.md §7).
#
#   scripts/demo/build-seedbank.sh [<target-dir>]
#
# The target defaults to ~/repos/sgt-demo/seedbank and the script refuses to
# build over an existing one; pass FORCE=1 to replace it.
#
# WHAT THIS IS FOR, AND WHY IT IS A SCRIPT AND NOT A TARBALL
#
# The demo's whole job is that its *history* is designed. Twelve `sgt save`s,
# each one a beat, and two of them deliberately silent -- the search engine
# lands at episodes 2 and 3 and renders nothing at all until episode 4 gives it
# a box to type in. A tarball of the finished tree would carry the app and lose
# the only part that matters. So the episodes are kept as trees under
# `seedbank/` here and replayed, one save each, in order.
#
# THE ORDER OF THE FIRST FOUR STEPS IS NOT ARBITRARY
#
# `.gitignore` has to name `node_modules/` and `dist/` BEFORE the first save.
# sgt reads gitignored paths as `ignored` tier (sgt/core/tiers.py) and skips
# them; without the entry it mines a few thousand dependency files, the fold
# stops being something you can render in under a second, and the demo is over.
# `npm ci` therefore runs against a scaffold that already carries the right
# ignore file, and `sgt init` runs after the root commit so the kernel binds to
# a repo that has one.
#
# WHAT THIS SCRIPT CANNOT PROMISE
#
# A rebuild is not bit-identical to any other. Feature LABELS are LLM-generated
# and differ every run, the checkpoint cut differs, and the PARTITION itself
# moves whenever the entity graph changes -- teaching it to read JSX elements as
# references took this repo from eleven features to seven in one step. Feature
# ids from a previous build are therefore dead, and a dead one is worse than
# useless: `sgt revert <id>` falls back to matching it as an OP-ID PREFIX and
# reverts something unrelated while reporting success. Resolve by label or
# re-derive; never paste an id from an old run into a script.
#
# What the checks at the bottom enforce is not a shape but two properties: the
# search engine stays separable from the search box, and the silent gap stays
# silent.
set -euo pipefail

script_dir="$(cd "$(dirname "$0")" && pwd)"
payload="$script_dir/seedbank"
target="${1:-$HOME/repos/sgt-demo/seedbank}"

# `sgt` from PATH by default. Point SGT at another install to build against a
# frozen or a development copy -- which is what you want when the tree you are
# building from is also the tree you are editing.
# Exported, because the silent-gap check below shells out to render-frontiers.sh
# and that script has to fold with the same sgt this one built with.
export SGT="${SGT:-sgt}"
# The npm cache is overridable because a shared one that has been written by
# another user's install throws EACCES on rename, and the failure looks like a
# network problem rather than a permissions one.
npm_cache="${NPM_CACHE:-}"

say() { printf '  %s\n' "$*"; }

[ -d "$payload/scaffold/tree" ] || { echo "no episode payload at $payload" >&2; exit 1; }
command -v "$SGT" >/dev/null 2>&1 || { echo "no sgt on PATH; set SGT=<path>" >&2; exit 1; }

# Which sgt built the store is a property OF the store, and it is invisible afterwards. An sgt
# that does not own imports mines a store in which nothing owns an import line, and beat 6 then
# deletes `Tray.tsx` while leaving `import { TrayButton } from './Tray'` behind -- with every
# check in this script passing. So say out loud which install is about to do the mining.
say "sgt: $(command -v "$SGT")"
sgt_py="$(sed -n '1s/^#!//p' "$(command -v "$SGT")" 2>/dev/null)"
if [ -x "${sgt_py:-}" ] && ! "$sgt_py" -c "
import sgt.core.op as op, sys
sys.exit(0 if op._symbol_kind('a.ts::__import__::./b') == 'import' else 1)
" 2>/dev/null; then
    say "NOTE: this sgt does not own imports -- a revert will leave dangling import lines."
    say "      For the seedbank demo, build with the install that does."
fi

if [ -e "$target" ]; then
    [ "${FORCE:-0}" = 1 ] || { echo "$target exists; FORCE=1 to replace it" >&2; exit 1; }
    rm -rf "$target"
fi
mkdir -p "$target"
target="$(cd "$target" && pwd)"
cd "$target"

# --- the scaffold, installed and committed as one root commit ----------------
cp -R "$payload/scaffold/tree/." "$target/"
say "scaffold: $(git -C "$payload" >/dev/null 2>&1; ls -A "$target" | wc -l | tr -d ' ') entries"

if [ -n "$npm_cache" ]; then npm ci --cache "$npm_cache" >/dev/null; else npm ci >/dev/null; fi
say "npm ci: $(ls node_modules | wc -l | tr -d ' ') packages"

git init -q
git add -A
git commit -q -m "$(cat "$payload/scaffold/message")"
say "root commit $(git rev-parse --short HEAD)"

"$SGT" init >/dev/null
say "sgt init"

# --- the twelve episodes, one save each --------------------------------------
#
# `npm run build` is deliberately NOT run between episodes. `tsc -b` drops a
# `tsconfig.tsbuildinfo` next to the config, and a save taken while it is there
# mines it as an opaque op -- a build artifact recorded as if it were a piece of
# the developer's work. The scaffold's `.gitignore` names it for the same
# reason, so running a build by hand later cannot pollute a later save either.
for dir in "$payload"/e[0-9][0-9]; do
    ep="$(basename "$dir")"
    cp -R "$dir/tree/." "$target/"
    npx tsc --noEmit -p tsconfig.json
    "$SGT" save -m "$(cat "$dir/message")" --no-color >/dev/null
    say "$ep  $(git rev-parse --short HEAD)  $(cat "$dir/message")"
done

# --- the checks that decide whether the demo is usable -----------------------
#
# Checked here rather than left to whoever opens it, because every failure below
# is silent: a repo with the search engine folded into the same feature as the
# search box still runs, still looks right, and simply cannot demonstrate the one
# thing it was built to demonstrate.
#
# WHY THESE CHECKS AND NOT A FEATURE COUNT
#
# This used to assert `features >= 8`, as a stand-in for "the graph did not
# collapse". That proxy failed the first time the entity graph got *better*:
# teaching it to read a JSX element as a reference made the graph denser and
# repartitioned the repo from eleven features to seven, and the guard called the
# improvement a collapse. Seven features over correct edges is a better graph
# than eleven over missing ones, and no count can tell those two apart.
#
# So the count is kept only as a floor against total degeneracy, and the checks
# that can actually refuse a build are the two properties the demo rests on:
# the engine is separable from the box, and the silent gap is still silent.
# Read off sgt's own trailer line rather than counted from the indented rows --
# those include subsystem headers, which are groupings and not features, so
# counting them overstates by one per subsystem.
features="$("$SGT" log --tree 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' \
    | sed -n 's/^\([0-9][0-9]*\) features*$/\1/p' | tail -1)"
features="${features:-0}"
say "$features features"
[ "$features" -ge 3 ] || { echo "only $features features; the graph is degenerate" >&2; exit 1; }

# Property 1: the search ENGINE and the search BOX are in different features, so
# the engine can be reverted on its own and the gap demonstrated.
#
# Derived from the blame projection rather than matched against feature labels or
# ids. Labels are LLM-generated and differ run to run, and a stale feature id is
# worse than useless: it resolves as an op-id PREFIX and reverts something
# unrelated while reporting success.
python3 - "$SGT" <<'PY'
import json, subprocess, sys
sgt = sys.argv[1]

def features_of(path):
    out = subprocess.run([sgt, "advanced", "blame", path, "--json"],
                         capture_output=True, text=True).stdout
    if not out.strip().startswith("{"):
        sys.exit(f"could not blame {path}")
    return {s["feature_id"]: s.get("label", "") for s in json.loads(out).get("spans", [])
            if s.get("feature_id")}

box = features_of("src/SearchBox.tsx")
for engine in ("src/search/match.ts", "src/search/rank.ts"):
    owned = features_of(engine)
    if not owned:
        sys.exit(f"{engine} is owned by no feature")
    shared = set(owned) & set(box)
    if shared:
        sys.exit(f"{engine} and src/SearchBox.tsx share feature(s) {sorted(shared)}.\n"
                 "The silent gap cannot be reverted separately from the search box. Refusing.")
print("  the search engine is separable from the search box")
for path in ("src/search/match.ts", "src/search/rank.ts", "src/SearchBox.tsx"):
    for fid, label in sorted(features_of(path).items()):
        print(f"    {path:22s} -> {fid[:10]}  {label}")
PY

# The fold is what the render panel consumes, so its byte-exactness against the
# git tree is the demo's load-bearing property, not a nicety. Dot-paths are
# excluded: they are `ignored` tier and correctly absent from a fold.
#
# Folded by COMMIT INDEX, not by `--at main`. A branch name resolves through the
# branch record, which `sgt save` does not advance -- in a freshly built repo it
# still points at the commit `sgt init` bound to, so `--at main` here returns the
# six-file scaffold and calls it HEAD. It is a quiet wrong answer rather than an
# error, which is exactly the kind this check exists to catch.
"$SGT" advanced fold --at "$(($(git rev-list --count HEAD) - 1))" --json \
    > "$target/.fold.json" 2>/dev/null
python3 - "$target" <<'PY'
import json, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
fold = json.loads((root / ".fold.json").read_text())["files"]
tracked = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", "HEAD"],
                         capture_output=True, text=True).stdout.split()
bad = []
for f in tracked:
    if f.startswith("."):
        continue
    want = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{f}"],
                          capture_output=True).stdout
    if fold.get(f, "").encode() != want:
        bad.append(f)
if bad:
    sys.exit(f"the fold at HEAD is not the tree at HEAD: {bad}")
print(f"  the fold at HEAD is byte-identical to HEAD ({len(tracked) - 1} files)")
PY
rm -f "$target/.fold.json"

# Property 2: the silent gap is still silent.
#
# The one the demo is actually named after -- the search engine lands at episodes
# 2 and 3 and moves no pixels, and the search box at episode 4 does. This is not
# checkable from the graph: whether a save shows up on the landing route is a
# question about the DATA, not about the diff, and the spike this demo came out
# of found a feature that was live and invisible for eleven positions because the
# default slice happened not to exercise it. So it is measured, by rendering the
# four frontiers either side of the gap and comparing the photographs.
#
# Only four frontiers, not thirteen: the check has to be cheap enough to run on
# every build. `render-frontiers.sh` with no range does the full sweep.
if [ -x "${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}" ]; then
    frames="$target/.frames"
    "$script_dir/render-frontiers.sh" "$target" "$frames" 2 5 >/dev/null
    for pair in "2 3" "3 4"; do
        set -- $pair
        cmp -s "$frames/shot-$1.png" "$frames/shot-$2.png" || {
            echo "frontier $2 changed the rendered page; episodes 2 and 3 are supposed to be" >&2
            echo "silent and this build's are not. The headline beat is gone. Refusing." >&2
            exit 1; }
    done
    if cmp -s "$frames/shot-4.png" "$frames/shot-5.png"; then
        echo "frontier 5 (the search box) changed nothing on the landing route." >&2
        echo "The gap has no payoff, so there is nothing to scrub into. Refusing." >&2
        exit 1
    fi
    rm -rf "$frames"
    say "the silent gap holds: frontiers 3 and 4 render identically, 5 does not"
else
    say "NO HEADLESS CHROME -- the silent gap was NOT checked. Set CHROME=<path> and run"
    say "  scripts/demo/render-frontiers.sh $target /tmp/frames"
fi

say "built at $target -- npm run dev, then scripts/demo/render-frontiers.sh to measure it"
