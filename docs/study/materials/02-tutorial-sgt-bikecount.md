# Practice: sgt, bikecount

Take a few minutes to practice on the project before the timed stages begin. Your facilitator can answer questions about the setup during this practice.

## What sgt records

`sgt` works on top of a Git repository. It organizes history around functions, classes, and the pieces of work they belong to.

Two terms appear throughout the interface:

A **feature** is a larger body of work, such as "hourly charts."

A **checkpoint** is one step within a feature, such as "split weekday and weekend averages." Some screens call checkpoints **chapters**. Both words refer to the same thing.

This practice covers the commands used in the timed stages. Most commands also print suggested next steps.

## Open the editor

In the session shell, run:

```
study-code
```

Leave the session shell window open in the background for the whole half — it keeps recording the session.

Everything else happens inside the editor. Open a terminal with **Terminal → New Terminal**, then open a second one with the **+** button on the terminal panel. Both record your commands, exactly like the session shell.

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

Leave the server running in Terminal 1. The pages always show the project as it stands, so you can come back to them after any change.

The main files are:

- `bikecount/pages/` — one file for each page
- `bikecount/metrics.py` — calculates the numbers shown on the pages
- `bikecount/data.py` — reads `data/counts.csv`
- `bikecount/charts.py` — creates the charts
- `check.py` — checks that every page can render successfully

You can check the whole project at any time, in **Terminal 2**:

```
python3 check.py
```

The stages only require reading code.

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

The **workbench** panel at the bottom shows features as rows across time. The chips on each row are checkpoints.

You can click features and checkpoints to inspect them. You can also right-click them to find actions such as **Revert** and **Restore**.

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

The search box in the workbench performs the same kind of search.

To see the complete names and handles for features and checkpoints, run:

```
sgt intent list
```

A checkpoint handle looks like:

```
f-08915a9f@1
```

The bottom of `sgt intent list` can also contain groups that combine work from several features. The third timed stage may refer to one of these groups.

## 4. Remove work and restore it

Choose a checkpoint from somewhere in the middle of `sgt intent list`.

Use its handle to preview a revert:

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

## 5. Help

For command help, run:

```
sgt --help
sgt <command> --help
```

## Before the timed stages

Run:

```
python3 check.py
```

Make sure the project passes the check. Tell the facilitator if any command or output was unclear.

