---
date: 2026-06-17
topic: semi-git
focus: semantic feature-level version control over git; agentic plan→fan-out→merge→revert
mode: elsewhere-software
---

# Ideation: semi-git (sgt) — semantic feature-level version control

## Topic Context
semi-git (`sgt`): a semantic, feature/concept-level version-control layer on top of git. Instead of
line-diffs and commits, users version-control FEATURES and CONCEPTS — plug a feature in/out, toggle a
concept, revert a feature cleanly without dependency breakage. A `.sgt` directory holds a semantic DAG
whose nodes map down to git commit hashes. Pipeline: `sgt plan "prompt"` decomposes a request into a DAG
of not-yet-done feature nodes; subagents implement branches in parallel with shared state; then
merge / revert / iterate. Specialized agents handle review, dependency resolution, and "smoothing out"
the git layer.

Author's open uncertainties: the exact command set; `.sgt` structure; how to iterate on an
already-merged feature; whether there's a fixed set of features; branch-as-merge vs branch-as-multiverse;
stash / explore-without-merging semantics; and the core doubt — "is this just planning agents / agentic
orchestration?"

### Grounding (prior art)
- **Graphite** — stacks branches, not features; auto-restack is the killer feature; no feature-level revert.
- **Jujutsu (jj)** — operation log, first-class conflicts, working-copy-as-commit, revsets; but "feature" is only a query, never a named node.
- **Darcs / Pijul** — patch commutation + dependency closure (closest formalism); LINE-level, no grouping primitive, humans resolve conflicts.
- **FOSD / feature flags** — named composable features but at build/runtime, not VCS; feature-interaction problem unsolved.
- **CodeCRDT (arXiv:2510.18893)** — 100% convergence, 0 merge failures, but 5–80% SEMANTIC conflict. Convergence ≠ correctness.
- **Agentic SWE tools** — strong single-task edits; NO feature-isolation or feature-revert primitive.
- **Gerrit Change-Id / topics** — persistent semantic identity surviving rebase; flat, not a DAG, no unit-revert.
- **EICO (author's own research)** — deterministic engine co-applying parallel agent edits ONLY when they commute AND are invariant-confluent → 0 semantic-error by construction; holds non-confluent edits back for serial resolution; learned planner finds max coordination-free batch. Invariants: type-validity, reference integrity, uniqueness, value-consistency. **The credible substrate for sgt's clean-merge/clean-revert/dependency-smoothing claims.**
- Cross-domain analogies: DB up/down migrations, circuit breakers, music stems, OS hotpatching, Lego interfaces, CRISPR guide-RNA specificity, tri-color GC, package-manager SAT/lockfiles.

## Topic Axes
1. Semantic model & `.sgt` structure
2. Plan authoring
3. Parallel execution & merge
4. Feature lifecycle algebra
5. Exploration / multiverse

## Ranked Ideas

### 1. The invariant lattice is the versioned root; `.sgt` is a projection over git
**Description:** Make the checkable semantic contract (EICO's invariants) the primary versioned object; the feature DAG is a derived view, and `.sgt` is projected from git commit-trailers (Gerrit Change-Id style) rather than a parallel database that can drift. A "feature" = a named delta to the invariant lattice. Code review collapses into invariant authoring.
**Axis:** Semantic model & `.sgt` structure
**Basis:** `direct:` EICO's four invariants + `external:` jj projects views over commits; Gerrit trailers survive rebase.
**Rationale:** The moat and the answer to "is it just orchestration?" — orchestration is the client; semi-git is the substrate the client cannot violate. No stateless orchestrator has a persistent, versioned, checkable semantic contract.
**Downsides:** Hardest schema question in the project ("feature" vs "concept" must be pinned); projection risks being lossy.
**Confidence:** 80% · **Complexity:** High · **Status:** Explored

### 2. `sgt plan` emits a constraint graph, not a pre-committed DAG
**Description:** Plan outputs constraints (requires / conflicts-with / depends-on + per-node interface); the DAG is solved from them and reshapes as subagents discover real dependencies. Plan is a replayable, versioned artifact.
**Axis:** Plan authoring
**Basis:** `external:` Darcs/Pijul compute dependency closure rather than declaring it + `reasoned:` fixing a constraint (text) is orders of magnitude cheaper than fixing merged code.
**Rationale:** Kills the costliest agentic failure (confidently-wrong upfront decomposition) at the cheapest moment; lets the plan absorb mid-flight discoveries instead of breaking.
**Downsides:** Solving constraint graphs into concrete DAGs is real engineering; under-constrained plans degenerate.
**Confidence:** 70% · **Complexity:** High · **Status:** Unexplored

### 3. There is no merge — landing is a continuous confluence property; conflicts are first-class quarantine nodes
**Description:** EICO as a continuous classifier: edits land the instant they commute + preserve invariants; non-confluent edits become durable `unmergeable` DAG nodes with their invariant-violation witness. A reconciliation agent first tries to rewrite the later edit to commute before escalating to a human.
**Axis:** Parallel execution & merge
**Basis:** `direct:` EICO hold-back + `external:` jj/Pijul first-class conflicts.
**Rationale:** Kills textual conflict markers that don't matter and surfaces the semantic conflicts that do (CodeCRDT's 5–80%); human reviews a ranked list of genuine disagreements, not a diff.
**Downsides:** Big mental-model shift; users may distrust auto-landing without a strong audit surface.
**Confidence:** 75% · **Complexity:** High · **Status:** Unexplored

### 4. Feature lifecycle as a closed algebra over regenerable, reference-counted nodes
**Description:** One mechanism, three faces — revert = GC by dependency-closure (refcount-0, CRISPR-style specificity); iterate-on-merged = amend the node's stored intent and re-derive the confluent delta; toggle = suspend (relax invariants, dependents recompute), not delete; append-only DAG. Toggling a set on together is a satisfiability solve returning the minimal conflicting subset.
**Axis:** Feature lifecycle algebra
**Basis:** `external:` Pijul closure, Gerrit identity, tri-color GC, package-manager SAT + `direct:` EICO reference-integrity.
**Rationale:** Retires three stated uncertainties at once (revert cleanly, iterate on merged, fixed set of features). Revert and iterate become the same operation viewed from different directions.
**Downsides:** Requires deterministic-enough re-derivation (EICO core supports this; pure-LLM re-runs would break reproducibility); storing intent + recipe per node is heavy.
**Confidence:** 75% · **Complexity:** High · **Status:** Unexplored

### 5. Multiverse with a measure: scored superposition, "collapse" replaces merge
**Description:** Explore holds N variants of one node as a scored superposition (oracle-pass, invariant-distance, cost); collapse picks/blends under the measure — confluent worlds blend, non-confluent force a choice. Losing variants become planner training signal + hot-swappable fallbacks. Exploration is ephemeral-by-default; only promotion writes to `.sgt`.
**Axis:** Exploration / multiverse
**Basis:** `external:` jj op-log / disposable commits + `reasoned:` premature commitment is the true cost of exploration; a multiverse without a metric is just abandoned branches.
**Rationale:** Gives the author's "branch-as-multiverse, sometimes don't merge" instinct formal teeth: explore-vs-merge becomes a lifetime (ephemeral vs promoted) plus a measure (which world wins).
**Downsides:** Defining a trustworthy per-variant measure is hard; superposition state can balloon.
**Confidence:** 65% · **Complexity:** Medium-High · **Status:** Unexplored

### 6. The confluence corpus: per-repo commute / dependency / invariant knowledge that compounds
**Description:** Every merge/conflict/toggle writes edges into a persistent corpus (which edits commute, which features conflict, harvested invariants). The planner queries it, so coordination-free batches grow larger and conflict-prediction sharpens the longer sgt lives in a repo.
**Axis:** Parallel execution & merge (compounding)
**Basis:** `reasoned:` Pijul computes closure per-op and discards it; persisting it across the DAG's life is the leverage + `direct:` EICO's learned planner is the consumer.
**Rationale:** The durable moat over Graphite and stateless agent swarms — the tool measurably improves with use; the feature-interaction problem becomes accumulated knowledge instead of a per-run guess.
**Downsides:** Cold-start value is low; corpus can encode stale priors as the codebase evolves.
**Confidence:** 70% · **Complexity:** Medium · **Status:** Unexplored

## Rejection Summary

| # | Idea | Reason Rejected |
|---|------|-----------------|
| 1 | Trust/audit ledger (review exceptions, not whole diff) | Folded into #1 + #3 |
| 2 | One-command CLI / verbs as outputs of a closed algebra | Folded into #4 (UX corollary) |
| 3 | Feature-set bisect, BOM recall, keystone analysis | Downstream payoffs of node→commit mapping; defer |
| 4 | Edge interface contracts | Folded into #2 (constraint graph carries interfaces) |
| 5 | Lockfile/SAT "can these coexist + minimal unsat core" | Folded into #4 as the toggle-satisfiability mechanism |
| 6 | Telecom DFC mediation; double-entry provenance; portable "feature crystals" | Interesting brainstorm variants, not core thesis; defer |
| 7 | "Codebase the user never sees" / semantic-diff-only UI | Provocative UX bet downstream of #1; defer |
