# Practice: sgt, bikecount

Take a few minutes to practice on the project before the timed stages begin. Your facilitator can answer questions about the setup during this practice.

## What sgt records

`sgt` works on top of a Git repository. It organizes history around the parts of the project, not around commits.

Two words appear throughout the interface, and every view draws the same picture with them:

A **feature** is one part of the project, such as "hourly charts." Each feature is one row in the history views.

A **checkpoint** is one stretch of work on one feature, such as "split weekday and weekend averages." Checkpoints are the blocks along a feature's row. Some screens call them **chapters**.

One piece of work can land on several features at once. The views draw those checkpoints linked together with one name for the whole thing, and `sgt revert` and `sgt restore` take that name directly.

This practice covers the commands used in the timed stages. Most commands also print suggested next steps.

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

The pages are plain reports. The one control on them is the date window at the top of every page.

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

This prepares the project for practice. When the first timed stage begins, `./stage 1` will replace anything you changed during practice with the correct starting state.

## The editor

The editor carries the **semi-git** extension. Click the semi-git icon in the left bar. You will use these views:

- **Now** shows the current state.
- **Features** shows features and their checkpoints.
- **Changes** shows edits that have not been saved into the history.

The **workbench** panel at the bottom is the history as a map:

![The sgt workbench: one row per feature, its checkpoints as blocks along the row, time running left to right](/materials/sgt_workbench.png)

- One row per **feature**; the row's colour is that feature's identity everywhere in the panel.
- Click a row and its card lists the **files** that feature's work touches — `pages/<name>.py` is the page of the same name, so the card answers "which part of the dashboard is this" directly.
- The blocks along a row are its **checkpoints**. Hover one to see its name; click it to select it.
- A hollow block is work that was reverted. A dashed block right of the "now" line is work on disk that has not been saved yet.
- A small ◆ on the time axis marks one piece of work that landed on several rows. Hover it and its blocks light up together; click it to see (and revert) the whole thing.

You can click features and checkpoints to inspect them, and right-click for actions such as **Revert** and **Restore**.

## 1. Read a change

Click a checkpoint in **Features** or in the workbench.

The terminal shows the same history, grouped by feature — one row per feature, its checkpoints along it:

```
sgt log
```

Each feature and checkpoint has a short ID. Use it with:

```
sgt show <short id>
```

You can also show a feature by its exact name:

```
sgt show "<feature name>"
```

The same command answers for a ◆ piece of cross-feature work. It says what that work was, which files it touches, the saves it spans, and what taking it out would remove.

Find and read:

- the work that added the CSV download
- the work that added the by-year table

To list what happened one save at a time, newest first:

```
sgt log --rail
```

## 2. Record some work

Open `README.md` and change one word.

The **Changes** view will show the edit.

Record it with:

```
sgt save -m "reword a line in the readme"
```

The first timed stage asks you to record changes in the same way.

## 3. Find earlier work

Search in your own words:

```
sgt find "the bit that works out the averages"
```

The results can include functions, features, and individual saves.

The search box in the workbench performs the same kind of search — it also finds saves by their message or hash, and cross-feature work by its name.

To open one feature — or one piece of cross-feature work — with the map still on screen and its checkpoints listed underneath:

```
sgt log --focus "<name>"
```

A checkpoint handle looks like:

```
f-08915a9f@1
```

The third timed stage refers to one of the ◆ rows `sgt log` draws under the lanes — one piece of work across several features.

## 4. Remove work and restore it

Pick any checkpoint from the map — `sgt log --focus "<feature name>"` lists a feature's checkpoints with their handles.

Use a handle to preview a revert:

```
sgt revert "<checkpoint handle>"
```

The preview leaves the project unchanged. Read it before continuing.

The preview tells you:

- which work will be removed
- which work will remain
- when only part of a checkpoint will be removed
- when remaining code still depends on something being removed

For example, **2/6 edits removed** means that two of the six edits in that checkpoint belong to the work being removed.

A line beginning with ⚠ points out code that may stop working after the revert.

Apply the revert with:

```
sgt revert "<checkpoint handle>" --yes
python3 check.py
```

If the check reports an error, read the warning and the failing file. The error may come from remaining code that still uses the work you removed.

Restore the checkpoint with:

```
sgt restore "<checkpoint handle>" --yes
python3 check.py
```

Useful recovery commands are:

```
sgt undo
sgt now
```

- `sgt undo` reverses your most recent sgt action.
- `sgt now` shows the current state.

The third and fourth timed stages ask you to remove and restore work.

