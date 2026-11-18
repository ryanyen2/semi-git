# User workflows

This is a tour of what `ugh` does today, one use case at a time. Each section names the commands,
walks through a real example, and says where it is solid and where it still has limits.
The [last section](#5-what-is-fixed-and-what-still-has-limits) collects the limits in one place.

Read [The model](the-semantic-tree.md) first. The short version: history is a set of symbol-level
edits called ops. The current state of your codebase is a subset of those ops called the ideal,
and putting that subset back together reproduces exactly what is on disk. `ugh` never writes your
code. You or your agent do that, and `ugh` tracks which edits are currently included.

Every example below ran against a scratch repo while writing this doc, or comes from the
project's own test suite, cited by file. None of the terminal output here is invented.

## 1. The solo daily loop

This is git's daily loop, with `ugh` reading along.

```bash
ugh init                    # once per repo: read your existing history into the op store
# edit files with your editor or agent, the same as always
ugh save -m "add input validation"   # commit your edits as git would, and read them into steps
ugh status                  # files, symbols, coverage, two-version choices, unsaved edits, the plan
ugh undo                    # step back: undo your last save, revert, or restore
```

`ugh undo` only moves forward. Instead of rewinding history, it takes out what your last save,
revert or restore did as a new change, so you never lose the record of what actually happened and
work you committed since stays.

A save prints each step it recorded with its address (`rate_limit.py::limit@3f2a1c0`). That moment
is when you remember what the work was, so it is also when you can name it: `ugh save -m "..." --as
"rate limiting"` names the save's steps. `ugh` never names anything itself, and a later edit to a
named definition carries no name unless you save it `--as` that name or run `ugh name <address>
"rate limiting"`. `ugh log` prints each save with the names on its steps, how many steps carry no
name, and how many are out.

## 2. Remove one thing from a big, tangled edit

This is the main reason to use `ugh` instead of plain git.

Say an agent, or you moving fast, lands three unrelated changes in one sweep: rate limiting, a
caching layer, and a retry policy, spread across several files, in one commit. The caching layer
turns out to be wrong. In plain git, undoing only that means finding every line the caching work
touched and reverting those lines by hand, or cherry-picking around a commit that also holds the
two features you want to keep, and hoping the surgery does not damage anything else.

`ugh` already tracked every edit as a change to one definition, so you can name the work and take
it out. This is real output from a scratch repo built for this doc: three files committed, then an
agent pass that changed two of them in one commit.

```
$ ugh revert cache.py::get_cached --yes
  revert cache.py::get_cached
  goes: 1 step(s) (787d66b "agent pass: rate limit, caching")
  files: cache.py
  check: not run yet
  ✓ revert applied — 1 step(s) undone, 0 held, the parser passes. (`ugh undo` reverses this.)
```

After that command, `get_cached` is gone from `cache.py`. `set_cached`, the other function in the
same file, is untouched. `rate_limit.py` and `retry.py` are byte for byte what they were before.
`ugh restore cache.py::get_cached` puts it back:

```
$ ugh restore cache.py::get_cached --yes
  restore cache.py::get_cached
  comes back: 1 step(s) (f7d23f8 "ugh revert cache.py::get_cached")
  files: cache.py
  check: not run yet
  ✓ restore applied — 1 step(s) undone, 0 held, the parser passes. (`ugh undo` reverses this.)
```

A step is one save's change to one unit: a top-level definition with the blank lines before it and
the comments that follow it, the head of a file, or a whole file. A save has a step on a unit only
where the unit's text changed, so a save that appends a definition after another is one step, on
the new one. Taking a step out is a new edit: the step's change un-applied onto the unit as it is
now. That edit is a commit whose `Ugh-Out:` trailer names the ops of the steps it took out, so what
is out lives in git history and a clone or a rebase knows it. Bringing a step back is
taking out the edit that took it out, so edits you made in between combine with it. Where they
rewrote the same lines, nothing is written for that step and both versions are shown.

A step is held when other remaining work stands on it, and the preview says why: other code uses
the definition it created (`uses`), a later save changed that definition or added to the file it
created (`changed_later`), or a later save rewrote the same lines (`same_lines`). The preview also names the removal set: the work
plus everything that stands on it, never cutting another save in two. `ugh revert <target> --whole`
takes that whole set out. The project's check (the tiers in `.sgt/oracle.json`) must pass on the
tree before anything is written. Then it runs on the result, together with the parser (every file
that parsed must still parse): a failure at a line inside a definition the take-out changed holds
that step, and a failure anywhere else applies nothing and shows the output.

The target here was an address. Every verb that acts on steps takes one:

```bash
ugh revert caching                   # every step named caching
ugh revert caching@787d66b           # those steps in one save
ugh revert cache.py                  # every step on one file
ugh revert cache.py::get_cached      # every step on one top-level definition
ugh revert cache.py::get_cached@787d66b   # one step
ugh revert 787d66b                   # every step one save made
ugh revert 787d66b..                 # every save from there through HEAD
```

An address reaches steps that are out as well as steps that are in, so `restore` takes the same
addresses. `ugh show <address>` prints the steps, the ask behind each save it touches, and the
planner's answer before anything is changed.

### When you do not know the address

`ugh revert` takes only an address. A phrase like `ugh revert "the caching layer"` is refused, with
the nearest `ugh find` hits:

```
$ ugh find caching
  caching        name
  787d66b        subject: agent pass: rate limit, caching
```

`ugh find` matches names, save subjects, definition names and captured asks by plain word
matching, and prints addresses. No language model is involved anywhere in `ugh`.

## 3. Working with other people: sync and two-version choices

A save is a plain git commit, and every clone reads the same steps out of the same history, so
only the names you gave travel beside your branches (on `refs/ugh/state`). `ugh sync` fetches a
teammate's branch and merges it one definition at a time: a definition only one side changed takes
that side, one both sides changed on different lines combines both, and one both sides changed on
the same lines keeps yours in the file, with no conflict markers, as a two-version choice.

This example comes from the project's own test suite (`tests/core/test_sync.py`).

Alice and Bob both cloned `main.py`, where `foo()` returns `1`.

Alice fixes a bug in `foo()` so it returns `999`:

```bash
ugh save -m "fix: foo off-by-one"
ugh push
```

Bob, not knowing about Alice's fix, changes the same line of the same function to return `42`:

```bash
ugh save -m "fix: foo edge case"
ugh push        # rejected, because main has moved: run `ugh sync origin main`
ugh sync origin main    # merged; main.py::foo is a two-version choice
```

The merge commit names the choice in an `Ugh-Choice: main.py::foo` trailer, and Alice's version is
its second parent, so any clone that has the merge knows about the choice and can read both
versions. Until it is settled, `ugh status` lists it and `ugh save` refuses, naming it.

```bash
ugh resolve main.py::foo            # prints ours (the file now) beside theirs
ugh resolve main.py::foo --theirs   # write Alice's version
```

`--ours` keeps Bob's version, and `--apply` keeps whatever the file now holds, after Bob edits it by
hand. Each commits with an `Ugh-Resolves: main.py::foo` trailer, and the choice is closed on every
clone that gets that commit. The project's check does not gate it.

The merge works by definition rather than by line across the file, so two people adding different
functions at the same place merge cleanly where git would conflict, in the order yours then theirs.
If you merge with plain `git merge` instead, you get git's result, and `ugh` accepts that merge as
settled.

Names merge per step against the last state both sides shared: a step one side renamed takes the
new name, and a step both sides renamed differently keeps yours, with both names printed under its
address so you can settle it with `ugh name`.

## 4. Using this with Claude Code or any MCP client

`ugh mcp` runs a stdio MCP server so an agent can call `ugh` directly instead of running it as a
shell command. It exposes 10 tools: `ugh_init`, `ugh_log`, `ugh_check`, `ugh_save` (with `as`),
`ugh_name`, `ugh_revert`, `ugh_restore`, `ugh_show`, `ugh_find`, and `ugh_plan`. Every operand is an
address, and every step in a result carries its address.

A plan is a list of names declared before their edits exist: `ugh plan export audit` (or
`ugh_plan` with `declare`) records them with HEAD as the baseline. The agent saves each piece of work
`--as` the name it declared. `ugh plan` then reports each name with the saves since the baseline
that carry it, or `missing`, and lists the steps since the baseline that carry no declared name.
`ugh plan --done` clears it.

So an agent driving `ugh` over MCP can declare a plan, save and name its own work, and revert and
restore by address, which covers sections 1 and 2 above. The commands for working with other people, `sync` and `resolve`, have no MCP tool. If a
sync leaves a two-version choice, `ugh_save` refuses and names it, and a person settles it with
`ugh resolve` in the terminal.

## 5. What is fixed, and what still has limits

A review on 2026-07-12 found ways that ordinary git history could break the rules `ugh`
depends on. These are fixed now.

- A file or symbol that was deleted and then re-added no longer disappears. A symbol's version is
  a hash of its content and the version it came from, so a re-added symbol continues its earlier
  chain and content that comes back (a comment changed and changed back, a change retyped after it
  was taken out) is a new version.
- Symlinks are left alone; ugh never mines them.
- `ugh sync` is a three-way merge against the common ancestor, and a teammate's revert is a commit
  like any other, so it stays reverted instead of coming back.

These limits remain, either by design or because they are not built yet:

- Op ids are a function of each symbol's content sequence since the horizon `ugh init` mined
  from, so the same commits mined from two different horizons give different ids. A rebase that
  keeps each symbol's content sequence keeps its ids.
- A rename is one op that removes the old name and creates the new one; the renamed definition
  starts a new chain.

- `ugh` reads Python, TypeScript, and TSX into real symbols using tree-sitter. Any other language
  is tracked as exact, faithful whole-file content. Nothing is misread, but you cannot revert one
  symbol at a time in those files.
- Some TypeScript constructs, such as enums, `type` aliases, and namespaces, are kept as exact file
  content rather than pulled out as individual symbols, so you cannot revert them on their own.
- A revert takes out an import its own work added once nothing else needs it, and holds one that
  remaining code still uses. Code that reaches a definition without naming it (`getattr` with a
  string, a callback registered elsewhere) is only caught by the project's check.
- `ugh status` is slow on a large op store. On a store of several thousand ops it can take
  around a minute, because rebuilding the full valid state is expensive and the summary currently
  does it more than once per run. This is a speed problem, not a correctness one.

Run `ugh check` any time you are unsure about the state of a repo. It checks that the current state
is valid, and `ugh check --tree` that the files it builds match what git actually has.
