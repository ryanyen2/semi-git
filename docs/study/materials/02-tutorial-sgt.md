# Practice: sgt

A few minutes on a practice project first. Ask anything now. Once the stages start we can only answer questions about the stage instructions themselves.

## What it is

`sgt` sits on top of an ordinary git repository. Git records which lines in which files changed. `sgt` records which functions and classes changed, and groups related work under a name.

Two words are worth learning, because you will type both.

A **feature** is a body of work that grew over time, like "the hourly charts".

A **checkpoint** is one step inside a feature, like "split it into weekday and weekend". Checkpoints are what you usually want: a feature can be months of work, a checkpoint is normally one afternoon. Some screens call these **chapters**. Same thing.

Ten minutes will not make you fluent and we do not expect it to. Every command ends by printing what you might want to run next.

## The practice project

Run `study-practice`. It prints a `cd` line -- run that line, and you are in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

You are in the right place if `ls` shows `cart.py`, `discount.py`, `receipt.py` and `shipping.py` (plus a test file for each).

## 1. Open the editor

    study-code

That opens the practice project in VS Code with the **semi-git** extension. Click the semi-git icon in the left bar:

- **Now**, for where things stand.
- **Features**, the work as a tree. Expand a feature to see its checkpoints.
- **Changes**, for what you have edited and not yet saved.

At the bottom, the **workbench** panel draws every feature as a row across time. The chips under each row are its checkpoints. Right-clicking either one offers **Revert** and **Restore**, the two commands section 5 covers.

There are two more views in that sidebar, **Forks** and **Compositions**. Nothing in this session needs them.

## 2. Read one change

Click a checkpoint in the Features tree or the workbench. It shows what it covers, in functions rather than lines. The same thing in the terminal, where `@2` means "the third checkpoint of The Cart":

    sgt show "The Cart@2"

`sgt log` lists the jobs somebody did, newest first, in their own words, and `sgt log --map` draws one row per feature.

## 3. Record some work

Make a small edit to `receipt.py`: change a word in the docstring at the top of the file. (Leave the code alone, so the tests keep passing.) The **Changes** view shows it. Record it, in your own words:

    sgt save -m "reword the receipt docstring"

It files your change under the feature it belongs to and prints which one. Plain `sgt save` works too and says `no words captured`, because the words are yours to give. Stage 1 asks you to do exactly this, on changes someone else made.

## 4. Find a piece of work

Describe it in your own words:

    sgt find "the bit that works out postage"

It lists the closest matches to what you typed: functions, features, and individual saves. The search box in the workbench toolbar does the same. Some rows are shortened to fit, so to get a handle you can type back, use `sgt intent list` -- it prints every feature and checkpoint with its handle.

## 5. Take something out, and put it back

Do this whole sequence now. It is the most useful thing on this sheet.

    sgt revert "The Cart@2"

Nothing has happened yet. That was a preview. Four things in it are worth reading: which checkpoint says **removed**, which say **kept**, any that say something like **2/6 edits removed** (that one shared code with what you are taking out), and the line counting the other features it leaves alone. Now do it:

    sgt revert "The Cart@2" --yes
    python -m pytest -q

Then put it back. **`--yes` again**. Without it you get another preview and nothing happens:

    sgt restore "The Cart@2" --yes
    python -m pytest -q

`restore` is `revert`'s opposite and takes the same words. If you ever lose track of where you are, `sgt undo` reverses whatever you last did, and `sgt now` says where things stand.

## 6. Some work spans several features

One job done over a few afternoons can end up spread across more than one feature. `sgt` groups that too, and lists those groups at the bottom of:

    sgt intent list

They are removed and restored by name, exactly like a chapter:

    sgt revert "<the name it lists>"
    sgt restore "<the name it lists>"

**These groups are the one thing the sidebar does not show yet**, so this is a terminal command. If a later task names a piece of work you cannot find in the tree, look at the bottom of `sgt intent list` and type its name.

## 7. Help

    sgt --help
    sgt <command> --help

## Back to the real project

When you are done practising, run:

    study-work

That puts you back in the project the stages use. The stages will not find their commands anywhere else.

## Before we start

Tell us if anything printed something you could not make sense of.
