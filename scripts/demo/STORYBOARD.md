# Sketchpad demo storyboard

What to type, what to point at, and what you should see. Every command here has been run against
`~/repos/sgt-demo/sketchpad` at commit 56b79bf.

`RUNBOOK.md` is the setup and the preflight. Read it first, once. This file is the take.

There are two cuts. The short cut is one screen and about forty seconds. The long cut is three
windows and about four minutes. The long cut contains the short one, so you can record the long
cut and pull the short one out of it.

---

## What the subject is, in one line each

Say this or something like it over the first shot. Do not say more than this.

- Sketchpad was Ivan Sutherland's 1963 PhD program. You draw with a light pen, and you tell the
  computer conditions the drawing has to satisfy.
- The program then moves the drawing until the conditions hold.
- This is a reimplementation of it, built in nineteen steps, and sgt recorded every step.

Do not explain sgt before the first shot. Show the drawing, then show that the drawing has a
history you can walk.

---

## Short cut, about forty seconds

One window: the app in a browser at `http://localhost:5174/`. No terminal.

| # | Do | On screen | Say |
|---|---|---|---|
| 1 | Nothing. Hold three seconds. | Hexagon lattice. Bottom line reads `RELAXATION . 12 PASSES`. | "Seven groups of seven hexagons, fastened at their corners." |
| 2 | Click `SEMICIRCLE`. | Every hexagon becomes a semicircle. Fish scales. | "One switch changes the master. Every copy of it changes, at every depth." |
| 3 | Click `SEMICIRCLE` again. | Back to hexagons. | Nothing. |
| 4 | Click `ZIGZAG`. | A row of nine zigs. Bottom line changes to `ONE PASS . 16 VARIABLES ORDERED`. | "Same solver. This drawing it can solve outright, in one pass." |
| 5 | Click `FREEDOMS`. | Sixteen numbered circles, 1 to 16, left to right. | "That is the order it works in. It finds the last one first and fills backwards." |
| 6 | Hold two seconds. | Unchanged. | Nothing. |

Stop recording. That is the cut.

The two switches in shot 4 and 5 are the point of the whole thing. The hexagons need four hundred
passes of guessing and the zigzag needs none, and the difference is only how they are fastened.

---

## Long cut, about four minutes

Three windows. See `RUNBOOK.md` section 3 for the layout.

### Beat 1, the drawing (about 30 seconds)

Browser only. Run the short cut, shots 1 to 5.

End with `ZIGZAG` and `FREEDOMS` both on.

### Beat 2, the history is legible (about 40 seconds)

Switch to the terminal.

```bash
sgt log --map
```

Point at three things and nothing else.

- Eleven rows. "Eleven features. Nobody typed these names except where I corrected them."
- The bars. "Each row is one feature. Each column is one save. The bar is how much of that save
  landed in that feature."
- The bottom row, `show the solving order`, 17 edits, only in the last column. "That is the
  numbered circles you just saw. Newest thing in the program."

Then:

```bash
sgt show "show the solving order"
```

Point at the `symbols` line and the last line before `next`.

- "Five files. One of them, `Freedoms.tsx`, exists only because of this."
- "`reverting this removes 17 edits`. It knows the size of what it would take out before it does
  anything."

### Beat 3, walk the history (about 60 seconds)

Switch to the timeline page: `file:///tmp/sketchpad-timeline.html`.

Drag the slider from the far left to the far right, slowly, once. Then stop at three places.

| Stop at | What is on screen | Say |
|---|---|---|
| save 1 | An empty scope with a stored drawing. | "Save one. The scope, and a file that loads." |
| save 8 | A hexagon in a circle, with lettered circles on it. | "Save eight. Six corners on the circle, five statements that the sides are equal, and the program moved the corners until both held." |
| save 18 | The hexagon lattice. | "Save eighteen. Same file, more program." |

Then point at the ticks under the slider, which are darker where the picture moved.

- "Every one of these is that commit, folded onto disk, served, and photographed. Eighteen of
  nineteen changed the picture."
- Point at the one pale tick. "That one did not. It is the save that turned constraint types into
  table entries. The program got easier to extend and the drawing did not move. Both are true and
  the timeline says so."

### Beat 4, subtract something (about 60 seconds)

Back to the terminal, in a scratch clone. **Never run this in the demo repo on camera.** See
`RUNBOOK.md` section 5.

```bash
sgt revert "show the solving order"
```

Stop on the preview before confirming. Point at:

- The lane, `17→0 edits`, and the one checkpoint marked `removed`.
- `removes 17 edits across 5 symbols · 6 files`.
- `13 other features unchanged`.

Say: "It resolves the name, not a commit and not a file. This is the feature, and this is what
leaving it out would cost."

Confirm. Then, in the browser on the scratch clone's port:

- The `FREEDOMS` switch is gone from the bank. Five switches became four.
- Everything else works. Toggle `ZIGZAG`. The zigzag still draws and still says `ONE PASS`.

Say: "The overlay is gone. `Freedoms.tsx` is gone. Nothing else moved, and the program still
compiles and still runs."

Then:

```bash
sgt undo
```

Toggle `FREEDOMS` back on in the browser. The numbers are back.

### Beat 5, close (about 20 seconds)

Back to `sgt log --map`.

Say: "Nineteen saves. Eleven features. Every one of them can be walked to, looked at, and taken
back out by name."

Stop recording.

---

## What not to do on camera

- Do not run `sgt log --refresh` or `sgt log --rebuild`. Both rewrite what
  `sgt revert "show the solving order"` removes, and the take stops working. See findings 79
  and 80.
- Do not run `sgt save`. Same reason.
- Do not open sheet one with `CONSTRAINTS` off. It is a circle and a hexagon and reads as nothing.
- Do not narrate the solver. The plate line says which method ran, and that is enough.
- Do not say the word "feature" before beat 2. In beat 1 they are switches and drawings.

## The numbers, if you are asked

| Claim | Command that proves it |
|---|---|
| 19 saves, 11 features | `sgt log --map` |
| 18 of 19 frontiers changed the picture | `scripts/demo/render-frontiers.sh` output table |
| the revert removes 17 edits in 6 files | `sgt revert "show the solving order"` preview |
| the counterfactual compiles | `npx tsc --noEmit` after the revert, 0 errors |
| undo is exact | `diff -rq src <pristine>/src` after `sgt undo` |
| one pass beats relaxation here | the plate line, `ONE PASS . 16 VARIABLES ORDERED` against `RELAXATION . 12 PASSES` |
