# Code review and architecture rethink

Date: 2026-08-05. Reviewed at main, HEAD 5161871. Every claim below was checked against the code, not the design docs. File and line references are to current main.

## Verdict

sgt today is three bookkeeping systems that the user has to reconcile in their head. The first is git, which stays the real substrate and reasserts itself at every edge (history rewrites, raw merges, other people's tooling). The second is the op kernel, which is sound and well guarded but only models Python and TypeScript at symbol level. The third is the intent layer, which is keyed three different ways and whose free-form aligner is dead code in production. The stated goal is the opposite. The user should hold one model (plans, saves, features, consequences) and never think about git. The gap is not in any single module. The gap is that the seams between the three systems are conventions, not code.

The good news is that most of the missing pieces already exist in the codebase and are simply not wired together. The drift detector exists and is never called. The aligner exists and has no caller outside tests. The resume affordance exists and is broken by a one-line doc contradiction. The fold view computes file content at any past point and only one webview panel can show it. The refactor this document proposes is mostly wiring, plus a small number of real design decisions that are named explicitly in part 6.

---

## Part 1. What the codebase cannot support today

These are the hard limits a user or agent will hit. Each one is stated with the evidence and the workflow it breaks.

### 1.1 Language and file support

- Symbol extraction covers Python and TypeScript/TSX only. `_EXT_LANG` lists `.py .pyi .ts .mts .cts .tsx` (entities/extract.py:111-118). Plain JavaScript (`.js`, `.jsx`, `.mjs`) gets zero entities. Go, Rust, Java, C, Ruby, and every other language get zero entities.
- Every unsupported or non-code file collapses to one whole-file pseudo-symbol keyed by blob OID (core/mine.py:504-527, core/tiers.py:150-165). A 5000-line JSON or Markdown file is one lane, one revert unit, one clustering node. Two people editing different parts of it collide as a whole-file fork.
- A syntactically broken file (a normal state mid-edit) silently degrades to whole-file for that commit (mine.py:532-549).
- TypeScript entities extract but cannot be attributed to features, because attribution uses git blame line spans and the code notes that TypeScript files resolve to None and render dim (entities/graph.py:273-298).
- Symlinks are invisible to mining and to materialization. Mining skips mode 120000 (store/gitbind.py:524-542) and the fold never writes through a symlink (core/lens.py:1069-1080). A layout that depends on tracked symlinks is not reproduced.
- Submodules have no content model. Only the gitlink pointer is kept (gitbind.py:500-522).
- Binary and opaque files are stored whole, hex-encoded (about 2x size), with no size cap, inside op JSON that travels on `refs/sgt/state` (store.py:99, mine.py:504-526). A large binary bloats the state ref and is hex-decoded on every cold `all_ops` read. Large repos scale badly for no reason, because the same bytes already live in git's object store.

### 1.2 Algorithm limits

- The atom is the top-level function or class. Methods and nested entities are tracked for history but are not independent clustering or materialization units (core/op.py:70-103, lens/cluster.py:77-86). A large class whose methods belong to different features cannot be split across lanes.
- Two live versions of one symbol can never coexist on one branch. Same-symbol forks are dropped from the ideal and parked at the common ancestor until a human resolves them (core/order.py:184-197, core/sync/resolve.py:104-124). Parallel work on the same function vanishes from `sgt state` until `sgt resolve` runs. There is no auto-merge and no textual 3-way merge engine anywhere. `sgt/merge/` contains only stale `.pyc` files, and `merge-op` drafts a manual chain extension that carries the other tip as advisory text (core/rewrite.py:19-30, 177-221).
- Identity breaks on common refactors. A rename combined with a substantial edit (over about 20 percent token change) misses every matcher tier and records delete plus add (core/identity.py:126-176). A function-to-method reshape always breaks identity by design (mine.py:577-580). A move to another file combined with a rename and an edit also breaks.
- The call graph that feeds `requires` edges is matched by leaf name only, and ambiguous names produce no edge (entities/graph.py:239-252). Cross-module calls and dynamic dispatch are invisible to the structural signal.
- Clustering is only conditionally stable. Leiden runs with a fixed seed, so an unchanged repo reclusters identically (lens/cluster.py:255-267), but any new commit changes the inputs. The temporal prior that damps churn ships with alpha 0.25 marked provisional (cluster.py:56-66), and feature ids survive only where member overlap stays at or above 0.5 (lens/tree.py:814-893). Feature ids and labels move under the user as work accumulates. Labels are LLM-generated and are not reproducible across runs, keys, or models (lens/label.py:191-234).
- Signal caps make mass edits invisible to clustering. Ops touching more than 20 symbols, commits touching more than 80, and hub symbols touched by 15 percent of ops contribute no edges (cluster.py:37-46).
- `reduce_to_ideal` cost about 28 seconds per call on a large store before memoization, and the memo is a 64-entry LRU (order.py:30-41). A cold `sgt status` on a big repo takes tens of seconds.

### 1.3 Conflicts with the git substrate

- A backward or sideways history rewrite silently corrupts sgt state. `git reset --hard`, `commit --amend`, `git rebase`, and `git branch -f` leave the witness at a dropped sha and the ideal naming vanished ops. The documented remedy is the manual verb `sgt advanced resync` (core/lens.py:775-784). Nothing detects the rewrite automatically.
- The advertised drift detector is dead code. The gitbind module header claims out-of-band commits are detected so the graph never silently drifts (gitbind.py:5-6), and `detect_orphans` implements it (gitbind.py:789), but nothing in `sgt/` calls it. Only a test does.
- An in-progress git merge, cherry-pick, or revert freezes sgt. `sgt save` refuses and tells the user to run `git merge --continue` or `--abort` (cli/porcelain.py:172-178). The user is pushed back into raw git exactly when they are most confused.
- A cleanly completed raw `git merge` between two sgt branches is mis-mined (diffed against the first parent) and the forked chain is parked. The user finds out later, when a revert or save refuses (mine.py:426-438 and the put refusal at lens.py:863-869). The failure moved from silent data loss to a confusing refusal, which is better but is still not a merge story.
- Detached HEAD is half-supported. Sessions, land, and sync all refuse (core/session.py:117-118, sync/land.py:220-222, gitbind.py:853-860), and detached shas accrete as clone-local noise in the ideal table.
- An unconfigured repo gets `user.email=sgt@semi-git.local` written at repo scope, so commits are authored as "semi-git", not as the user (gitbind.py:358-361).

### 1.4 Collaboration limits

- sgt state travels on the single ref `refs/sgt/state`, which plain git tooling does not fetch or push by default. A teammate using plain git clone, pull, push, a GitHub PR merge, or CI silently drops sgt state. Coherence requires that everyone use `sgt push` and `sgt sync` (cli/sync.py:86-101, sync/state_ref.py).
- A GitHub squash merge destroys the `Sgt-Op:` trailers, and recovery degrades to mining the coarse squash, which forks the fine ops (sync/ingest.py:254-281).
- The whole intent layer (turns, rationale, prompts linkage, review queue, plan sessions) is local to one clone and never travels (state.py:172-197). The "why" recorded on one machine is invisible on every other machine. The team tier is deferred to M2 (intent/rationale.py:13-18).

---

## Part 2. The mental model audit

The three-questions model (what am I working on, what can I do, what happens if I do it) is the right spine, and `now_view` implements the first question well. The failures are in consistency, not in concept.

- The verb surface is 54 top-level subcommands. The daily set (now, log, save, undo, revert, restore, resolve, sync, push, land, plan, switch, diff) is buried among maintenance verbs (`resync`, `migrate`, `fsck`, `identity`, `tiers`), overlapping pairs, and verbs the workbench calls that may no longer match the CLI (see part 5). The specific traps:
  - Five spellings of revert with five blast radii: `revert <sel>`, `revert --keep-dependents`, `revert --session`, `revert feature@n`, and `intent revert <theme>`.
  - `feature regroup merge`/`split` (metadata-only, instant, reversible) vs `merge-op`/`split-op` (kernel rewrites that draft hollows needing fulfill and commit). Near-identical names, opposite weight. The most dangerous pair on the surface.
  - Five publish-shaped verbs: `save`, `advanced commit`, `land`, `propose land`, `push`. A git user's "commit" maps to `save`, and sgt also has a different `advanced commit`.
  - Four go-back spellings (`switch`, `restore`, `revert --to`, `fold --at`), none of which materializes an arbitrary past save (part 4).
  - `sgt status` no longer exists. It was folded into `log --summary`, so the first command a git user will type returns an error.
- The rich target resolver (feature, checkpoint `feature@n`, session, natural language) exists only for revert and restore (cli/ideal_edit.py:211-317). `sgt diff` accepts only git revs, and `sgt switch` accepts only a branch name. The user has to know which address forms work with which verb, which is exactly the kind of bookkeeping sgt is supposed to remove.
- Bare `sgt log` serves cached state and prints a hint to run `--refresh` (cli/inspect.py:548-552). The default answer to "what is going on" can be stale, and the user has to know the flag. The VS Code extension has the opposite problem and refreshes too much (part 4).
- Consequence preview is strong in exactly one place and absent elsewhere. Feature and symbol revert get a dry-run diff, a dependent frontier bucketed into blast, carry, and foundation, and a keep-list toggle (cli/ideal_edit.py:145-174, tui/consequence.py). Restore, land, propose land, undo, checkpoint revert, and the workbench batch revert get a confirm dialog with text only, no diff and no dependent list (editor/vscode/src/commands.ts:185-194, gitBridge.ts:114-137, 192-246, workbench.ts:290-340). The user learns that "sgt shows me what will happen before it happens" and then the promise silently doesn't hold for half the destructive verbs.
- Undo behavior is not uniform where it counts. `sgt undo` works one step back and refuses shared-out operations, which is right. But a land of the checked-out branch journals an undoable edit while a land of another branch refuses undo (sync/land.py:317 vs 334). The same verb has opposite undo behavior depending on where HEAD was.

---

## Part 3. The agent integration audit

The planned path (agent drafts a plan through MCP, edits, `sgt save` auto-confirms matches, rationale is reflected) is coherent. Everything around that narrow path is broken or missing.

### 3.1 Broken links, in order of leverage

1. The skill and the MCP tool contradict each other on the session id. `sgt-plan/SKILL.md:24` tells the agent to pass `$CLAUDE_CODE_BRIDGE_SESSION_ID`. The MCP tool description says to use `$CLAUDE_CODE_SESSION_ID` and explicitly not the bridge id (mcp/server.py:342). The prompt hook keys chat turns by the payload session id (cli/intent.py:179), and the join at api.py:2022-2023 fires only when the stored id equals the hook id. An agent that follows the skill stores the wrong id, the prompt never reaches its commit, and `claude --resume` from a stalled plan gets no id. The flagship trace-back feature fails silently because of one line of documentation.
2. The free-form aligner is dead code. `align_session`, the seven-stage pipeline that segments a conversation and aligns episodes to op clusters, says "Not wired to the save beat yet" (intent/align_session.py:20-22) and has no caller outside tests. It is the only writer of the review queue and of aligner rationale. So a prompt that does not become an explicit plan step never becomes a rationale on any op, `sgt intent review` is always empty, and the "review N alignments" next-action rung is unreachable (api.py:2253-2256). About 45KB of alignment code is inert.
3. Durable linkage fires only on `sgt save` or an explicit MCP confirm (`_fold_plan_matches` is called only from `porcelain._save`, cli/porcelain.py:247, 280-289). An agent that commits through raw git leaves no plan match, no session stamp, and no rationale. The preview shows the match and then the link dies with the session.
4. There is no bridge for plans created outside sgt. Nothing ingests Claude Code plan-mode output or `docs/plans/*.md`. If the agent plans anywhere except an explicit `sgt_plan_intake` call, the plan layer sees nothing and 100 percent of the work reads as drift.
5. There is no re-attach on resume. A resumed conversation does not rebind to its plan, and re-running intake mints a new session with a new baseline, orphaning the first plan's hollows until the 7-day sweep (loop/plan.py:201-208, 306-318). The comment at api.py:2245 explicitly declines to add `sgt plan resume`.
6. Ownership is convention only. Any agent can confirm or close any other agent's plan (mcp/server.py:210-221, 249-259). Checkpoint previews are also not isolated between concurrent sessions, so agent B's commits show up as candidate matches for agent A's steps (loop/match.py:231-243).

### 3.2 The MCP surface is missing half the lifecycle

Agents get log, grid, status, diff, revert, restore, the plan loop, and recall (mcp/server.py:269-392). Agents do not get save, land, sync, push, propose, resolve, sessions, fold, now, or any feature verb (the server docstring says feature verbs are CLI-only for now, server.py:29). The consequence is the exact back-and-forth the user complains about. The agent can plan and edit, but the human must drop to the CLI to save, land, resolve, or reorganize, and the agent cannot see or drive the state the human sees.

### 3.3 Status surfacing in the UIs

- Plan status does render in several places (grid ghost cells, plan CodeLens on matched and drifted spans, the plan status bar, the Now tree, drift diagnostics). The parts that exist are good.
- The live agent activity feed is captured by the PostToolUse hook and fetched by the extension, then dropped. `NowView.context.turns` and `context.activity` exist in the types (editor/vscode/src/types.ts:697-719) and no tree reads them. The user cannot see what the agent is doing right now even though the data is already on disk and already fetched.
- The TUI renders plan ghosts and a fork banner but no drift and no session or agent identity (tui/graph.py:820-827, 458-483). The state banner at graph.py:458 is the natural one-line attach point.
- A plan that is building (in progress and not yet stalled) never appears in the Now tree. Only stalled plans reach the needs-you section, so a healthy working agent is invisible there until it goes quiet for an hour.
- Recently-done saves in the Now tree are inert rows with no command (nowTree.ts:104-110). The most natural entry point into "look at that version" does nothing when clicked.

---

## Part 4. Scrubbing, time travel, and the disk

What the user asked for is to scrub to a past state, look at that version of the code on disk, and not have sgt fall over. The current reality has three levels.

- Viewing a past diff works, but only for git-resolvable refs. `sgt diff a b` accepts branches, tags, HEAD~N, and shas, and is an op-set diff grouped by symbol, not a text diff (api.py:373-406). It does not accept saves, checkpoints, features, or op ids, even though revert does.
- Viewing file content at a past point exists in exactly one place. `fold_view` computes full file content at any frontier, and only the workbench webview code panel renders it (editor/vscode/src/sgt.ts:268-270, workbench.ts:544-617). The CLI `sgt fold --at` prints file paths only, never bytes (cli/inspect.py:772). There is no `sgt show <spec> <file>` equivalent of `git show rev:path`.
- Materializing a past state on disk does not exist. `sgt switch` runs `git checkout <branch>` and only accepts a branch tip (porcelain.py:102-122). Passing a sha would detach HEAD with no guard, and a save on a detached HEAD produces orphaned commits (the arg goes straight to git checkout). Rewinding a checkpoint (`sgt revert feature@n`) is a forward edit that rewrites and commits, not a visit. The TUI has no scrubber at all. The playhead in the workbench is view-only.

The refresh concern in the question is real but points at the design, not against it. The extension refreshes off one watcher on `.sgt/**/*.json` with a debounce (extension.ts:127-142), and a full refresh spawns about 8 sgt subprocesses, one of which (`sgt log --tree --refresh`) forces a full recluster every time (sgt.ts:135-141). Materializing an old tree in place would trip mine-on-contact, move the witness, and thrash every cache. So visiting the past must not happen in the working tree at all. Part 6 proposes `sgt peek`, which materializes into an ephemeral read-only git worktree, which sgt already knows how to create for sessions (gitbind.py:943), and which has its own `.sgt` directory by construction, so the main tree's state is untouched and no refresh storm happens.

---

## Part 5. Concrete bugs found (fix these regardless of any rethink)

1. Skill vs MCP session id contradiction (part 3.1 item 1). One line in `sgt-plan/SKILL.md`. Everything about trace-back and resume hangs on it.
2. The workbench action bar for merge, rename, move, and split emits bare `sgt merge`, `sgt rename`, `sgt move`, `sgt split --apply` through `cliArgsFor` (workbench.ts:420-452), while the typed methods that emit the current `sgt feature regroup ...` verbs exist and are never called (sgt.ts:232-254). The live UI path can be calling relocated verbs.
3. `sgt switch` passes its argument straight to `git checkout` with no branch check (porcelain.py:113, gitbind.py:900-904). A sha detaches HEAD and later saves are orphaned. Refuse non-branch args with a pointer to the future peek verb.
4. `detect_orphans` is advertised in the gitbind module docstring and never called (gitbind.py:5-6, 789). Either wire it (part 6A) or delete it and fix the docstring.
5. The committer identity default writes `sgt@semi-git.local` at repo scope (gitbind.py:358-361). Prompt or fail loudly instead of silently authoring commits as a bot.
6. `sgt/lifecycle/` and `sgt/merge/` are empty directories holding stale `.pyc` files. Delete them.
7. Dead undo branch for `kind="propose"` that is never appended (core/oplog.py:199, sync/land.py:334). Either append it on propose-land or remove the branch.
8. Land undo asymmetry (part 2). Pick one behavior, document it in the oplog docstring, and make the confirm dialog say which case the user is in.
9. VS Code refresh runs about 8 subprocesses where `compose` already contains most of the data (types.ts:531-544), and forces a recluster on every tree read. Split "read the map" from "rebuild the map".

---

## Part 6. The rethink

One sentence of intent for the whole design: the user manipulates one graph of intents, saves, and features, and everything else (git, refs, worktrees, hooks) is implementation that sgt keeps consistent by itself.

### 6A. sgt owns the repo

Today sgt is a polite guest in the user's git repo. It refuses eleven raw git verbs and hopes. The rethink makes managed mode explicit. In a managed repo:

- Mine-on-contact grows a reconciliation step. It already compares the witness to HEAD. When HEAD moved backward or sideways (rewrite, amend, forced move), it runs the existing resync recovery automatically instead of trusting stale tables and waiting for a later refusal. The detection signal and the remedy both exist today (lens.py:500-601, lens.py:775). They are not connected. Auto-resync preserves exclusions, so reverts do not resurrect, which is exactly what the exclusion OR-Set was built for.
- Wire `detect_orphans` into the same contact path so out-of-band commits are labeled as such in the log rather than absorbed anonymously.
- In-progress raw merges stop being a dead end. `sgt now` and `sgt save` should detect `MERGE_HEAD` and offer the resolution path in sgt vocabulary (finish or abort, then resync), not just refuse with a git command to run.
- Be honest about what the refusal table covers. The eleven refusals only fire through the `sgt git` passthrough (porcelain.py:32-45). A user typing plain `git rebase` in their shell bypasses everything, which is why auto-resync above is the real guard and the refusal table is only a teaching aid. Managed mode could optionally install the guard as a git hook (pre-rebase, reference-transaction) so the protection follows the repo rather than the spelling of the command.
- Decide the transport question once. Either commit the op store into the tree like the other committed artifacts, so plain git carries everything and a plain clone is complete, or keep `refs/sgt/state` and accept that sgt-to-sgt is the only supported collaboration. The current position (a side ref that silently drops on plain-git usage, plus trailers that die on squash) is the worst of both. The op store is content-addressed and append-only, so committing it is mergeable by construction. The cost is repo size, which is exactly why 1.1's binary handling must change first (store opaque images as blob OID references, not embedded hex, since the bytes are already in git's object store).

### 6B. One id spine for intent

Today a "why" join must cross three keyings (plan session id, Claude session id, hook payload session id) and survives only on the planned path. The rethink is one work-unit id, minted at first contact, stamped everywhere.

- Whatever arrives first (a prompt hook event, a plan intake, an adopted markdown plan) mints the intent id. Turns, hollow ops, matches, op attribution, saves, and rationale all carry that one id. The three-way join in `_atom_prompt` (api.py:1985-2026) disappears.
- Wire `align_session` into the save beat behind a flag. The code exists and is tested. On each save, align the unplanned turns against the ops the save minted, queue low-confidence pairs into the existing review store, and light up the already-built `sgt intent review` and next-action rungs.
- Add plan adoption. `sgt plan adopt <file>` ingests a markdown plan, and the Claude Code integration should make the skill instruct the agent to call intake at plan-mode exit. The hook already fires on every prompt, so a cheap heuristic (a plan-mode exit event, or a write under `docs/plans/`) can also prompt adoption without agent cooperation.
- Add `sgt plan resume`. Rebind by Claude session id when captured, otherwise offer the stalled plans by name. Re-running intake with an existing id must reuse the session, not mint a new baseline.
- Enforce ownership at the tool boundary. A confirm or done call for a session another agent owns fails without an explicit override flag.

### 6C. MCP parity and in-situ reflection

The rule going forward: any action a user can take on the graph, an agent can take through MCP, and any state a surface shows, the agent can read. Concretely, add `sgt_save`, `sgt_now`, `sgt_fold`, `sgt_resolve` (draft plus fulfill plus land), `sgt_sync`, `sgt_land`, and the feature verbs, each returning the same JSON projection the CLI returns, with the same consequence preview available under an emit flag before any write. The agent then works, checkpoints, saves, resolves, and lands without the human relaying between surfaces, and every one of those writes trips the existing `.sgt` watcher, so the extension reflects agent activity with no new plumbing.

For the in-situ part, the data is already flowing and gets dropped at the last step. Render `context.activity` in the Now tree as a live "agent is editing X" section. Give recently-done rows a command (open the save in the workbench with the playhead at that commit). Put drift and session identity in the TUI state banner (graph.py:458 is the attach point). All three are display-only changes over data that already exists.

### 6D. Visiting the past

Three verbs, in increasing weight, all read-only until the user says otherwise.

- `sgt show <spec> [file]` prints file content at a frontier. The computation exists in `fold_view`. The spec grammar is the one revert already speaks (save, `feature@n`, commit index, op id), so the address forms stop depending on the verb.
- `sgt peek <spec>` materializes the frontier into an ephemeral read-only git worktree under a scratch path and prints (or opens) it. No HEAD move in the main tree, no witness move, no cache invalidation, no refresh storm, because it is a different directory with its own `.sgt`. Both primitives already exist: session infrastructure creates and tracks worktrees (session.py:132), and `restore_worktree_to` (gitbind.py:1082) already writes an arbitrary tree state to disk and is wired only to land's internal rollback. Closing the peek deletes the worktree.
- Promoting a peek. If the user wants to keep what they see, the existing forward edits take over (`sgt revert feature@n` or a session started from that frontier). The rule that history only moves forward stays intact, and scrubbing never mutates anything.

The workbench playhead then gets a "open on disk" button that calls peek, which is the piece the user asked for.

### 6E. Verb diet and consequence symmetry

- The daily surface is roughly twelve verbs (now, log, save, undo, revert, restore, resolve, show, peek, diff, sync, push, land, plan, switch). Everything else moves under `advanced`, which is already the pattern (KTD2). Kill the overlapping names: `merge-op`, `split-op`, and `transplant` stay advanced-only, and `commit` should not exist next to `save` and `land` at the top level.
- Every mutating path goes through the same preview projection. The machinery (`verb_preview_view`, `so_what_for`, blast, carry, foundation) exists and is wired into exactly one verb family. Restore, land, propose land, undo, and the workbench batch verbs each get the same preview before their confirm. Until a verb has a preview, its confirm dialog must say "no preview available for this operation" rather than implying the check happened.
- One resolver. The address ladder that revert speaks (op, symbol, feature, checkpoint, session, natural language) becomes the shared resolver for diff, show, peek, and fold.

### 6F. Honest degradation

The kernel's limits are acceptable if the user can see them and predict them.

- Surface the tier per file. The UI should show which files get symbol-level semantics and which are whole-file opaque, so a fork on a JSON file is not a surprise. The tiers machinery exists (`sgt tiers`), and the surfaces never show it.
- Add JavaScript. The TypeScript grammar parses JavaScript, so mapping `.js`, `.jsx`, `.mjs`, and `.cjs` to the existing parser is cheap and removes the largest silent coverage hole for the likely audience.
- Pin labels. Once a feature label has been shown to the user, keep it until the user renames it or confirms a suggested rename. LLM relabel churn breaks the "know what you are working on" question at the root. The label cache and pin stores exist and the policy just has to prefer them.
- State the non-goals in the README and docs. Submodule content, tracked symlinks, two live versions of one symbol on a branch, and detached-HEAD workflows are out of scope. The code already behaves that way. Saying it removes the unknown.

---

## Part 7. Sequencing

Ordered so that each phase is shippable and none blocks the previous.

Phase 0, correctness, days. Part 5 items: the skill id fix, the workbench verb paths, the switch guard, the committer identity, the empty packages, the dead undo branch, the land undo docs.

Phase 1, one id spine, one to two weeks. Unified intent id, aligner wired to save behind a flag, plan adopt, plan resume, ownership enforcement. Exit test: a Claude Code session that plans in plan mode, is interrupted, resumed, and finishes with raw prompts along the way produces a graph where every save answers `sgt why` with the actual prompt text, with no manual intake call.

Phase 2, surfaces and parity, one to two weeks. `show` and `peek`, MCP parity verbs, activity feed rendered, recently-done rows clickable, TUI drift banner, refresh diet (compose becomes the one read, recluster only on explicit rebuild), preview symmetry for restore, land, and undo.

Phase 3, substrate, the big one. Auto-resync on rewrite detection, orphan labeling, opaque images by blob reference, the transport decision (committed op store vs state ref) with a migration under the existing manifest pattern, and then the managed-mode default. Exit test: a teammate clones with plain git, runs `sgt init`, and sees the full graph. A rebase in the working repo self-heals on next contact without a manual resync.

The order is deliberate. Phase 1 makes the intent layer trustworthy, which makes the surfaces in phase 2 honest, which makes the substrate work in phase 3 visible to the user as reliability rather than plumbing.
