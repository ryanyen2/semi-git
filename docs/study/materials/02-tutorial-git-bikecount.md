# Practice: git, bikecount

Try these steps before starting the tasks.

Practice reading, finding, and undoing changes with Git. You can use the history views or the terminal.

## The project

**bikecount** shows bicycle counts from the Fremont Bridge in Seattle. Its charts and tables are used in a quarterly report.

Open http://localhost:8000 and look through the pages. Use the date range at the top to choose which dates to show.

![Date range controls](/stages/bikecount-window.png)

The hour-of-day page has separate charts for weekdays and weekends.

![Weekday and weekend charts](/stages/bikecount-hourly-split.png)

Each page has a file in `bikecount/pages/`:

| File | Page |
|---|---|
| `overview.py` | Front page |
| `hourly.py` | Hour of day |
| `monthly.py` | Monthly totals |
| `yearly.py` | By-year table |
| `sides.py` | East versus west |

`bikecount/metrics.py` calculates the numbers, and `bikecount/charts.py` draws the charts.

## Start practice

Run this command in the study terminal:

```
./stage 0
```

The project will reset before the first task.

## 1. Read the history

Click a commit in **Graph** to see its changes, or run:

```
git log --oneline
git show <commit hash>
```

Find the commits that added the CSV download and the by-year table. Read what each one changed.

## 2. Find a change

Search for commits that added or removed the text "average":

```
git log --oneline -S "average"
```

Use `git log --stat` to see which files each commit changed. Use `git blame <file>` to see which commit last changed each line.

## 3. Undo and restore a change

Undo the latest commit:

```
git revert HEAD
```

Git creates a new commit that reverses the change. Undo that new commit to restore the change:

```
git revert HEAD
```

## 4. Resolve a conflict

Find the commit that added the hour-of-day page and revert it:

```
git revert <commit hash>
git status
```

If Git reports conflicts, resolve the files listed under **Unmerged paths**:

- For **both modified**, open the file, keep the code that should remain, and delete the conflict markers. Save the file and run `git add <file>`.
- For **deleted by them**, choose whether to keep or remove the file. In this example, remove the hour-of-day page with `git rm <file>`.

After resolving the conflicts, finish the revert and check that the pages still render:

```
git revert --continue
python3 check.py
```

To cancel a revert while resolving conflicts, run `git revert --abort`.

