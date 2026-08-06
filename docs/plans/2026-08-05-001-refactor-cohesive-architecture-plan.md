# Cohesive refactoring plan: sgt as one system

Date: 2026-08-05. Grounded in the two reviews of the same date (`docs/design/2026-08-05-code-review-and-architecture-rethink.md` and `docs/design/2026-08-05-developer-experience-review.md`), five code audits at HEAD 5161871, and direct reads of `sgt/core/lens.py`, `sgt/cli/porcelain.py`, `sgt/lens/label.py`, `sgt/loop/plan.py`, `sgt/state.py`, and `sgt/mcp/server.py`. Decisions in this plan are made, not offered as options. Rejected alternatives are named where the choice was close.

Part 8 records the reflection pass. The plan below is the post-reflection version, and part 8 lists what the reflection changed, so a reader can see the iteration rather than take it on faith.

## 0. What the refactor must deliver

One sentence: a developer (or their agent) works in one system whose surfaces answer in the developer's own vocabulary within perceptual latency, and git becomes an implementation detail that sgt keeps consistent by itself.

The three root causes the reviews established, which this plan removes rather than patches:

1. There is no resident process, so every read pays cold mine-on-contact, the editor multiplies subprocess reads, and an LLM call plus a re-cluster sit inside the read path. The core loop (edit, then see it reflected) measured 5 to 10 seconds on a toy repo.
2. The machine ontology (ops, hex ids, cluster labels) is what surfaces present, while the human layer (episodes, the developer's own words) exists only as an overlay. About 60 view functions in `sgt/api.py` each decide their own hierarchy and counting, so surfaces disagree.
3. The seams are conventions. Git can move under sgt undetected, the intent layer is keyed three ways, plans made outside sgt are invisible, and MCP exposes half the verb surface, so agents and humans relay through each other.

## 1. Target architecture

Five layers. Each layer only consumes the layer below it.

```
L5  surfaces        CLI renderers · TUI renderers · VS Code · agent skills
L4  queries         q.now · q.history · q.graph · q.preview   (the ONLY read API)
L3  engine          sgt serve: watcher, debounced mine, hot projections,
                    async label upgrader, push invalidation, verb dispatch
L2  work ledger     one work-unit id spine: prompts, plans, turns, matches,
                    rationale, attribution aliases
L1  kernel          ops, ideal algebra, mining, clustering  (unchanged algebra)
L0  substrate       git binding + reconcile-on-contact + managed-mode hooks
```

The layer rule is the enforcement mechanism for "surfaces stop disagreeing": after phase 2, no file under `sgt/cli/`, `sgt/tui/`, or `editor/` may import anything from `sgt.api` other than the four queries, and CI greps for it.

### The ten decisions

D1. Build a resident engine (`sgt serve`) rather than caching harder. The latency floor is architectural (cold process per read). The engine hosts the existing library code (`sgt.core.lens`, `sgt.api` internals) behind the same JSON-RPC stdio framing `sgt/mcp/server.py` already implements, plus a unix socket for local clients. Rejected: a precompute-only design with no daemon. Precompute fixes `sgt now` but cannot fix editor refresh fan-out, push invalidation, scrub folding, or async label upgrades, and we would rebuild half an engine in caches.

D2. The CLI never auto-spawns the engine. A developer who only uses the terminal gets no surprise daemon. Cold CLI runs the identical library code in-process and reads the write-time projections (D5), which keeps it in the 0.1 to 0.7s band. The engine is spawned by the editor extension, by `sgt serve` explicitly, or by the MCP entry point. When a socket exists, the CLI delegates to it and gets engine-hot latency. The fallback is the same code, not a degraded imitation, which is what makes it robust rather than a cascade.

D3. Collapse the ~60 `*_view` functions into four canonical queries with one counting rule. `q.now` (state of actions), `q.history(grain=episode|save|op, feature, range)`, `q.graph(mode=map|tree|rail, at)`, `q.preview(verb, selection)`. Everything else becomes an internal helper or is deleted. Counting rule: history counts saves in the current ideal's story; bookkeeping commits (D8) are folded and reported as a separate count, never mixed in. The log-says-5, map-says-8 class of bug becomes impossible by construction.

D4. One work-unit id (`w-` prefix) replaces the three-way keying. Minted at first contact by whichever event arrives first (prompt hook, plan intake, plan adoption, or a `save -m` with no active unit), and every record (turns, hollow steps, matches, rationale, prompt sidecars) keys on it. Existing keys (plan session ids, Claude session ids, worktree session names, shas) become aliases in one table on the work unit. Op attribution strings are not rewritten; resolution goes through the alias table, so the migration re-keys only local tables and never touches the op store.

D5. Nothing on the read path may touch the network or re-cluster. Labels are quoted before they are generated: at feature birth the label is chosen deterministically from words the developer or agent already wrote (plan step title, then `save -m` message, then commit subject, with short or stopword-only subjects ranked last). The engine's idle-time upgrader may propose better names through the existing `suggestions` store, and a label that has been shown never changes except through `feature rename` or an accepted suggestion. Re-clustering runs on the engine's idle beat or explicit `--rebuild`, never inside a query.

D6. `q.now` is also precomputed at write time. Every mutating beat ends by writing `.sgt/local/now.json` with a fingerprint (reusing `_sync_fingerprint`'s HEAD + dirty digest). Cold `sgt now` reads the file, checks the fingerprint with two cheap git calls, and only falls back to computing when stale. Write beats to wire: `record_ideal`, `put`, `lens/verbs` apply paths, `oplog.append` callers, `plan.intake/confirm_match/mark_done/abandon`, `intent.turns.record_turn`, the activity hook append, and `sync`/`land` completion.

D7. Reconcile-on-contact replaces the honor system. `_sync` gains one check when `prev_head != head`: if the witness is not an ancestor of HEAD (one `merge-base --is-ancestor`, batched through the existing cat-file channel), the ref was rewritten, and the existing resync recovery runs automatically, preserving exclusions (never `--reseed`). `detect_orphans` gets wired into the same contact to label out-of-band commits in history instead of absorbing them anonymously. Managed mode additionally installs git hooks (`reference-transaction`, `post-rewrite`, `pre-push`) so protection follows the repo, not the spelling of the command. Rejected: intercepting git via PATH shims, which is fragile and invisible.

D8. Bookkeeping is marked at the source. Every materialization commit sgt makes for its own mechanics (revert, restore, undo forward-commits) gets an `Sgt-Bookkeeping: 1` trailer and a provenance flag on its ops. Human-facing lists fold them (with a "+N bookkeeping" count). For pre-existing history, folding may match the known subject shapes (`sgt restore …`, `sgt revert …`) as a display-only heuristic, never for semantics.

D9. State travels with plain git. Keep `refs/sgt/state` (committing the op store into the tree pollutes every diff and was rejected), but make it survive plain tooling: `sgt init`/first-contact adds an additive fetch refspec for `refs/sgt/*`, and the managed-mode `pre-push` hook publishes the state ref before the branch (same ordering invariant `sgt push` enforces). We do not add a push refspec, because a configured `remote.<r>.push` silently changes what bare `git push` pushes. GitHub squash merges still degrade to the committed `ideal.json` recovery record, and `sgt sync` reports that degradation explicitly.

D10. The terminal gets fast truthful snapshots; the editor gets the live surface. The TUI stays a renderer of one-shot output (no interactive TUI investment), because the developer's ambient attention lives in the editor, and the terminal's job is a sub-200ms answer. Rejected: building a live TUI, which would duplicate the editor surface for a smaller return.

## 2. Phase 0: repair (days)

Small, independent, all shippable one by one. No architecture.

1. Fix `sgt-plan/SKILL.md:24` to `$CLAUDE_CODE_SESSION_ID` (matches `mcp/server.py:342` and the hook keying at `cli/intent.py:179`).
2. Point the workbench action bar at the current verbs: route `applyVerb` through the typed `Sgt.merge/rename/move/splitApply` methods (`editor/vscode/src/sgt.ts:232-254`) and delete the stale `cliArgsFor` spellings (`workbench.ts:420-452`).
3. Guard `sgt switch`: verify the argument is a local branch before calling `checkout_branch` (`cli/porcelain.py:113`), and name the coming `peek` verb in the refusal message.
4. Stop caching failure as permanent: in `Labeler._resolve` (`lens/label.py:236-246`), cache fallback labels with a retry-after timestamp instead of retrying the network on every refresh, and never block a render on the retry. This is a stopgap that phase 2 subsumes, and it alone removes most of the measured 5-second edit loop.
5. Add `sgt status` as an alias of `sgt log --summary`. Muscle memory beats surface purity.
6. Committer identity: refuse to commit with a clear message when `user.name`/`user.email` are unset, instead of silently writing `sgt@semi-git.local` (`store/gitbind.py:358-361`).
7. Delete the empty `sgt/lifecycle/` and `sgt/merge/` packages and the dead `kind="propose"` undo branch (`core/oplog.py:199,246`), and either wire or delete the false claim in `gitbind.py:5-6` (D7 wires it, so mark it with a pointer to this plan).
8. Make `sgt why` answer for wrong-shaped input: a save sha resolves to its ops and prints them with a "did you mean" list instead of dumping the full help.
9. Document the land undo asymmetry (`sync/land.py:317` vs `:334`) in the oplog docstring and in the land confirmation text, until phase 3 unifies the preview.

Exit: each fix has a regression test; the one-line-edit refresh drops under 2s on the testbed from item 4 alone.

## 3. Phase 1: the work-unit spine (about 1 to 2 weeks)

Goal: any question of the form "why does this code exist" resolves through one id with one hop, regardless of how the work started or ended.

1. New module `sgt/work/unit.py`: mint `w-<hex>` ids, an alias table artifact (`state.py` registry slot `work_aliases`, local), and `resolve(key) -> work_unit`. Alias kinds: `plan`, `claude_session`, `worktree_session`, `sha`.
2. Re-key local tables (`plan_sessions`, `plan_matches`, `turns`, `rationale`, `intent_prompts` local tier) onto work-unit ids under a migration in the existing manifest pattern (`core/migrate.py` precedent). Op attribution is untouched; `api.py:1985-2026`'s `_atom_prompt` collapses to one alias-table lookup.
3. Wire `align_session` into the save beat: called from `_save` after `_fold_plan_matches` (`cli/porcelain.py:247`), guarded exactly like `auto_retire_open` (`porcelain.py:252-257`), behind config `intent.align_on_save` defaulting on. Low-confidence pairs land in the existing review store, which makes `sgt intent review` and the "review N alignments" next-action rung live for the first time.
4. Plan adoption: `sgt plan adopt <file>` (and the same over MCP) runs intake on a markdown plan and aliases it to the current Claude session when the hook has seen one. Update the `sgt-plan` skill: on plan-mode exit, the agent calls intake with the plan text and its session id. The hook side needs no new events.
5. Plan resume: `sgt plan resume [name]` rebinds the current session to a stalled work unit (by Claude session alias when present, else by pick list), and `intake` with an existing `session_id` reuses the unit instead of minting a new baseline (`loop/plan.py:201-208`).
6. Ownership: `confirm_match`, `mark_done`, and `abandon` verify the caller's session alias against the unit's owner and refuse with `--force` as the override (`mcp/server.py:210-221, 249-259` are the holes today).
7. Concurrency: extend the existing `capture_lock` (`intent/turns.py:43-57`) to cover the plan-table read-modify-write in `confirm_match`/`mark_done`.

Exit test (words on screen included): start a plan in Claude Code plan mode, interrupt it, resume it, finish with two raw prompts along the way, commit once via the agent and once via `sgt save`. Then `sgt why` on any resulting save prints the actual prompt text and the plan step title, and `sgt now` shows the unit as one item through its whole life. No manual intake call anywhere in the scenario.

## 4. Phase 2: engine and canonical queries (about 2 to 3 weeks)

Goal: the same words, an order of magnitude faster, from one read API. This phase changes no rendered vocabulary, which is what makes it reviewable: existing output goldens stay green (modulo the staleness banner, which disappears).

1. `sgt/engine/serve.py`: a per-repo process. Components, all reusing existing code: the watcher (worktree, `.sgt`, `.git/refs`), a debounced call into `lens.get()`, hot in-memory projections, the label upgrader queue (D5), and verb dispatch that simply calls the existing verb functions. Transport: the JSON-RPC framing from `mcp/server.py` over a unix socket (`.sgt/local/serve.sock`) plus stdio. Single-instance claim via pidfile plus socket liveness probe; a second start attaches instead.
2. `sgt/queries/` implementing D3's four queries as pure functions over the store, engine-hosted or in-process, byte-identical either way. Response envelopes carry a schema version (the `state.py` envelope pattern). The extension's `types.ts` regenerates from these.
3. Write-time `now.json` per D6, with the write-beat wiring enumerated there. Cold `sgt now` fast path: read file, two git calls for the fingerprint, render. Budget: under 150ms warm-disk on a 10k-op store.
4. Read purity per D5: quote-first birth labels, upgrader in the engine, `--refresh` stops implying relabel, `--rebuild` remains the explicit full re-cluster and prints what it will cost first.
5. VS Code: spawn or attach `sgt serve`, subscribe to push invalidation, drop the 8-subprocess refresh and the self-write cooldown heuristic (`sgt.ts:83-96`, `extension.ts:127-142`), which the engine makes unnecessary because it knows its own writes. Scrub folds route through the engine (hot) rather than a process per drag position.
6. MCP becomes an adapter over the same dispatch table, which delivers parity nearly for free: add `sgt_save`, `sgt_now`, `sgt_show`, `sgt_resolve`, `sgt_sync`, `sgt_land`, and the feature verbs, every mutating tool honoring the same `emit` preview contract the CLI paths use.
7. CI adds the layer-rule grep (part 1) and the latency gates on a seeded 10k-op synthetic store: `sgt now` under 150ms cold-process, `q.now` under 20ms engine-hot, `sgt log` under 300ms, edit-to-surface under 1s without label upgrade, scrub fold under 100ms engine-hot.

Downstream consequences handled: a cold CLI write while the engine holds hot state is detected by the engine's own `.sgt` watcher plus the existing store flock as the write mutex; engine crash leaves the socket stale, the CLI probes and falls back in-process, and the next editor attach cleans up; a worktree session directory has its own `.sgt`, so it gets its own engine or none.

## 5. Phase 3: presentation inversion and time travel (about 1 to 2 weeks)

Goal: the surfaces speak the developer's language. This phase changes only vocabulary and affordances, on top of phase 2's fast substrate.

1. Episode-first everywhere: `q.history`'s default grain is the episode (checkpoint); the save list renders episodes titled by quoted words; ops appear only under `--full`/detail expansion. Machine ids leave default output (short human handles only; hex under `--json`).
2. Bookkeeping folding per D8, including the trailer at `put` time and the display heuristic for old history.
3. In-situ agent presence: render `context.activity` in the Now tree ("agent is editing X, step 3 of 5"), show building (not just stalled) plans, make recently-done rows open the workbench at that save, and put drift plus session identity in the TUI state banner (`tui/graph.py:458`).
4. Consequence symmetry: restore, land, propose land, undo, checkpoint revert, and the workbench batch verbs all render `q.preview` (blast, carry, foundation, diff) before their confirm, replacing text-only modals (`commands.ts:185-194`, `gitBridge.ts:114-137, 192-246`, `workbench.ts:290-340`).
5. Time travel: extract the selection resolver ladder from `cli/ideal_edit.py:211-317` into `sgt/select/resolve.py`, shared by revert, restore, diff, fold, and the two new verbs. `sgt show <spec> [path]` prints file bytes at a frontier (the computation is `fold_view`'s). `sgt peek <spec>` materializes the frontier into an ephemeral read-only worktree using the session worktree machinery (`core/session.py:132`) plus `restore_worktree_to` (`gitbind.py:1082`), writes a `.sgt/peek.json` marker inside it so any sgt command run there answers "this is a read-only peek of <repo> at <spec>" instead of cold-initializing, and `sgt peek --close` (or closing the last editor window on it) removes the worktree. The workbench playhead gets an "open on disk" action that calls peek.
6. Verb diet: the twelve-verb spine on top-level help, everything else under `advanced`, and the `merge-op`/`split-op`/`transplant` family documented as the escape hatch it is.

Exit test: the testbed scenario from the DX review re-run. `sgt now` shows "put the clear command back" style entries only (no hex, no self-commands), `sgt log` and `sgt log --map` agree on counts, and a first-time user asked to narrate the repo's history from `sgt log` output uses no sgt jargon to do it.

## 6. Phase 4: substrate hardening (about 2 weeks)

Goal: git stops being able to surprise sgt, and scale stops being a silent limit.

1. Reconcile-on-contact per D7 (ancestor check, auto-resync preserving exclusions, `detect_orphans` wired, loud one-line report when it fires: "history was rewritten under sgt; recovered N ops, parked M").
2. Managed-mode hooks per D7/D9 (`reference-transaction` marker, `post-rewrite` marker, `pre-push` state publication), installed by `sgt init` with consent and removable by `sgt advanced unmanage`.
3. Fetch refspec travel per D9, and `sgt sync`'s squash-degradation report upgraded to name exactly which ops were coarsened.
4. Opaque images by blob OID: op payloads for opaque-tier files store the git blob OID plus size, with bytes inline only under a small cap; materialization reads through `gb.blob_bytes`; `fsck` verifies referenced blobs exist; migration under the manifest pattern (`migrate.py` ops-v3 precedent). This is what makes D9's transport size sane and removes the 2x hex bloat.
5. JavaScript coverage: map `.js/.jsx/.mjs/.cjs` to the TypeScript grammar in `_EXT_LANG` (`entities/extract.py:111-118`), and surface the per-file tier in blame hover and the Changes tree so opaque files are visibly opaque before they surprise anyone.
6. In-progress merge assistance: `sgt now` detects `MERGE_HEAD` and presents finish/abort as the next action in sgt vocabulary, then reconciles on completion (closing the freeze documented at `porcelain.py:172-178`).

Exit test: in a managed repo, `git rebase -i` squashing three saves, then any sgt command: state self-heals on contact, exclusions survive, and the log narrates the rewrite. A teammate clones with plain git, runs `sgt init`, and sees the full graph including intent-layer aliases that were pushed.

## 7. Explicit non-goals

Submodule content, tracked symlinks, two live versions of one symbol on one branch, detached-HEAD workflows, an interactive TUI (D10), and auto-merge of same-symbol forks (the fork-parking model stays; `resolve` remains the guided path). These get stated in the README so they are known limits rather than discovered ones.

## 8. The reflection pass

Method: with the draft complete, I walked three concrete personas through the plan as if implemented, looking for places where the plan fulfills its tasks but misses the developer's mindset. Changes were folded back into the text above; this section records them so the iteration is visible.

Persona A, terminal-first solo developer. Walking their day exposed two problems in the draft. First, the draft had the CLI auto-spawning the engine on first use, which means a surprise resident process for someone who never asked for one; D2 now forbids auto-spawn and commits to the cold path staying fast through write-time projections, which the measurements say is achievable. Second, `sgt status` was scheduled with the phase 3 verb diet, but muscle memory failure is a first-session experience, so it moved to phase 0.

Persona B, agent-driven developer who plans in Claude Code and walks away. The draft's adoption story required the agent to cooperate (call intake). Reflection: the developer who most needs adoption is the one whose agent did not cooperate. The hook-side alias minting in D4 means even a fully uncooperative session still gets a work unit from its first prompt, so the worst case degrades to "unnamed unit with real prompt evidence" rather than "invisible". Also, the draft measured the resume scenario only by data linkage; the phase 1 exit test now requires the unit to read as one item in `sgt now` across its whole life, because that is what the developer actually experiences.

Persona C, the scrubbing developer. Walking "stop at a version and look at it on disk" found a real footgun in the draft: a peek worktree contains its own empty `.sgt`, so running any sgt command inside it would cold-initialize a second store and confuse everything downstream. The `.sgt/peek.json` marker and read-only answer in phase 3 item 5 exist because of this pass. Same pass raised the question of whether peek trips the main repo's watchers: it does not, because the worktree is a different directory, which confirmed the design.

Cross-cutting reflection on the label policy: the draft said "quote the developer's words" without ranking them, and real commit subjects include "wip". The selection rule in D5 (plan title, then save message, then subject, stopword-poor subjects last) plus the suggestions-based upgrade path came from imagining a repo full of low-effort messages. The principle held (never generate when the developer wrote something), but it needed the ranking to survive contact with real data.

One thing reflection did not change: the phase order. I tested an alternative that did presentation (phase 3) before the engine (phase 2), on the theory that words matter most. It fails the imagined experience: the right words arriving after 5 seconds still read as a broken tool, and half of phase 3 (activity feed, clickable saves, scrub previews, preview symmetry) is only viable on the engine's latency. Fast-then-honest survives the walk-through; honest-then-fast does not.

## 9. Test strategy

- Regression: every phase 0 item lands with a test that fails on main today.
- Goldens: phase 2 must keep rendered-output goldens byte-stable (minus the staleness banner); phase 3 replaces them deliberately, and each replacement golden is reviewed as words a user will see. The two pre-existing kernel golden failures on main get fixed or quarantined in phase 0 so the signal is clean.
- Latency gates in CI on a seeded 10k-op store (generator adapted from `experiments/`): the numbers in phase 2 item 7, enforced, so the DX regression class cannot return silently.
- Migration tests: work-unit re-key (phase 1) and blob-OID images (phase 4) both run against a copy of the sgt repo's own `.sgt` store and a synthetic adversarial store (hollow ops, parked forks, mid-migration crash resume).
- Scenario tests: the three persona walks in part 8 become scripted end-to-end tests (testbed fixtures), because they caught more than the unit-level criteria did.

---

## 10. Execution log

Phase 0 is complete. A latency-independent slice of phase 3 was pulled forward, and part 8's
reasoning is why: the argument for doing the engine before presentation was that the right words
arriving after five seconds still read as a broken tool. Phase 0's label fix cut the edit-to-surface
loop from about 5.0s to about 1.25s on the testbed, which removes that objection for the
presentation work that needs no engine. Nothing pulled forward depends on phase 2.

Phase 0, all nine items:

1. The `sgt-plan` skill now names `$CLAUDE_CODE_SESSION_ID`, the id the prompt hook actually keys
   by, with a test that fails if the skill and the MCP tool description drift apart again.
2. The workbench action bar dispatches through the typed `Sgt` methods. Its bare `sgt merge` /
   `rename` / `move` / `split` argv named verbs that were re-homed under `feature`, so the CLI
   printed its help text and exited 0 while the UI reported success.
3. `sgt switch` refuses anything that is not a local branch instead of detaching HEAD.
4. Fallback labels carry a retry-after backoff, and a credential that cannot be built at all
   short-circuits the pass in-process.
5. `sgt status` exists again, as an alias onto `log --summary`'s own handler.
6. A repo with no git identity now says so instead of silently authoring commits as semi-git.
7. The empty `sgt/lifecycle/` and `sgt/merge/` directories are gone. The `kind="propose"` undo
   branch was kept and documented rather than deleted: it is correct defensive code, and the actual
   defect was the docstring that claimed a blanket refusal.
8. `sgt why` moved to the top level. The planned fix was a better error message, but the real defect
   was the verb's path: `why_view` already answered for a commit sha, and `feature why` meant the
   natural spelling printed help.
9. The land undo asymmetry is stated in the oplog docstring and in an `undo_note` the confirm prompt
   prints above the prompt itself.

Pulled forward from phase 3:

- D8 bookkeeping folding, plus the count agreement it enables. `sgt log` and `sgt log --map`
  disagreed on the same repo (6 versus 8) because one counted episodes and the other counted every
  commit; both now report saves, and the map discloses what it folded.
- D5 quote-first labeling, in its dominance-gated form: a leaf whose op mass is dominated by one
  commit takes that commit's subject as its name, with no LLM call. Clusters spanning several
  episodes still get a synthesized name.
- The `now` surface shows a plan that is actively being built and the live agent-action feed. Both
  were already recorded and neither was displayed.
- The first slice of the preview symmetry in §5 item 4: `sgt.restore` in VS Code now runs the same
  `--emit` dry run and opens the same before/after diff that revert always did, instead of
  confirming with prose. The remaining verbs in that item (land, propose land, undo, and the
  workbench batch reverts) still confirm with text only.
- `sgt show <spec> [<path>]`, the read half of §5 item 5. The bytes were always computable and only
  the workbench playhead ever displayed them. `sgt peek` (materializing a frontier into an ephemeral
  read-only worktree) is not started; the shared selection resolver it would use is still inside
  `cli/ideal_edit.py`.

Pulled forward from phase 1 (the bounded parts that need no id-spine migration):

- Resuming a plan is non-destructive. Re-taking a session id you own is how an interrupted agent
  picks work back up, and intake minted a fresh baseline there, silently reclassifying everything
  already built as drift. The baseline and creation time are kept, superseded hollows are cleaned
  up rather than orphaned, and a resume that cannot restate its Claude session id no longer erases
  the captured one. This is §3 item 5's substance, without the `sgt plan resume` verb.
- `sgt_plan_done` enforces the ownership the skill always claimed (§3 item 6): an agent that
  identifies itself cannot close another agent's plan.
- A save no longer closes the plan it just told the user to resolve. Two of the three long-standing
  test failures were this bug, not fixture rot: `sgt save` printed "run `sgt save --resolve-plan`"
  and the same housekeeping beat deleted the hollow that command needs.
- `sgt intent align` makes the alignment pipeline reachable as a dry run. §3 item 3 said to wire it
  into the save beat behind a flag defaulting on; reading `align_session`'s own docstring changed
  that. It is unwired deliberately, because a wrong ALIGN record on a young corpus is a wrong answer
  from `sgt why`, and the author left the calibration question open rather than guessing. A dry run
  answers that question without overriding it. On the testbed it reports 9 sessions, 252 candidate
  pairs, 0 confident alignments and 10 held for review -- so defaulting it on would have added an
  inference pass to every save to record nothing. The plan's original instruction was wrong and the
  code said so.

Test state at the end of this pass: three failures, all pre-existing against 5161871
(`test_land_fork_refusal_persists_the_fork_records` and the `diverged_chain` / `mixed_coverage`
kernel projection goldens, which fail from feature-id churn). The two goldens were deliberately not
regenerated: they are red for a real reason, and regenerating would record that churn as expected.
The CLI surface golden was regenerated, reviewed, and is green. The two `save --resolve-plan`
failures are gone, because they were reporting the bug fixed above rather than stale fixtures --
worth remembering the next time a long-red test is assumed to be rot.

## 11. Second pass: what the developer's actual workflow changed

Merged `origin/main` and continued into phases 2 and 3. One fact reshaped both: this repo's own
developer plans in Claude Code's plan mode or with a planning plugin, and does not call
`sgt plan intake`. The agentic loop is built on an agent *declaring* its plan, so for the way the
work actually arrives, sgt was told nothing and the entire plan and intent layer had nothing to
show. §3 item 4 treated adoption as one item among several; it is closer to the premise.

The answer is not a bigger on-ramp but a smaller one. sgt already observes everything it needs: the
`UserPromptSubmit` hook records every ask verbatim, so the developer has already said what they
want, in their own words, at a known time. `sgt.intent.working` derives the current task from that
and never writes. A stated plan step still wins when one exists, and `source` records which kind of
words these are so the surface cannot pass a raw prompt off as deliberation.

That reframes D4's work-unit spine too. Its value was never the id; it was that a question about
intent should resolve without the developer having declared anything. Deriving the task is the same
goal reached without a migration, and the spine is now worth doing for *joining* records, not for
making the layer work at all.

Landed in this pass:

- `working_on`: `sgt now` and `sgt log`'s header lead with the developer's own words, and the
  suggested next action carries them (`sgt save -m "<your ask>"`), offered only when it pastes
  verbatim -- a suggestion you have to repair is worse than none.
- One `state_lines` renderer for both surfaces. They were two copies that had already drifted in
  wording, which is the smallest instance of the problem D3 exists to end.
- Presentation honesty, three cases where the tool claimed more than it knew. The staleness banner
  printed on every read, so it carried no information and taught developers to ignore it; it now
  names what is actually missing and says nothing when the view is current. The rail legend named
  every glyph the renderer can draw rather than the ones on screen. And "working on X (9h ago)" sat
  directly above "nothing pending" -- two contradicting lines, where the newer one loses.
- `sgt undo` shows what it will do first. The one command whose job is recovering from a surprise
  was itself a surprise, and everything it reports was computable before the fact.
- MCP parity for the verbs an agent needs to close its own loop: `sgt_save`, `sgt_now`, `sgt_show`.
  An agent could read the graph and edit code but record neither, so a human relayed every save by
  hand. They go through a `_run_cli_json` adapter that runs the real verb instead of reimplementing
  it -- two copies drift, and drifting surfaces are what this plan exists downstream of -- which is
  also how the remaining verbs get a surface cheaply. The skill now says the loop is for multi-step
  work and ordinary work is edit-then-save; requiring the ceremony for everything is why the plan
  layer saw nothing.
- Three tests that went red on main, none of them about the behavior they name. Two pinned an
  arbitrary hash-sort order of a fork's tips; one took whichever op sorted first and assumed it
  carried a tracked member. Worth keeping from the first: that order reaches the resolve UI, where
  "left" is therefore not reliably "yours". Making it meaningful belongs in the surfacing layer,
  not in `forks()`, which sits on the ideal-validity hot path. Worth keeping from all three: a test
  that has been red for a while is not evidence that it is stale.

- Features addressed by name. The hint under every `sgt log` said `sgt revert 08ccdb12`, asking the
  reader to work out which feature that is from a view where the hex appears nowhere and the name
  appears on every row. `resolve_feature` already accepts an exact label, so the suggestion is now
  the same command in words that are on screen. `--focus` also stopped printing a 64-character id
  across its header beside the name it belongs to.

Still open: the resident engine and the rest of the canonical-query collapse (D1/D3) -- `sgt now`
sits around 0.5s, fine for a command you type and wrong for a surface an editor refreshes on every
save; the work-unit spine as a joining mechanism; episode-first history; `sgt peek`; and phase 4
entirely.

A note for whoever picks this up. Every item in this pass came from reading real output and asking
what a developer does with each line, not from the plan's own list -- and the plan was wrong twice
(the aligner, the assumption that plans are declared). The remaining items are worth re-deriving
the same way rather than executed as written.

## 12. Cleanup pass, and two things deliberately left

A four-angle review (reuse, simplification, efficiency, altitude) over this branch's own additions.
Two findings were defects in the new code: `oplog.preview`'s skip loop could never advance past one
entry, and its symbol list used an ad-hoc filter instead of `is_behavioral`, so the undo preview
printed sgt's own pseudo-symbols in the line whose job is recognizability.

The efficiency finding was the instructive one, because it was invisible at the scale I was testing.
`now_view` had gone from 7 git subprocesses to 9, and `sgt log` from two full-history walks to four:
a `git log -1` for one timestamp and a `--grep` for the bookkeeping trailer, both walks made by
surfaces that had just run `history()`. Widening that one walk's format to carry the committer time
and the trailer deletes both -- measured free on 2632 commits -- and `history()` keeps its 3-tuple so
the miner never sees it. The turn store was also parsed twice per `now_view`; it keeps every prompt
ever typed and is never pruned, so that cost grows forever. Now read once and shared.

Two altitude findings were real and are **not** done, both bounded:

- **`_run_cli_json` is one missing function papered over.** `tool_now` and `tool_show` reach library
  entry points; only `save` needed an adapter, because its payload is built inside its printer
  (`_render_save`). Extracting `save_payload()` (~40 lines, one file) lets `tool_save` call
  porcelain directly and deletes the adapter's reason to exist -- along with an `os.chdir` and a
  stdout redirect on a process that speaks JSON-RPC over stdout. Generalizing that to every verb is
  the phase-2 work; doing it for `save` alone is not.
- **`subject_label` is naming policy inlined into a traversal loop, and it already diverges.**
  `lens/map.py` plumbs subject counts into `label_tree` and gets the new naming; `lens/reconcile.py`
  calls `label_tree` without them and silently does not, so the same feature can be named two ways
  depending on which path built the tree -- the exact "surfaces disagree" failure this plan exists
  to end. `Labeler.leaf_request` already receives `subjects`; the ladder (pin > authored > the
  developer's words > LLM > structural) wants to be one ordered policy the Labeler owns. ~30 lines,
  and it needs a `"source": "subject"` cache tag so `--rebuild` and the backoff treat it correctly.

Also left, with reasons: the bookkeeping subject heuristic will linger while `put`'s callers each
decide the flag by hand (two of four sgt-own materialization paths still pass nothing); deriving it
from the message inside `put` is ~10 lines and would let the legacy list freeze. And `state_lines`
is at the right depth but the wrong address -- it lives in `cli/inspect.py` while importing line
primitives from `tui/graph.py`, where the repo's other shared renderers live.
