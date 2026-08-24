# Practice: sgt

Ten minutes on a practice project first. Ask anything now. Once the real requests start we can only answer questions about the requests themselves.


## What it is

`sgt` sits on top of an ordinary git repository. Git records which lines in which files changed. `sgt` records which functions and classes changed, and groups related work under a name.

Two words are worth learning, because you will type both of them.

A **feature** is a body of work that grew over time, like "the hourly charts".

A **chapter** is one step inside a feature, like "split it into weekday and weekend". Chapters are what you usually want. A feature can be months of work; a chapter is normally one afternoon.

Ten minutes will not make you fluent and we do not expect it to. Every command ends by printing what you might want to run next.

## The practice project

Run `study-practice`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

If anything below shows names you do not recognise, you are in the real project. Run `study-practice` again.

## 1. The editor first

    study-code

That opens the practice project in VS Code with the **semi-git** extension. Click the semi-git icon in the left bar:

- **Now**, for where things stand.
- **Features**, the work as a tree. Expand a feature to see its chapters.
- **Changes**, for what you have edited and not yet saved.

At the bottom, the **workbench** panel draws every feature as a row across time. The chips under each row are its chapters.

Right-clicking a feature or a chapter offers the same verbs as the commands below. **Toggle Feature Blame** puts the owning feature at the end of whichever line your cursor is on.

## 2. Look around, in the terminal

    sgt now           where things stand
    sgt log           the jobs somebody did, newest first, in their own words
    sgt log --map     one row per feature, with its chapters underneath

In `--map`, the bars show how busy a feature was at that moment. The `@0`, `@1`, `@2` chips underneath are its chapters, each with a name.

## 3. List the chapters

    sgt intent list

One line per chapter, each with the handle you can type back:

    ● The Cart  [f-3f9a21b4]  3 checkpoint(s)
        [0] Cart Basics        (f-3f9a21b4@0)
        [1] Remove Items       (f-3f9a21b4@1)
        [2] Cart Total         (f-3f9a21b4@2)

## 4. Ask what one thing is

Hand back a handle, a name, or a function:

    sgt show "The Cart@Cart Total"       what that chapter covers
    sgt show cart.py::total              what one function belongs to

The chapter view tells you which symbols it covers, which saves built it, and what removing it would cost. Read that last line before you remove anything.

## 5. Find something when you do not know its name

Describe it:

    sgt find "the bit that works out postage"

It ranks features, chapters and functions against your words. The search box in the workbench toolbar does the same.

## 6. Take one chapter out, and put it back

Do this whole sequence. It is the most useful thing in these ten minutes.

    sgt revert "The Cart@Cart Total"

Nothing has happened yet. That was a preview, and three things in it are worth reading: which chapter is marked **removed**, which ones say **kept**, and the line saying how many other features are unchanged. Now do it:

    sgt revert "The Cart@Cart Total" --yes
    python -m pytest -q

Then put it back:

    sgt restore "The Cart@Cart Total" --yes
    python -m pytest -q

Both take the same words. If you would rather not think about which verb undoes what, `sgt undo` reverses whatever you last did.

You can name the whole feature instead of one chapter, and it will take the lot. The preview lists every chapter it would remove, so read it before saying yes.

## 7. Your assistant

`claude` starts it in the study shell. It can drive this tool as well as the shell, so "what happened to the free shipping rule" and "take the cart total out" are both things you can type at it.

It can also plan before it acts. Ask it to plan first, or use its plan mode, and it lays out the steps before touching anything. You do not have to try that now.

## 8. Help

    sgt --help
    sgt <command> --help

## Before we start

Tell us if anything printed something you could not make sense of.

