# VS Code extension

The extension shows which feature owns each line of code, draws the feature tree as a rail
alongside a commit timeline, and lets you merge, split, rename, move, or revert a feature from the
editor. It also drives sgt's git bridge -- switch, save, undo, sync, push, land -- so day-to-day
git work never has to leave VS Code. It never edits your code directly. It calls `sgt`'s read
views and its feature, kernel, and git-bridge verbs.

An activity-bar container (`semi-git`) holds four tree views -- Features, Forks, Changes, and
Compositions -- and a **Composition Workbench** webview panel gives the same data a full-window,
rail3-style layout with an inspector and a `code(I)` panel driven by `sgt fold --at`. A workspace
with no `.sgt` store yet still activates the extension; the Features view shows an
**Initialize semi-git** welcome action (`sgt.init`) and a matching walkthrough
(**Get started with semi-git**) instead of an error.

## Install from source

```bash
cd editor/vscode
npm install
npm run compile        # type-check and bundle to dist/extension.js
```

Press `F5` in VS Code to launch an Extension Development Host, or package it with `npx vsce
package` and install the `.vsix`. The extension activates in any workspace that has
`.sgt/local/ideal.json`, which `sgt init` writes. It calls the `sgt` on your `PATH`, which you can
override with the `sgt.path` setting.

## What you get

| Surface | What it shows |
| --- | --- |
| Feature blame | A colored tint per line, by the feature that owns it (`blame_view`) |
| Composition Workbench | A rail view of the feature tree (hierarchy + commit timeline) and an inspector with a `code(I)` panel, with a hover preview on every action that changes state |
| `sgtFeatures` / `sgtForks` / `sgtChanges` / `sgtCompositions` (activity bar) | The feature tree; open forks (badge = count); drift, unmanaged paths, and the trust queue; active plan sessions and proposals |
| Plan CodeLens and status bar | Lines that match or drift from the active `sgt plan` session (U14) |
| Revert preview | A read-only diff of what reverting a feature would change, before you commit |
| Git bridge | Palette commands + an always-visible status-bar oracle chip for `switch`/`save`/`undo`/`sync`/`push`/`land` |
| Fork resolution | An N-column view of a fork's tip images, plus the `merge-op` → hand-edit → `fulfill` → `land` wizard |
| Hovers, diagnostics, inlay hints | A symbol hover with label/rationale/coupling; a Hint on drifted spans (with a "Save to clear" quick-fix) and a Warning on forked ones; an opt-in `‹feature ·N ops›` inlay hint |
| Trust queue and proposal review | Acknowledge mined ops from `sgtChanges`; partial-accept land and publish a proposal as a GitHub PR from `sgtCompositions` |

## A plain walkthrough

If you've never used this before, here's what a real session looks like, step by step. No prior
knowledge of semi-git's internals required.

### 1. Open a project for the first time

Open a folder in VS Code. If it hasn't been set up with semi-git yet, you'll see a `semi-git` icon
in the activity bar (the icon strip on the far left) with a single button: **Initialize
semi-git**. Click it. That runs `sgt init` for you and starts a short walkthrough that checks the
`sgt` command is on your machine and opens the workbench for the first time.

### 2. Write code like you normally would

You, or your AI coding agent, write and save code exactly as before -- semi-git never touches your
files. As you open a file, every line gets a thin colored stripe in the left margin and a faint
tint in the background. Each color is a different **feature** -- a piece of functionality
semi-git has automatically grouped that line into (for example, "user login" or "PDF export").
Hover over any line to see that feature's name.

### 3. Check what's new

Click the `semi-git` icon in the activity bar. You'll see four lists:

- **Features** -- everything semi-git knows about, as a tree (bigger areas contain smaller ones).
- **Forks** -- places where two different versions of the same code disagree and need a decision
  from you. Empty most of the time.
- **Changes** -- edits that haven't been checked in yet, files semi-git doesn't understand yet, and
  a queue of edits waiting for someone to glance at them.
- **Compositions** -- your branches, any in-progress planning sessions, and proposals waiting to be
  merged in.

If **Forks** has a number badge on it, something needs your attention before you can land your
work (see step 7).

### 4. Open the full picture

Run **semi-git: Open Composition Workbench** from the command palette (`Cmd/Ctrl+Shift+P`), or
click the status-bar icon at the bottom of the window. This opens a full tab with three parts side
by side: the feature tree on the left, a timeline of every change across the top, and a detail
panel on the right. Click any feature to see its details and its actual code on the right.

### 5. Look back in time

Above the timeline there's a small handle -- the playhead. Click anywhere on the timeline, or drag
the handle, and the code panel on the right instantly updates to show what the code looked like at
that exact point, plus a green check or red X for whether the checks would have passed then.
Nothing changes on disk while you do this -- it's only a preview. Press `Esc`, or click "Back to
current", to return to normal.

### 6. Reorganize a feature

Click a feature in the tree, then use the buttons above the code panel: **Rename**, **Merge
into**, **Split**, **Move**, **Revert**. Hovering any of these paints the affected code in orange
(code that would change) or blue (code this depends on) before you commit to anything. For quick,
safe actions -- rename, merge, split, move -- clicking just does it, and a small "Undo"
notification appears afterward. For actions that can touch a lot of code, like revert, clicking
first "arms" the action: the effect is previewed, and you confirm with `Enter` or back out with
`Esc`.

### 7. Resolve a conflict

If **Forks** shows a badge, click it, pick the conflict, and semi-git opens a side-by-side view:
one column per version of the code that disagrees. Follow the buttons in order:

1. **Draft merge** -- semi-git prepares a starting point that reconciles both versions.
2. **Open affected files** -- jump straight to the files you need to edit by hand.
3. Edit the code until it looks the way you want.
4. **Fulfill from working tree**, then **Land** -- semi-git records your edit as the resolution and
   finishes the merge.

### 8. Use it instead of the git commands you already know

Six commands, on the palette or the status bar, cover what you'd normally type into a terminal:

| Instead of typing... | Run this command |
| --- | --- |
| `git checkout <branch>` | **Switch Branch** |
| `git add -A && git commit` | **Save** |
| `git reset --hard HEAD~1` | **Undo Last Ideal Edit** |
| `git fetch && git merge` | **Sync** |
| `git push` | **Push** |
| merging a branch in on the server | **Land Branch** |

**Save** always runs immediately -- it can't lose work. **Push** and **Land Branch** ask you to
confirm first, since both affect other people. **Land Branch** refuses to run at all while any
conflict (fork) is open, and tells you which one.

### 9. Review what an agent, or a teammate, did

Open **Changes** in the sidebar. Anything semi-git picked up automatically that wasn't part of an
active plan shows up as **drift** -- right-click a line flagged this way in the editor and use the
quick-fix to save and clear it. The **trust queue** below it lists edits grouped by who or what
made them; right-click a group and choose **Acknowledge** once you've looked it over.

Under **Compositions**, a proposal (a bundle of finished work waiting to be merged in) has two
right-click actions: **Land Proposal** (merge it in -- if it covers more than one feature, you get
a checklist to land only some of them) and **Publish Proposal** (push it and open a GitHub pull
request).

That's the whole loop: write code, glance at the sidebar, open the workbench for the bigger
picture, resolve conflicts through the wizard, and drive git through the six bridge commands
instead of the terminal.

### Feature blame

Each line is tinted in its owning feature's color, on the background, the left border, and the
overview ruler. Hovering shows the feature's label and id. Toggle it with **semi-git: Toggle
Feature Blame** (`sgt.blame.enabled`). Colors come from the feature id through a deterministic
hash in `src/color.ts`, so the same feature always gets the same color in the gutter and in the
feature map, and the colors adjust to the theme for contrast.

### Composition Workbench

**semi-git: Open Composition Workbench** (`sgt.openWorkbench`) opens a webview panel with a rail
pane (tree + timeline) and an inspector pane, grounded in
`experiments/patch_clustering/out/rail3.html`. It reads one `sgt compose --json` (`compose_view`)
per refresh instead of separate map/history/status/... calls.

- The rail's left region is the feature tree from `map_view`. Subsystems and features are listed
  in DFS order and indented by depth, each one collapsible, with a colored dot, a label, and a
  size bar.
- The rail's right region, past a divider, is a shared commit-index axis from `history_view`.
  Every mined commit appears in order. Each feature has a lifebar from its first op to its last,
  and a glyph for each op at its commit index. The glyphs use the kernel's op-kind names: `◆` add,
  `+` extend, `~` rework, `−` prune, `⋔` move, `⋈` merge, and `·` touched.
- The connectors between features are cross-feature dependencies from `map_view`'s `edges`, which
  come from the coupling graph rolled up to feature pairs. Each node shows up to a threshold of
  them, and the overflow count is reported rather than dropped.
- The titlebar's composition button opens a picker over HEAD and active plan sessions (by branch);
  picking one re-folds the inspector's code panel at that ref. The oracle chip mirrors
  `status_view.oracle`.

Hovering a row or an edge dims everything else and highlights the hovered node and its dependency
neighbors. Color always means identity. Status is shown by a glyph or a stroke, never by a second
color. Clicking a feature opens the inspector with its label, rationale, size, an action bar
(Rename, Merge into, Split, Move ops, Revert), and a `code(I)` panel: the feature's files folded
at the selected composition, via one side-effect-free `sgt fold --at <ref> --json` call (never
materializes the working tree).

Hovering Split or Revert runs the real `plan_split` or `plan_revert_feature` preview live and
paints the features it would affect. For Revert this can cover more than the one feature you
named, because it is the real closure of the kernel edit rather than a guess. Merge into and Move
ops arm a mode where you pick a target. Hovering a candidate feature previews the merge or move
against it, and clicking confirms and applies it. Every preview is read-only, through `sgt preview <verb> ...
--json`. Only a click on Split or Revert, or a confirmed Merge or Move target, writes anything,
through `sgt merge`, `split --apply`, `rename`, `move`, or `revert`.

A draggable playhead over the timeline scrubs an arbitrary commit-index frontier, not just
HEAD/sessions. Click anywhere on the timeline (or drag the handle above it) and the inspector's
code panel switches to `code(I)` at that frontier -- via `sgt fold --at <commit-index> --json`,
debounced and snapped to the axis's op columns -- with the frontier's own oracle verdict and an
"I·N" op-count flag, plus a "Back to `<composition>`" button and `Esc` to return to the normal
view. It never changes what the action bar previews or applies against; that always stays the
selected composition. If a feature is selected, the code panel filters to that feature's files by
the same `dir`-prefix match the composition code panel uses, without a re-fold per selection
change while dragging.

### Git bridge

Six palette commands (**semi-git: Switch Branch**, **Save**, **Undo Last Ideal Edit**, **Sync**,
**Push**, **Land Branch**) drive sgt's D3 daily-loop verbs and its network verbs, so `git
checkout`/`stash`/`reset`/`pull`/`push` never have to run directly against a `.sgt`-tracked repo:

- **Switch** mines the current ref first (nothing is lost), then checks out the branch you pick or
  type, and re-mines it -- `sgt switch <branch>`.
- **Save** mines the working tree and, if there are uncommitted ops, materializes a witness commit
  for the resulting ideal -- `sgt save [-m <message>]`. It never destroys anything, so it runs
  immediately, no confirmation.
- **Undo** pops the last recorded ideal edit and restores the prior ideal exactly, as a fresh
  forward commit (history is append-only) -- `sgt undo`.
- **Sync** fetches a remote/branch and unions the op store, surfacing any same-symbol fork instead
  of doing a textual merge -- `sgt sync [remote] [branch]`.
- **Push** and **Land Branch** are confirmed first, since both write to shared/remote state --
  `sgt push [remote] [branch]` and `sgt land <branch>` (the U23 CAS shared-branch advance). Land
  refuses up front, with a link to `sgtForks`, while any fork is open.

The status bar's oracle chip (`oracle: ✓`/`✗`/`…`/`○`, plus `◊N` when forks are open) mirrors
`status_view.oracle` and is always visible, even when the oracle isn't configured yet. Clicking it
opens the Composition Workbench.

### Fork resolution

A fork is the one true conflict in sgt -- two chain tips claiming the same symbol -- and it blocks
`land` until it's resolved. `sgtForks` lists every open fork (its badge is the open count); an
editor diagnostic (below) marks the conflicting symbol in place. **semi-git: Resolve Fork**, or the
tree item's context menu, opens a webview with one column per tip, each showing that tip's full
image of every file the fork touches (from `fork_detail_view`, no extra fold call). The wizard
walks the real kernel verbs, one confirm at a time, and never guesses at a merge on your behalf:

1. **Draft merge** runs `sgt merge-op <tip-a> <tip-b>`, which drafts a hollow op reconciling the
   two tips (you can give it an intent string first).
2. **Open affected files** jumps you to the working tree so you can hand-edit it to the reconciled
   result you want.
3. **Fulfill from working tree** runs `sgt fulfill <draft-id> --from-tree`, which stages your edit
   as the drafted hollow's image.
4. **Land** runs `sgt commit`, committing the staged candidate (gated on the oracle, same as
   every other commit).

`sgt pin` is not offered as an alternative here -- it exists only as a core-kernel verb, with no
CLI entry point to call.

### Hovers, diagnostics, and inlay hints

Hovering a symbol shows its feature label and id, the tree's own rationale for why it's grouped
that way (`map_view`'s `why`), its op count and size, and up to five features it's coupled with
(`map_view`'s cross-feature `edges`) -- plus links to preview a revert or open the workbench. This
sits alongside the simpler whole-line blame hover; VS Code stacks both.

Two `DiagnosticSeverity` classes light up as you edit, each toggled by its own setting:

- **Drift** (`sgt.diagnostics.drift`) -- a Hint on every span `sgt drift` reports as mined but
  unpredicted by any active plan session, with a "Save to clear" quick-fix that runs `sgt save`.
- **Forks** (`sgt.diagnostics.forks`) -- a Warning on the symbol a fork is open on, placed via a
  blame lookup for that file (fork records don't carry their own line spans).

An opt-in inlay hint (`sgt.inlayHints.enabled`, default off) appends `‹feature-label ·N ops›` to
each symbol's definition line, for when you want the feature/op-count context without opening the
workbench or hovering.

### Trust queue and proposal review

`sgtChanges`' trust queue lists mined ops grouped by provenance (agent session, git author, ...).
**semi-git: Acknowledge** on a group or a single op runs `sgt review-queue ack`, dequeuing it (with
an optional note) so it stops showing up as pending review.

`sgtCompositions`' proposal rows carry two actions. **Land Proposal** runs `sgt propose land`; if
the proposal spans more than one feature, you get a checklist to land a subset instead of the
whole thing (unpicking a feature another chosen feature `requires` is refused up front, naming the
missing dependency, before anything runs). **Publish Proposal** runs `sgt propose publish`,
pushing the proposal's rendered branch and creating or updating its GitHub PR via `gh`.

### Plan CodeLens and status bar

When a plan session is active, from `sgt plan intake`, matched and drifted lines get a one-line
CodeLens (`✦ matches plan step N` or `◇ drift`) that opens a diff of the step's intent against the
real edit. A status bar item shows step progress (`○` pending, `●` matched). Toggle it with
`sgt.plan.enabled`. Both are hidden when no session is active.

### Revert preview

From the feature map's action bar, or **semi-git: Preview Revert Feature**, open a read-only diff
of the current files against the predicted files after a revert. It runs `sgt revert <feature>
--emit` and writes nothing. If the revert is refused, for example because of a fork, it shows the
reason instead of a diff. **semi-git: Revert Feature** applies it for real after a confirmation,
then rebuilds the files and commits.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `sgt.path` | `sgt` | Path to the `sgt` executable. |
| `sgt.blame.enabled` | `true` | Tint each line by the feature that owns it. |
| `sgt.plan.enabled` | `true` | Show a CodeLens on lines that match or drift from the active plan session. |
| `sgt.inlayHints.enabled` | `false` | Show a `‹feature-label ·N ops›` inlay hint at each symbol's definition line. |
| `sgt.diagnostics.drift` | `true` | Show a Hint diagnostic (with a "Save to clear" quick-fix) on drifted spans. |
| `sgt.diagnostics.forks` | `true` | Show a Warning diagnostic on symbols with an open fork. |

## How it talks to sgt

Every read runs `sgt <verb> --json` in the workspace root, using the JSON views in `sgt/api.py`.
Results are cached in `src/store.ts` and refreshed when a file under `.sgt/` changes or you save a
Python file. Writes call the same verbs the CLI calls. The workbench's hover preview and the
CLI's `sgt preview <verb> ...` read the same `feature_verb_preview_view` projection, so the
extension, the CLI, and MCP all read one schema.
