# Developer experience review: does sgt match how a developer's memory works

Date: 2026-08-05. Companion to `2026-08-05-code-review-and-architecture-rethink.md`. That document audited what the code does. This one reviews the experienced product: whether the ontology matches how developers think, whether the output reads like an answer, and why the tool feels slow and scattered even where the features exist. Every measurement below was taken today on the sgt testbed (`~/repos/sgt-testbed`, 5 saves, 1 feature, tiny Python files), which is the most charitable possible repo.

## 1. The measurements

| Action | Wall time | Note |
| --- | --- | --- |
| `sgt --help` | 0.09s | Interpreter startup is fine. The cost is elsewhere. |
| `sgt now` | 0.57s | The ambient "where am I" read. |
| `sgt log` | 0.65s | Prints a staleness warning on every run. |
| `sgt log --map` | 0.37s | |
| `sgt log --refresh`, no changes | 0.78s | |
| `sgt log --refresh`, cold | 10.1s | 15 percent CPU. Mostly waiting on the network. |
| `sgt log --refresh` after a one-line edit | 5.0s | 38 percent CPU. The core loop of the product. |

The numbers that decide the feel are the last two. The product's promise is ambient awareness, and the implementation puts an LLM labeling call and a re-cluster inside the read path. So the loop the whole product exists for (edit, then see the edit reflected with words) costs 5 to 10 seconds on a toy repo, and the VS Code extension multiplies the cost by spawning about 8 of these processes per refresh. A habit-forming ambient surface needs roughly 100ms to feel instant and must stay under about 1 second to hold attention. sgt is 5x over that budget for a plain read and 50x over it for the core loop, at toy scale, before the 28-second `reduce_to_ideal` of a large store enters the picture.

Speed is not polish here. The product is a feedback loop, so latency is product failure, and no amount of surface work changes the feel until the read path stops doing network calls and clustering.

## 2. Do developers operate at the entity level? No.

The kernel's ontology is content-addressed ops over symbol chains, projected to a downward-closed fork-free set, clustered into features by graph partitioning. As machinery, part 1 of the companion review found it sound. As the thing the user reads, it is wrong, because none of those objects is a memory cue.

A developer's memory of their own work is episodic. They remember goals with a time and a feeling attached ("yesterday I got auth working", "the afternoon the flaky test ate"). They navigate by place and by recognition, meaning files and regions of files they recognize on sight, not symbol names they can recall. They explain their work as cause and effect ("I changed X because Y broke"). And they carry open loops ("I still need to handle the empty case"). Episodes, places, causes, open loops. Not one of the kernel's nouns.

The tell is in sgt's own testbed history. `sgt now` prints, under "recently done":

```
ec43a03a  sgt restore f-08ccdb123f23b7d1c98388f5033d6037688aabbd7e6c83b63e8e1de57d0e62bc
80d3c218  sgt revert f-08ccdb12@2
```

The developer did "put the clear command back". The system recorded its own plumbing, with a 64-character hex id, as the story of the developer's day. The same leak shows everywhere: `f-08ccdb123f23` and `theme-09b5547367da` as row labels, `(co-changed, llm)` provenance tags in a list a human is meant to read, "1 op(s) across 0 feature(s)" as the description of a freshly edited file, and a "cross-feature theme" that crosses exactly one feature.

The irony is that sgt already contains the right layer. The intent checkpoints ("Scaffolding Task List CLI", "Extend Add/List Fields") are episodes, and `sgt intent list` is the closest any surface comes to how a developer would narrate the work. But the checkpoint layer is built as an overlay on the op spine, and the surfaces present the spine. The product inversion this review argues for: episodes are the presentation spine in every surface, and ops are the manipulation substrate behind a "show the machinery" door. The entity level is exactly right for manipulation (revert precision, blame, dependency blast radius) and exactly wrong for reading. Keep the algebra. Stop making people read it.

## 3. Describing work without excess

The rule that fixes the output: information is an answer to a question someone is actually asking, at the grain of that question. The four questions developers ask, and the grain of each:

- Where was I? The last episode and the open loops. Three to five lines.
- What changed since X? Episode titles in the developer's own words, one line each.
- Why is this code like this? The prompt or plan step that caused it. That is the intent layer's whole purpose.
- What is left? Unfulfilled intents, as verb phrases.

Today's output answers with inventory instead: counts of ops and features, glyph legends (the legend line under `--map` is longer than the map it explains), ids, and provenance. And the labels are generated when they should be quoted. "Task Command Additions" is LLM-speak for what the developer called "Add 'done <index>' command" in a save subject that sgt already possesses verbatim. The label policy should be a strict preference order: the user's own words (save message, prompt), then the agent's plan step title, then and only then an LLM label, and a label that has been shown to the user never changes without the user confirming a rename. The current pipeline rebuilds LLM labels on refresh, which is both the network call in the read path (section 1) and the name churn that breaks recognition (companion review, part 1.2).

## 4. Reading the code for why it is written this way

- `sgt/api.py` is 2,533 lines holding about 60 `*_view` functions. Each time a surface needed data, a new projection was added. No single place decides the information hierarchy, which is why the surfaces scatter, and no single place counts, which is why `sgt log` says 5 saves while `sgt log --map` says 8 on the same repo (one view counts the ideal's saves, the other counts commits including sgt's own bookkeeping commits). The scattered feel of the product is this projection sprawl, experienced from the outside. The fix is roughly four canonical queries (now, history, graph, preview) with parameters, so a disagreement between two surfaces becomes impossible by construction.
- The codebase is written for the author's memory, and it shows. The comments are essays, the invariants are named (F3, F6, LAW-0), the crash-consistency work is genuinely careful. The same care has never been pointed at the user's memory. Reading every plan in `docs/plans/`, there are acceptance criteria for CRDT merges and schema envelopes, and there is not one latency budget and not one "these are the words the user will see" criterion anywhere. The plans optimize internal coherence. Experienced coherence was nobody's exit test, and both this document's problems (section 1 and section 3) went unnoticed because nothing measured them.
- The no-daemon design decision is the root of the latency floor. Every command re-pays mine-on-contact, git subprocess warmup, and parse caches, because nothing stays resident. The webview front-end is proof that the team can do polish (rAF-coalesced scrubbing, dim-in-place instead of re-layout, a one-shot ghost-to-car landing animation, reduced-motion support). All of it is capped by a transport that spawns a cold Python process per read, serialized through a FIFO queue, so a playhead drag waits 300 to 600ms per fold. The UI is a remote terminal to a batch CLI. That is an architecture property, not a CSS property, and it is why the product feels sluggish despite well-crafted parts.
- Error surfaces answer the wrong question. `sgt why c761609` (a save sha, the most natural argument a developer would try) prints the full 60-line help text. `sgt status` (the first command every git user types) does not exist. Wrong-shaped input should get "that is a save; its ops are X and Y, did you mean one of those", not a manual.

## 5. What this changes in the plan

The companion document's phases stand. The following are added or promoted, because the experience findings make them load-bearing rather than cosmetic.

1. One precomputed answer file. `now_view` gets computed at write time (on save, on the activity hook, on land) and written to `.sgt/local/now.json`. `sgt now`, the log header, the status bar, and the Now tree read the file. Warm read target: under 150ms including interpreter start. Ambient surfaces stop paying mine-on-contact entirely.
2. Nothing on the read path talks to the network or reclusters. New content renders immediately with the developer's own words as the label. An async upgrade queue improves labels later, and an improved label for something already shown arrives as a suggestion, not a replacement.
3. Episode-first presentation everywhere. The save list becomes an episode list. Bookkeeping commits (revert and restore materializations) are marked at save time and folded out of every human-facing list, so the story of the day contains only things the developer did.
4. Ids leave the default output. Short human handles only. Hex ids appear under `--json` and `--full`.
5. The projection collapse (four canonical queries) joins phase 2, not as cleanup but as the mechanism that makes surfaces stop disagreeing.
6. Latency budgets become tests. The repo already has golden tests. Add timing gates on a seeded 10k-op store: `sgt now` under 150ms, `sgt log` under 300ms, edit-to-surface under 1 second without label upgrade. A plan without these numbers in its exit criteria is how the current numbers happened.
7. Every plan template gains two exit criteria: the exact words a user sees in the touched surfaces, and the wall-clock cost of the touched path. Cheap to write, and either one would have caught everything in this review.
