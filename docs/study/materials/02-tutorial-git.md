# Practice: git

Ten minutes on a practice project first. Ask anything now. Once the real requests start we can only answer questions about the requests themselves.

You already know git. This is not a lesson. It is here so that nothing on this machine surprises you later, and so that you have seen the editor before you need it.

## The practice project

Run `study-practice`. It puts you in a throwaway copy of a small shopping cart program. Nothing you do to it counts.

It has four pieces: `cart.py` (adding and removing things, and the total), `discount.py` (a percentage off, or a coupon code), `receipt.py` (printing a receipt), and `shipping.py` (what postage costs). Sixteen commits, and `python -m pytest -q` passes.

Every command on this sheet runs in the practice copy, and those four files only exist there. If `ls` shows anything else, you are in the real project: run `study-practice` and try again.

## 1. The editor first

```
study-code
```

That opens the practice project in VS Code with **GitLens** installed. Three things are worth finding now, because you will want them later:

- **Source Control** in the left bar, for what has changed and where you commit.
- **Commit Graph.** The GitLens icon in the left bar, or *GitLens: Show Commit Graph* from the command palette. The history as a graph you can click through.
- **File History.** Right-click any file, *Open File History*. Blame also appears greyed out at the end of whichever line your cursor is on.

Open `shipping.py` and look at its file history. Four commits touch it. That is the shape of the thing you will be asked about later.

## 2. Look around, in the terminal

```
git log --oneline
git log --stat
```

## 3. Ask what one change was

Take a real one from that list:

```
git show 44da4ad
git show 44da4ad -- shipping.py
```

## 4. Follow one thing through time

This is the useful one. `git log -S` finds the commits where the number of times some text appears changed, so it tells you when something arrived and when it went away:

```
git log --oneline -S "FREE_OVER"
```

Three commits come back: free shipping over fifty arrived, then vanished inside a commit about per-item pricing whose message does not mention it, then came back. Try to see that same story in the Commit Graph.

Also worth having:

```
git log -p -- shipping.py
git blame shipping.py
```

## 5. Take something out, and put it back

```
git revert 7e6e383
```

Makes a new commit that undoes an old one. It can conflict if later commits touched the same lines. When it does, git stops and leaves the conflict markers in the file for you to resolve, then `git add` the file and `git revert --continue`. `git revert --abort` walks away from the whole thing.

To put back something you reverted, revert the revert:

```
git revert HEAD
```

Or throw the lot away with `git reset --hard 7e6e383`. This is the practice copy, so break it if you like.

Branches, for trying something you might throw away:

```
git checkout -b try-something
git checkout main
git branch -D try-something
```

## 6. Your assistant

`claude` starts it in the study shell. It knows git well and it can run commands for you, so "work out when free shipping stopped applying" is a perfectly good thing to type at it.

It can also plan before it acts. If you ask it to plan first, or use its plan mode, it lays out the steps it intends to take before touching anything. You do not have to try that now. It is worth knowing about for the second request.

Use the editor, the terminal, the assistant, or all three. Whatever you would normally do.

## Before we start

Tell us if any of that behaved differently from what you expected.
