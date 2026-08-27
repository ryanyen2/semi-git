# Seedbank demo — the operator's runbook

The recording *script* (`docs/design/2026-08-27-demo-recording-script.md`) says what the demo
argues. This says which commands to type, in which window, in what order, and what you should
see when each one lands. Every command here has been run.

---

## 0. Set two paths, once per terminal

Everything below uses these. Set them in **every** terminal you open.

```bash
export SGT_SRC=~/repos/semi-git          # the sgt source tree — see the note below
export DEMO=~/repos/sgt-demo/seedbank-v3
export SGT=$SGT_SRC/.venv/bin/sgt        # NOT the `sgt` on your PATH
```

### The one trap, named up front

**`sgt` on your PATH is not necessarily the `sgt` this demo needs.** The demo depends on
import ownership (a feature can own an `import` line, so reverting it removes the import).
An sgt without that mines a store in which *nothing* owns an import line — and beat 6 then
deletes `Tray.tsx` while leaving `import { TrayButton } from './Tray'` sitting above it. The
app breaks with two `tsc` errors, and every other check still passes.

Check, don't assume:

```bash
$SGT_SRC/.venv/bin/python -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))"
# must print: import        (if it prints `nested`, this tree is on the wrong branch)
```

`SGT_SRC` must be a tree with `feat/live-render-timeline` checked out. If it is
`~/repos/semi-git` and that prints `nested`, that tree is still on `main` — either check the
branch out there, or point `SGT_SRC` at `~/repos/semi-git-render`.

---

## 1. Preflight — run this immediately before recording

```bash
bash $SGT_SRC/scripts/demo/demo-preflight.sh $DEMO
```

Rehearses every beat against a throwaway copy and leaves the demo repo untouched. **16 passed,
0 failed** or do not start recording. It catches the wrong-sgt trap, a dirty tree, a label that
moved on a rebuild, and a revert that no longer compiles.

---

## 2. Window layout

| where | what |
|---|---|
| **Terminal A** | the app — `npm run dev`, stays up the whole take |
| **Terminal B** | the sgt commands you type on camera (beats 2 and 6) |
| **Terminal C** | the overlay — only for beat 4 |
| **VS Code** | the workbench + render panel — beats 3 and 5 |
| **Browser** | `localhost:5173` (app), `localhost:5174` (overlay) |

Terminal B is the only one on camera. Make its font large.

---

## 3. The beats, in order

### Beat 1 — the present

**Terminal A:**
```bash
cd $DEMO && npm run dev
```
Open **http://localhost:5173**. Seed catalog, 24 varieties, stars on every card, "tray empty"
pill in the header. Leave this running for the whole session.

Say nothing. The viewer just sees a real app.

### Beat 2 — the history is not a list of commits

**Terminal B:**
```bash
cd $DEMO
$SGT log --tree
$SGT intent list
```
First shows 13 features under named themes. Second names each feature's chapters in English.
This is the vocabulary the rest of the demo speaks in — no hashes appear on camera.

### Beat 3 — the silent gap

In **VS Code** (see §4 for how to launch it), open the **SGT Workbench** panel and drag the
playhead: **episode 3 → 4 → 5**.

- at 3 and 4 the page does **not** move — the whole search engine lands invisibly
- at 5 the search box appears

The measured evidence, if you want it as a figure rather than a scrub:
```bash
bash $SGT_SRC/scripts/demo/render-frontiers.sh $DEMO
```
It renders every frontier and reports which ones changed pixels. 3 and 4 are byte-identical.

### Beat 4 — hover a symbol, see its pixels

**Terminal C:**
```bash
bash $SGT_SRC/scripts/demo/with-overlay.sh $DEMO
```
Then open **http://localhost:5174** — a *second* port, deliberately. The overlay runs from a
scratch copy so it never lands in the demo repo's own history.

- hairline rail on the **left edge** — approach it and it widens into a list of symbols
- hover a symbol → its regions light, everything else dims
- hover the page → a chip names the symbol and the feature that last changed it
- **backtick** hides the instrument for a clean take

Two shots worth filming:
- `TrayButton` → exactly the 24 stars, nothing else
- `Chips` → the TRAITS row **in the header** *and* the chips on **every card**. One symbol,
  two distant regions — the many-to-many claim, in one gesture.

`#sgt=<text>` in the URL deep-links to a lit state, so a figure is reproducible without a mouse.

### Beat 5 — drag the playhead, the app becomes that frontier

In VS Code, **Cmd+Shift+P → `sgt: Open Running App (render panel)`**.

> The panel does **not** open by itself, and no button in the workbench opens it. If you drag
> the playhead without opening it first, nothing happens — that is the expected behaviour, not
> a bug.

It opens beside the graph, boots its own dev server against a scratch fold, then drag the
playhead: **1 → 4 → 5**. At 4 the ranking engine is in the tree and the page does not move; at
5 the search box appears. Beat 3 asserted that from screenshots; here you watch it happen.

~320–377 ms per step, one server, never restarted. The bar above the frame always names the
frontier it is showing and dims the picture while the next one folds.

### Beat 6 — subtract from the present — **the headline**

**Terminal B:**
```bash
$SGT revert "seed tray"
```
By name. No hash, no symbol path. The preview names what goes and what stays; confirm.

Cut to the browser on **5173**: every star is gone, the "tray empty" pill is gone, **nothing
else moved**. Not the app at a past commit — today's app minus one idea.

---

## 4. Launching VS Code with the render panel

The render panel lives in the extension built from `$SGT_SRC`. Build it once:

```bash
cd $SGT_SRC/editor/vscode && npm run compile
grep -c sgtRender dist/extension.js     # must print 1, not 0
```

Then:
```bash
code $SGT_SRC/editor/vscode
```
Press **F5**. A second VS Code window opens (`[Extension Development Host]`). In **that**
window, open `$DEMO` as the folder.

**If `grep` printed 0, you built the wrong tree** — that is exactly the state where the
workbench appears normally and the render-panel command does not exist at all.

Point the extension at the same sgt as everything else, in the dev host's settings:
```json
{ "sgt.path": "<the value of $SGT>" }
```
Otherwise the extension shells out to PATH's sgt and re-derives the store with it.

---

## 5. Between takes — reset

`sgt revert` is reversible, and `restore` is its exact inverse:

```bash
cd $DEMO && $SGT restore "seed tray"
git -C $DEMO status --porcelain    # must print nothing
```
`sgt undo` also works. The preflight verifies this round-trips byte-identically — if `git
status` shows anything, do not start the next take.

Full rebuild, if a take corrupts the repo beyond a restore:
```bash
SGT=$SGT $SGT_SRC/scripts/demo/build-seedbank.sh $DEMO   # FORCE=1 to replace
```
Takes a few minutes. The feature *labels* come from the episode messages, so `"seed tray"`
survives a rebuild — but feature **ids** do not. Never paste an id from an old run.

---

## 6. Do not improvise these on camera

- **Reverting a feature other than `seed tray`.** Six of the seven original features break the
  build when reverted, because later work legitimately builds on them
  (`2026-08-27-import-ownership.md` §7). Only a leaf reverts clean.
- **`sgt revert <loose natural language>`** falls into a disambiguation menu that ends in
  `re-invoke: sgt revert 9df47906` — a hash, the exact thing this demo argues against. Use the
  exact label.
- **Hover-to-highlight is a browser overlay, not an editor feature.** Beat 4 is in the page,
  not in the gutter. Don't claim otherwise.

## 7. Rough edges that are visible but survivable

- The CLI echoes a full 64-character feature id on the rewind line. Ugly, not wrong.
- `styles.css` keeps its `.tray-*` rules after the revert — CSS is opaque-tier (one symbol per
  file) and that edit belongs to another feature. Unused rules render nothing, so it is
  invisible on camera, but the tree is not perfectly clean.
- The "tray empty" pill sits between the `<h1>` and the tagline and reads slightly oddly.
