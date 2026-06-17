# semi-git — v1 findings

Status: the v1 vertical slice is built and verified end-to-end against the real
OpenAI backend. A user can modify a codebase at the **intent/feature level** and
plug features in and out cleanly.

## What works (verified)

- **Intent → valid code.** `sgt do "<intent>"` classifies the prompt, delegates to
  the OpenAI coding backend (typed effects via structured output), gates the result
  through the EICO confluence check, and lands it as a feature node. The generated
  code is invariant-valid and runnable, and matches the stated intent (e.g. a 6-char
  md5 URL shortener). Verified via `scripts/e2e_smoke.py` and the `sgt` CLI.
- **Clean plug-out.** Reverting an independent feature removes exactly that feature
  and leaves the rest of the codebase invariant-valid and runnable.
- **Dependency-aware revert (closure).** When feature B calls a function defined by
  feature A, the dependency is inferred from the call graph; reverting A removes B
  too, so no dangling caller is ever left behind. Support-kind nodes (concept /
  infrastructure) are garbage-collected when orphaned; capabilities are not.
- **Never leaves broken code.** Every revert re-materializes from the remaining
  active effects and is gated by the invariant predicate before commit.
- **Git underneath.** Each operation materializes the working tree and commits, with
  a `Sgt-Node-Id` trailer mapping commits to semantic nodes (survives amend/rebase).

The canonical state function is the replay of active nodes' effect-bundles from
empty — which is what makes "drop a feature and re-materialize" sound.

## Observations

- The intake classifier exercises real judgment: "add `short_link` that calls
  `shorten`" was sometimes routed as a **refinement of the shortener capability**
  (merged into one node) and sometimes as a **separate capability depending on it**.
  Both are correct; they differ only in revert granularity. This is the
  system-distilled-graph behavior working as intended.

## Known v1 limitations (deferred, see the plan)

- **Effects are additive** (`add_def` / `add_import` / `set_const` / `rename_def` /
  `add_call`). Modifying the body of an existing function is not yet an effect op
  (`replace_def` is future work). New behavior is composed as new units.
- **Per-file invariants.** Reference integrity is checked per file; cross-module
  import resolution is shallow (an imported name is treated as defined).
- **Single language** (Python AST), per plan KTD3.
- **Gardener** (auto split/merge/relabel) and the **quarantine/reconciliation UI**
  are minimal in v1: conflicting effects are held back and reported, not yet
  re-written to commute. Multiverse, the confluence corpus, RL training, and the
  Codex/Gemini backends are deferred.

## Reproduce

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
uv run pytest -q                  # 47 deterministic tests
uv run python scripts/e2e_smoke.py  # live OpenAI end-to-end (uses .env)
```
