# Seedbank demo runbook

The recording script (`docs/design/2026-08-27-demo-recording-script.md`) says what the demo
argues. This file says which commands to type, in which window, in what order, and what you
should see when each one lands. Every command here has been run.

For a short video rather than the full six-beat take, read
`docs/design/2026-08-27-video-cuts.md` first. It covers a thirty second cut that needs one
command and no editor, and a two minute cut that needs two, and it lists what currently must
not go on camera.

If you are here because the app is not changing when you drag the playhead, read
"When the page does not change" at the bottom first.

---

## 1. Set two paths, once per terminal

Everything below uses these, so set them in every terminal you open.

```bash
export SGT_SRC=~/repos/semi-git          # the sgt source tree, see the check below
export DEMO=~/repos/sgt-demo/seedbank-v3
export SGT=$SGT_SRC/.venv/bin/sgt        # not the `sgt` on your PATH
```

The `sgt` on your PATH is probably not the one this demo needs. The demo depends on import
ownership, which means a feature can own an `import` line, so reverting the feature removes
the import too. An sgt without import ownership mines a store where nothing owns an import
line, and beat 6 then deletes `Tray.tsx` while leaving `import { TrayButton } from './Tray'`
above it. The app breaks with two `tsc` errors, and every other check still passes.

Check instead of assuming:

```bash
$SGT_SRC/.venv/bin/python -c "import sgt.core.op as o; print(o._symbol_kind('a.ts::__import__::./b'))"
# must print: import
```

If it prints `nested`, that tree is on `main`. Either check out `feat/live-render-timeline`
there, or point `SGT_SRC` at `~/repos/semi-git-render`.

---

## 2. Preflight, immediately before recording

```bash
bash $SGT_SRC/scripts/demo/demo-preflight.sh $DEMO
```

It rehearses every beat against a throwaway copy and leaves the demo repo untouched. There are
17 checks and you need all 17 before you start recording. It catches the wrong sgt, a dirty
tree, a label that moved on a rebuild, and a revert that no longer compiles.

As of 2026-08-27 it reports 16 passed, 1 failed, and the failure is real. The demo repo has a
`.vscode/settings.json` in it, written by a past session against the advice in section 5, so
the working tree is dirty. Move that one `sgt.path` setting into VS Code User settings and
delete the workspace copy.

Check the stylesheet too, because the preflight does not. The seedbank was restyled on
2026-08-27, to a white page with blue, green and orange, but the restyle lives in the build
inputs under `scripts/demo/seedbank/e*/tree/src/styles.css`. A repo built before it still has
the cream and serif version:

```bash
head -3 $DEMO/src/styles.css     # --paper: #ffffff means rebuilt, #faf7f0 means not
```

Rebuilding is section 7. Do it before filming rather than after, because it takes a few minutes
and it changes what beat 1 looks like: the tray counter becomes a square-cornered box rather
than a rounded pill, and every radius in the app is 3px.

---

## 3. Window layout

| where | what |
|---|---|
| Terminal A | the app, `npm run dev`, stays up the whole take |
| Terminal B | the sgt commands you type on camera, beats 2 and 6 |
| Terminal C | the provenance overlay, only for beat 4 |
| VS Code | the workbench and the render panel, beats 3 and 5 |
| Browser | `localhost:5173` for the app, `localhost:5174` for the overlay |

Terminal B is the only one on camera, so make its font large.

There are three separate copies of the app running by the end, and they are deliberately
separate. `localhost:5173` is your own dev server and it always shows the present. The overlay
on `localhost:5174` is a second dev server with an instrument layered on top. The render panel
is a third dev server that VS Code starts and manages by itself. None of them ever changes the
other two.

---

## 4. The beats, in order

### Beat 1, the present

In Terminal A:

```bash
cd $DEMO && npm run dev
```

Open http://localhost:5173. You should see the seed catalog, 24 varieties, a star on every
card, and a "tray empty" counter in the header. Before the restyle that counter is a rounded
pill on a cream page. After it, the counter is a square-cornered box on a white page, sitting
top right. Leave the server running for the whole session.

Say nothing here. The viewer just sees a real app.

### Beat 2, the history is not a list of commits

In Terminal B:

```bash
cd $DEMO
$SGT log --tree
$SGT intent list
```

The first command shows 13 features under named themes. The second names each feature's
chapters in English. That is the vocabulary the rest of the demo speaks in.

Hashes do appear here, which is worth knowing before you narrate it. Measured 2026-08-27.
`sgt log --tree` prints an eight character id in parentheses after every feature, and
`sgt intent list` prints `[f-033224873a4a]` on every feature line and `(f-03322487@1)` on every
chapter line. The demo's argument is that you never have to type a hash, which is still true
and is what beat 6 shows, but saying that no hash is on screen is wrong. `sgt intent list` also
truncates labels mid-word, so it reads as noise at video resolution. Film `log --tree` and
leave `intent list` for a viewer who pauses.

Do not run `sgt status` in the same take. It says 14 features where the tree says 13, on this
repo, at the same moment. Finding 72 in `docs/study/sgt-findings.md` has the cause. Features
with no parent are counted by one surface and dropped by the other.

### Beat 3, the silent gap

In VS Code, see section 5 for how to launch it, open the SGT Workbench panel and drag the
playhead from episode 3 to 4 to 5. At 3 and 4 the page does not move, because the whole search
engine lands invisibly. At 5 the search box appears.

If you want the measured evidence as a figure instead of a scrub:

```bash
bash $SGT_SRC/scripts/demo/render-frontiers.sh $DEMO
```

It renders every frontier and reports which ones changed pixels. Frontiers 3 and 4 are
byte-identical.

### Beat 4, hover a symbol and see its pixels

In Terminal C:

```bash
bash $SGT_SRC/scripts/demo/with-overlay.sh $DEMO
```

Then open http://localhost:5174. It is a second port on purpose, and it is a second script on
purpose. The overlay is a Vite plugin that draws provenance on top of the page, and anything
under the demo repo's `src/` or `tools/` gets mined into the demo's own feature graph. So the
script copies the app to a scratch directory, adds the overlay there, and reads blame from the
real repo. Your `npm run dev` from beat 1 keeps running and is not affected.

What to show:

- A hairline rail sits on the left edge. Move toward it and it widens into a list of symbols.
- Hover a symbol and its regions light up while everything else dims.
- Hover the page and a chip names the symbol and the feature that last changed it.
- Press backtick to hide the instrument for a clean take.

Two shots are worth filming. `TrayButton` lights exactly the 24 stars and nothing else.
`Chips` lights the TRAITS row in the header and the chips on every card, which is one symbol in
two distant regions, and that is the many-to-many claim in a single gesture. Both verified on
2026-08-27: 24 outlines and 25 outlines.

Do not read the rail's own number out loud for `Chips`. The rail says 159 and the highlight
draws 25, because the rail counts every stamped element and the highlight outlines only the
outermost element of each region. Both numbers are right and they count different things, which
is finding 71. `TrayButton` does not nest inside itself, so its 24 and 24 agree.

Putting `#sgt=<text>` in the URL deep-links to a lit state, so you can reproduce a figure
without a mouse.

### Beat 5, drag the playhead and the app becomes that frontier

Open the panel first. In VS Code, press Cmd+Shift+P and run
`sgt: Open Running App (render panel)`.

The panel does not open by itself, and no button in the workbench opens it. If you drag the
playhead without opening it first, nothing happens anywhere, and that is the expected
behaviour rather than a bug. The extension only spends a fold on a scrub when the panel is
already open.

The panel opens beside the graph as a VS Code tab whose title is "sgt — running app". Look
inside VS Code, not in your browser. The panel starts its own dev server on a free port,
pointed at a scratch copy of the folded tree under the extension's storage directory. Your
browser tab on `localhost:5173` is a different server showing the present, and it will never
move no matter how far you drag.

Once the panel is up, drag the playhead from 1 to 4 to 5. At 4 the ranking engine is in the
tree and the page does not move. At 5 the search box appears. Beat 3 asserted that from
screenshots, and here you watch it happen.

Each step takes about 320 to 377 ms, on one server that is never restarted. The bar above the
frame always names the frontier it is showing, and it dims the picture while the next one
folds.

### Beat 6, subtract from the present, the headline

In Terminal B:

```bash
$SGT revert "seed tray"
```

By name, with no hash and no symbol path. The preview names what goes and what stays, then you
confirm.

Cut to the browser on 5173. Every star is gone, the "tray empty" counter is gone, and nothing
else moved. It is not the app at a past commit. It is today's app minus one idea.

---

## 5. Launching VS Code with the render panel

The render panel lives in the extension built from `$SGT_SRC`, so build it once:

```bash
cd $SGT_SRC/editor/vscode && npm run compile
grep -c sgtRender dist/extension.js     # must print 1, not 0
```

Then:

```bash
code $SGT_SRC/editor/vscode
```

Press F5. A second VS Code window opens, titled `[Extension Development Host]`. In that second
window, open `$DEMO` as the folder.

If `grep` printed 0, you built the wrong tree. In that state the workbench looks completely
normal and the render panel command does not exist at all.

Point the extension at the same sgt as everything else. Put it in the dev host's User
settings, through Cmd+Shift+P and "Preferences: Open User Settings (JSON)":

```json
{ "sgt.path": "<the value of $SGT>" }
```

Use User settings rather than Workspace settings. A workspace setting is written to
`$DEMO/.vscode/settings.json`, which leaves an untracked directory in the demo repo, so the
preflight reports a dirty tree and refuses to pass.

Without the setting the extension shells out to the `sgt` on your PATH and re-derives the store
with it.

---

## 6. When the page does not change

Work through these in order.

First, check which window you are watching. Beat 5 renders into a VS Code tab titled
"sgt — running app", and it never touches `localhost:5173`. If your browser is the only thing
on screen, you are watching the server from beat 1, which always shows the present.

Second, check that the panel is open. Run `sgt: Open Running App (render panel)` from the
command palette before you touch the playhead. Scrubbing with the panel closed does nothing at
all, on purpose, because a drag fires continuously and each fold writes to disk.

Third, check that the command exists. If Cmd+Shift+P does not offer
`sgt: Open Running App (render panel)`, you are running an extension built from a tree without
the render panel. Rebuild with `npm run compile` in `$SGT_SRC/editor/vscode` and confirm
`grep -c sgtRender dist/extension.js` prints 1.

Fourth, if the panel opens but shows an error where the app should be, it is telling you why
the dev server did not start. It waits 30 seconds for a response. The usual cause is a missing
`node_modules` in `$DEMO`, since the panel symlinks the workspace's install into its scratch
directory rather than running its own `npm install`.

Fifth, if the bar reads correctly but the frame stays white, check that you are running an sgt
built after 2026-08-27. Before that date, materializing a fold rewrote every file even when the
bytes had not changed, so each scrub touched `vite.config.ts` and `tsconfig.json` and Vite
restarted the whole dev server rather than hot-replacing the files that differed. A restart that
lands while the panel's frame is still loading leaves it blank, and nothing retries. You can
confirm the server itself is healthy by opening its port in a normal browser, which you can find
with `ps ax | grep "vite --host 127.0.0.1"`.

---

## 7. Between takes, reset

`sgt revert` is reversible, and `restore` is its exact inverse:

```bash
cd $DEMO && $SGT restore "seed tray"
git -C $DEMO status --porcelain    # must print nothing
```

`sgt undo` also works. The preflight verifies that the round trip is byte-identical, so if
`git status` shows anything, do not start the next take.

For a full rebuild, if a take corrupts the repo beyond a restore:

```bash
SGT=$SGT $SGT_SRC/scripts/demo/build-seedbank.sh $DEMO   # FORCE=1 to replace
```

It takes a few minutes. The feature labels come from the episode messages, so `"seed tray"`
survives a rebuild, but feature ids do not. Never paste an id from an old run.

---

## 8. Do not improvise these on camera

Do not revert a feature other than `seed tray`. Six of the seven original features break the
build when reverted, because later work legitimately builds on them, and
`2026-08-27-import-ownership.md` section 7 explains why. Only a leaf reverts cleanly.

Do not type `sgt revert` with loose natural language. It falls into a disambiguation menu that
ends in `re-invoke: sgt revert 9df47906`, which is a hash, the exact thing this demo argues
against. Use the exact label.

Do not describe hover-to-highlight as an editor feature. Beat 4 is a browser overlay that
draws in the page, not in the editor gutter.

---

## 9. Rough edges that are visible but survivable

The CLI echoes a feature id on the rewind line, and again under it as `sgt revert 0934ac87`. It
used to be the full 64 characters and is now 8, so it no longer dominates the frame, but a
careful viewer still sees a hash in a demo that argues against them.

`styles.css` keeps its `.tray-*` rules after the revert. CSS is opaque-tier, meaning one symbol
per file, and that edit belongs to another feature. Unused rules render nothing, so it is
invisible on camera, but the tree is not perfectly clean.

A search that matches nothing leaves the page empty below the header rule, because the app has
no empty state. Do not film a query with no results.
