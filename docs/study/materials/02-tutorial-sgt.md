# Practice: sgt

Ten minutes on a practice project first. Ask anything now. Once the real
requests start we can only answer questions about the requests.

## What it is

`sgt` sits on top of an ordinary git repository. Git records which lines in
which files changed. `sgt` records which functions and classes changed, and
groups related work under a name. It calls those groups features.

Ten minutes won't make you fluent, and we don't expect it to. Every command ends
by printing what you might want to run next, so you can follow that rather than
memorising anything.

## Type it as `sgt` from inside the project folder

If `sgt` isn't found, use `../bin/sgt` instead. Your facilitator will tell you
which applies on this machine.

## 1. Look around

```
sgt now          a short summary of where things stand
sgt log          your saved work, newest first
sgt log --map    the same history, one row per feature over time
```

## 2. Record a change

Edit one of the functions, then:

```
sgt save -m "what you changed, in your own words"
```

Your words become the name of the work. It then tells you which feature the
change landed in.

## 3. Ask what something is

Every command prints short ids. Hand any of them back:

```
sgt show <id>
```

You get what it covers, what would come with it, and what you can do next.

## 4. Take something out

```
sgt revert <what>
```

- It shows you what would happen first, and only does it if you add `--yes`.
- `<what>` can be a function like `cart.py::total`, a feature name, or a plain
  English phrase like "the thing that formats dates".

Two more:

```
sgt restore <what>    bring something back
sgt undo              reverse the last thing sgt did
```

Try it now: remove a function, read what it says it would do, do it, then undo
it.

## 5. Help

```
sgt --help
sgt <command> --help
```

Your assistant knows these commands too.

## Before we start

Tell us if anything printed something you couldn't make sense of.
