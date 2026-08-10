# VS Code extension

The extension shows which feature owns each line of code, draws the feature tree next to a
timeline of commits, and lets you merge, split, rename, move, or revert a feature from the editor.
It also runs your day-to-day git commands for you: switch, save, undo, sync, push, and land. So
you never have to leave VS Code for those. It never edits your code directly. It only calls
`sgt`'s read commands and its commands for features, kernel state, and git.

An activity bar container called `semi-git` holds five tree views: Now, Features, Forks, Changes, and
Compositions. A Composition Workbench panel shows the same data in a full window, with a tree and
timeline, an inspector, and a panel that shows your files as they looked at any point you pick. If
a workspace has no `sgt` store yet, the extension still activates. The Features view shows an
**Initialize semi-git** button instead of an error, along with a short walkthrough.

## Install

First install the `sgt` command itself, because the extension is a front end for it and does
nothing without it.

```bash
uv tool install semi-git
```

Then download `semi-git-<version>.vsix` from the
[latest release](https://github.com/ryanyen2/semi-git/releases/latest). In VS Code, open the
Extensions view, click the `...` menu at the top, and choose "Install from VSIX".

Finally, run this in your repo so the extension knows where `sgt` is:

```bash
sgt init --agent
```

That writes `.vscode/settings.json` with an absolute `sgt.path`. The absolute path matters, because
a VS Code started from your Dock or Applications folder inherits the login shell's PATH rather than
your terminal's, and `sgt` usually lives in a directory only your terminal knows about. If the
extension can't run `sgt`, it says so and offers to open the setting, rather than leaving the panels
empty.

The extension activates in any workspace that has run `sgt init`.

### Building from source

```bash
cd editor/vscode
npm install
npm run compile        # type-check and bundle to dist/extension.js
```

Press `F5` in VS Code to launch a development instance, or run `npx @vscode/vsce package` to build
a `.vsix` file yourself.

## What you get

| Surface | What it shows |
| --- | --- |
| Feature blame | A colored tint on each line, based on the feature that owns it |
| Composition Workbench | The feature tree and a timeline of commits, with an inspector that shows a feature's code at any point you pick, and a preview before any action that changes state |
| Now, Features, Forks, Changes, and Compositions (activity bar) | What needs you and the one next action, the feature tree, open forks with a count badge, changes no plan predicted, and active sessions and proposals |
| Plan status | Lines that match or drift from an active plan you started with `sgt plan` |
| Revert preview | A read-only preview of what reverting a feature would change, before you commit to it |
| Git bridge | Commands and a status bar item for switch, save, undo, sync, push, and land |
| Fork resolution | A side-by-side view of a fork's two versions, walking you through merge, edit, and land |
| Hovers, diagnostics, and inlay hints | A hover on each symbol showing its feature, why it was grouped that way, and what else it is coupled to. A hint on drifted code, a warning on forked code, and an optional inline note showing a symbol's feature and how many edits it has |
| Proposal review | Land part of a proposal, or publish one as a GitHub PR, from the Compositions view |

## A plain walkthrough

If you have never used this before, here is what a real session looks like, step by step. You do
not need any prior knowledge of how semi-git works internally.

### 1. Open a project for the first time

Open a folder in VS Code. If it has not been set up with semi-git yet, you will see a `semi-git`
icon in the activity bar, the icon strip on the far left, with a single button: **Initialize
semi-git**. Click it. That runs `sgt init` for you and starts a short walkthrough that checks that
the `sgt` command is installed and opens the workbench for the first time.

### 2. Write code like you normally would

You, or your AI coding agent, write and save code exactly as before. Semi-git never touches your
files. As you open a file, every line gets a thin colored stripe in the left margin and a faint
tint in the background. Each color is a different feature, a piece of functionality semi-git has
automatically grouped that line into, for example "user login" or "PDF export". Hover over any
line to see that feature's name; the hover's **Open Workbench** link jumps to the graph with that
feature selected and spotlit, so code → graph is one click (the command palette form is
**semi-git: Reveal in Workbench**).

### 3. Check what is new

Click the `semi-git` icon in the activity bar. You will see five lists:

- **Now**, the short answer to "what should I do next": edits not saved yet, anything that needs a
  decision from you, the last few saves, and a single next action whose row runs the command for
  you.
- **Features**, everything semi-git knows about, shown as a tree where bigger areas contain
  smaller ones.
- **Forks**, places where two different versions of the same code disagree and need a decision
  from you. This is empty most of the time.
- **Changes**, two read-only lists: **Unplanned changes**, edits semi-git found that no plan of
  yours predicted, one row per symbol, and **Untracked files**, paths it does not understand yet.
- **Compositions**, your branches, any in-progress planning sessions, and proposals waiting to be
  merged in.

If **Forks** shows a number badge, something needs your attention before you can land your work.
See step 7.

### 4. Open the full picture

Run **semi-git: Open Composition Workbench** from the command palette (`Cmd/Ctrl+Shift+P`), or
click the status bar icon at the bottom of the window. This opens a tab with three parts side by
side: the feature tree on the left, a timeline of every change across the top, and a detail panel
on the right. Click any feature to see its details and its actual code on the right.

### 5. Look back in time

Above the timeline there is a small handle, the playhead. Click anywhere on the timeline, or drag
the handle, and the code panel on the right instantly shows what the code looked like at that
exact point, along with a green check or red mark for whether your checks would have passed then.
Nothing changes on disk while you do this. It is only a preview. Press `Esc`, or click "Back to
current", to return to normal.

### 6. Reorganize a feature

Click a feature in the tree, then use the buttons above the code panel: **Rename**, **Merge
into**, **Split**, **Move**, **Revert**. Hovering any of these paints the affected code in orange
for code that would change, and blue for code this depends on, before you commit to anything. For
quick, safe actions such as rename, merge, split, and move, clicking just does it, and a small
"Undo" notification appears afterward. For an action that can touch a lot of code, such as revert,
clicking first previews the effect, and you confirm with `Enter` or back out with `Esc`.

### 7. Resolve a conflict

If **Forks** shows a badge, click it, pick the conflict, and semi-git opens a side-by-side view
with one column per version of the code that disagrees. Follow the buttons in order:

1. **Draft merge**, semi-git prepares a starting point that reconciles both versions.
2. **Open affected files**, jump straight to the files you need to edit by hand.
3. Edit the code until it looks the way you want.
4. **Fulfill from working tree**, then **Land**. Semi-git records your edit as the resolution and
   finishes the merge.

### 8. Use it instead of the git commands you already know

Six commands, on the palette or the status bar, cover what you would normally type into a
terminal:

| Instead of typing... | Run this command |
| --- | --- |
| `git checkout <branch>` | **Switch Branch** |
| `git add -A && git commit` | **Save** |
| `git reset --hard HEAD~1` | **Undo Last Ideal Edit** |
| `git fetch && git merge` | **Sync** |
| `git push` | **Push** |
| merging a branch in on the server | **Land Branch** |

**Save** always runs right away. It cannot lose work. **Push** and **Land Branch** ask you to
confirm first, since both affect other people. **Land Branch** refuses to run at all while any
fork is open, and tells you which one.

### 9. Review what an agent, or a teammate, did

Open **Changes** in the sidebar. Anything semi-git picked up that was not part of an active plan
shows up under **Unplanned changes**, one row per symbol; clicking a row jumps to that code. This is
the short list to read after an agent run: not the whole diff, only the parts that fall outside what
you asked for. Nothing here is a chore to tick off — you act on it by editing code. In the editor,
code that has drifted from your last save also gets a hint with a quick fix that saves it.

Under **Compositions**, a proposal, a bundle of finished work waiting to be merged in, has two
right-click actions: **Land Proposal**, which merges it in and, if it covers more than one
feature, gives you a checklist to land only some of them, and **Publish Proposal**, which pushes
it and opens a GitHub pull request.

That is the whole loop: write code, glance at the sidebar, open the workbench for the bigger
picture, resolve conflicts through the wizard, and drive git through the six bridge commands
instead of the terminal.

### Feature blame

Each line is tinted in its owning feature's color, on the background, the left border, and the
overview ruler. Hovering shows the feature's label and id. Toggle it with **semi-git: Toggle
Feature Blame** (`sgt.blame.enabled`). Each feature's color comes from its id, so the same feature
always gets the same color in the gutter and in the feature map, and colors adjust to your VS Code
theme so they stay readable.

### Composition Workbench

**semi-git: Open Composition Workbench** (`sgt.openWorkbench`) opens a panel with two parts: a
tree and timeline on one side, and an inspector on the other. It reads one combined snapshot from
`sgt` per refresh instead of making several separate calls.

- The left side is the feature tree. Features and the subsystems that contain them are listed in
  order, each collapsible, with a colored dot, a label, and a bar showing its size.
- The right side, past a divider, is a shared timeline built from every mined commit, in order.
  Each feature gets a bar spanning its first op to its last, and a mark for each op at the commit
  it happened in. The marks use short symbols for the kind of op: add, extend, rework, prune, move,
  merge, or touched.
- Lines connecting features show real dependencies between them, rolled up from the symbol-level
  dependency graph. Each feature shows up to a limit of these connections, and if there are more,
  it tells you the extra count instead of just dropping them.
- A button in the title bar lets you pick a different branch or active session to view. Picking
  one reloads the inspector's code panel at that point. The status chip next to it mirrors your
  current build and test status.

Anything not done yet gets its own place on the same axis rather than a badge. When you have
uncommitted edits or an active plan with steps still open, a vertical `now` rule appears with a
faintly washed band to the right of it, and each pending thing is drawn there as an ordinary card in
its feature's color. Two kinds, told apart by their outline and not by a second color: a filled,
slowly pulsing card is real edits sitting on disk that will land on your next save, and a hollow,
still card is a plan step someone intends where no code exists yet. Each card carries the step's
name; hovering shows its reason and the symbols it is predicted to touch, and clicking selects that
step in the inspector. If the pane is too narrow to name every card, the extras collapse into a
single named stack card rather than shrinking into unreadable stubs. The band is absent when nothing
is pending, so an idle repo keeps exactly the axis it had. `sgt log --map` draws the same band in the
terminal, past a `┊` rule.

Hovering a row or a connecting line dims everything else and highlights what you are looking at
and what it depends on; the time-axis ticks where that feature was actually worked on brighten
with it, so a lane answers *when* as well as *what*. The Rail view shows one row per save, newest
first, with up to three colored chips naming the feature(s) that save touched — hovering a save
lights every feature lane it reached. Color always means which feature something belongs to.
Status is shown with a symbol or an outline, never with a different color. Clicking a feature opens the inspector
with its label, the reason it was grouped that way, its size, action buttons (Rename, Merge into,
Split, Move ops, Revert), and a code panel showing that feature's files as they look at the point
you have selected. That panel is read-only and never checks out those files for real.

Hovering Split or Revert runs a live preview of that exact action and highlights every feature it
would affect. For Revert, this can cover more than the one feature you named, because it shows the
real, complete effect of the change rather than a guess. A Restore that can't legally apply —
because a different version of the same symbol is live — doesn't fail silently: the preview
explains the one-live-version rule and offers the two ways forward (swap the versions, or
reconcile them with `sgt resolve`). Merge into and Move ops let you pick a
target feature. Hovering a candidate previews the merge or move against it, and clicking confirms
and applies it. Every preview is read-only. Only a click on Split or Revert, or a confirmed Merge
or Move target, actually changes anything.

A draggable handle over the timeline lets you scrub to any point in your commit history, not just
your current branch or session. Click anywhere on the timeline, or drag the handle above it, and
the inspector's code panel switches to show your files as they looked at that point, along with
whether your checks would have passed then and how many ops were included. A "Back to current"
button and the `Esc` key return you to the normal view. This never changes what the action buttons
preview or apply against. Those always stay pointed at your current branch or session. If a
feature is selected, the code panel filters to that feature's files only, and dragging the handle
does not reload the files on every small move, only when you stop.

### Git bridge

Six commands in the command palette, **semi-git: Switch Branch**, **Save**, **Undo Last Ideal
Edit**, **Sync**, **Push**, and **Land Branch**, cover your daily git work and your work with
remotes, so you never have to run `git checkout`, `stash`, `reset`, `pull`, or `push` directly
against a repo `sgt` is tracking.

- **Switch** records your current state first, so nothing is lost, then checks out the branch you
  pick or type, and reads its history back in.
- **Save** records your working tree and, if there are new edits, commits them. It never destroys
  anything, so it runs immediately with no confirmation needed. The toast that follows names the
  feature(s) the save's work landed in — and when a save minted a brand-new, unnamed feature, it
  offers a **Name it…** button so you can label the work the moment you still remember what it
  was. (On the command line the same moment is `sgt save --as "<label>"`.)
- **Undo** removes the last recorded change and restores what came before it, as a new forward
  commit, since history in `sgt` is never rewritten, only added to.
- **Sync** fetches a remote branch and merges its op set into yours, flagging any fork on a symbol
  you both touched instead of doing a text-based merge with conflict markers.
- **Push** and **Land Branch** ask you to confirm first, since both write to shared or remote
  state. **Land Branch** refuses up front, with a link to the Forks view, while any fork is open.

The status bar's status chip is always visible, even before you have set up any build or test
checks, and shows whether they currently pass, fail, are running, or are not set up, plus how many
forks are open if any. Clicking it opens the Composition Workbench.

### Fork resolution

A fork is the one real kind of conflict in `sgt`: two versions of the same symbol, each claiming
to be the next step after the same starting point. It blocks landing until you resolve it. The
Forks view lists every open fork with a count badge, and a marker in the editor points at the
symbol in conflict. **semi-git: Resolve Fork**, or the right-click menu on a fork, opens a view
with one column per version, each showing every file that version touches. The wizard walks you
through the real steps one confirmation at a time, and never guesses at a merge on your behalf:

1. **Draft merge** starts a placeholder that reconciles the two versions. You can give it a short
   description of the intent first.
2. **Open affected files** takes you to the working tree so you can hand-edit it to the result you
   actually want.
3. **Fulfill from working tree** saves your edit as the content for that placeholder.
4. **Land** commits the result, checked against your build and test checks like any other commit.

There is a lower-level command, `sgt pin`, that this wizard does not use and does not offer,
because it has no command-line entry point of its own.

### Hovers, diagnostics, and inlay hints

Hovering a symbol shows its feature's label and id, the reason it was grouped that way, its number
of edits and size, and up to five other features it is coupled with, along with links to preview a
revert or open the workbench. This sits alongside the simpler whole-line blame hover. VS Code
shows both.

Two kinds of markers appear as you edit, each with its own setting to turn it off:

- **Drift** (`sgt.diagnostics.drift`) is a hint on any code that has been edited but not yet saved
  through `sgt`, with a quick fix that saves it and clears the marker.
- **Forks** (`sgt.diagnostics.forks`) is a warning on the symbol a fork is open on.

An inlay hint, off by default (`sgt.inlayHints.enabled`), adds a short note after each symbol's
definition showing its feature and how many edits it has, for when you want that context without
hovering or opening the workbench.

### Proposal review

In the Compositions view, a proposal has two right-click actions. **Land Proposal** merges it in.
If the proposal spans more than one feature, you get a checklist to land a subset instead of the
whole thing, and if you try to leave out a feature that another feature you kept depends on, it
tells you that up front instead of letting you proceed. **Publish Proposal** pushes the proposal's
branch and creates or updates its GitHub pull request.

### Plan CodeLens and status bar

When you have an active plan, started with `sgt plan intake`, lines that match or drift from it
get a short note above them, either "matches plan step N" or "drift", that opens a comparison of
what the plan expected against what was actually written. A status bar item shows how many steps
are done. Toggle this with `sgt.plan.enabled`. Both are hidden when no plan is active.

### Revert preview

From the feature map's action buttons, or **semi-git: Preview Revert Feature**, open a read-only
preview comparing your current files against what they would look like after a revert. This
writes nothing. If the revert would be refused, for example because of an open fork, it shows the
reason instead of a preview. **semi-git: Revert Feature** applies it for real after you confirm,
then rebuilds the files and commits.

## Settings

| Setting | Default | Description |
| --- | --- | --- |
| `sgt.path` | `sgt` | Path to the `sgt` executable. |
| `sgt.blame.enabled` | `true` | Tint each line by the feature that owns it. |
| `sgt.plan.enabled` | `true` | Show a note on lines that match or drift from the active plan. |
| `sgt.inlayHints.enabled` | `false` | Show a short feature and edit-count note at each symbol's definition line. |
| `sgt.diagnostics.drift` | `true` | Show a hint, with a quick fix, on code that has drifted from what was last saved. |
| `sgt.diagnostics.forks` | `true` | Show a warning on symbols with an open fork. |

## How it talks to sgt

Every read runs `sgt <command> --json` in your workspace root. Results are cached and refreshed
whenever a file under `.sgt/` changes, or you save a Python file. Writes call the same commands
the terminal version of `sgt` calls. The workbench's preview on hover and the command line's own
preview option read the same data, so the extension, the command line, and any MCP client all see
the same information.
