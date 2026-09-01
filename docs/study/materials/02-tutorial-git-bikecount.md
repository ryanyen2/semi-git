# Practice: git, bikecount

Take a few minutes to practice on the project before the timed stages begin.

This practice covers the Git actions you will use during the timed stages: reading history, finding earlier work, and reverting it.

## Open the editor

In the session shell, run:

```
study-code
```

Leave the session shell window open in the background for the whole half — it keeps recording the session.

Everything else happens inside the editor. Open a terminal with **Terminal → New Terminal**, then open a second one with the **+** button on the terminal panel.

Use the two terminals like this:

- **Terminal 1** runs the dashboard server, so you can look at the pages.
- **Terminal 2** runs every other command, in this practice and in the stages.

## The project

**bikecount** is a small web dashboard for bicycle counts from the Fremont Bridge in Seattle. It reads the city's hourly count data and produces several pages used for a quarterly report.

Start the dashboard in **Terminal 1**:

```
python3 -m bikecount.server
```

Open http://localhost:8000 and look through these pages:

- Front page
- Hour of day
- Monthly totals
- By-year table
- East versus west

There is also a CSV download at `/daily.csv`.

Leave the server running in Terminal 1.

The pages are plain reports. The one control on them is the date window at the top of every page:

![The nav and the date window, which every page carries](/stages/bikecount-window.png)

The pages open on the most recent year. Two of the timed stages are about 2018, so set the window when you get there.

The hour-of-day page carries two charts, weekdays and weekends, which are separate things on the checklists you will be asked to fill in:

![The hour-of-day page's weekday and weekend charts, side by side](/stages/bikecount-hourly-split.png)

**Every page is one file, and file names map to pages.** When any view — a diff, a save's echo, a feature's card — names a file, this list says which part of the dashboard it is:

- `bikecount/pages/overview.py` — the front page: the busiest-day figure and the last-fortnight chart
- `bikecount/pages/hourly.py` — the weekday and weekend hour-of-day charts
- `bikecount/pages/monthly.py` — the month-by-month chart
- `bikecount/pages/sides.py` — the east v west comparison
- `bikecount/pages/yearly.py` — the one-row-per-year table
- `bikecount/metrics.py` — works out every number the pages show
- `bikecount/charts.py` — draws the charts, including the marks on unusual days
- `bikecount/events.py` — the project's list of unusual days
- `bikecount/data.py` — reads `data/counts.csv`
- `check.py` — checks that every page can render successfully

You can check the whole project at any time, in **Terminal 2**:

```
python3 check.py
```

## Start the practice state

In **Terminal 2**, run:

```
./stage 0
```

This prepares the project for practice. `./stage 1` puts it back to this same state, so nothing you try here carries into a stage.

## The editor

You will use three parts of the editor:

- **Source Control** shows your current changes and lets you commit them.
- **Graph** shows the commit history.
- **Timeline**, at the bottom of the Explorer, shows commits that changed the open file.

You can use either the editor or the terminal during the stages.

## 1. Read what the project is made of

Click a commit in the Graph to see the files and lines it changed.

The terminal provides the same information:

```
git log --oneline
git show <commit hash>
```

Find and read:

- the commit that added the CSV download
- the commit that added the by-year table

The first timed stage asks you to do this across the whole project: which piece of work put which part of the dashboard there.

## 2. Find earlier work

Git can search history for commits that added or removed a piece of text:

```
git log --oneline -S "average"
```

Useful commands include:

```
git log --stat
git blame <file>
```

- `git log --stat` shows which files each commit changed.
- `git blame` shows which commit last changed each line.
- The editor Timeline shows commits that changed the current file.
- The grey annotation beside the current line shows the commit that last changed it.

The second timed stage asks you to find a particular piece of work.

## 3. Remove work and restore it

First, try a simple revert:

```
git revert HEAD
```

This creates a new commit that reverses the latest commit.

Run the same command again to restore that work:

```
git revert HEAD
```

Now practice a revert that has conflicts.

Use `git log --oneline` to find the commit that first added the hour-of-day page, then run:

```
git revert <commit hash>
```

Git will stop when later work overlaps with the commit you are removing.

Run:

```
git status
```

Files under **Unmerged paths** need your attention.

For a file marked **both modified**:

1. Open the file.
2. Find the conflict markers from `<<<<<<<` through `>>>>>>>`.
3. Keep the code that should remain.
4. Delete the conflict-marker lines.
5. Save the file.
6. Run:

```
git add <file>
```

For a file marked **deleted by them**, choose whether the file belongs to the work being removed. For this practice example, remove the hour-of-day page:

```
git rm <file>
```

When `git status` shows no unmerged paths, finish the revert:

```
git revert --continue
python3 check.py
```

If you want to cancel a revert while it is still in progress, run:

```
git revert --abort
```

The third timed stage also asks you to remove work that later commits depend on.

