# Practice: git

Ten minutes on a practice project first. Ask anything now. Once the real
requests start we can only answer questions about the requests.

You already know git. This is only to check that nothing on this machine is set
up oddly, and to remind you what's available.

## 1. Look around

```
git log --oneline
git log --stat
```

## 2. Ask what one change was

```
git show <commit>
git show <commit> -- <file>
```

## 3. Follow one file or one piece of text over time

```
git log -p -- <file>
git log -S "<some text>"
git blame <file>
```

`git log -S` finds commits where the number of times some text appears changed.
It's the usual way to find when something arrived or disappeared.

## 4. Undo something

```
git revert <commit>
```

Makes a new commit that undoes an old one. It can conflict if later commits
touched the same lines. Fix the conflict, or `git revert --abort`.

Branches, for trying something you might throw away:

```
git checkout -b try-something
git checkout main
git branch -D try-something
```

## 5. Help

```
git help <command>
```

Your assistant knows git well, so you can just ask it.

## Before we start

Tell us if any of that behaved differently from what you expected here.
