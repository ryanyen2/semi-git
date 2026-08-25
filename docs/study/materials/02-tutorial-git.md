# Practice: git

A few minutes on a practice project first. Ask anything now. Once the stages start we can only answer questions about the stage instructions themselves.

You already know git. This is not a lesson. It is a warm-up on the four things the stages will ask you to do, so that nothing on this machine surprises you later.

## The practice project

Run `study-practice`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

It has four pieces: `cart.py` (adding and removing things, and the total), `discount.py` (a percentage off, or a coupon code), `receipt.py` (printing a receipt), and `shipping.py` (what postage costs). Sixteen commits, and `python -m pytest -q` passes.

If `ls` shows anything else, you are in the real project. Run `study-practice` and try again.

## 1. Open the editor

```
study-code
```

That opens the practice project in VS Code with **GitLens** installed. Find these now, because the stages will want them:

- **Source Control** in the left bar, for what has changed and where you commit.
- **Commit Graph.** The GitLens icon in the left bar, or *GitLens: Show Commit Graph* from the command palette. The history as a graph you can click through.
- **File History.** Right-click any file, *Open File History*.

## 2. Read one change

Open the Commit Graph and click a commit. You see what it changed, file by file. The same thing in the terminal:

```
git show 44da4ad
```

## 3. Record some work

Make a small edit to `receipt.py` (change any wording in a string). Then record it the way you normally would: stage it and commit it in Source Control, with a message. Or in the terminal:

```
git add receipt.py
git commit -m "reword the receipt footer"
```

Stage 1 asks you to do exactly this, on changes someone else made.

## 4. Find a piece of work

`git log -S` finds the commits where some text arrived or went away:

```
git log --oneline -S "FREE_OVER"
```

Three commits come back: free shipping over fifty arrived, then vanished inside a commit about per-item pricing whose message does not mention it, then came back. Try to see the same story in the Commit Graph. Also useful: `git log --stat`, `git blame shipping.py`, and File History in the editor.

## 5. Take something out, and put it back

```
git revert 7e6e383
```

That makes a new commit undoing an old one. It can conflict if later commits touched the same lines. When it does, git stops and leaves conflict markers in the file: resolve them, `git add` the file, and `git revert --continue`. Or walk away with `git revert --abort`.

To put back what you removed, revert the revert:

```
git revert HEAD
```

Do this whole loop once now, conflicts and all. It is the most useful thing on this sheet, and this is the free copy.

## Before we start

Tell us if any of that behaved differently from what you expected.
