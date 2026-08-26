# Practice: sgt, footfall

A few minutes on the project itself before the stages start. Ask us anything now. Once the stages start we can only answer questions about the stage instructions themselves.

## What it is

`sgt` sits on top of an ordinary git repository. Git records which lines in which files changed. `sgt` records which functions and classes changed, and groups related work under a name.

Two words are worth learning, because you will type both.

A **feature** is a body of work that grew over time, such as "the hourly charts".

A **checkpoint** is one step inside a feature, such as "split it into weekday and weekend". Checkpoints are usually what you want: a feature can be months of work, where a checkpoint is normally one afternoon. Some screens call these **chapters**. They are the same thing.

Ten minutes will not make you fluent, and we do not expect it to. Every command ends by printing what you might want to run next.

## The project you are looking after

**footfall** is a small web dashboard over the pedestrian counter on Spencer Street in Melbourne. The city has counted people walking past every hour since 2013 and publishes the file. The dashboard reads that file and draws a handful of pages, and its numbers go into the transport committee's quarterly paper.

Start it and look at it:

    python3 -m footfall.server

Then open http://localhost:8000 and click through the five pages: the front page, the hour-of-day page, monthly totals, the by-year table, and the north against south comparison. There is also a csv download at `/daily.csv`. Stop the server with Ctrl-C when you have seen them.

The code is laid out like this:

- `footfall/pages/` is one file per page. Each one has a `render()` that returns the html for that page, and the navigation and the routing are built from whatever is in that folder.
- `footfall/metrics.py` works out the numbers the pages show: daily totals, the busiest day, the hour-of-day averages, the by-year summary.
- `footfall/data.py` reads `data/counts.csv` and hands the rows to everything else.
- `footfall/charts.py` draws the bar charts the pages embed.
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

    study-code

That opens the project in VS Code with the **semi-git** extension. Click the semi-git icon in the left bar:

- **Now**, for where things stand.
- **Features**, the work as a tree. Expand a feature to see its checkpoints.
- **Changes**, for what you have edited and not yet saved.

At the bottom, the **workbench** panel draws every feature as a row across time. The chips under each row are its checkpoints. Right-clicking either one offers **Revert** and **Restore**, which section 4 below covers.

There are two more views in that sidebar, **Forks** and **Compositions**. Nothing in this session needs them.

## 1. Read one change

Click a checkpoint in the Features tree or in the workbench. It shows what that piece of work covers, in functions rather than lines. The same thing in the terminal:

    sgt log

That lists the jobs somebody did, newest first, in their own words. Each row carries a short id, the seven-character code near the left. Pass one of those to `sgt show`:

    sgt show <a short id from a row>

`show` takes an id or an exact name, never a phrase, so pasting a whole row's description back at it will not work. The feature name on the right of a row works too, in quotes:

    sgt show "<a feature name, exactly as it is written>"

Read the work that added the csv download, and the work that added the by-year table. `sgt log --map` draws one row per feature.

## 2. Record some work

Open `README.md` and change a word in it. The **Changes** view shows the edit. Record it, in your own words:

    sgt save -m "reword a line in the readme"

It files your change under the piece of work it belongs to and prints which one. Plain `sgt save` works too and says `no words captured`, because the words are yours to give. Stage 1 asks you to do exactly this, on changes somebody else made.

## 3. Find a piece of work

Describe it in your own words:

    sgt find "the bit that works out the averages"

It lists the closest matches to what you typed: functions, features, and individual saves. The search box in the workbench toolbar does the same thing.

Some rows are shortened to fit. To get a name you can type back, run:

    sgt intent list

That prints every feature and checkpoint with its handle. The groups at the bottom are pieces of work that span several features; **the sidebar does not show those**, so that command is the only place they appear. Stage 3 names one of them.

## 4. Take something out, and put it back

Do this whole sequence now. It is the most useful thing on this sheet.

Pick a checkpoint from `sgt intent list` -- one from the middle of the list, not the newest -- and preview taking it out:

    sgt revert "<that name>"

Nothing has happened yet. That was a preview, and four things in it are worth reading: which checkpoint says **removed**, which say **kept**, any that say something like **2/6 edits removed** (that one shares code with what you are taking out), and the line counting the other features it leaves alone. Now do it:

    sgt revert "<that name>" --yes
    python3 check.py

Then put it back. **`--yes` again.** Without it you get another preview and nothing happens:

    sgt restore "<that name>" --yes
    python3 check.py

`restore` is `revert`'s opposite and takes the same words. If you lose track of where you are, `sgt undo` reverses whatever you last did, and `sgt now` says where things stand.

## 5. Help

    sgt --help
    sgt <command> --help

## Before we start

Run `python3 check.py` once more to see the project still works, and tell us if anything above printed something you could not make sense of.

