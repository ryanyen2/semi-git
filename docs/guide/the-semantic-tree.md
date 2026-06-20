# The semantic tree

git versions your codebase by its **text** — lines, diffs, commits. `sgt` versions it by its
**features** — a graph of the capabilities, concepts, fixes, and infrastructure that make up
your code, and which files/lines each one produced. This page is the mental model. If you only
read one page, read this one.

## What's in the tree

The tree (the *semantic DAG*) lives in `.sgt/` next to your `.git/`. It has two kinds of thing:

**Nodes** — one per feature/concept. Each node has:

- a **kind**: `capability` (does something), `concept` (a domain idea), `infrastructure`
  (plumbing), `fix` (revises earlier work), or `exploration`.
- a **status**: `active` (in the materialized tree), `planned` (designed but not built yet),
  `suspended` (temporarily switched off), or `quarantined` (held back because it doesn't yet
  fit cleanly).
- an **intent**: one sentence describing what it's for.
- the **effects** it authored (the typed edits — add this function, replace that statement)
  and the git **commits** that materialized them.

**Edges** — directed relationships between nodes:

- `depends_on`: this feature needs that one (inferred from which feature defines a name another
  uses). This is what makes plug-out correct.
- `revises`: a later fix of an earlier node.
- `derives_from`: produced by iterating another node's intent.

It is a **DAG**: dependencies point one way and never form a cycle.

## The one rule: sgt never writes your code

Your coding agent (or you) writes the code, in your editor, however you like. `sgt` only ever
reasons about and reorganizes the *graph*, and rebuilds the working tree from it. The test for
"should sgt do this?" is: *does it invent logic that wasn't there?* If yes, that's the coding
agent's job, not sgt's.

A consequence worth internalizing: **the working tree is a function of the graph.** Reverting a
feature isn't a text patch — it's "drop that node's effects and re-materialize." That's why a
plug-out is clean even months later.

## The workflow

```
   plan ──▶ implement (your agent) ──▶ checkpoint ──▶ revert / switch / reconcile
    │            (you write code)         │                  (reshape the tree)
    └─ decompose an intent into           └─ record what you built, distilled
       reviewable PLANNED nodes              into typed effects under a node
```

1. **`sgt plan "…"`** — decompose an intent into reviewable `planned` nodes (no code yet). Each
   carries its declared `provides`/`needs` and dependency edges. `sgt "…"` is shorthand.
2. **Implement** a planned node with your own editor/agent.
3. **`sgt checkpoint --fulfills <node> --intent "…"`** — record your on-disk edits under that
   node and flip it `active`. A bare `sgt checkpoint` records ad-hoc edits as a new node.
   Body edits are distilled at **statement** granularity, so two edits to different statements
   of one function don't conflict.
4. **Reshape** the tree without touching text:
   - **`sgt revert <feature>`** — plug a feature out, by dependency closure.
   - **`sgt switch <feature> off|on`** — suspend / restore (keeps history).
   - **`sgt reconcile [<feature>]`** — re-gate quarantined work that now fits.
   - Add **`--emit`** to any of the first two for a dry-run that writes nothing.

Every mutation runs through the **confluence gate**: nothing lands unless it commutes with the
current tree and preserves the codebase's invariants. Work that doesn't fit is *quarantined*
(held, durable, visible) rather than silently dropped — then resolved with `reconcile`.

## Drift: the tree vs. your editor

When you edit files directly, the working tree **drifts** from the graph's replay. `sgt` detects
this and refuses to mutate over un-recorded changes (so it never clobbers your work) until you
`sgt checkpoint` them — or pass `--force`. Both UIs show drift so a stale overlay reads as stale.

## How the visual tools map to this

Both the [VS Code extension](vscode-extension.md) and the [TUI](tui.md) are windows onto this
same tree (via `sgt … --json`):

- **Semantic blame** = "which feature node owns this line" — the per-feature analogue of
  `git blame`. Computed from the effect log, exact down to the statement.
- **The graph view** = the DAG itself: nodes colored by a stable per-id hue, edges = `depends_on`,
  status by shape/line-style, conflicts flagged.
- **Revision navigation** = `revert`/`switch --emit` rendered as a read-only diff: see exactly
  what plugging a feature out would do, before doing it.
