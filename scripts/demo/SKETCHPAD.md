# Recording the sketchpad take

A three minute screen recording. You inherit a working program, and you remove one recorded idea
at a time to show what each one was holding in place. The subject is sgt. The sketchpad app is
just the picture that makes sgt's behaviour visible.

The app is a reimplementation of Sutherland's 1963 Sketchpad. Its drawing file stores geometry the
way a light pen left it, so corners are bunched, sides are unequal and groups sit crooked. What
you see on screen is the running program holding that imprecise geometry in place, and six
recorded ideas do the holding.

| Idea, as sgt knows it | What it holds | Reverting removes | Visible on |
|---|---|---|---|
| a corner stays on its circle | corners on the rim | 8 edits | sheet 1 |
| lines of equal length | the hexagon regular | 5 edits | sheet 1 |
| fastened at the corners | the lattice together | 11 edits | sheet 2 |
| full size | every group the same size | 11 edits | sheet 2 |
| stand upright | every group level | 7 edits | sheet 2 |
| Line Orientation | nothing in these drawings | 8 edits | nowhere |

Five of the six change the picture when you remove them. Each reverts by name, compiles, and
restores exactly with `sgt undo`. A seventh feature, `the relaxation solver`, refuses to come out
cheaply, and shot 6 uses it for that reason.

The demo repo is `~/repos/sgt-demo/sketchpad-v2` and it is not in this repository, so ask Ryan for
it and for the golden record at `~/repos/sgt-demo/.sgt-golden-v2` before you start.

The seedbank demo is a different repo with its own file at `RUNBOOK-seedbank.md`. The four shot
sketchpad-v3 take is at `sketchpad-v3/RUNBOOK.md`.

## Setup

Set these in every terminal you use.

```bash
export SGT_SRC=~/repos/semi-git
export DEMO=~/repos/sgt-demo/sketchpad-v2
export SGT=~/.local/share/uv/tools/semi-git/bin/sgt   # 0.6.10 or later
```

The build has to own import lines. Since 0.6.5 the released build does, so
`uv tool install --force $SGT_SRC` is enough. Check it either way.

```bash
"$(dirname "$SGT")/python" -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))"
# must print: import
```

If it prints `nested`, reinstall from a source tree at 0.6.5 or later. Point `SGT` at the uv tool
directory and not at `~/.local/bin/sgt`, because `check-ideas.sh` looks for python next to `$SGT`
and rejects the shim in `~/.local/bin`.

Record against a disposable clone, never against `$DEMO`.

```bash
rm -rf /tmp/sketchpad-live && git clone -q $DEMO /tmp/sketchpad-live
cp -r $DEMO/.sgt /tmp/sketchpad-live/.sgt
cd /tmp/sketchpad-live && npm ci --cache /tmp/npm-cache-sketchpad-v2 && npx vite --port 5175
```

Run a real `npm ci` in the clone. A symlinked `node_modules` escapes `.gitignore` and gets
committed by accident, and a vite cache shared with another checkout crashes the page with a
doubled React error.

You need three windows. The browser holds the app and, for shot 7, the four grid tabs. One
terminal runs vite in the clone and stays off camera. The other terminal is where you type sgt
commands on camera, and it stays in `/tmp/sketchpad-live`.

## Preflight

Run this immediately before recording. It rehearses every operation on throwaway clones.

```bash
bash $SGT_SRC/scripts/demo/check-ideas.sh
# want: 17 passed, 0 failed
```

## Rules

1. Don't run `sgt save`, `sgt log --refresh` or `sgt log --rebuild` inside `$DEMO`. A re-mine
   keeps the six hand named ideas at the sizes in the table, which was fixed in 0.6.10, and it
   still re-clusters and renames the machine named rows around them, so the map you rehearsed
   stops matching the map you record.
2. Don't leave `$DEMO` open in an editor. The VS Code extension re-mines in the background with
   nobody typing. Measured on 2026-09-03, 45 seconds of an open window grew `.sgt/tree/tree.json`
   from 93k to 112k and added a garbage label. Find the process with
   `lsof -a -d cwd 2>/dev/null | grep sketchpad-v2`.
3. Treat the preflight as the drift detector, not any single edit count.

## The take

Roughly three minutes. The quoted lines are what to say, or something close.

### Shot 1, the drawing (20 s)

Show the app on sheet 2, which is the 49 hexagon lattice. Flip the SHEET 2 switch off, hold two
seconds on the single hexagon in its circle, and flip it back on.

> "You're looking at a reimplementation of Sketchpad, Sutherland's 1963 thesis program. The
> drawing file it loads is full of imprecise geometry, with corners off the circle, unequal
> sides, and groups dropped crooked, the way a hand would leave them. Everything you see being
> straight on screen is the running program holding it straight, and each of the conditions doing
> the holding is a separate recorded idea in the history."

The whole take depends on the viewer getting that the drawing is held by the program rather than
drawn accurately. Say it here and don't repeat it. Say "conditions" and not "features" until the
map is on screen in shot 2.

### Shot 2, the history has names (30 s)

```bash
sgt log --map
```

The map reads 17 features over 20 saves. Point at the `Geometric Constraints` group, which holds
`lines of equal length`, `fastened at the corners`, `full size`, `Line Orientation` and
`stand upright`.

> "sgt recorded the program being built across twenty saves and reads that history as named
> features. The rows I'm pointing at are the conditions holding the drawing you just saw, and
> each one is a name I can act on."

```bash
sgt show "fastened at the corners"
```

> "Eleven edits. sgt knows what removing an idea costs before anything happens."

### Shot 3, subtract and restore (50 s)

```bash
sgt revert "fastened at the corners"    # confirm the preview, then reload the browser
```

The lattice comes apart. Groups overlap and gaps open, and the console output is unchanged.

> "You're looking at today's program minus the one idea that fastens the hexagon groups at their
> corners. Nobody edited a file, and the version you see never existed in the git history. The
> drawing file is unchanged, the console output is unchanged, and the program still compiles. It
> just no longer knows that corners are shared between groups."

```bash
sgt undo    # reload; the lattice snaps back together
```

> "And restored, byte for byte."

Leave a moment of silence on the snap back. It's the strongest beat in the take.

### Shot 4, a different idea fails differently (30 s)

```bash
sgt revert "stand upright" --yes    # reload
```

The lattice holds together and every outer group leans at its own angle.

> "A different idea produces a different failure. Fastening still works and sizes still work, and
> nothing says the groups have to be level any more. Each idea, taken out, shows you what it was
> responsible for."

```bash
sgt undo
```

### Shot 5, the founding idea on sheet 1 (30 s)

```bash
sgt revert "lines of equal length" --yes    # reload, then flip SHEET 2 off
```

The hexagon is lopsided, with two corners bunched and one short side. The corners still sit on
the circle.

> "Sheet one is the original 1963 demonstration, a sloppy hexagon told to be regular. Without the
> equal length constraint the program still puts every corner on the circle, and nothing makes
> the sides agree. You're watching the famous snap from Sutherland's thesis run in reverse."

```bash
sgt undo
```

### Shot 6, the idea that refuses (20 s)

```bash
sgt show "the relaxation solver"    # point at the consequences line, don't revert
```

> "Not everything can come out. Removing the solver would touch 76 edits, and 29 of those are
> later work built on top of it. sgt tells you which ideas are removable and which ones the rest
> of the program depends on, before you touch anything."

### Shot 7, four variants at once (30 s)

Launch the grid off camera before you start, and arrange the four tabs side by side.

```bash
bash $SGT_SRC/scripts/demo/idea-grid.sh
# 5501 today, 5502 no fastening, 5503 no full size, 5504 no stand upright
```

Pan across the four windows.

> "One repository, no branches, no feature flags, nothing edited by hand. Today's program next to
> today's program minus each of three ideas, all four running at once. Every window loads the
> same drawing file and prints the same console output. The only difference is which ideas the
> program still has."

## After the take

```bash
cd $DEMO && git status --short     # prints nothing; the take never writes here
rm -rf /tmp/sketchpad-live /tmp/sketchpad-grid
```

The demo repo is unchanged, so the next take needs no reset.

## Don't improvise into these

- `lines of equal length` and `a corner stays on its circle` only move sheet 1, because a master
  shape's internal geometry never relaxes. The shot 7 grid stays on sheet 2 and uses the other
  three ideas.
- `Line Orientation` reverts cleanly and shows nothing, because no drawing in the repo uses the
  horizontal or vertical constraint. Keep it off camera.
- Only point at the six idea names and `the relaxation solver`. They're hand authored and stable.
  The machine mined labels around them change on every rebuild.
- Don't leave sheet 1 up with CONSTRAINTS off. Without the glyphs it's a plain hexagon in a
  circle and there's nothing to look at.

The switches you touch on camera are SHEET 2, which shows the lattice when on and the single
hexagon when off, and CONSTRAINTS, which draws the lettered constraint circles over the geometry.
ZIGZAG, SEMICIRCLE and FREEDOMS stay off. The push buttons along the bottom are the light pen
console and none of them appear in the take.

## When something breaks

**The preflight fails, or a name resolves to the wrong number of edits.** The store drifted,
almost always because something ran a mining pass in `$DEMO`. Restore the golden copy and rerun.

```bash
cd $DEMO && mv .sgt /tmp/sgt-drifted-$(date +%s) && cp -r ~/repos/sgt-demo/.sgt-golden-v2 .sgt
bash $SGT_SRC/scripts/demo/check-ideas.sh
```

Keep the golden copy out of `/tmp`. macOS empties `/tmp` file contents and leaves the directories,
so a stale copy looks present and restores nothing.

**The golden copy is missing.** Rebuild the authored ideas from the git history, which is the only
part that has to survive. Move the pins aside first, or `--rebuild` reproduces the mangled
partition from them.

```bash
cd $DEMO
mkdir -p /tmp/sgt-aside && mv .sgt/pins/pins.json .sgt/authored/features.json /tmp/sgt-aside/
"$SGT" log --rebuild                                              # ~25s for the 20 commits
SGT="$SGT" $SGT_SRC/scripts/demo/sketchpad-rebuild/author-ideas.sh "$DEMO"   # want: 5 passed
"$SGT" feature rename "Constraint Solver" "the relaxation solver"
cp -r .sgt ~/repos/sgt-demo/.sgt-golden-v2
```

`author-ideas.sh` re-derives each idea's ops from the `Sgt-Op:` commit trailers, so it runs from
nothing. Then run the preflight again.

**The whole repo is missing.** `build-sketchpad-v2.sh` replays the 20 saves, and the rebuild takes
about ten minutes end to end. It needs the original `~/repos/sgt-demo/sketchpad` as its source,
which now sits in `~/.Trash/sketchpad`.

```bash
cd $SGT_SRC
FORCE=1 bash scripts/demo/sketchpad-rebuild/build-sketchpad-v2.sh
cd $DEMO && $SGT log --refresh
bash $SGT_SRC/scripts/demo/sketchpad-rebuild/author-ideas.sh
$SGT feature rename 02004cfc "the relaxation solver"
cp -r .sgt ~/repos/sgt-demo/.sgt-golden-v2
```

The original sketchpad repo is why v2 exists. There, constraint types lived as entries in two
const tables, and sgt's TypeScript grammar has no symbol for an entry inside a top level const,
so the entries landed in residue where only the newest edit reverts cleanly. In v2 each
constraint type is its own file under `src/kinds/` with its own import line, and sgt attributes
both units exactly.

**The app shows a stale picture after a revert.** Vite cached the old module. Hard reload, and
restart vite in the clone if that doesn't fix it.

**The page is blank with a React hook error.** The clone shares a vite dependency cache with
another checkout. Run a real `npm ci` in the clone.

## The numbers, if someone asks

| Claim | What proves it |
|---|---|
| 20 saves, five subtractable ideas | `sgt log --map` in `$DEMO` |
| every idea reverts by name, compiles, moves its sheet, and undoes exactly | `check-ideas.sh`, 17 checks |
| the base drawing is genuinely imprecise and the program holds it | the seed coordinates in `src/drawing.ts`, and the lattice solving in one pass with 12 variables ordered |
| fastening costs 11 edits to remove, the solver costs 76 | `sgt show "fastened at the corners"` and `sgt show "the relaxation solver"` |
| the four grid variants run at once from one repo | `idea-grid.sh`, four ports responding |
