# Practice: sgt

A few minutes on a practice project first. Ask anything now. Once the stages start we can only answer questions about the stage instructions themselves.

## What it is

`sgt` sits on top of an ordinary git repository. Git records which lines in which files changed. `sgt` records which functions and classes changed, and groups related work under a name.

Two words are worth learning, because you will type both.

A **feature** is a body of work that grew over time, like "the hourly charts".

A **chapter** is one step inside a feature, like "split it into weekday and weekend". Chapters are what you usually want: a feature can be months of work, a chapter is normally one afternoon.

Ten minutes will not make you fluent and we do not expect it to. Every command ends by printing what you might want to run next.

## The practice project

Run `study-practice`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts. If anything below shows names you do not recognise, you are in the real project. Run `study-practice` again.

## 1. Open the editor

    study-code

That opens the practice project in VS Code with the **semi-git** extension. Click the semi-git icon in the left bar:

- **Now**, for where things stand.
- **Features**, the work as a tree. Expand a feature to see its chapters.
- **Changes**, for what you have edited and not yet saved.

At the bottom, the **workbench** panel draws every feature as a row across time. The chips under each row are its chapters. Right-clicking a feature or a chapter offers the same verbs as the commands below.

## 2. Read one change

Click a chapter in the Features tree or the workbench. It shows what the chapter covers, in functions rather than lines. The same thing in the terminal:

    sgt show "The Cart@Cart Total"

`sgt log` lists the jobs somebody did, newest first, in their own words, and `sgt log --map` draws one row per feature.

## 3. Record some work

Make a small edit to `receipt.py` (change any wording in a string). The **Changes** view shows it. Record it:

    sgt save

It describes what you changed and files it under the feature it belongs to. Read what it printed: that wording is the record. Stage 1 asks you to do exactly this, on changes someone else made.

## 4. Find a piece of work

Describe it in your own words:

    sgt find "the bit that works out postage"

It ranks features, chapters and functions against your words. The search box in the workbench toolbar does the same. `sgt intent list` lists every chapter with a handle you can type back.

## 5. Take something out, and put it back

Do this whole sequence now. It is the most useful thing on this sheet.

    sgt revert "The Cart@Cart Total"

Nothing has happened yet. That was a preview. Three things in it are worth reading: which chapter is marked **removed**, which say **kept**, and the line saying how many other features are unchanged. Now do it:

    sgt revert "The Cart@Cart Total" --yes
    python -m pytest -q

Then put it back:

    sgt restore "The Cart@Cart Total"
    python -m pytest -q

`restore` is `revert`'s opposite and takes the same words. If you ever lose track of where you are, `sgt undo` reverses whatever you last did, and `sgt now` says where things stand.

## 6. Help

    sgt --help
    sgt <command> --help

## Before we start

Tell us if anything printed something you could not make sense of.
