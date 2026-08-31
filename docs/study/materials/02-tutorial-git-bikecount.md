# Practice: git, bikecount

Take a few minutes on the project itself before the stages start. Ask us anything now, because once the stages start we can only answer questions about the stage instructions themselves.

You already know git, so this page is not a lesson. It is a warm-up on the four things the stages will ask you to do, on the same project the stages use. The point is that nothing about this machine should surprise you once a stage's clock is running.

## The project you are looking after

**bikecount** is a small web dashboard over the bicycle counter on the Fremont Bridge in Seattle. The city has counted people crossing every hour since 2013 and publishes the file. The dashboard reads that file and draws a handful of pages, and its numbers go into the cycling team's quarterly report.

Start it and look at it:

    python3 -m bikecount.server

Then open http://localhost:8000 and click through the five pages: the front page, the hour-of-day page, monthly totals, the by-year table, and the east against west comparison. There is also a csv download at `/daily.csv`. Stop the server with Ctrl-C when you have seen them.

The code is laid out like this:

- `bikecount/pages/` is one file per page. Each one has a `render()` that returns the html for that page, and the navigation and the routing are built from whatever is in that folder.
- `bikecount/metrics.py` works out the numbers the pages show: daily totals, the busiest day, the hour-of-day averages, the by-year summary.
- `bikecount/data.py` reads `data/counts.csv` and hands the rows to everything else.
- `bikecount/charts.py` draws the bar charts the pages embed.
- `check.py` renders every page and fails if one of them blows up. It is the quickest way to see whether the project still works, and it takes about a second:

```
python3 check.py
```

You will not have to write any code in the stages. You will have to read some.

## Put the project in its warm-up state

In the session shell, run:

```
./stage 0
```

The command puts the project back to where it stood just before the changes the first stage is about, so nothing you see during the warm-up spoils that stage. Running `./stage 1` later undoes everything you do between now and then, so nothing you try here can go wrong.

## Open the editor

```
study-code
```

The command opens the project in VS Code. Find these three places now, because the stages use all of them:

- **Source Control**, in the left bar, lists what you have changed and is where you commit.
- **Graph**, in the lower half of that same view, draws the history as a graph you can click through.
- **Timeline**, at the bottom of the Explorer, lists the commits that touched the file you click.

## 1. Read one change

Open the Graph and click a commit. The editor shows what that commit changed, file by file. The terminal shows the same thing:

```
git log --oneline
git show <paste a hash from that list>
```

On this machine git prints straight to the terminal. It never opens the pager screen that you would normally leave by pressing q, so long output simply scrolls past and you scroll back to read it.

Read the commit that added the csv download, and the one that added the by-year table. Together they show most of how the pages are put together.

## 2. Record some work

Open `README.md` and change a word in it. Then record the change the way you normally would. Stage it and commit it in Source Control with a message, or do the same in the terminal:

```
git add README.md
git commit -m "reword a line in the readme"
```

Stage 1 asks you to do exactly this, on changes somebody else made.

## 3. Find a piece of work

`git log -S` finds the commits where a piece of text arrived or went away. Any word from the code works:

```
git log --oneline -S "average"
```

The Graph has no search like that, so use the two together. The command gives you the hashes, and clicking those commits in the Graph shows what each one changed.

Two more commands are useful here. `git log --stat` lists the files each commit touched, and `git blame <file>` names the commit that last changed each line. The editor shows the same information in two places: the Timeline lists the commits that touched the open file, and the grey note at the end of the line your cursor is on names the commit that last changed that line.

Stage 2 asks you to find one particular piece of work this way.

## 4. Take something out, and put it back

```
git revert HEAD
```

The command makes a new commit that undoes the most recent one. On this machine git uses the commit message it suggests instead of opening a terminal editor, so the revert finishes in one step. Put the work back by running `git revert HEAD` again, which reverts the revert.

**Now try one that does not apply cleanly.** In `git log --oneline`, find the commit that first added the hour-of-day page, and revert it:

```
git revert <that hash>
```

Git stops partway, because later commits built on the work you are removing. It leaves the affected files for you to settle, and `git status` lists them under "Unmerged paths". You will see two kinds:

- A file listed as **both modified** has conflict markers in it, from `<<<<<<<` to `>>>>>>>`. Open the file in the editor and edit it down to the lines that should survive, with the marker lines deleted. Save it, then run `git add <that file>`.
- A file listed as **deleted by them** has no markers. The revert wants to delete the whole file, but a later commit changed it, so git leaves the choice to you. Run `git rm <that file>` to delete it, or `git add <that file>` to keep it. Here the right choice is `git rm`, because the page is part of the work you are removing. A kept page file still calls the functions the revert takes out, and the project will not run.

Git's own hint writes those two commands as `git add/rm <pathspec>`. It is shorthand for "`git add` or `git rm`, followed by a file path", not a command to type as written.

When `git status` no longer lists unmerged paths, finish the revert and check the project:

```
git revert --continue
python3 check.py
```

If the check fails, read which file the error names. The usual cause is a kept file that still calls something the revert removed. If you want out instead, any time before the `--continue`, run `git revert --abort` and everything goes back to how it was before the revert.

Do this now. Stage 3 asks you to remove work that several later commits have landed on, and the warm-up is the only place to practise getting out of a conflicted revert.

## Before we start

Run `python3 check.py` once more to see that the project still works, and tell us if anything above behaved differently from what you expected.

