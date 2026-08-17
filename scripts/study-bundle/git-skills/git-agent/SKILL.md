---
name: git-agent
description: Read this whenever you are doing coding work in a git repository. It tells you how to orient in one cheap call, the default posture for recording your work (add commits on top; never rewrite history unless the human asks for it in those words), how to read history to answer "why is this code the way it is", which git actions are yours versus the human's (the ones that rewrite shared or published history), and how to show git output in a transcript without dumping a screenful. Load it before your first git command, not after a surprising one.
---

# Working in a git repo

git records your codebase as a chain of commits, each a snapshot with a message saying why. That
gives you two things worth using deliberately: a way to record your own work so the next person
(or you, later) can see what changed and why, and a searchable record of how every line got to be
the way it is.

Your job is to use both without disturbing history other people are already building on. The rest
of this file is about doing that without wasting the human's time or leaving the repo in a state
they did not ask for.

## Orient with one call

Before your first edit, run this. It is cheap and it answers "where am I and is anything already
in flight" in one look:

```bash
git status && git log --oneline -n 20
```

`status` shows the branch, whether the tree is dirty, and whether a merge/rebase/cherry-pick is
paused mid-way. `log --oneline` shows the recent shape of history. Two things in that output mean
**stop and tell the human** rather than working around them:

- **A paused merge/rebase/cherry-pick** (`status` says "You are currently rebasing", "fix conflicts
  and run git ... --continue", or the tree has `<<<<<<<` conflict markers). Anything you commit now
  is committed against a half-finished operation. Do not `--continue`, `--abort`, or commit over it
  on your own — say what you found and let the human decide.
- **A detached HEAD** (`status` says "HEAD detached at ..."). Commits you make here belong to no
  branch and are easy to lose. Point it out before you add work.

## The default posture: add, don't rewrite

Almost all of the time, the right way to record your work is to **make a new commit on top of what
is already there**:

```bash
git add -A && git commit -m "…"
```

That is additive: it never changes a commit that already exists, so nothing anyone has already
pulled or built on can break. This is the default and you should stay in it unless the human asks
you, in those words, to do something to the *history itself*.

**Rewriting history is opt-in.** `git commit --amend`, `git rebase` (interactive or not),
`git reset --hard`, `git squash`, `git filter-branch`/`filter-repo`, and force-pushing all
*replace* commits rather than adding to them. They are the right tool when the human wants a tidier
history — "squash these into one", "amend that last commit", "rebase onto main" — but they are
never something to reach for on your own initiative because a diff looked messy. A clean-looking
history is not worth silently discarding commits the human may still want, and a rewrite you did
uninvited is the kind of change that is expensive to notice and expensive to undo.

So: if the task is "implement X", commit X on top. If the task is "clean up / squash / amend /
rebase X", then a rewrite is what was asked — do it, but read the next section first, because
whether the target is already shared decides whether it is even safe.

## The one line you do not cross: published history

A rewrite is safe on commits that live only in your working repo. It is *not* safe on commits that
have been pushed to a shared branch, because other clones still reference the originals — a
force-push there rewrites what other people are standing on.

Before any rewrite, check whether the target is already published:

```bash
git log --oneline @{upstream}..HEAD   # commits you have that the remote does not — safe to rewrite
git log --oneline HEAD..@{upstream}   # commits the remote has that you do not — a pull, not a rewrite
```

Commits in the first list are yours alone and fair game. If a rewrite would touch anything *below*
that boundary — already on `origin/main` or any branch others use — that is the human's call, not
yours. Say so and hand them the exact command; do not force-push a shared branch to make your local
history win. Reverting a bad commit with a *new* commit (`git revert <sha>`) is the additive way to
undo published work, and it is almost always what you want there instead.

## Reading history to answer "why"

git's best feature for you is the recorded reason behind a line. Before you change unfamiliar code,
ask history why it is the way it is instead of guessing and breaking a constraint someone put there
on purpose:

| You want to know | Use |
|---|---|
| Who last changed each line here, and in which commit | `git blame <file>` |
| The full message + diff of the commit a blame points at | `git show <sha>` |
| When a particular string or symbol was added or removed | `git log -S'<text>' -- <path>` (the "pickaxe") |
| Every commit that touched this file, following renames | `git log --follow -p -- <file>` |
| What changed on this branch since it left main | `git log --oneline main..HEAD` |

`git show <sha>` on the commit a blame names usually turns "why is this null-check here" into the
message that explains it. Read that before removing the check.

## What is yours and what is theirs

Yours, freely: every read above (`status`, `log`, `blame`, `show`, `diff`), and recording your own
work additively (`git add` / `git commit`, `git revert <sha>` to undo a published commit with a new
one). These do not rewrite anything and do not touch a remote.

Theirs, unless they ask in those words and you have checked the published-history boundary above:

- **Rewrites of history** — `commit --amend`, `rebase`, `reset --hard`, squash, `filter-*`. Fine on
  your own unpushed commits when asked; off-limits on anything already shared.
- **Anything that changes a shared remote** — `git push --force`/`--force-with-lease`, pushing to a
  branch you do not own, deleting a remote branch. These change what other people pull.
- **Resolving a paused merge/rebase or aborting one** — the tree is mid-operation and the human can
  see a consequence in their terminal that you cannot.

When one of these is the right next step, say so and hand it over with the exact command, the same
way you would flag any change that is hard to reverse. That is more useful than doing it, because
they can see the state you would be changing.

## Showing git output to a human

git's history views are built to be read live, and pasted into a transcript they are a wall of text
that costs tokens and buries your point.

- Reading it yourself: use the porcelain you need and summarize in your own words.
- Showing them recent shape: `git log --oneline -n 20` is narrow and scannable; prefer it to a full
  `git log`.
- Never paste a full `git log` (every message and author), a long `git log --graph`, or a large
  `git diff`/`git show` in full. Point at the command and let them run it, or quote the one or two
  lines that matter.

## Committing well

The commit message is the "why" the next reader gets. `git add -A && git commit -m "…"` is the
whole of it — spend the effort on the message, not the ceremony:

- Say what changed and why, not how. "Fix null pointer in user lookup when email has uppercase" beats
  "fix bug", which tells the next person nothing.
- One coherent change per commit. If you did two unrelated things, that is two commits (`git add -p`
  to stage one of them), so either can be read — or reverted — on its own later.
- Do not commit generated files, secrets, or unrelated churn you happened to touch. `git status`
  before you `add -A`, and stage narrowly if the tree has more in it than your change.
