# The sketchpad take

This file contains everything you need to record the sketchpad demo, from the environment setup
through each shot, including what to say and what to do when something goes wrong. Read it once
before your first attempt so the structure makes sense, and refer back to individual sections
during setup and recording.

The demo repo lives at `~/repos/sgt-demo/sketchpad-v2`, which replaced the original sketchpad
repo because the original repo's history could not support the revert structure the take needs
(section 8 explains why and how to rebuild it from scratch). The seedbank demo is a separate
repo with its own recording file at `RUNBOOK-seedbank.md`, and the two recordings are
independent of each other.

---

## 1. What the take shows

The subject of the recording is sgt. The sketchpad app is the visual output that makes sgt's
behavior visible to the viewer.

The app itself is a reimplementation of Ivan Sutherland's 1963 Sketchpad. Its underlying
drawing file stores geometry the way a light pen originally left it, meaning corners are bunched
and off the circle, groups are dropped crooked and at the wrong sizes. What you see on screen
is the running program holding all of that imprecise geometry to the correct positions. Six
recorded ideas do the holding, and each one acts as a geometric condition. The table below
lists all six, what each one enforces, how many edits reverting it removes, and which sheet
shows the visual effect.

| Idea, as sgt knows it | What it holds | Reverting it removes | Where it shows |
|---|---|---|---|
| a corner stays on its circle | corners on the rim | 8 edits | sheet 1 |
| lines of equal length | the hexagon regular | 8 edits | sheet 1 |
| fastened at the corners | the lattice together | 11 edits | sheet 2 |
| full size | every group the same size | 11 edits | sheet 2 |
| stand upright | every group level | 7 edits | sheet 2 |
| horizontal or vertical | nothing in these drawings | 8 edits | nowhere |

Five of the six ideas have a visible effect when removed. Each one reverts cleanly by name,
compiles without errors, changes the drawing in its own recognizable way, and restores exactly
with `sgt undo`. The preflight script `scripts/demo/check-ideas.sh` runs 17 checks that verify
all of the above, and all 17 should pass before you record.

The take is a live ablation study. You inherit a working program, ask what each recorded idea
contributes to it, and answer the question by removing one idea at a time and watching what
happens to the drawing. The terminal console never changes across any of these variants, which
is what makes the visual differences unmistakably the program's behavior and never a manually
toggled flag.

One idea deliberately refuses to come out. Running `sgt show "the relaxation solver"` reports
that removing the solver would touch 78 edits with 29 later edits built on top of them, which
makes it too expensive to subtract. The point is that sgt knows which ideas are removable and
which ones are structurally load-bearing, and it knows before you change anything.

---

## 2. How to work the app with a mouse

You don't draw anything during the recording. Read this section anyway so nothing surprises you
on camera.

The black square in the center of the screen is the scope, which is the main drawing area.
Below it sits a row of push buttons (DRAW, CIRCLE CENTER, MOVE, DELETE, COPY, HORV, STOP) and
a bank of toggle switches. The only controls you interact with during the take are the toggle
switches.

| Switch | What it does |
|---|---|
| SHEET 2 | Turned on at startup, it shows the 49-hexagon lattice. Turning it off shows sheet 1, which is the single hexagon inscribed in its circle. |
| ZIGZAG | Shows a row of nine zigs, and overrides the SHEET 2 switch when turned on. |
| SEMICIRCLE | Swaps the master hexagon shape for a semicircle, and every copy in the lattice changes at once because they all reference the same master. |
| CONSTRAINTS | Draws the lettered constraint circles as overlays on the geometry. |
| FREEDOMS | Numbers the constraint-solving order, and only draws when ZIGZAG is active. |

The push buttons replicate the original light-pen console and work with a mouse, but each one
operates as a multi-step sequence rather than a single click. DRAW places a starting point and
the next DRAW places the endpoint to complete a line segment. MOVE grabs the nearest point and
drags it until you press STOP. COPY takes two clicks and constrains two lines to equal length.
DELETE removes whatever you point at. None of the push buttons appear in the take.

---

## 3. Setup

Set three environment variables once in each terminal you use. Every command in the rest of
this file assumes they exist.

```bash
export SGT_SRC=~/repos/semi-git
export DEMO=~/repos/sgt-demo/sketchpad-v2
export SGT=$SGT_SRC/.venv/bin/sgt        # never the sgt on your PATH
```

You must use the sgt binary from the development virtualenv, not the one installed on your
PATH. The PATH version is a different build that doesn't own import lines, and its failure mode
is confusing rather than obvious. The revert will preview correctly, and then it will refuse to
apply while naming files it never actually touches. Run the following check to make sure you
have the right build.

```bash
$SGT_SRC/.venv/bin/python -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))"
# must print: import
```

If it prints `nested` instead of `import`, check out the `feat/live-render-timeline` branch in
the sgt source tree and rebuild.

You need three windows arranged on screen during the recording.

| Where | What |
|---|---|
| Browser | The sketchpad app, and later the four side-by-side grid tabs from shot 7 |
| Terminal A | Runs `npm run dev` in the clone, stays off camera for the whole recording |
| Terminal B | The terminal where you type sgt commands on camera |

The entire take runs against a disposable clone, never against the `$DEMO` repo directly. Build
the clone with the following commands.

```bash
rm -rf /tmp/sketchpad-live && git clone -q $DEMO /tmp/sketchpad-live
cp -r $DEMO/.sgt /tmp/sketchpad-live/.sgt
cd /tmp/sketchpad-live && npm ci --cache /tmp/npm-cache-sketchpad-v2 && npx vite --port 5175
```

Terminal B stays in `/tmp/sketchpad-live` for the rest of the session. The `npm ci` install must
be a real install, not a symlinked `node_modules`. A symlinked `node_modules` escapes the
`.gitignore` (because the `node_modules/` pattern matches a directory but not a symlink), which
means it gets committed by accident, and a shared vite cache between two checkouts crashes the
page with a doubled-React error.

---

## 4. Preflight

Run one script immediately before you start recording. The script rehearses every operation the
take performs, using throwaway clones, and reports 17 individual checks.

```bash
bash $SGT_SRC/scripts/demo/check-ideas.sh
# want: 17 passed, 0 failed
```

### The one rule you must not break

**Never run `sgt save`, `sgt log --refresh`, or `sgt log --rebuild` inside the `$DEMO` repo.**

The five idea features in the demo repo are hand-authored. If anything triggers a mining pass,
sgt silently rewrites the authored features. The names will keep resolving so nothing looks
wrong at first, but the reverts will quietly grow larger until they eventually break the build.
Nothing warns you when the overwrite happens. If the preflight fails, the store has almost
certainly drifted. Restore the known-good backup and rerun the checks.

```bash
cd $DEMO && mv .sgt /tmp/sgt-drifted-$(date +%s) && cp -r /tmp/sgt-golden-v2 .sgt
```

---

## 5. The take

The recording runs about three minutes. The quoted lines under each shot are what to say on
camera, or something close to them. The "why" notes explain the reasoning behind each beat, so
you can improvise the wording without accidentally breaking the argument.

### Shot 1, the drawing (20 seconds)

**Do.** Show the app on sheet 2, which is the 49-hexagon lattice. Hold the shot for a moment.
Then flip the SHEET 2 switch off, hold for two seconds on the single hexagon inscribed in its
circle, and flip it back on.

**Say.** "You're looking at a reimplementation of Sketchpad, Sutherland's 1963 thesis program.
The drawing file it loads is full of imprecise geometry, because it stores corners that are off
the circle, sides that are unequal, and groups dropped crooked and at the wrong sizes, exactly
the way a hand would leave them. Everything you see being straight and regular on screen is the
running program holding it straight. Each of the conditions doing the holding is a separate
recorded idea in the repo's history."

**Why.** The entire take depends on the viewer understanding that the drawing is held by the
program rather than drawn accurately in the first place. State the point clearly in shot 1 and
don't repeat it after that.

### Shot 2, the history has names (30 seconds)

**Do.** Switch to Terminal B and run the following command.

```bash
sgt log --map
```

Point at the named rows in the output, specifically `lines of equal length`, `fastened at the
corners`, `full size`, and `stand upright`.

**Say.** "sgt recorded the program being built across twenty saves, and it reads the history as
named features. The four rows I'm pointing at are four of the conditions holding the drawing
you just saw, and each one is a name I can act on directly."

Then run the next command and point at the last line of its output.

```bash
sgt show "fastened at the corners"
```

**Say.** "Eleven edits. sgt knows the cost of removing an idea before anything actually
happens."

### Shot 3, subtract and restore (50 seconds)

**Do.** Run the revert, confirm the preview when prompted, and then reload the browser tab.

```bash
sgt revert "fastened at the corners"
```

**On screen.** The lattice has come apart. Groups overlap and gaps open between them, but the
terminal console output is identical to before.

**Say.** "You're now looking at today's program minus the one idea that fastens the hexagon
groups together at their corners. Nobody edited a file by hand, and the version you see never
existed anywhere in the git history. The drawing file is unchanged, the console output is
unchanged, and the program still compiles and runs. It simply no longer knows that corners are
meant to be shared between groups."

Then run the undo and reload the browser. The lattice snaps back together.

```bash
sgt undo
```

**Say.** "And restored, byte for byte."

**Why.** The revert and restore together are the claim in its most compact form. The moment the
lattice snaps back together is the strongest visual beat in the take, so leave a moment of
silence on it.

### Shot 4, a different idea fails differently (30 seconds)

**Do.** Run the revert with `--yes` to skip the preview confirmation, then reload the browser.

```bash
sgt revert "stand upright" --yes
```

**On screen.** The lattice still holds together, but every outer group now leans at its own
angle instead of sitting level.

**Say.** "A different idea produces a different failure. Fastening still works, sizes still
work, but nothing says the groups have to be level any more. Each idea, taken out, shows you
exactly what it was responsible for holding in place."

```bash
sgt undo
```

### Shot 5, the founding idea on sheet 1 (30 seconds)

**Do.** Run the revert with `--yes`, reload the browser, and then flip the SHEET 2 switch off
to show sheet 1.

```bash
sgt revert "lines of equal length" --yes
```

**On screen.** The hexagon is visibly lopsided, with two corners bunched together and one side
noticeably shorter than the others. The corners still sit on the circle.

**Say.** "Sheet one is the original 1963 demonstration itself, a sloppy hexagon told to be
regular. Without the equal-length constraint, the program still puts every corner on the
circle, but nothing makes the sides agree with each other. You're watching the famous snap
from Sutherland's thesis run in reverse."

```bash
sgt undo
```

### Shot 6, the idea that refuses (20 seconds)

**Do.** Run the show command and point at the consequences line. Don't attempt a revert.

```bash
sgt show "the relaxation solver"
```

**Say.** "Not everything can come out. Removing the solver would require 78 edits, and 29 of
those are later work that was built on top of it. sgt tells you which ideas are removable and
which ones the rest of the program depends on, before you touch anything."

### Shot 7, four simultaneous variants (30 seconds)

**Do.** Before the recording starts, off camera, launch the grid script and arrange the four
browser tabs side by side.

```bash
bash $SGT_SRC/scripts/demo/idea-grid.sh
# today on port 5501, no fastening on 5502, no full size on 5503, no stand upright on 5504
```

On camera, pan across the four browser windows.

**Say.** "One repository, no branches, no feature flags, nothing manually edited. You're
looking at today's program alongside today's program minus each of three different ideas, all
four running at once. Every window loads the same drawing file and produces the same console
output. The only difference is which ideas the program still has."

Stop recording.

---

## 6. After the take

Verify that the demo repo is untouched and clean up the temporary directories.

```bash
cd $DEMO && git status --short     # should print nothing, because the take never wrote to it
rm -rf /tmp/sketchpad-live /tmp/sketchpad-grid
```

The demo repo is unchanged after a recording, so the next take needs no reset.

---

## 7. Limits to know before you improvise

Keep these constraints in mind so you don't accidentally demonstrate something that doesn't
work or creates a misleading impression.

- **The two sheet-1 ideas don't affect the lattice.** Sutherland's own rule is that a master
  shape's internal geometry never relaxes, so `lines of equal length` and
  `a corner stays on its circle` only produce visible changes on sheet 1. The four-window grid
  in shot 7 stays on sheet 2 and uses the other three ideas.
- **`horizontal or vertical` reverts cleanly but shows nothing.** No drawing stored in the repo
  actually uses the horizontal-or-vertical constraint, so reverting it produces no visible
  change. It exists in the feature map but stays off camera.
- **Mined feature labels change on every rebuild.** The five idea names and `the relaxation
  solver` are hand-authored and stable across rebuilds. Only point at those names, because the
  automatically mined labels will be different next time.
- **Don't say "feature" during shot 1.** Until the feature map is visible on screen in shot 2,
  refer to the ideas as conditions and the output as drawings.
- **Don't leave sheet 1 visible with CONSTRAINTS turned off.** Without the constraint glyphs
  overlaid, sheet 1 looks like a plain hexagon inscribed in a circle, which isn't visually
  interesting enough to hold the viewer's attention.

## 8. When something breaks

**The preflight fails, or a name resolves to the wrong number of edits.** The sgt store has
drifted, almost always because something accidentally ran a mining pass in the demo repo.
Restore the golden backup copy and rerun the checks.

```bash
cd $DEMO && mv .sgt /tmp/sgt-drifted-$(date +%s) && cp -r /tmp/sgt-golden-v2 .sgt
bash $SGT_SRC/scripts/demo/check-ideas.sh
```

**The golden backup copy is missing.** The entire demo repo is reproducible from three scripts.
The rebuild takes about ten minutes end to end.

```bash
cd $SGT_SRC
FORCE=1 bash scripts/demo/sketchpad-rebuild/build-sketchpad-v2.sh   # replays 20 saves
cd ~/repos/sgt-demo/sketchpad-v2 && $SGT log --refresh              # mines the feature map
cd $SGT_SRC && bash scripts/demo/sketchpad-rebuild/author-ideas.sh  # authors the five idea names
cd ~/repos/sgt-demo/sketchpad-v2
$SGT feature rename 02004cfc "the relaxation solver"
cp -r .sgt /tmp/sgt-golden-v2
```

A full rebuild is the only supported repair path. The original sketchpad repo is the reason why.
In that repo, constraint types lived as entries in two const tables, but sgt's TypeScript grammar
doesn't have a symbol for entries inside a top-level const. The entries ended up in residue,
where only the newest edit ever reverts cleanly. In v2, each constraint type is its own file
under `src/kinds/` with its own import line, and sgt attributes both units exactly. The
masters-and-instances save is also split in v2 so that fastening and sizing are recorded as
separate saves.

**The app shows a stale picture after a revert.** Vite cached the old module. Hard-reload the
browser tab, and if that doesn't fix it, restart vite in the clone.

**The page comes up blank with a React hook error.** The clone is sharing a vite dependency
cache with another checkout. Run a real `npm ci` in the clone instead of using a symlinked
`node_modules`.

## 9. The numbers, if someone asks

| Claim | What proves it |
|---|---|
| 20 saves, five subtractable ideas | `sgt log --map` in `$DEMO` |
| every idea reverts by name, compiles, moves its sheet, and undoes exactly | `scripts/demo/check-ideas.sh`, 17 checks |
| the base drawing is held by the program, starting from genuinely imprecise geometry | the seed coordinates stored in `src/drawing.ts`, and the lattice solving in a single pass with 12 variables ordered on the plate |
| fastening costs 11 edits to remove, the solver costs 78 | `sgt show "fastened at the corners"` and `sgt show "the relaxation solver"` |
| the four grid variants run simultaneously from one repo | `scripts/demo/idea-grid.sh`, four ports responding |
