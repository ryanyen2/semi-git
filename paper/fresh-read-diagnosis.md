# Fresh-Read Structural Diagnosis

Read performed 2026-08-20 by a reader who has never seen this project before.

---

## The thesis as I understood it (one sentence)

AI agents have broken the assumption that developers hold a decomposition of their code, making function-granularity version control newly justified; sgt attempts this but its write operations are not yet stable (50% per-session failure), so the read layer (attribution, preview, dependency display) should ship alone and the write layer should be deferred until comprehension-based trust is established.

---

## The argument arc (major moves)

1. **Intro**: AI agents changed who authors code; the developer no longer holds the decomposition version control demands.
2. **Scenarios (S3)**: Five operations a developer needs all take "a named set of edits" as argument, and recovering that set is no longer free.
3. **Design (S4)**: Four commitments (function grain, set-based versions, fork rule, inferred names) enable those operations, each with a stated price.
4. **Walkthrough (S5)**: The five operations work end-to-end on a curated example.
5. **Study design (S6)**: Three independent claims require three kinds of evidence; the decisive measure is undetected errors.
6. **Findings (S6b)**: [Placeholder -- no data yet.]
7. **Where the Design Breaks (S7)**: On its own repository, the tool's write operations fail at a 50% per-session rate, the fork rule blocks 27/28 external repos, identity tracking loses renames, and layout records account for 68% of violations.
8. **Pilot (S7-pilot)**: Three pilots found 21 tool defects and 4 study-design defects; comprehension held on first contact, mutation did not survive; the participant's verdict bifurcated: "for reading, yes; for writing, no."
9. **Discussion synthesis (S7b)**: Traces each commitment to its cost, compares with alternatives (Darcs, Jujutsu, GitButler, sem), and argues that the read layer should ship before the write layer (five mechanisms from the trust/automation literature).
10. **Conclusion (S8)**: Restates the read/write split, three generalizable findings, and the temporal recommendation.

---

## Gaps: where the sentence-sequence skips or backtracks

### Gap 1: The paper's motivation is write operations; its conclusion is "don't ship write operations"

**Where it shows**: Sections 1 and 3 build urgency around five WRITE operations (revert, restore, switch, propose land, stacked review). By Section 7b the paper recommends deferring all of them. The reader who bought the problem in Section 1 arrives at a conclusion that says the solution doesn't work yet.

**Structural cause**: The paper inherited its framing from an earlier stage of the project when write operations were the pitch. The actual finding (read layer independently valuable) needs to be the framing from the start, but it isn't introduced until page ~35. The intro should set up the tension: "we built this for write operations; the evaluation showed the read layer is where the value lives; this paper reports both the attempt and what it taught us."

### Gap 2: The findings section (6b) is entirely placeholder

**Where it shows**: The argument promises that the study answers the central question (do developers detect more errors with sgt?). The answer is missing. Everything that follows -- Sections 7, 7-pilot, 7b -- proceeds as though the study either hasn't happened or its results don't matter.

**Structural cause**: The paper was written while the study was in progress. But the paper's structure treats Section 6b as load-bearing (Table 1's measures are the declared arbiter). The rest of the paper proceeds without that evidence, which means the argument's actual weight rests on the self-evaluation (S7) and the pilot (S7-pilot), not on the study. If the study data exists, it needs to go in. If it doesn't exist, the paper needs to be restructured so the argument doesn't depend on something the reader can't see.

### Gap 3: No transition between "the design works" (S5) and "the design breaks" (S7)

**Where it shows**: Section 5 ends with a curated walkthrough where everything works. Section 6 declares the study. Section 7 immediately opens with "50% per-session violation rate." The reader gets whiplash: the walkthrough just demonstrated a smooth experience; two pages later the tool fails on every other session.

**Structural cause**: The walkthrough is a hand-picked best case on a curated repository; the evaluation is randomised over real histories. There is no sentence that says "the walkthrough used a repository recorded live by its author; the evaluation tests what happens on repositories the tool has never seen." The missing transition is a single paragraph explaining why the walkthrough's success and the evaluation's failure are not contradictions.

### Gap 4: The "three safety properties" (S7b) appear deep in discussion without having been introduced

**Where it shows**: Section 7b introduces a tripartite framework (nothing removed / operates on what named / new work recordable) that turns out to be the real organising principle of all evaluation findings. But it appears on page ~38, after the reader has already processed 10 pages of scattered findings.

**Structural cause**: These three properties should be introduced in the study design or at the top of the evaluation section, then used to organise all subsequent reporting. Instead they are introduced retroactively. The reader who read sections 7 and 7-pilot had no framework for sorting the findings they encountered there.

### Gap 5: The "codebook was wrong" subsection (S7b) is disconnected

**Where it shows**: The section about evaluating intent recovery against transcripts (the codebook, contentless turns, the 88% continuation-token problem) appears inside the discussion synthesis. It addresses the second claim from Section 6 (grouping accuracy) but has no visible connection to what precedes or follows it in Section 7b.

**Structural cause**: This is a self-contained methodological finding about how to measure intent recovery. It belongs either in Section 6 (as part of the evaluation design, explaining why the metric is hard) or as a standalone subsection of Section 7. In Section 7b it interrupts the per-commitment cost accounting and breaks the discussion's flow.

---

## Redundancies: where the same claim appears twice without advancing

### Redundancy 1: "One request becomes 4-6 features"

Stated in: abstract, Section 4.4, Section 7 (findable-not-decomposable), Section 7b (inferred names cost), conclusion. Each occurrence re-establishes the same point (sub-intent not intent) with slightly different framing but no new evidence. The reader encounters this finding five times across the paper.

**Fix**: State it once with full evidence (probably in the evaluation section), then reference the number without re-arguing it.

### Redundancy 2: Bansal et al. on explanations increasing acceptance

Cited in: Section 4.5 (no model-in-the-loop), Section 6b (findings framing), Section 7-pilot (pilot thematic analysis), Section 7b (what would change our conclusion), Section 7b (comparison). The same conceptual point (previews can increase acceptance of wrong results) is argued from scratch each time.

**Fix**: Introduce the mechanism once (probably in the pilot, where it's grounded in an observation), then use it as shorthand elsewhere.

### Redundancy 3: The pilot's "comprehension held, mutation didn't" verdict

Stated identically in: Section 7-pilot paragraph header, Section 7-pilot thematic analysis, Section 7b (design implication), conclusion. The claim advances on its first and second appearances (data, then interpretation). The third and fourth just restate.

### Redundancy 4: The self-reporting failure pattern

The "silent success" or "green checkmark over corruption" pattern is described in: Section 7 (session rate, with the merge anecdote), Section 7-pilot (pilot findings, paragraph 2), Section 7b (three safety properties, property 2), Section 7b (design implication, fifth mechanism), conclusion. Each description is vivid and slightly different, but the conceptual content is the same: a tool whose output confirms a false state damages trust in a way crashes don't.

### Redundancy 5: The fork rule's 18x amplification and its consequences

Stated with full explanation in: Section 4.3 (design), Section 7 (session rate), Section 7b (per-commitment cost), conclusion. The number is the same each time; what advances is interpretation (from "this is what we chose" to "this is what it cost" to "this is why it should be loosened"). But the re-explanation of the mechanism is redundant.

---

## Places where I felt "wait, why?" or "so what?"

### 1. "Why should I trust the walkthrough?" (Section 5)

The walkthrough is presented without any caveat about its representativeness. It follows from the design section and reads as evidence that the design works. Only 15 pages later does the reader learn that this repository is essentially the only one where the write layer functions. A single sentence at the walkthrough's opening ("this repository was recorded live by its author and represents the best case the design achieves; Section 7 reports what happens elsewhere") would inoculate the reader.

### 2. "Why are there three evaluation/discussion sections?" (Sections 7, 7-pilot, 7b)

Section 7 (Where the Design Breaks) reports self-evaluation numbers. Section 7-pilot reports pilot findings. Section 7b (What the Design Bought and What It Cost) synthesises both. The boundaries between them are unclear: Section 7-pilot contains extensive thematic analysis with literature grounding that belongs in a discussion section; Section 7 contains interpretive claims (Muir's trust stages) that belong in discussion; Section 7b re-analyses the same data already reported in 7 and 7-pilot.

**Structural cause**: The paper was written incrementally (the self-evaluation existed first, then the pilot happened, then synthesis was needed). The three sections are temporal layers of the research process rather than logical divisions of an argument. A reader encountering them in sequence processes the same findings three times at increasing levels of interpretation, without being told that's the structure.

### 3. "What does the study actually contribute if findings are missing?" (Section 6b)

Section 6 devotes four pages to a study design whose results are placeholders. The design itself is interesting (pre-registered, counterbalanced, three task types, six measures). But a paper with an empty findings section creates a strange reading experience: the reader is asked to evaluate the quality of a design they will never see executed. If the study has not run, the paper's structure should not promise results it cannot deliver.

### 4. "Why is the comparison with alternatives in the discussion, not in related work?" (Section 7b)

The comparison table in Section 7b (Darcs, Jujutsu, GitButler, sem, sgt) makes arguments about design tradeoffs that would help the reader understand the design section. Placed after 20 pages of evaluation, it feels like an afterthought. The related work section discusses each alternative narratively but never draws the table; the discussion draws the table but assumes the reader remembers the narrative.

**Structural cause**: The comparison requires evaluation data (rebuild rates, refusal rates) that don't exist until Section 7. But the design-space framing (what each system gives up and gains) could live in related work with forward references to evaluation numbers.

### 5. "So what does this mean for a CHI reader?" (missing throughout)

The paper is deeply technical and self-evaluative. It reports what sgt does, how it fails, and what the failure teaches about design. What it rarely does is connect these findings to the broader HCI research programme. Questions a CHI reader would ask:
- What does this teach us about human-AI collaboration more generally?
- How does this change how we think about version control as a coordination mechanism?
- What design principles transfer to OTHER tools that mediate between developers and AI agents?

The "read before write" recommendation is the closest thing to a transferable principle, but it's buried in Section 7b rather than being the paper's headline contribution.

### 6. "Why the Rosch/basic-level discussion?" (Section 7b)

The argument that over-decomposition is a "level mismatch" (developers think at basic level, tool presents subordinate categories) is interesting but arrives very late and isn't set up by anything earlier. The Rosch citation appears for the first time on page ~42. If this is a key interpretation of the 4-6x over-decomposition finding, it should appear when that finding is first fully reported, not in a synthesis section.

### 7. "What happened with the study repositories?" (Section 7-pilot, subsection on validation)

The study repository validation subsection (end of pilot section) contains critical information -- both repos pass an 8/8 derivability gate, reconstruction is exact, four tangle cases are separated correctly -- but it reads like an appendix attached to the pilot rather than a contribution of its own. It's unclear whether this validates the tool or the study protocol.

---

## Summary of structural problems

The paper has a **motivation mismatch**: it is framed around write operations (revert, restore, switch) but its actual contribution is understanding why those operations fail and why the read layer is independently valuable. The conclusion says the write layer shipped too early; the introduction doesn't know that yet.

The paper has a **section-ordering problem**: evaluation material is split across three sections (7, 7-pilot, 7b) with substantial overlap in claims and literature. The reader processes findings at least twice before reaching synthesis.

The paper has a **missing-data problem**: the study findings (Section 6b) are placeholders. The argument's declared arbiter (undetected errors) has no result. Everything that follows operates without that evidence.

The paper has a **repetition problem**: five or six key findings (4-6x decomposition, silent-success pattern, comprehension/mutation split, 18x amplification, Bansal mechanism) are each re-argued from scratch 3-5 times across the paper rather than stated once and referenced.

The paper's **strongest original contribution** -- the "read before write" temporal recommendation with five supporting mechanisms from the automation-trust literature -- is structurally positioned as a discussion subsection rather than as a core claim. If this is what the paper is actually about (and the evaluation evidence suggests it is), it should be elevated to the thesis level and framed from the introduction.
