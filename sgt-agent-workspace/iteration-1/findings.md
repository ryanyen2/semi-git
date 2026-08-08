# sgt-agent eval loop, iteration 1

Six runs: three prompts x (with skill / baseline), each in its own fixture copy so the arms could not
contaminate each other. Mechanically-checkable assertions graded by `sgt-agent-workspace/grade.py`.

## Result: 11/11 both arms — the assertions do not discriminate

The baseline matched the skill on every coarse behaviour I asserted: it oriented cheaply, stopped on
the blocked repo, inspected before reverting, and summarised in prose without dumping the map. That is
the honest headline, and it says my assertions were too easy rather than that the skill is useless.
Opus is strong enough to reach sensible defaults here unaided.

## Where the skill did change the outcome

The difference was in specifics, not in coarse behaviour:

* **eval-1 (with skill) caught the op-versus-feature trap.** It reported that reverting the op costs
  1 edit with 0 dependents, *and* that reverting the enclosing feature would have cost 16 edits across
  both files, then deliberately reverted the op. The baseline reverted correctly but never surfaced
  that the feature-level revert was the adjacent, much more expensive mistake.
* **eval-2 (with skill) suppressed a false alarm; the baseline relayed it as advice.** Both hit the
  drift discrepancy below. The skill's run judged it an artifact and left it out; the baseline told the
  user "a `sgt save` would absorb that", which is wrong — the save cannot fix it.
* **Cost.** With-skill was faster on the two orientation-shaped tasks (130s vs 171s; 92s vs 112s).
  On eval-1 it was slower (363s vs 203s) because it went deeper and hit two frictions.

## Product bugs the loop surfaced

1. **`code(I)` reorders symbols, producing false drift on a clean tree.** PRE-EXISTING — reproduced on
   pre-merge code, so not merge damage. In the `healthy` fixture, `code(current_ideal)` emits `_get`
   before `backoff` (and loses the blank-line separation) while the file on disk has the reverse.
   Same byte length, different order. `sgt advanced fsck --tree` reports `drift: [fetch.py]` on a
   clean tree, and `log --summary` then advises `sgt save`, which cannot fix it. This violates the
   byte-fidelity invariant the README states outright. Found independently by two agents.
2. **`sgt show <git sha>` is rejected although `sgt show` prints shas.** Its own `saves` block lists
   `4e2fa14 retry with backoff`; feeding that back fails. `why_view` already resolves a sha via
   `_commit_why`, so the resolver has the capability and `identify` does not use it. Same class as the
   silent-no-op family: sgt names an id the next command refuses.
3. **`sgt advanced preview revert <symbol>` fails** — that path takes a feature, not a symbol, and the
   error does not say so.
4. **A whitespace-only edit cannot be recorded.** No symbol changed, so no op is mined and `put()`
   refuses with `would overwrite uncommitted changes`; the tree can only be reverted. Now documented
   in the skill's CLI-fallback reference, along with `sgt save -m` versus a bare positional (the
   fallback table's `plan intake "<text>"` row had primed the wrong form).

## What to change next iteration

Replace the coarse assertions with ones the baseline can actually fail: whether the answer
distinguishes op-scope from feature-scope cost, and whether a false-positive drift line is relayed to
the user as if actionable. Both are behaviours the skill uniquely produced here.
