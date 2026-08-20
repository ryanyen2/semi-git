# Practice: sgt

Ten minutes on a practice project first. Ask anything now. Once the real requests start we can only answer questions about the requests themselves.

## What it is

`sgt` sits on top of an ordinary git repository. Git records which lines in which files changed. `sgt` records which functions and classes changed, and groups related work under a name. It calls those groups **features**.

Ten minutes will not make you fluent and we do not expect it to. Every command ends by printing what you might want to run next, so you can follow that rather than memorising anything.

## The practice project

Run `study-practice`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

`sgt` has already read its history and found four features:

| Feature | What it is |
|---|---|
| **The Cart** | adding and removing things, and the total |
| **Discounts** | a percentage off, or a coupon code |
| **Receipts** | turning a cart into something you can print |
| **Shipping** | what it costs to post an order |

Those names are what you hand back to the commands below.

## 1. The editor first

```
study-code
```

That opens the practice project in VS Code with the **semi-git** extension installed. Click the semi-git icon in the left bar and you get:

- **Now**, for where things stand and anything waiting on you.
- **Features**, the four above as a tree. Expand one to see what it covers.
- **Changes**, for what you have edited and not yet saved.

At the bottom, the **SGT Workbench** panel draws every feature as a row across time, so you can see which ones were being worked on at the same moment. There is a search box in its toolbar: type `shipping` into it.

Right-clicking a feature offers the same verbs as the commands below. **Toggle Feature Blame** puts the owning feature at the end of whichever line your cursor is on.

Open `shipping.py` with blame on. That is the shape of the thing you will be asked about later.

## 2. Look around, in the terminal

```
sgt now           where things stand
sgt log           your saved work, newest first
sgt log --map     one row per feature, across time
sgt log --tree    just the four features and their handles
```

In `--map`, the bars are how busy a feature was at that moment, and the `@0`, `@1`, `@2` chips underneath are its **checkpoints** — the chapters within one feature.

## 3. Ask what one thing is

Hand back a name, a function, or a save id. All three of these work:

```
sgt show "Shipping"                what the feature covers
sgt show cart.py::total            what one function belongs to
sgt show 44da4ad                   what one save did
```

Try the first. It tells you it covers five things in two files, lists the four saves that built it, and says how many edits removing it would take with it.

For a feature's chapters:

```
sgt log --focus "Shipping"
```

## 4. Find something when you do not know its name

Describe it:

```
sgt find "the thing that works out postage"
```

It ranks features, saves and functions against your words and hands you back the ids. The search box in the workbench toolbar does the same thing.

## 5. Record a change

Edit anything — a function, or just the README — then:

```
sgt save -m "what you changed, in your own words"
```

Your words become the name of that work, and it tells you which feature the change landed in. Do it once now so you have seen it happen.

## 6. Take something out, and put it back

Do this whole sequence. It is the most useful thing in these ten minutes.

```
sgt revert "Receipts"
```

Nothing has happened yet. That was a preview, and three things in it are worth reading: which chapters would go, that it removes 14 edits across 2 files, and the line saying **3 other feature(s) unchanged**. Now do it:

```
sgt revert "Receipts" --yes
python -m pytest -q
```

`receipt.py` and its tests are gone, and the other nine tests still pass. Put it back:

```
sgt undo
python -m pytest -q
```

Eleven again. `sgt undo` reverses the last thing sgt did; `sgt restore "<name>"` brings back something removed longer ago.

You can also take out one chapter rather than a whole feature:

```
sgt revert "Shipping"@2
```

Preview first is the rule everywhere, including in the editor.

## 7. Your assistant

`claude` starts it in the study shell. It can drive this tool as well as the shell, so "what came along with free shipping over fifty" and "take the receipts out" are both things you can just type at it. The workbench paints what a change would do to the graph while it happens.

It can also plan before it acts. Ask it to plan first, or use its plan mode, and it lays out the steps it intends to take before touching anything; `sgt` records that plan next to the work, so afterwards you can compare what it said it would do with what it did. You do not have to try that now. It is worth knowing about for the second request.

## 8. Help

```
sgt --help
sgt <command> --help
```

## Before we start

Tell us if anything printed something you could not make sense of.
