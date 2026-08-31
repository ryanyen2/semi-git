# Practice: sgt, bikecount

Take a few minutes on the project itself before the stages start. Ask us anything now, because once the stages start we can only answer questions about the stage instructions themselves.

## What it is

`sgt` sits on top of an ordinary git repository. Git records which lines in which files changed. `sgt` records which functions and classes changed, and groups related work under a name.

Two words are worth learning, because you will type both.

A **feature** is a body of work that grew over time, such as "the hourly charts".

A **checkpoint** is one step inside a feature, such as "split it into weekday and weekend". Checkpoints are usually what you want, because a feature can be months of work while a checkpoint is normally one afternoon. Some screens call checkpoints **chapters**. They are the same thing.

Ten minutes will not make you fluent, and we do not expect it to. Every command ends by printing what you might want to run next.

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

    study-code

The command opens the project in VS Code with the **semi-git** extension. Click the semi-git icon in the left bar to find three views:

- **Now** says where things stand.
- **Features** shows the work as a tree. Expand a feature to see its checkpoints.
- **Changes** lists what you have edited and not yet saved.

At the bottom of the window, the **workbench** panel draws every feature as a row across time, and the chips under each row are its checkpoints. Right-clicking a feature or a chip offers **Revert** and **Restore**, which section 4 below covers.

The sidebar has two more views, **Forks** and **Compositions**. Nothing in this session needs them.

## 1. Read one change

Click a checkpoint in the Features tree or in the workbench. The editor shows what that piece of work covers, in functions rather than lines. The terminal shows the same thing:

    sgt log

The command lists the jobs somebody did, newest first, in their own words. Each row carries a short id, the seven-character code near the left. Pass one of those ids to `sgt show`:

    sgt show <a short id from a row>

`show` takes an id or an exact name, never a phrase, so pasting a whole row's description back at it will not work. The feature name on the right of a row works too, in quotes:

    sgt show "<a feature name, exactly as it is written>"

Read the work that added the csv download, and the work that added the by-year table. `sgt log --map` draws the same history as one row per feature.

## 2. Record some work

Open `README.md` and change a word in it. The **Changes** view shows the edit. Record it, in your own words:

    sgt save -m "reword a line in the readme"

The command files your change under the piece of work it belongs to and prints which one. Plain `sgt save` works too, and its record then says `no words captured`, because only you can say what a change was for. Stage 1 asks you to do exactly this, on changes somebody else made.

## 3. Find a piece of work

Describe the work in your own words:

    sgt find "the bit that works out the averages"

The command lists the closest matches to what you typed, across functions, features, and individual saves. The search box in the workbench toolbar does the same thing.

Some rows are shortened to fit the screen. To get a name you can type back, run:

    sgt intent list

The command prints every feature and checkpoint with its handle. The groups at the bottom are pieces of work that span several features. **The sidebar does not show those groups**, so `sgt intent list` is the only place they appear, and stage 3 names one of them.

## 4. Take something out, and put it back

Work through this whole section now, because stages 3 and 4 ask you to do exactly this with a clock running.

Pick a checkpoint from `sgt intent list`, one from the middle of the list rather than the newest. Type it by the handle in the brackets, such as `f-08915a9f@1`, because a checkpoint's plain name does not resolve on its own. Feature names and the group names at the bottom of the list work as written. Preview taking it out:

    sgt revert "<that handle>"

Nothing has happened yet, because without `--yes` the command only prints a preview. Read the preview before going on. It says which checkpoint is **removed** and which are **kept**, a row like **2/6 edits removed** means that checkpoint shares some code with what you are taking out, and a line starting with ⚠ names code that still calls what you are removing. Now do it:

    sgt revert "<that handle>" --yes
    python3 check.py

The check can come out red here. That is the ⚠ line come true: something you kept still calls what you removed, and putting the work back will fix it. Do that now. `restore` is `revert`'s opposite and takes the same handle, and it needs `--yes` for the same reason:

    sgt restore "<that handle>" --yes
    python3 check.py

If you lose track of where you are, `sgt undo` reverses whatever you last did, and `sgt now` says where things stand.

## 5. Help

    sgt --help
    sgt <command> --help

## Before we start

Run `python3 check.py` once more to see that the project still works, and tell us if anything above printed something you could not make sense of.

