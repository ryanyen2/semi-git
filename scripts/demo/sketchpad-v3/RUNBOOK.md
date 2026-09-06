# Recording the sketchpad-v3 take

Four shots, about three minutes, one per step of the paper's walkthrough. You read a history you
didn't write, find the one piece of work behind a behaviour, let an agent extend it, and then take
it out to see the program without it.

The demo repo is `~/repos/sgt-demo/sketchpad-v3`, which is twenty commits plus the golden record
at `~/repos/sgt-demo/.sgt-golden-v2`. Neither is in this repository, so ask Ryan for both and put
them under `~/repos/sgt-demo/` before you start. You never record in the demo repo. `stage.sh`
clones it into a throwaway directory with a fresh copy of the golden record and starts vite, and
everything below runs in that clone.

Every number here was measured on sgt 0.6.10 against the record re-cut on 2026-09-05. The previous
record is kept at `~/repos/sgt-demo/.sgt-golden-v2-pre-carve-2026-09-05`.

## Before recording

```bash
SGT_SRC=~/repos/semi-git
bash $SGT_SRC/scripts/demo/sketchpad-v3/check-v3.sh      # want: 17 passed, 0 failed
bash $SGT_SRC/scripts/demo/sketchpad-v3/stage.sh         # prints the take dir, vite on :5501
```

The preflight runs the whole take with the agent scripted, so a failure there means the take will
fail on camera. It takes a few minutes because it typechecks and photographs each step.

Put the browser on http://localhost:5501/ on the left and a terminal on the right, with its
working directory set to the take directory. Start Claude Code in that same directory so its sgt
MCP server points at the take. Say the word "sgt" out loud only after shot 1 has shown it.

On camera the agent is Claude Code answering `request.txt`. If it goes off script,
`agent.sh <take-dir>` makes the same edit and produces the same save.

## Rules

1. Record only in a directory `stage.sh` made. Run `stage.sh` again for a second attempt, which
   discards the previous clone.
2. Don't run `sgt log --refresh` or `sgt log --rebuild` during a take. A refresh keeps the hand
   named features exactly, which was fixed in 0.6.10, and it still re-clusters and renames the
   machine named rows, so the map you rehearsed stops matching the map you record.
3. `sgt save` is safe. The take depends on it.

## Shot 1, the history as sgt presents it (30 s)

Show the lattice on sheet 2, then run:

```bash
git log --oneline | head -20
sgt log --map
```

> "Twenty commits, one per request the agent was given, and eighteen of them touch more than one
> piece of work, which is what makes a commit an awkward thing to undo. The map is the same
> history arranged by what the work was about. Each row is one feature, the blocks on a row are
> its checkpoints, and the headers group features into subsystems."

The map prints `17 features · 20 saves · 5 subsystems`. Point at `fastened at the corners` under
`Geometric Constraints`, which is what shots 2 to 4 are about.

The first line says two saved edits are not shown and suggests `sgt log --refresh`. Don't run it.
The two edits are prune operations on `App.draw` and `Scope.track` and nothing here depends on
them.

## Shot 2, finding the feature (30 s)

```bash
sgt find "hexagon groups held together at their corners"
sgt show c69fc3e
sgt show "fastened at the corners"
```

> "The search phrase is what I can see on screen, not anything from the code. The third result is
> the request that fastened the groups by their corners. The save it landed in holds 35 edits and
> reverting the save would cost 56. The feature those edits belong to is 11 edits over two saves,
> and the feature is the unit I can act on."

Expect `find` to rank c69fc3e third at 0.56, `show c69fc3e` to report 35 edits and 10 symbols in 4
files with a revert cost of 56 edits and 32 built on top, and `show "fastened at the corners"` to
report 11 edits in `src/kinds/T.ts` across saves c69fc3e and 535846c.

## Shot 3, the agent makes the change (60 s)

Paste `request.txt` into Claude Code.

> Set the six outer hexagon groups apart from the centre group so the seams between the groups
> show.

The agent calls `sgt_plan_intake` before editing, with one step bound to `fastened at the
corners`, edits `src/kinds/T.ts`, typechecks, and calls `sgt_save` in its own words. Reload the
browser. The seven groups sit apart and the console still reads `ONE PASS . 12 VARIABLES ORDERED`.

Point at the save echo, which says the save was filed into `fastened at the corners` and the plan
step was fulfilled by one operation. Then run:

```bash
sgt log --focus "fastened at the corners"
```

> "The feature now has two checkpoints. The first is the eleven edits from the original saves, and
> the second is the two edits the agent just made, under the agent's own words. Its work joined the
> history of the same piece of work instead of arriving as an unrelated commit."

If the agent skips the plan or edits other files, stop, run `git checkout -- . && git clean -fd
src`, and use `agent.sh <take-dir>`.

## Shot 4, taking the fastening out (50 s)

```bash
sgt revert "fastened at the corners"          # the preview; nothing is applied
sgt revert "fastened at the corners" --yes    # then reload
```

> "sgt states the cost before anything changes. It removes 13 edits across two files, one
> neighbouring feature gains an edit, and 24 other features are untouched."

After applying, the groups fall where the light pen dropped them, the program still compiles, and
the console changes to `RELAXATION . 4 PASSES`, because the fastenings were what let the solver
finish in one pass.

```bash
sgt undo    # reload; the seam lattice is back
```

> "Restored byte for byte, including the agent's seam."

## What the take leaves behind

After the agent's save the map gains two singleton rows, `sgtLoc.transform.visit` and `App.press`,
which are the two stale edits from shot 1 arriving as their own lanes. They push the map over its
row budget, so `Geometric Constraints` folds back into one row. Show the map in shot 1 only. To put
the feature on screen after the save, use `sgt log --focus "fastened at the corners"`, which shot 3
already does.

## The narration free version

```bash
python3 $SGT_SRC/scripts/demo/sketchpad-v3/render-video.py [<out-dir>]   # -> demo.gif + frame-*.png
```

It stages its own take, runs the same commands, and renders each step as a frame with the terminal
on the left and the app on the right. It needs Chrome, node, vite, sgt and PIL. Use the GIF where a
voice recording isn't wanted, and cut the PNG frames into an mp4 if you need video.

## After the take

```bash
pkill -f "vite --port 5501"; rm -rf ~/repos/sgt-demo/.take-sketchpad-v3
```

## If the golden record is missing

Rebuild it from sketchpad-v2 as the last section of `../SKETCHPAD.md` describes, then
`cp -r ~/repos/sgt-demo/sketchpad-v2/.sgt ~/repos/sgt-demo/.sgt-golden-v2`. Keep the copy out of
`/tmp`, because macOS empties `/tmp` file contents and leaves the directories, so a stale copy
looks present and restores nothing.
