# Practice: sgt, footfall

Try these steps before starting the tasks.

Practice reading, finding, and undoing changes with sgt. You can use the history views or the terminal.

A **feature** is a part of the project, such as "hourly charts." A **checkpoint** is a set of changes to a feature, such as "split weekday and weekend averages." Some views call checkpoints **chapters**.

## The project

**footfall** shows pedestrian counts from Spencer Street in Melbourne. Its charts and tables are used in a quarterly report.

Open http://localhost:8000 and look through the pages. Use the date range at the top to choose which dates to show.

![Date range controls](/stages/footfall-window.png)

The hour-of-day page has separate charts for weekdays and weekends.

![Weekday and weekend charts](/stages/footfall-hourly-split.png)

Each page has a file in `footfall/pages/`:

| File | Page |
|---|---|
| `overview.py` | Front page |
| `hourly.py` | Hour of day |
| `monthly.py` | Monthly totals |
| `yearly.py` | By-year table |
| `sides.py` | North versus south |

`footfall/metrics.py` calculates the numbers, and `footfall/charts.py` draws the charts.

## Start practice

Run this command in the study terminal:

```
./stage 0
```

The project will reset before the first task.

## 1. Read the history

The **workbench** shows a row for each feature. Blocks along a row are its checkpoints.

![Features and checkpoints in the workbench](/materials/sgt_workbench.png)

Click a feature or checkpoint to see its changes and files. A ◆ marks work that changed several features. Click it to see those changes together. A hollow block marks work that has been reverted.

You can also read the history in the terminal:

```
sgt log
sgt show <short id>
```

Use the ID shown in the history, or enter a name in quotes:

```
sgt show "<feature name>"
```

Find the work that added the CSV download and the by-year table. Read what each one changed.

## 2. Find a change

Search by describing what you are looking for:

```
sgt find "calculate averages"
```

You can also use the search box in the workbench.

To list a feature's checkpoints and their IDs, run:

```
sgt log --focus "<feature name>"
```

Checkpoint IDs look like `f-08915a9f@1`.

## 3. Undo and restore a change

Choose a checkpoint and preview removing it:

```
sgt revert "<checkpoint id>"
```

Read which changes would be removed and which would remain. Warnings identify code that may stop working. The preview leaves the project unchanged.

Apply the revert and check that the pages still render:

```
sgt revert "<checkpoint id>" --yes
python3 check.py
```

Restore the checkpoint:

```
sgt restore "<checkpoint id>" --yes
python3 check.py
```

Both commands also accept the name of work marked with a ◆. In the workbench, right-click a feature or checkpoint for **Revert** and **Restore**.

Use `sgt undo` to reverse your most recent sgt action. Use `sgt now` to see the current state.

