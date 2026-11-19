# ugh for VS Code

`ugh` tracks your code one definition at a time. A save is a git commit, and each save's change to
one definition is a step. You can give steps a name, take any of them out of the code, and bring
them back. This extension shows that history beside your code and runs the `ugh` CLI for every
read and every change.

## Surfaces

- **Workbench** (the `ugh` tab in the bottom panel). The timeline puts the saves left to right and
  one row per name. Select a cell, a row or a range of saves to see its steps and what taking them
  out would do, then take them out or bring them back. The ledger lists the same saves newest
  first. The playhead steps from save to save and drives the running app panel.
- **Status** (activity bar). It shows the check on HEAD, the files with unsaved edits (click one
  for its diff against HEAD), the two-version choices a sync left (click one to resolve it), and
  the plan, with each declared name built or missing.
- **Names** (activity bar). Each name has its step and save counts, and the unnamed steps come
  last. A row reveals the name in the workbench. Its buttons preview a take-out and rename it.
- **In the editor.** Hovering a definition's first line shows its name and its steps, newest
  first, with links to take it out or name it. A thin tint at the left edge shows each named
  definition in its name's colour (`ugh: Toggle blame`). A pending two-version choice is a warning
  with a Resolve quick fix. An inlay hint can show the name at the end of a definition's first
  line (`ugh.inlayHints.enabled`).
- **Status bar.** It shows the unsaved files, the pending choices and the check's verdict on
  HEAD, and a click opens the workbench.
- **Resolve.** It opens VS Code's diff editor with theirs on the left and your file on the right.
  You then take theirs, keep ours, or keep the file as you edited it.
- **Running app** (`ugh: Open running app`). It runs `ugh.render.devCommand` in a scratch copy of
  the tree, outside your repo. Moving the workbench's playhead shows the app as it was at that
  save.
- **Palette.** Save (with a message and an optional name), Undo, Sync, Push, Initialize, Take
  out, Bring back, Name, Rename, Resolve, and the definition-at-cursor versions in the editor's
  context menu. An address can be typed or picked from `ugh find` results as you type.

## Requirements

```bash
uv tool install ugh
cd your-project
ugh init --agent
```

`ugh init --agent` writes an absolute `ugh.path` into `.vscode/settings.json`, because a VS Code
started from the Dock does not see your terminal's PATH. A folder that has no ugh record yet shows
an Initialize action.

## Settings

| Setting | Default | |
| --- | --- | --- |
| `ugh.path` | `ugh` | The `ugh` executable. |
| `ugh.blame.enabled` | `true` | The tint at the left edge of named definitions. |
| `ugh.inlayHints.enabled` | `false` | A definition's name at the end of its first line. |
| `ugh.render.devCommand` | `npx vite --host 127.0.0.1 --port ${port} --strictPort` | What the running app panel serves the tree with. |

## Develop

```bash
npm install
npm run compile   # type-check and bundle to dist/extension.js
npm test          # node --test, Node 22.6 or newer
```

Press F5 to open an Extension Development Host. `dev/preview.html` renders the workbench from the
fixtures in `dev/fixtures/`.
