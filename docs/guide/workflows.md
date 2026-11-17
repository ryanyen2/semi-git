# User workflows

This is a tour of what `ugh` does today, one use case at a time. Each section names the commands,
walks through a real example, and says where the feature is solid and where it still has limits.
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
ugh save -m "add input validation"   # read your edits into ops and commit a record of them
ugh status                  # files, symbols, features, coverage, and any open forks or drift
ugh undo                    # step back: undo your last save, revert, or restore
```

`ugh undo` only moves forward. Instead of rewinding history, it records a new change that undoes
the last one, so you never lose the record of what actually happened.

A save also answers "where did that work go": it prints the feature(s) the new edits landed in,
by name, with a typeable handle. That moment is when you remember what the work was, so it is
also when you can name it — `ugh save -m "..." --as "rate limiting"` names the save's feature on
the spot (a permanent, user-authored label that wins over any auto-generated one). A save that
scatters across three or more features says so, since that usually means the grouping and your
intent have drifted apart.

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

Taking a step out is a new edit: the step's change un-applied onto the definition as it is now.
Bringing it back is taking out the edit that took it out, so edits you made in between combine
with it. Where they rewrote the same lines, nothing is written for that step and both versions are
shown.

A step is held when other remaining work stands on it, and the preview says why: other code uses
the definition it created (`uses`), a later save changed that definition (`changed_later`), or a
later save rewrote the same lines (`same_lines`). The preview also names the removal set: the work
plus everything that stands on it, never cutting another save in two. `ugh revert <target> --whole`
takes that whole set out. Before anything is committed the project's check runs on the result (the
tiers in `.sgt/oracle.json`; with none configured, every file that parsed must still parse), and a
step the check points at is held too.

The target here was a `file::symbol` name. It can also be an op id, a save's commit sha, a feature,
or one of a feature's checkpoints:

```bash
ugh revert <sha>                     # every step one save made
ugh revert <feature-id-or-label>     # every op the feature tree grouped under that feature
ugh revert <feature>@<n>             # the ops of one checkpoint, as `ugh log` numbers them
```

How reliable the grouped forms are depends on where the grouping comes from.

- Symbol-level and save-level revert always work. They only need the op set, nothing else.
- Feature-level revert needs the feature tree to have grouped the ops first. `ugh log --refresh` builds
  that grouping from how symbols tend to change together across your history, so it needs enough
  commits and enough churn to find real seams. On a brand-new repo it reports one feature for
  everything, because there is no history yet to split on. Use `ugh feature rename` to name a
  feature in your own words.

So symbol-level and save-level revert are the reliable tools today. Feature-level revert works well once
`ugh log --refresh` has had real history to learn from. Run `ugh show <file>::<symbol>` to see which
feature a symbol landed in before you trust the grouping.

### When you do not know the exact name

If none of op id, `file::symbol`, or feature label matches, `ugh revert` and `ugh restore` fall
back to a plain-English target.

```
$ ugh revert "the caching layer"
? [revert] 'the caching layer' did not resolve; did you mean:
  1. cache.py::get_cached (symbol) — the only caching-related function in the diff
     would undo 1 step(s), hold 0
     re-invoke: ugh revert cache.py::get_cached
```

An LLM proposes ranked candidates grounded in your repo's own op ids, symbols, and feature labels.
It never invents one. Each candidate is checked for real before it is shown, so a made-up or
no-longer-live name never makes it into the list. Nothing is applied by default. Re-run the
command with the exact name it printed to apply it, or add `--yes` to apply the top candidate
directly. This needs `OPENAI_API_KEY`. With no key set, the command fails with a clear message
instead of guessing.

This is the least reliable of the three ways to name a target, by design. It is a proposal, always
previewed first, and whatever gets applied is still the same exact, predictable op, symbol, or
feature revert described above. The only difference is that you pointed at it with a guess in
plain English instead of an exact name.

## 3. Working with other people: sync and forks

If two people edit the same function at the same time, that is a real conflict, and `ugh` does not
make it go away. What it changes is the size of the conflict. `ugh` narrows it down to the one
symbol both people touched, which it calls a fork, and merges everything else right away with no
conflict markers to resolve.

This example comes from the project's own test suite
(`tests/core/test_sync.py::test_sync_records_a_fork_and_lands_the_forked_symbol_at_the_common_ancestor`).

Alice and Bob both cloned `main.py`, where `foo()` returns `1`.

Alice fixes a bug in `foo()` so it returns `999`:

```bash
ugh save -m "fix: foo off-by-one"
git push
```

That goes through cleanly, because she pushed first.

Bob, not knowing about Alice's fix, changes the same function to return `42`:

```bash
ugh save -m "fix: foo edge case"
git push        # rejected, because main has moved
ugh sync origin main
```

`sync` fetches Alice's commit and merges the two op sets. It sees that both edits started from the
same version of `foo`, which makes this a fork. It commits a state where `foo()` sits at the
content it had before the fork, so it never silently picks one side over the other. It writes the
fork down as shared state in `.sgt/forks.json` and tells Bob what to do next:

```bash
ugh status
# ⚠ 1 open fork — divergent edits to one symbol:
#     main.py::foo  →  ugh resolve main.py::foo
```

Bob resolves it:

```bash
ugh resolve main.py::foo          # prints both versions of foo
# edit main.py so foo holds the version you want, then:
ugh resolve main.py::foo --apply
```

`resolve --apply` runs your build and test checks against the reconciled version first and refuses
to commit it unless those checks pass, or you record a human override with a reason (`--override`). A fork cannot be
closed by a version nobody verified. Once it lands, `main.py::foo` is one continuous chain again,
and the fork record closes.

When nothing forks, which is the common case because most syncs touch separate files, `ugh sync`
just merges, reports `merged=True`, and you see none of the above.

## 4. Using this with Claude Code or any MCP client

`ugh mcp` runs a stdio MCP server so an agent can call `ugh` directly instead of running it as a
shell command. It exposes 13 tools, not the full command set: `ugh_init`, `ugh_log`, `ugh_save`,
`ugh_show`, `ugh_find`, `ugh_advanced_fsck`, `ugh_revert`, `ugh_restore`, `ugh_plan_intake`,
`ugh_checkpoint`, `ugh_drift`, `ugh_plan_done`, and `ugh_plan_adopt`.

So an agent driving `ugh` over MCP can inspect state, run the plan → checkpoint → drift loop, save
its own work, and do symbol-level revert and restore, which covers section 1 and part of section 2
above. The commands for working with other people, `sync` and `resolve`, have no MCP tool. If a
sync produces a fork, a person has to run those commands in the terminal.

## 5. What is fixed, and what still has limits

A review on 2026-07-12 found ways that ordinary git history could break the rules `ugh`
depends on. These are fixed now.

- A file or symbol that was deleted and then re-added no longer disappears from your working tree.
  The case that used to cause a silent delete now keeps the file and reports it in `ugh status`
  and `ugh check --tree`. Re-adding a symbol now chains onto its earlier history, which recovered most of
  the ops the old version dropped. One edge case remains: if a symbol is deleted and re-added with
  the exact same content, the two versions still register as a fork.
- Symlinks are now left alone. `ugh` never writes or deletes through a symlink, whether the
  symlink is the file itself or one of its parent directories.
- `ugh sync` now does a proper three-way merge against the common ancestor, so a teammate's
  deliberate revert stays reverted instead of coming back.

These limits remain, either by design or because they are not built yet:

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
