# Practice: git, bikecount

A few minutes on the project itself before the stages start. Ask us anything now. Once the stages start we can only answer questions about the stage instructions themselves.

You already know git. This is not a lesson. It is a warm-up on the four things the stages will ask you to do, on the project the stages use, so that nothing on this machine surprises you later.

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
- `check.py` renders every page and fails if one of them blows up. It is the safety net, and it takes a second:

```
python3 check.py
```

You will not have to write any code in the stages. You will have to read some.

## Put the project in its warm-up state

In the session shell, run:

```
./stage 0
```

That puts the project back to where it stood just before the changes the first stage is about, so nothing you see here spoils that stage. Everything you do from now until you run `./stage 1` is undone by `./stage 1`, so nothing you try can go wrong.

## Open the editor

```
study-code
```

That opens the project in VS Code with **GitLens** installed. Find these three now, because the stages will want them:

- **Source Control** in the left bar. It shows what you have changed, and it is where you commit.
- **Commit Graph.** Click the GitLens icon in the left bar, or run *GitLens: Show Commit Graph* from the command palette. This is the history as a graph you can click through.
- **File History.** Right-click any file and choose *Open File History*.

## 1. Read one change

Open the Commit Graph and click a commit. It shows you what that commit changed, file by file. The same thing in the terminal:

```
git log --oneline
git show <paste a hash from that list>
```

Read the commit that added the csv download, and the one that added the by-year table. Between them they will tell you most of how the pages are put together.

## 2. Record some work

Open `README.md` and change a word in it. Then record it the way you normally would: stage it and commit it in Source Control, with a message. Or in the terminal:

```
git add README.md
git commit -m "reword a line in the readme"
```

Stage 1 asks you to do exactly this, on changes somebody else made.

## 3. Find a piece of work

`git log -S` finds the commits where a piece of text arrived or went away. Any word from the code will do:

```
git log --oneline -S "average"
```

Try to see the same story in the Commit Graph. Also worth knowing: `git log --stat` shows which files each commit touched, `git blame <file>` says which commit last changed each line, and File History does the same thing in the editor.

Stage 2 asks you to find one particular piece of work this way.

## 4. Take something out, and put it back

```
git revert HEAD
```

That makes a new commit undoing the most recent one. This one applies cleanly. Put it back by reverting the revert:

```
git revert HEAD
```

**Now try one that does not apply cleanly.** Pick a commit from a few pages back in `git log --oneline` -- one that later work has built on, such as the commit that first added the hour-of-day page -- and revert it:

```
git revert <that hash>
```

Git will stop and leave conflict markers in the files. Resolve them, `git add` the file, then `git revert --continue`. Or walk away with `git revert --abort`.

Do this now. Stage 3 asks you to remove work that several later commits have landed on, and this is the only place you can practise getting out of it.

## Before we start

Run `python3 check.py` once more to see the project still works, and tell us if anything above behaved differently from what you expected.

