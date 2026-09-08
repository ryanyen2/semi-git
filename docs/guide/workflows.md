# User workflows

This is a tour of what `ugh` does today, one use case at a time. Each section names the commands,
walks through a real example, and says where the feature is solid and where it still has limits.
The [last section](#7-what-is-fixed-and-what-still-has-limits) collects the limits in one place.

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
ugh now                     # where am I: what's in progress, what needs you, and the one next thing
ugh log --summary                  # files, symbols, features, coverage, and any open forks or drift
ugh undo                    # step back: undo your last save, revert, or restore
```

`ugh now` is the fast "orient me" view. It answers where you are in mid-task: what work is in
flight, what is waiting on a decision from you (an open fork, an ambiguous plan match), and the one
next thing to do. Reach for it when you sit back down; reach for `ugh log` when you want the history.

`ugh switch <branch>` is the `ugh` version of `git switch`. It also rebuilds that branch's files
from its recorded state, so the files on disk and the state `ugh` tracks always move together.
This layer does not do much on its own. It is what lets everything described below work without
changing how you write code day to day.

`ugh undo` only moves forward. Instead of rewinding history, it records a new change that undoes
the last one, so you never lose the record of what actually happened.

A save also answers "where did that work go": it prints the feature(s) the new edits landed in,
by name, with a typeable handle. That moment is when you remember what the work was, so it is
also when you can name it — `ugh save -m "..." --as "rate limiting"` names the save's feature on
the spot (a permanent, user-authored label that wins over any auto-generated one). A save that
scatters across three or more features gets a warning, since that usually means the grouping and
your intent have drifted apart (`ugh feature regroup move` re-files work).

## 2. Remove one thing from a big, tangled edit

This is the main reason to use `ugh` instead of plain git.

Say an agent, or you moving fast, lands three unrelated changes in one sweep: rate limiting, a
caching layer, and a retry policy, spread across several files, in one commit. The caching layer
turns out to be wrong. In plain git, undoing only that means finding every line the caching work
touched and reverting those lines by hand, or cherry-picking around a commit that also holds the
two features you want to keep, and hoping the surgery does not damage anything else.

`ugh` already tracked every edit as a change to one symbol, so you can name the exact symbol and
remove it along with anything built on top of it. This is real output from a scratch repo built
for this doc: three files, one function each, committed together the way a single agent pass
would land them.

```
$ ugh revert cache.py::get_cached
 ▸ rewind  cache.py::get_cached

 removes 1 edit(s) across 1 symbol(s) · 1 file(s): cache.py
  ✓ revert applied — 1 edit(s) removed, 0 added. (`ugh undo` reverses this.)
```

After that command, `get_cached` is gone from `cache.py`. `set_cached`, the other function in the
same file, is untouched. `rate_limit.py` and `retry.py` are byte for byte what they were before.
`ugh restore cache.py::get_cached` puts it back:

```
$ ugh restore cache.py::get_cached
 ▸ restore  cache.py::get_cached

 restores 1 edit(s) across 1 symbol(s) · 1 file(s): cache.py
  ✓ restore applied — 0 edit(s) removed, 1 added. (`ugh undo` reverses this.)
```

If the symbol already has a *different* live version (you reverted, kept working, and now want the
old one back), `restore` does not silently do nothing and does not fork: it explains that only one
version of a symbol can be live, names both versions by the save each came from, and prints the
two ways forward — swap (`ugh revert <live>` then `ugh restore <ghost>`) or reconcile
(`ugh resolve <symbol>`, which combines them through the merge-op flow).

The target here was a `file::symbol` name. It can also be an op id, a feature, or an agent
session:

```bash
ugh revert <feature-id-or-label>     # every op the feature tree grouped under that feature
ugh revert --session <session-name>  # every op attributed to one agent session's landing
```

How reliable each of these is depends on where the grouping comes from.

- Symbol-level revert always works. It only needs the op set, nothing else.
- Feature-level revert needs the feature tree to have grouped the ops first. `ugh log --tree` builds
  that grouping from how symbols tend to change together across your history, so it needs enough
  commits and enough churn to find real seams. On a brand-new repo it reports one feature for
  everything, because there is no history yet to split on. Use `ugh feature regroup merge`, `ugh
  feature regroup split`, `ugh feature rename`, and `ugh feature regroup move` to correct or seed
  the grouping by hand.
- Session-level revert does not depend on the grouping at all. `ugh` stamps every op an agent
  session lands with which session it came from, so `ugh revert --session <name>` is exact from
  the very first run, before there is any history to cluster.

Reverting a set (a feature or a session) removes those ops *and* anything left dangling once the
whole set is gone -- the same "along with anything built on top of it" rule as a single-symbol
revert, applied to the set as a unit. So a dependent that two removed ops both propped up comes out
too, rather than being left in an unbuildable state; the preview always shows the full removal
before you apply it.

So symbol-level revert and session-level revert are the reliable tools today. Feature-level revert
works well once `ugh log --tree`, or your own corrections, has had real history to learn from. Run
`ugh advanced blame <file>` to check whether the current grouping actually covers the file you care
about before you trust it.

### When you do not know the exact name

If none of op id, `file::symbol`, or feature label matches, `ugh revert` and `ugh restore` fall
back to a plain-English target.

```
$ ugh revert "the caching layer"
? [revert] 'the caching layer' did not resolve; did you mean:
  1. cache.py::get_cached (symbol) — the only caching-related function in the diff
     would apply — 1 edit(s) removed, 0 added
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
ugh advanced forks
# main.py::foo — tips <alice's op> / <bob's op> — run `ugh resolve main.py::foo` to reconcile
```

Bob resolves it:

```bash
ugh resolve main.py::foo          # the guided one-liner: drafts the merge, then --apply lands it
# — or the same thing by hand, if you want each step:
ugh advanced merge-op <alice_op> <bob_op> --intent "reconcile foo fix"
# drafts a placeholder op chained onto Alice's tip, still needing real content
# edit main.py by hand or with an agent to the real reconciled foo()
ugh advanced fulfill <draft-id> --from-tree
ugh advanced commit
```

`advanced commit` runs your build and test checks against the reconciled version first and refuses
to commit it unless those checks pass, or you record a human override with a reason. A fork cannot be
closed by a version nobody verified. Once it lands, `main.py::foo` is one continuous chain again,
and the fork record closes.

When nothing forks, which is the common case because most syncs touch separate files, `ugh sync`
just merges, reports `merged=True`, and you see none of the above.

For a shared branch that several people, or CI, or several agents might try to advance at once,
`ugh land <branch>` only lets one advance through at a time. It compares against the branch's
current git ref and only writes if that ref has not moved since you checked. If someone else won
the race, you re-merge against the new tip and try again.

## 4. Parallel agent sessions

`ugh session` is for agent work that needs to run on its own for a while, in isolation from
everything else.

```bash
ugh session start caching-refactor --base main   # a real git worktree, own branch, own directory
# point an agent or yourself at that worktree, and edit and save there normally
ugh session status --watch      # warns early if two sessions start touching the same code
ugh session land caching-refactor    # lands its ops onto main and stamps which session they came from
```

The stamp that `land` writes on every op is what makes `ugh revert --session caching-refactor`
work later. It is a permanent record kept with the op itself, not something read off the session,
so it still resolves long after the session has landed and its scratch worktree is gone. `ugh
session gc` cleans up sessions whose owning process has died, and leaves live ones alone.

## 5. Proposing and publishing work for review

For a review flow close to a pull request, but where a reviewer can accept features one at a time:

```bash
ugh propose create --base main --title "Add rate limiting + caching"
ugh propose status <id>                       # current, needs re-merge, or forked, against base
ugh propose land <id> --subset rate-limiting  # advance main by just that feature's ops
ugh propose render <id> --github              # a suggested branch and PR body in plain markdown
ugh propose publish <id> --remote origin      # push, and create or update the GitHub PR through gh
```

`--subset` is the point of this command. A reviewer can accept the features that are ready without
waiting on the rest. `land` refuses if the features you picked are missing something they need, and
it tells you what is missing instead of applying a broken state.

One limit worth knowing: if two features sit directly next to each other in the same file, meaning
two functions back to back that share the whitespace between them, accepting only one of them with
`--subset` can produce a file that is missing the separator between them. This is a known limit in
this version (see the last section). Features in separate files do not have this problem.

## 6. Using this with Claude Code or any MCP client

`ugh mcp` runs a stdio MCP server so an agent can call `ugh` directly instead of running it as a
shell command. It exposes 14 tools today, not the full command set: `ugh_init`, `ugh_log`,
`ugh_status`, `ugh_diff`, `ugh_advanced_fsck`, `ugh_revert`, `ugh_restore`,
`ugh_advanced_oracle_run`, `ugh_plan_intake`, `ugh_checkpoint`, `ugh_recall`, `ugh_drift`, and
`ugh_plan_done`.

So an agent driving `ugh` over MCP can inspect state, recall why the code it is about to touch is
the way it is (`ugh_recall`), run the plan → checkpoint → drift loop, and do symbol-level revert and
restore, which covers section 1 and part of section 2 above. The
commands for working with other people, `sync`, `land`, `merge-op`, `session`, and `propose`, have
no MCP tool yet. If a sync produces a fork, or
you want to start a named agent session, a person has to run those commands in the terminal. This
gap is tracked as its own follow-up piece of work, separate from the kernel-correctness work
described below.

## 7. What is fixed, and what still has limits

A review on 2026-07-12 found four ways that ordinary git history could break the rules `ugh`
depends on. All four are fixed now.

- A file or symbol that was deleted and then re-added no longer disappears from your working tree.
  The case that used to cause a silent delete now keeps the file and reports it in `ugh log --summary`
  and `ugh advanced fsck`. Re-adding a symbol now chains onto its earlier history, which recovered most of
  the ops the old version dropped. One edge case remains: if a symbol is deleted and re-added with
  the exact same content, the two versions still register as a fork.
- Symlinks are now left alone. `ugh` never writes or deletes through a symlink, whether the
  symlink is the file itself or one of its parent directories.
- `ugh land` now either fully succeeds or fully fails. A red build, an interrupted run, or a lost
  race leaves your working tree and `.sgt/` state exactly as it found them, instead of writing
  partial state before the build and test check runs.
- `ugh sync` now does a proper three-way merge against the common ancestor, so a teammate's
  deliberate revert stays reverted instead of coming back.

These limits remain, either by design or because they are not built yet:

- `ugh` reads Python, TypeScript, and TSX into real symbols using tree-sitter. Any other language
  is tracked as exact, faithful whole-file content. Nothing is misread, but you cannot revert one
  symbol at a time in those files.
- Some TypeScript constructs, such as enums, `type` aliases, and namespaces, are kept as exact file
  content rather than pulled out as individual symbols, so you cannot revert them on their own.
- `revert --keep-dependents` only reaches one hop. Only the symbols that directly depend on the
  reverted one get a placeholder to keep working. Anything further downstream is dropped, the same
  as a plain revert.
- Imports are ordinary text, not a symbol with its own command. Nothing warns you that a revert
  left an unused import behind, or offers to remove one.
- `ugh log --summary` is slow on a large op store. On a store of several thousand ops it can take
  around a minute, because rebuilding the full valid state is expensive and the summary currently
  does it more than once per run. This is a speed problem, not a correctness one.

Run `ugh advanced fsck` any time you are unsure about the state of a repo. It checks that the
current state is valid and that the files it builds match what git actually has.
