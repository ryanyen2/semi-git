---
title: "research: Scoop check — Intent Clustering Overlay novelty vs. prior art"
type: research
status: completed
date: 2026-07-17
---

# research: Scoop Check — Intent Clustering Overlay

Novelty inferred from `feat/intent-clustering-overlay`'s plan doc (`docs/plans/2026-07-17-001-feat-intent-clustering-overlay-plan.md` in the `intent-clustering-overlay` worktree) — no explicit research problem/novelty was given in the request that triggered this check, so both were derived from that plan document per the `scoop_check` skill's instruction to proceed on the most reasonable interpretation without pausing to ask.

## 1. Verdict

**Level 3 — Medium Overlap**

## 2. Delta

Unlike **AtomicCommitBench** (Lin, Zhou & Li, 2026), which shows that feeding an LLM agent dependency and hunk-role cues as soft evidence improves selective-revert-friendly commit grouping but explicitly designs for the agent to make the final partitioning decision itself ("leaving final grouping to the agent"), the proposed intent-clustering overlay computes its dependency-graph signal as an **independent, post-hoc confidence tier** over a partition whose membership is already fixed by deterministic provenance data — so the LLM never participates in the revert-critical decision at all — yielding a formally provable equivalence between an intent-based revert and a hand-issued revert over the same op-set, a safety guarantee AtomicCommitBench's own evaluation explicitly does not test (its TCR metric "does not evaluate full `git revert` behavior").

A secondary delta against the closest *classical* (pre-LLM) analog: unlike **SmartCommit** (Shen et al., 2021), whose dependency/similarity graph *directly decides* an intra-commit partition subject to human adjustment before it becomes new commits, the proposed work separates *what is grouped* (a pure function of provenance + dependency data, spanning many commits/features) from *how much to trust the cross-feature claim* (the tier) from *whether the LLM may touch membership at all* (it may not) — a three-way separation no candidate paper implements.

## 3. Decomposed claim

- **Problem framing:** Given a VCS/program-history system with an existing structural (symbol/feature) clustering, derive and expose a second, cross-cutting "intent" grouping of atomic operations by provenance/why-axis, and make that grouping safely actionable (revertible) — without weakening the safety guarantees (preview, oracle gate, fork refusal) of the underlying edit system.
- **Core mechanism:** A deterministic-first fallback ladder — (a) partition ops by earliest-witnessing-commit into atoms, (b) coalesce atoms sharing a conventional-commit scope, (c) an LLM assigns theme names/coalesces scope-less atoms *bounded strictly to* the fixed atom partition (never emits op-ids; cached by content-hash), (d) tier each group by dependency-graph connectivity (`coupled` > `co-changed` > `thematic`) — with "revert by intent" routed through the unmodified deterministic ideal-edit/oracle-gate/preview pipeline.
- **Key insight:** Membership must remain a pure deterministic function of provenance + dependency data; the LLM is confined to naming/coalescing over a *fixed* partition, never deciding membership — enabling a formal equivalence guarantee for a destructive revert, and eliminating rebuild "theme flicker." Cross-feature span is graded by real dependency connectivity, not asserted.
- **Application domain:** An operation-log-based (op-DAG), content-addressed, merge-semilattice edit model with feature-tree clustering and an existing LLM-assisted labeling substrate — a semantics-aware VCS/dev-tool layer, not a generic ML clustering paper.

## 4. Structured papers

*(15 from live search across arXiv/DBLP/OpenAlex/OpenReview/Semantic Scholar/Crossref, queries below; 5 recalled from training knowledge, tagged `model-recall`, added because the "commit untangling" subfield — the closest established research area — did not surface via the exact search phrasings used)*

**Queries used:** (1) *"grouping code changes by developer intent across features version control"*; (2) *"commit intent mining and change clustering"*; (3) *"LLM-assisted commit grouping for safe code revert"*. Year range 2015–2026. `open_alex` was unavailable in this environment (import error); `dblp` and `openreview` returned 0 results for all three queries. A large share of arXiv hits were pure keyword-collision noise (control-theory papers matching "safe"/"commit"/"revert") and are excluded below as manifestly off-domain — itself a mild negative-novelty signal, since no CS.SE-titled arXiv paper ranked above that noise for these exact phrasings.

| # | Title | Date | Problem framing | Core mechanism | Key insight | Application domain | Overlap | Source |
|---|---|---|---|---|---|---|---|---|
| 1 | Developer-Intent Driven Code Comment Generation | 2023-05 | Generate code comments reflecting developer intent | Comment-generation model conditioned on code/commit context | Comments should reflect "why," not just "what" | Code documentation tooling | 1 | crossref |
| 2 | Tracking the Ripple Impact of Code Changes through VCS | 2019-12 | Trace downstream impact of a change across a codebase | Impact/traceability analysis over VCS history | Changes ripple beyond their immediate location | VCS change-impact analysis | 1 | crossref |
| 3 | Automated Context Generation for AI Code Assistants (contextify-ai) | 2026-05 | Capture developer intent at commit time; cross-check vs. detected changes | AST smart-diff + LLM dual-section context files; mismatch verification | Explicit intent capture closes the AI-assistant context gap | AI-assistant documentation automation | 1 | crossref |
| 4 | Coming: a Tool for Mining Change Pattern Instances from Git Commits | 2018-10 | Detect instances of a user-specified structural change pattern | XML-specified fine-grained AST diff pattern matching | Predefined structural patterns recur across commits | Empirical MSR tooling | 1 | arxiv |
| 5 | Commit-Level Software Change Intent Classification (Heričko et al.) | 2024-03 | Classify a commit's maintenance-activity intent | Fine-tuned CodeBERT/GraphCodeBERT embeddings, classified | Code-change semantics beat message text for intent | VCS/commit intent classification | 2 | crossref |
| 6 | Exploring Popular Software Repositories: Sentiment Analysis and Commit Clustering | 2024 | Cluster commits, correlate with contributor sentiment | Sentiment analysis of commit messages + clustering | Sentiment correlates with commit patterns | VCS mining/commit clustering | 2 | crossref |
| 7 | **Atomizer: LLM-based Multi-Agent Framework for Intent-Driven Commit Untangling** | 2026-01 | Split one composite commit into concern-coherent groups | LLM Chain-of-Thought + grouper/reviewer multi-agent refinement loop; LLM decides membership | Give LLM more authority + iterative self-review to fix structural blindness | Commit untangling (intra-commit), C#/Java | 2 | semantic_scholar |
| 8 | Mining VCS for Automatically Generating Commit Comment | 2017-11 | Auto-generate a commit message from a diff | Mining-based comment generation | Commit comments can be synthesized from the change | VCS mining | 1 | semantic_scholar |
| 9 | On the Use of Commit Messages for Corrective Software Maintenance (mapping study) | 2026-03 | Map research landscape on commit messages in maintenance | Systematic literature mapping (secondary study) | Commit messages often fail to convey intent | VCS/commit-message research | 2 | semantic_scholar |
| 10 | Automatic Data-Driven Software Change Identification (Heričko) | 2023-06 | Identify/characterize software changes | Code representation learning (embeddings) | Learned representations capture change semantics | VCS/change identification | 2 | semantic_scholar |
| 11 | AgentPack: Dataset of Code Changes Co-Authored by Agents and Humans | 2025-09 | Curate a corpus with more reliable intent descriptions | Corpus curation + LLM fine-tuning benchmark | Agent-authored commits carry more explicit intent than noisy human ones | Dataset/benchmark for code-editing models | 2 | semantic_scholar |
| 12 | Intent Discovery for Enterprise Virtual Assistants | 2022 | Discover conversational intents from utterances | Utterance embedding + clustering | Embedding+clustering surfaces latent NL intent | Conversational AI/NLU (not code) | 1 | crossref |
| 13 | Code Revert Prediction with GNNs (J.P. Morgan Chase) | 2024-03 | Predict whether a change will be reverted | GNN over code-import graph + features | Structural relationships improve revert-risk prediction | Industry VCS defect/revert prediction | 2 | arxiv |
| 14 | An Empirical Analysis of Git Commit Logs for Code-Clone Inconsistency | 2024-09 | Study clone-pair co-change consistency | git-log mining + co-change ratio analysis | Clone pairs co-change only ~half the time, often inconsistently | VCS/code-clone maintenance | 2 | arxiv |
| 15 | **AtomicCommitBench: Can Coding Agents Reconstruct Commit Histories from Squashed Patches?** | 2026-07 | Partition a squashed patch into a replayable commit sequence for review/selective revert | Hunk-to-commit partitioning benchmark; Dependency-Aware Commit Evidence (DACE) | Dependency/role cues (not file locality) needed for correct grouping | Coding-agent output history reconstruction | 3 | arxiv |
| 16 | Herzig & Zeller, "The Impact of Tangled Code Changes" | 2013 | Quantify how often real commits bundle unrelated changes | Empirical measurement study (no grouping algorithm) | Tangled commits are pervasive, corrupt ground truth | VCS/empirical SE | 2 | model-recall |
| 17 | Dias et al. (EpiceaUntangler), "Untangling Fine-Grained Code Changes" | 2015 (SANER) | Split one developer's in-progress edits into atomic commits | Supervised ML classifier (Random Forest) over IDE-tracked features + dendrogram clustering | Live fine-grained IDE features substitute for static call-graph analysis | VCS/commit untangling (pre-commit, IDE plugin) | 3 | model-recall |
| 18 | Barnett et al. (ClusterChanges), "Helping Developers Help Themselves" | 2015 (ICSE) | Decompose one code-review changeset for easier review | Def-use/use-use dependency graph over Roslyn program entities, directly partitions | Structural dependency relationships reveal natural commit boundaries | Deployed industrial code-review tooling (Microsoft) | 3 | model-recall |
| 19 | Kirinuki et al., "Hey! Are You Committing Tangled Changes?" | ~2014 | Warn a developer in real time of a mixed-concern commit | Historical-similarity/heuristic detector | Real-time prevention beats post-hoc untangling | VCS/commit-hygiene tooling | 2 | model-recall |
| 20 | Shen et al., "SmartCommit: A Graph-Based Interactive Assistant" | 2021 (ESEC/FSE) | Decompose one diff into activity-coherent atomic commits, human-reviewed before finalizing | Multi-relation weighted graph → partition, with interactive human adjustment | Multiple signals + human-visible adjustable partition beat either alone | Deployed interactive VCS assistant (83 engineers, 9 months) | 3 | model-recall |

## 5. Comparison result

*(Full per-axis analysis, verified against full-text for 5 of 7 deep-dive candidates — Atomizer, AtomicCommitBench, SmartCommit, Dias et al./EpiceaUntangler, and Barnett et al./ClusterChanges were fetched and read in full; Herzig & Zeller and Kirinuki et al. could not be located as accessible PDFs this session — paywalled/rate-limited — and are characterized from well-established general knowledge, flagged accordingly.)*

**Proposed work** — see Decomposed claim above.

**Atomizer** (Zhu et al., ICSE 2026, arXiv:2601.01233) — Problem framing: partial (intra-commit intent grouping, classification-accuracy eval). Core mechanism: differ (LLM directly decides & iteratively refines membership — opposite authority allocation). Key insight: differ (more LLM authority vs. withholding it). Application domain: partial (benchmark datasets, no deployed overlay). → **1/4 axes matching → Level 4, Low Overlap**

**AtomicCommitBench** (Lin, Zhou & Li, 2026, arXiv:2607.03332) — Problem framing: match (explicit "selective revert" framing, the closest found). Core mechanism: partial (DACE dependency cues resemble tiering but feed the LLM's decision, not an independent post-hoc label). Key insight: differ (explicitly "leaving final grouping to the agent"). Application domain: partial (diagnostic benchmark, no real `git revert` tested). → **2/4 axes matching → Level 3, Medium Overlap**

**SmartCommit** (Shen et al., ESEC/FSE 2021) — Problem framing: partial (activity-oriented grouping, but intra-commit/authoring-time). Core mechanism: partial (multi-signal dependency graph → partition → mandatory human review before it's real — the strongest classical mechanism echo found). Key insight: partial (shared "human must confirm before it's real" value, different specific rationale). Application domain: partial (deployed industrial VCS assistant, but conventional git, no LLM). → **2/4 axes matching → Level 3, Medium Overlap**

**EpiceaUntangler / Dias et al.** (SANER 2015, arXiv:1502.06757) — Problem framing: partial (untangling by task, but pre-commit/single-developer). Core mechanism: differ (supervised ML classifier + dendrogram clustering, not deterministic-partition/LLM/tiering). Key insight: differ. Application domain: partial (IDE-plugin tooling). → **1/4 axes matching → Level 4, Low Overlap**

**ClusterChanges / Barnett et al.** (ICSE 2015) — Problem framing: partial (structural splitting for review, not cross-commit/revert). Core mechanism: partial (def-use/use-use dependency graph directly partitions — same primitive family as the `coupled` tier, but the graph *is* the decision, no LLM). Key insight: differ. Application domain: partial (deployed at Microsoft). → **2/4 axes matching → Level 3, Medium Overlap**

**Herzig & Zeller** (MSR 2013) — *unverified, general knowledge* — Problem framing: partial (foundational tangled-commit phenomenon). Core mechanism: differ (static-analysis voter heuristics). Key insight: differ. Application domain: partial. → **1/4 axes matching → Level 4, Low Overlap**

**Kirinuki et al.** (~2014) — *unverified, general knowledge* — Problem framing: partial (tangled-commit detection). Core mechanism: differ (real-time similarity heuristic, not a grouping/tiering/revert mechanism). Key insight: differ. Application domain: partial. → **1/4 axes matching → Level 4, Low Overlap**

---

**Overall verdict: Level 3 — Medium Overlap.** No prior work replicates the full bundle (cross-commit/cross-feature scope + deterministic-first partition + LLM confined to naming + independent dependency-graph trust tier + revert proven equivalent to manual, through an unmodified oracle-gated pipeline). But three papers each anticipate roughly half of it from different angles — AtomicCommitBench (dependency cues → selective revert, but LLM decides), SmartCommit (graph → partition → human review, but pre-LLM/intra-commit/no revert), and ClusterChanges (dependency graph as sole grouping signal, but no LLM/no revert) — which is enough real, citable overlap to require the delta be stated explicitly (as done above), but not enough to threaten the core claim.

## Sources & Research

Intermediate per-step working notes (search queries, abstract triage, candidate selection, full-paper deep-dive extraction) were logged during the check to `step1.md` through `step7.md` at the repo root, and the 5 full-text PDFs/extracted `.txt` retrieved during the deep dive are under `papers/` at the repo root.
