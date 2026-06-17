---
date: 2026-06-17
topic: semi-git
---

# semi-git (sgt) — Semantic Feature-Level Version Control

## Summary

semi-git (`sgt`) lets a developer version a codebase by its **features and concepts** instead of its **diffs**. A developer works through a normal, messy prompt stream; semi-git distills that stream into a living semantic graph of feature/concept nodes (system-built, user-correctable) and keeps the underlying git history as the materialization. Every mutation — a new feature, a fix, a re-organization, an iteration, a revert — only lands if it is **invariant-confluent** (via the EICO engine); otherwise it is quarantined and surfaced with a witness. semi-git owns the *classification* and *graph-manipulation* agents but **delegates all code-writing to external coding agents** (Claude Code, Codex, Gemini, …) through an adapter layer.

## Problem Frame

Today, version control operates at the line/commit level. Recovering a known-good state is easy, but **operating on the unit people actually think in — a feature or concept — is not**. You cannot cleanly "remove rate limiting" or "turn off this concept" without manual dependency archaeology, and reverting risks silent downstream breakage. Tools that come close still operate on top of traditional git: Graphite stacks *branches* (not features) with no feature-revert; jj makes conflicts first-class but a "feature" is only a transient query; Darcs/Pijul have commutative patches with dependency closure but at the *line* level with no grouping primitive and human conflict resolution; FOSD/feature-flags name composable features but at build/runtime, not in VCS, and the feature-interaction problem is unsolved. In parallel, agentic coding fans out work "by vibe" and merges "by hope" — CodeCRDT shows 100% syntactic convergence but 5–80% *semantic* conflict, because convergence ≠ correctness.

The author's EICO research (`/Users/ryanyen2/repos/ml-intern/eico`, an adjacent repo) is a deterministic engine that co-applies parallel agent edits **only when they commute and are invariant-confluent**, driving semantic-error to 0 by construction on a structured config language and on real Python AST. semi-git is the user-facing product that turns that engine into a way to manage a codebase semantically.

## Key Decisions

- **Manage versions semantically, not by diffs (the north star).** The user-facing unit is a feature/concept node; diffs are machinery beneath.
- **Intent-first, effects-native (B→C).** Intent is the source of truth; code is derived/compiled from intent; the internal versioned object is the typed **effect-bundle** per node. Adopting an *existing* repo (reverse-distilling intent from code) is deferred; v1 starts from nothing.
- **EICO is the universal mutation gate, not just a merge step.** New feature, fix, re-organization, and iteration all flow through one invariant-confluence check. One safety property everywhere.
- **A feature node = a named bundle of typed effects (intent + invariants) that materializes as git commits.** This unifies fan-out, land, and revert into "compose/decompose effect-bundles."
- **The graph is system-distilled and user-corrected.** An intake classifier routes the messy prompt stream; a gardener agent continuously splits/merges/relabels. The graph is *living* — neither a fixed set nor naively one-node-per-prompt. The correction surface is the product's trust anchor.
- **Re-derivation on graph change is gated by confluence.** Correcting or re-gardening the graph may recompile from intent, but EICO only applies the new derivation if it is invariant-confluent with the existing artifact; otherwise it quarantines and asks.
- **Two-tier command surface.** A freeform `sgt "..."` front door (classifier-routed) plus explicit graph verbs (`revert`, `modify`, `switch`, `split`, `merge`, `show`) whose argument is a fuzzy-to-exact node ref the resolver matches, disambiguating on collision. The verb declares the operation so the classifier never guesses intent; only the target is resolved.
- **semi-git owns the orchestration agents; it delegates coding.** semi-git provides only the **classification/intake** agent and the **tree-manipulation/gardener** agent (both designed to be RL-trainable later, building on EICO's learned planner). Actual code-writing is delegated to **external coding agents** (Claude Code, Codex, Gemini, and others) through a pluggable **coding-agent adapter** (MCP server and/or plugin protocol). semi-git is a substrate, not a coder.

## Actors

- A1. **Developer** — issues freeform prompts and explicit graph verbs; reviews quarantines; corrects/re-gardens the graph.
- A2. **Intake/classifier agent** (semi-git-owned) — routes each prompt into a lane (new capability / refine / fix / infra / explore / question) and resolves fuzzy node refs.
- A3. **Gardener/tree-manipulation agent** (semi-git-owned) — maintains the graph: split, merge, relabel, distill effect-bundles into nodes.
- A4. **EICO engine** (semi-git-owned, deterministic) — the confluence gate: decides which effect-bundles co-apply, computes dependency closure, certifies invariants.
- A5. **Coding-agent adapter** (semi-git-owned) — normalizes one or more external coding agents behind a single dispatch/return contract.
- A6. **External coding agent** (pluggable, e.g. Claude Code / Codex / Gemini) — receives a scoped task + effect-region, edits the repo, returns work.

## Key Flows

- F1. **Departure from nothing.** `sgt init` creates `.sgt/` (semantic graph + invariant set) and binds a fresh git repo. semi-git becomes the front door to the repo.
- F2. **Prompt → graph.** A freeform prompt enters; A2 classifies it; for capability-bearing prompts the system distills a provisional sub-graph and (for high-stakes/large work) shows it before dispatching.
- F3. **Fan-out → land.** A4 partitions work into coordination-free effect-sets; A5 dispatches each to an A6 backend; returned work is co-applied if confluent; non-confluent effects are quarantined.
- F4. **Quarantine → reconcile.** A quarantined conflict becomes a first-class node carrying an invariant-violation witness; a reconciliation attempt rewrites the later effect to commute; on success it lands, otherwise it escalates to A1.
- F5. **Iterate.** `sgt modify <ref> "..."` (or a refine-lane prompt) amends a node's intent and re-derives; mid-flight dependency discovery may spawn new concept nodes; the result is confluence-gated.
- F6. **Revert by closure.** `sgt revert <ref>` removes a node's effect-bundle; the closure check garbage-collects dependencies that drop to zero references; EICO verifies the remaining artifact is still invariant-valid before finalizing.
- F7. **Correct / re-garden.** The developer (or A3) reassigns effects, splits, or merges nodes; changes are confluence-gated like any other mutation.

## Requirements

### Semantic model & `.sgt`

- R1. `.sgt/` holds the semantic graph (nodes + edges), each node's intent, its typed effect-bundle, and the invariant set; the graph must remain a DAG at all times.
- R2. Each node carries a **kind**: capability, concept/subsystem, infrastructure, fix, or exploration. Capabilities may depend on concepts; fixes attach to a target node; explorations are ephemeral until promoted.
- R3. Each node maps to the underlying git commit(s) that materialize its effect-bundle, with a persistent identity that survives rebase/amend (Change-Id-style).
- R4. Reverting or toggling a node operates at the **effect level**, not the commit level, so a node whose git commits were entangled with another's can still be cleanly removed by re-materializing the remainder.
- R5. The system detects out-of-band git changes (commits made outside `sgt`) and reconciles them — distilling orphan commits into nodes or flagging them — so the graph never silently drifts from git.

### Command surface

- R6. A freeform `sgt "..."` entry point routes through the intake classifier.
- R7. Explicit graph verbs (`revert`, `modify`, `switch`, `split`, `merge`, `show`, plus `graph`/inspection) declare the operation; the classifier does not infer operation type for these.
- R8. A node-ref argument resolves across a fuzzy-to-exact spectrum: fuzzy phrase, node name, or exact node/commit id. On multi-match the system disambiguates; on zero-match it offers to create or asks.
- R9. Exact-id forms (e.g., `revert --to <id>`) bypass the classifier/resolver entirely for deterministic control.

### Intake & distillation

- R10. The classifier routes each prompt into one lane: new capability, refinement of an existing node, fix (revises a target), infrastructure, exploration, or pure question (not versioned).
- R11. Prompts are never assumed to be 1:1 with nodes; clarifications and refinements amend existing nodes rather than create new ones.
- R12. When classifier confidence is low or a prompt is genuinely ambiguous between new-vs-modify, the system asks rather than guessing.
- R13. The gardener continuously maintains the graph (split/merge/relabel) so structure tracks the codebase's true feature shape; the feature set expands on demand, never fixed up front.

### Execution & merge (EICO)

- R14. Work is fanned out by **effect-partition** into coordination-free sets, not by free assignment, so backends are given regions unlikely to collide by construction.
- R15. Returned edits are co-applied only when they commute and preserve invariants (type-validity, reference integrity, uniqueness, value-consistency); the maximal confluent set lands, the rest is held back.
- R16. A held-back conflict becomes a durable `unmergeable` node with a human-readable witness; the system attempts an automatic rewrite-to-commute before escalating.
- R17. Every mutation — feature, fix, re-garden, iterate, revert — passes through the same confluence gate; nothing lands ungated.

### Feature lifecycle algebra

- R18. `revert` removes a node by dependency closure and reference-counted GC; only nodes that drop to zero references are collected.
- R19. `modify` (iterate) amends a node's intent and re-derives the confluent delta; dependencies discovered mid-derivation spawn new nodes and reshape the graph.
- R20. `switch <ref> on|off` suspends/restores a node (relaxing/reinstating its invariants and recomputing dependents) without deleting it; the graph is append-only.
- R21. Re-derivation triggered by a graph correction is confluence-gated: working code changes only if the new derivation is invariant-confluent, else it quarantines with the conflicting witness and offers a cascade/override path.

### Coding-agent delegation

- R22. semi-git defines a **coding-agent adapter** contract: dispatch a scoped task (intent + target effect-region + repo state) and receive completed work, normalized across heterogeneous backends.
- R23. At least one backend is wired in v1 via MCP and/or a plugin protocol; the backend set is pluggable (Claude Code, Codex, Gemini, others) and not hard-coded.
- R24. The adapter extracts **typed effects** from a backend's returned work — preferably via structured edit output, otherwise by AST-diffing the returned patch — so EICO has effects to reason over. When effects cannot be extracted, the whole diff is treated as one opaque, conservatively-gated effect.
- R25. The adapter detects when a backend edited **outside its assigned effect-region** (scope violation) and quarantines or rejects the out-of-scope portion.
- R26. Backend failures (timeout, partial work, malformed output) are handled gracefully: retry, mark the node incomplete, or quarantine — never a silent corrupt land.
- R27. The classification and tree-manipulation agents are architected so their policies can later be **RL-trained** (learned router / learned planner), with v1 using LLM-prompted policies as the baseline.

## Acceptance Examples

- AE1. **Covers R8.** Given two nodes match `"rate limiting"`, when the developer runs `sgt revert "rate limiting"`, then the system lists both candidates and asks which, rather than reverting one by guess.
- AE2. **Covers R18.** Given `concept:api-keys` was introduced only for `capability:rate-limit`, when rate-limit is reverted, then api-keys is GC'd too; given a `dashboard` node also uses api-keys, then api-keys is retained and only rate-limit's own effects are removed.
- AE3. **Covers R15, R16.** Given two backends both wrap the redirect handler in order-sensitive ways, when their edits return, then the commuting parts land and the ordering conflict is quarantined with a witness ("order matters: rate-limit must precede analytics"); a rewrite-to-commute is attempted before escalation.
- AE4. **Covers R21.** Given the developer re-gardens by splitting a node, when re-derivation would alter working code non-confluently, then the change is quarantined (not silently applied) and the developer is offered cascade or override.
- AE5. **Covers R24.** Given a backend returns a raw diff with no structured edits, when the adapter cannot AST-parse part of it into typed effects, then that part is treated as one opaque effect and gated conservatively rather than dropped.
- AE6. **Covers R25.** Given a backend was scoped to the redirect handler but its patch also edits the data model, when the work returns, then the out-of-scope model edit is quarantined/rejected and surfaced.
- AE7. **Covers R5.** Given a developer ran `git commit` directly outside `sgt`, when `sgt` next inspects the repo, then the orphan commit is detected and either distilled into a node or flagged — the graph is not silently inconsistent with git.

## Scope Boundaries

### In scope (v1)
The semantic-version-management core: `.sgt` graph + effect-bundles, intake classification, system-distilled graph with user correction, EICO as the universal confluence gate, the two-tier command surface, the lifecycle algebra (revert/modify/switch), and a coding-agent adapter with at least one backend wired.

### Deferred for later
- Adopting an existing repo (reverse-distilling intent from code).
- Multiverse / explore-with-a-measure (scored superposition, collapse-not-merge) — exploration lane exists but scoring/collapse is later.
- The compounding confluence corpus (learned per-repo commute/dependency priors).
- RL training of the classification and tree-manipulation policies (architecture is RL-ready; training is later).
- Multi-developer / collaborative concurrent use.

### Outside this product's identity
- Being a code-writing agent itself — semi-git orchestrates and gates; it does not author code.
- Replacing git — git remains the materialization substrate beneath `.sgt`.
- A runtime feature-flag system — toggling is a VCS-level operation, not a runtime `if`.

## Dependencies & Assumptions

- Depends on the EICO engine (adjacent repo) as the confluence/invariant substrate; v1 may need a reduced effect model for real code.
- Assumes external coding agents expose a headless/non-interactive invocation usable by an adapter (to be confirmed by research).
- **Load-bearing assumption / primary risk:** typed effects can be extracted from arbitrary real-code edits richly enough that confluence remains a guarantee rather than degrading to a heuristic. EICO is proven only on a structured config language + Python AST; broad real-code coverage is the open research problem the thesis rides on.

## Outstanding Questions

### Resolve before planning
- None blocking — scope and architecture are pinned.

### Deferred to planning / implementation
- Which backend to wire first, and whether the adapter speaks MCP, a subprocess/CLI contract, or both (pending research).
- The concrete effect-extraction strategy for real code (structured-output tool calls vs. post-hoc AST diffing) and the fallback granularity.
- The minimal invariant set for v1 real-code support, and how non-effect-extractable edits are conservatively gated.
- Exact graph-verb names and the `.sgt` on-disk representation.

## Sources / Research

- EICO research + findings: `/Users/ryanyen2/repos/ml-intern/eico` (`DESIGN.md`, `FINDINGS.md`).
- Prior art (from ideation): Graphite (stacked diffs), Jujutsu (op log / first-class conflicts), Darcs/Pijul (patch commutation + dependency closure), FOSD / feature flags, CodeCRDT (arXiv:2510.18893), Gerrit Change-Id/topics.
- Ideation artifact: `docs/ideation/2026-06-17-semi-git-ideation.md`.
- External research on MCP + driving coding agents programmatically — in progress at planning time.
