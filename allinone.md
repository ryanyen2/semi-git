# Paper search report — intent-aligned clustering from conversation history

Queries run 2026-07-31 for the sgt intent-ledger design (mapping user prompts to code changes, intent trees, session continuity). Six focused queries, each across arXiv, Crossref, Semantic Scholar, OpenAlex, DBLP, OpenReview, plus a model-knowledge pass. (Replaces the 2026-07 clustering-perf scan, preserved in git history at 469aaa4.)

**Source errors (surfaced verbatim, not hidden):**
- OpenAlex: `504 Server Error: Gateway Timeout for url: https://api.openalex.org/works?...` on 5 of 6 queries (returned results only for the memory query).
- DBLP: `500 Server Error: Internal Server Error` on the memory query; 0 results elsewhere.
- OpenReview: 0 results on all queries (openreview-py installed via uv; likely auth/index limitation).
- Semantic Scholar: heavy rate limiting ("Rate limited. Waiting 3 seconds..." loops); results returned after backoff but two queries got only 1 paper.
- First run used the repo venv python which lacks `requests`; all non-arXiv sources silently degraded. Re-run via `uv run --with requests --with openreview-py`.

---

## Query A — "intent alignment between developer prompts and code changes AI coding assistant" (2022–2026)

### arXiv (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](http://arxiv.org/abs/2603.18976v1) | Evaluating 5W3H Structured Prompting for Intent Alignment in Human-AI Interaction | 2026 | arXiv | 0 |
| [2](http://arxiv.org/abs/2603.03074v1) | Design Generative AI for Practitioners: Exploring Interaction Approaches Aligned with Creative Practice | 2026 | arXiv | 0 |
| [3](http://arxiv.org/abs/2601.16513v1) | Competing Visions of Ethical AI: A Case Study of OpenAI | 2026 | arXiv | 0 |
| [4](http://arxiv.org/abs/2401.10065v3) | Code Prompting Elicits Conditional Reasoning Abilities in Text+Code LLMs | 2024 | arXiv | 0 |
| [5](http://arxiv.org/abs/2504.13903v1) | From Teacher to Colleague: How Coding Experience Shapes Developer Perceptions of AI Tools | 2025 | arXiv | 0 |
| [6](http://arxiv.org/abs/2503.16491v1) | The Impact of Generative AI Coding Assistants on Developers Who Are Visually Impaired | 2025 | arXiv | 0 |
| [7](http://arxiv.org/abs/2506.11022v2) | Security Degradation in Iterative AI Code Generation | 2025 | arXiv | 0 |
| [8](http://arxiv.org/abs/2506.01604v1) | Exploring Prompt Patterns in AI-Assisted Code Generation: Towards Faster and More Effective Developer-AI Collaboration | 2025 | arXiv | 0 |

### Crossref (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.22214/ijraset.2025.73682) | AI Code Review Assistant: A Modern Web Based Solution for Automated Code Analysis | 2025 | IJRASET | 0 |
| [2](https://doi.org/10.62970/ijirct.v12.i3.2606010) | Automated Context Generation for AI Code Assistants: An LLM-Powered Framework for Developer Intent Capture and Documentation Automation | 2026 | IJIRCT | 0 |
| [3](https://doi.org/10.2139/ssrn.7196799) | Analyzing the Gap Between Human Intent, Prompt Formulation, and Final Code Output in AI-Assisted Programming | — | SSRN | 0 |
| [4](https://doi.org/10.28945/5362) | Coding with AI as an Assistant: Can AI Generate Concise Computer Code? | 2024 | JITE:IIP | 2 |
| [5](https://doi.org/10.55041/ijsrem63072) | Smart Developer Assistant (SDA): An AI-Driven Multi-Agent Framework | 2026 | IJSREM | 0 |
| [6](https://doi.org/10.59350/qd4h2-1a990) | How AI Can Help Transform Developer Productivity Through Code Assistants | — | — | 0 |
| [7](https://doi.org/10.59350/wxbdd-nfr76) | How AI Can Help Transform Developer Productivity Through Code Assistants (dup) | — | — | 0 |
| [8](https://doi.org/10.58806/ijmir.2026.v3i6n03) | Skill-Augmented AI Coding Agents: A Two-Layer Framework for SKILL.md Design | 2026 | IJMIR | 0 |

### Semantic Scholar (1 paper)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://www.semanticscholar.org/paper/36c602bd7b75848316949ada34ad6f416d560d8c) | Developer Interaction Patterns with Proactive AI: A Five-Day Field Study | 2026 | IUI | 5 |

### OpenAlex (0 papers) — 504 gateway timeout. DBLP (0 papers). OpenReview (0 papers).

---

## Query B — "commit untangling tangled code changes decomposition" (2018–2026)

### Semantic Scholar (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://www.semanticscholar.org/paper/e75feb4ec19704e4117c84d4e6c934dcc23386da) | LLM-Based Detection of Tangled Code Changes for Higher-Quality Method-Level Bug Datasets | 2025 | arXiv | 5 |
| [2](https://www.semanticscholar.org/paper/37b8fcd5adb2c5cc88b6dfe6e57d6c619c35af27) | Tangling Pull Requests: Curating a Commit Untangling Dataset from Merged PRs | 2026 | — | 0 |
| [3](https://www.semanticscholar.org/paper/0ec3e0546f043f2602c78a96ffb333dfde9c8188) | **Atomizer: An LLM-based Collaborative Multi-Agent Framework for Intent-Driven Commit Untangling** | 2026 | arXiv | 4 |
| [4](https://www.semanticscholar.org/paper/27fa11f97213a0ad89dc194820e00cbb64ff9e8b) | CoRA: Decomposing and Describing Tangled Code Changes for Reviewer | 2019 | ASE | 36 |
| [5](https://www.semanticscholar.org/paper/a7826b678d84304f730a2269d80e63ab3391d188) | Visualizing a Tangled Change for Supporting Its Decomposition and Commit Construction | 2018 | COMPSAC | 10 |
| [6](https://www.semanticscholar.org/paper/5e874551b59251147ba1dd7522f3e42cdbc9053c) | LLM-Driven Collaborative Model for Untangling Commits via Explicit and Implicit Dependency Reasoning | 2025 | TOSEM | 2 |
| [7](https://www.semanticscholar.org/paper/fe97953bfc5daa10b5b7d126134dfc18d6e8978f) | **UTANGO: untangling commits with context-aware, graph-based, code change clustering learning model** | 2022 | ESEC/FSE | 27 |
| [8](https://www.semanticscholar.org/paper/d466552b7936400db486401732ab1f276e1d6f7f) | Code Change Intention, Development Artifact, and History Vulnerability: Putting Them Together for Vulnerability Fix Detection by LLM | 2025 | PACMSE | 16 |

### arXiv (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](http://arxiv.org/abs/2607.26730v1) | Tangling Pull Requests: Curating a Commit Untangling Dataset from Merged PRs | 2026 | arXiv | 0 |
| [2](http://arxiv.org/abs/2507.16395v3) | LLM-Driven Collaborative Model for Untangling Commits | 2025 | arXiv | 0 |
| [3](http://arxiv.org/abs/2601.21298v1) | Detecting Multiple Semantic Concerns in Tangled Code Commits | 2026 | arXiv | 0 |
| [4](http://arxiv.org/abs/2508.18535v1) | Tangling and Untangling Trees on Point-sets (off-topic: graph drawing) | 2025 | arXiv | 0 |
| [5](http://arxiv.org/abs/2505.08263v3) | LLM-Based Detection of Tangled Code Changes | 2025 | arXiv | 0 |
| [6](http://arxiv.org/abs/2504.01747v2) | The untangling number of 3-periodic tangles (off-topic: knot theory) | 2025 | arXiv | 0 |
| [7](http://arxiv.org/abs/2011.06244v4) | A Fine-grained Data Set and Analysis of Tangling in Bug Fixing Commits | 2020 | arXiv | 0 |
| [8](http://arxiv.org/abs/2003.14086v1) | ChangeBeadsThreader: An Interactive Environment for Tailoring Automatically Untangled Changes | 2020 | arXiv | 0 |

### Crossref (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.1109/compsac.2018.00018) | Visualizing a Tangled Change for Supporting Its Decomposition and Commit Construction | 2018 | COMPSAC | 8 |
| [2](https://doi.org/10.1109/ase.2019.00101) | CoRA: Decomposing and Describing Tangled Code Changes for Reviewer | 2019 | ASE | 20 |
| [3](https://doi.org/10.24963/ijcai.2019/552) | Commit Message Generation for Source Code Changes | 2019 | IJCAI | 68 |
| [4](https://doi.org/10.59350/9jdf6-z4z36) | Socially self-hosting source code with Tangled on Bluesky (off-topic) | — | — | 0 |
| [5](https://doi.org/10.59350/r80vb-7b441) | Socially self-hosting source code with Tangled on Bluesky (dup) | — | — | 12 |
| [6](https://doi.org/10.1109/icsme58944.2024.00038) | Compilation of Commit Changes Within Java Source Code Repositories | 2024 | ICSME | 0 |
| [7](https://doi.org/10.3102/ip.25.2194103) | Tangled in Code: Rope Weaving and Computational Thinking (off-topic) | 2025 | AERA | 0 |
| [8](https://doi.org/10.1109/icmla.2019.00096) | Feature Changes in Source Code for Commit Classification Into Maintenance Activities | 2019 | ICMLA | 10 |

### OpenAlex (0 papers) — 504. DBLP (0 papers). OpenReview (0 papers).

---

## Query C — "LLM agent memory conversation history retrieval software engineering" (2022–2026)

### Semantic Scholar (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://www.semanticscholar.org/paper/3a0401d72b2534e64fcb450fd9c0ffa4ef997040) | Structured Distillation for Personalized Agent Memory: 11x Token Reduction with Retrieval Preservation | 2026 | arXiv | 0 |
| [2](https://www.semanticscholar.org/paper/279af1143cecd6f6b2e43668284642da8ce58026) | IFCMemoryBench: Evaluating Long-Term Memory of LLM-Based Agents in BIM Information Retrieval | 2026 | — | 0 |
| [3](https://www.semanticscholar.org/paper/4f9de75b18c6bf659fb560e32f42e3e47b8b816c) | Accurate and Efficient Long-Term Memory for LLM Agents | 2026 | — | 0 |
| [4](https://www.semanticscholar.org/paper/fa6afa1b821c74ea098f81cd38de5abd04874143) | Active Context Compression: Autonomous Memory Management in LLM Agents | 2026 | arXiv | 13 |
| [5](https://www.semanticscholar.org/paper/7b87329d63df292a5102c017130b029f4df9b8d9) | Towards Structured, State-Aware, and Execution-Grounded Reasoning for Software Engineering Agents | 2026 | BoatSE@ICSE | 0 |
| [6](https://www.semanticscholar.org/paper/79a45b02e82b9f1e3252ec43b23df3d795c2d625) | SuperLocalMemory V3: Information-Geometric Foundations for Zero-LLM Enterprise Agent Memory | 2026 | arXiv | 1 |
| [7](https://www.semanticscholar.org/paper/507e2188a4c4c480afa3e626190eb57f1d5d9e1e) | Collaborative LLM Agents for End-to-End Software Development | 2026 | WWW | 0 |
| [8](https://www.semanticscholar.org/paper/e735128608be493b73022765c49c3f12c4c91591) | **Structurally Aligned Subtask-Level Memory for Software Engineering Agents** | 2026 | arXiv | 6 |

### OpenAlex (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.1609/aaaiss.v2i1.27688) | Memory Matters: The Need to Improve Long-Term Memory in LLM-Agents | 2024 | AAAI Symposium | 31 |
| [2](https://doi.org/10.18653/v1/2024.acl-long.747) | Evaluating Very Long-Term Conversational Memory of LLM Agents | 2024 | ACL | 51 |
| [3](https://doi.org/10.48550/arxiv.2512.20237) | MemR³: Memory Retrieval via Reflective Reasoning for LLM Agents | 2025 | arXiv | 0 |
| [4](https://openalex.org/W7117277506) | MemR³ (dup) | 2025 | arXiv | 0 |
| [5](https://doi.org/10.48550/arxiv.2502.12110) | **A-MEM: Agentic Memory for LLM Agents** | 2025 | arXiv | 9 |
| [6](https://doi.org/10.18653/v1/2026.findings-acl.1835) | LiCoMemory: Lightweight and Cognitive Agentic Memory for Efficient Long-Term Reasoning | 2026 | Findings of ACL | 0 |
| [7](https://doi.org/10.48550/arxiv.2401.02777) | From LLM to Conversational Agent: A Memory Enhanced Architecture with Fine-Tuning | 2024 | arXiv | 6 |
| [8](https://doi.org/10.48550/arxiv.2507.22925) | Hierarchical Memory for High-Efficiency Long-Term Reasoning in LLM Agents | 2025 | arXiv | 1 |

### Crossref (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.1109/asew67777.2025.00062) | Multi-agent systems for improved information retrieval | 2025 | ASEW | 1 |
| [2](https://doi.org/10.1016/j.infsof.2026.108078) | DevNous: An LLM-based multi-agent system for grounding IT project management in unstructured conversation | 2026 | IST | 0 |
| [3](https://doi.org/10.1145/3696630.3728717) | Facilitating Trustworthy Human-Agent Collaboration in LLM-based Multi-Agent System oriented Software Engineering | 2025 | FSE | 10 |
| [4](https://doi.org/10.5220/0014473600004052) | Agent-as-a-Graph: Knowledge Graph-Based Tool and Agent Retrieval | 2026 | ICAART | 1 |
| [5](https://doi.org/10.2139/ssrn.7160321) | Does Memory Credit Travel? Paired Factorial Audits of LLM-Agent Memory | — | SSRN | 0 |
| [6](https://doi.org/10.38071/2026-00397-5) | Developing and Evaluating an LLM-based Agent for ExplorViz Using CopilotKit | 2026 | Kiel SE Research | 0 |
| [7](https://doi.org/10.3390/software5020026) | Iterative Audit Convergence in LLM-Managed Multi-Agent Systems | 2026 | Software | 0 |
| [8](https://doi.org/10.2139/ssrn.7193639) | Multi-Artifact Versioning for LLM-Agent-Based Software | — | SSRN | 0 |

### arXiv (8 papers — query matched poorly, mostly off-topic SE meta-research)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](http://arxiv.org/abs/2406.04710v2) | Morescient GAI for Software Engineering | 2024 | arXiv | 0 |
| [2](http://arxiv.org/abs/2501.03569v1) | What Does a Software Engineer Look Like? (off-topic) | 2025 | arXiv | 0 |
| [3](http://arxiv.org/abs/2311.03374v1) | Generative AI for Software Metadata: IRSE Track at FIRE 2023 | 2023 | arXiv | 0 |
| [4](http://arxiv.org/abs/2406.07737v2) | The Future of AI-Driven Software Engineering | 2024 | arXiv | 0 |
| [5](http://arxiv.org/abs/2204.06033v1) | Text and Team: Article Metadata and Citations (off-topic) | 2022 | arXiv | 0 |
| [6](http://arxiv.org/abs/2406.04780v1) | Software Engineering for Collective Cyber-Physical Ecosystems (off-topic) | 2024 | arXiv | 0 |
| [7](http://arxiv.org/abs/2308.05381v4) | V-Model in Building ML-Enabled Software (off-topic) | 2023 | arXiv | 0 |
| [8](http://arxiv.org/abs/2602.08015v1) | Bridging the Gap: Evidence to Decision Frameworks (off-topic) | 2026 | arXiv | 0 |

### DBLP (0 papers) — 500 server error. OpenReview (0 papers).

---

## Query D — "hierarchical task decomposition LLM agent planning code generation" (2020–2026)

### Semantic Scholar (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://www.semanticscholar.org/paper/3b35ac696b7587cb7c24f0d5b00f1a9107364533) | AutoP2C: An LLM-Based Agent Framework for Code Repository Generation | 2025 | arXiv | 21 |
| [2](https://www.semanticscholar.org/paper/adfb2a3dd1d4913fb68f057a0b92c4aa224adcea) | **ReAcTree: Hierarchical LLM Agent Trees with Control Flow for Long-Horizon Task Planning** | 2025 | AAMAS | 7 |
| [3](https://www.semanticscholar.org/paper/a7875733fe7638b513ad6f0e42f75fd10f287d9c) | TDAG: A Multi-Agent Framework based on Dynamic Task Decomposition and Agent Generation | 2024 | Neural Networks | 61 |
| [4](https://www.semanticscholar.org/paper/ca7830d57999480fd403a94d63eea380dac4d851) | HIPIF: Hierarchical Planning and Information Folding for Long-Horizon LLM Agent Learning | 2026 | — | 1 |
| [5](https://www.semanticscholar.org/paper/356b85ae926b2a8b4cd794e10fe8f37891ebf8d7) | SagaLLM: Context Management, Validation, and Transaction Guarantees for Multi-Agent LLM Planning | 2025 | VLDB | 59 |
| [6](https://www.semanticscholar.org/paper/49be8c2931cae49c6dc1c636d2dfb15d4a01957e) | AdaCoder: An Adaptive Planning and Multi-Agent Framework for Function-Level Code Generation | 2025 | TSE | 8 |
| [7](https://www.semanticscholar.org/paper/21f54abab1f36e1c376226aeab7b4f4cdd1e3a78) | CodeTeam: An LLM-Powered Multi-Agent Framework for Repository-Level Code Generation | 2026 | — | 1 |
| [8](https://www.semanticscholar.org/paper/fac3e37670c6b58b5622f08d04f404d340fa380f) | CityEQA: A Hierarchical LLM Agent on Embodied QA (off-topic domain, relevant architecture) | 2025 | EMNLP | 37 |

### arXiv (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](http://arxiv.org/abs/2306.07353v1) | HDDL 2.1: Formalism and Semantics for Temporal HTN Planning | 2023 | arXiv | 0 |
| [2](http://arxiv.org/abs/2508.08322v1) | Context Engineering for Multi-Agent LLM Code Assistants | 2025 | arXiv | 0 |
| [3](http://arxiv.org/abs/2511.16787v1) | NALA_MAINZ at BLP-2025 Task 2: Multi-agent Bangla Instruction to Python | 2025 | arXiv | 0 |
| [4](http://arxiv.org/abs/2607.26977v1) | TREK: Travel Reasoning and Evaluation Kit (off-topic) | 2026 | arXiv | 0 |
| [5](http://arxiv.org/abs/2512.21309v2) | **A Plan Reuse Mechanism for LLM-Driven Agent** | 2025 | arXiv | 0 |
| [6](http://arxiv.org/abs/2309.08587v2) | Compositional Foundation Models for Hierarchical Planning | 2023 | arXiv | 0 |
| [7](http://arxiv.org/abs/2202.01385v1) | Hierarchical Deliberative-Reactive System Architecture for Task and Motion Planning | 2022 | arXiv | 0 |
| [8](http://arxiv.org/abs/2605.06957v1) | Learning and Reusing Policy Decompositions for Hierarchical Generalized Planning with LLM Agents | 2026 | arXiv | 0 |

### Crossref (8 papers — mostly Qeios micro-reviews, low signal)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.2139/ssrn.6506799) | LLM-Augmented Hierarchical Task Planning and Scheduling for Heterogeneous Multi-Agent Systems | — | SSRN | 0 |
| [2](https://doi.org/10.32388/p3nbe5) | Review of: DRC-Coder (peer-review note) | 2025 | Qeios | 0 |
| [3](https://doi.org/10.32388/z3cve7) | Review of: DRC-Coder (peer-review note) | 2025 | Qeios | 0 |
| [4](https://doi.org/10.32388/gaogr7) | Review of: DRC-Coder (peer-review note) | 2025 | Qeios | 0 |
| [5](https://doi.org/10.32388/1mmgbb) | Review of: DRC-Coder (peer-review note) | 2025 | Qeios | 0 |
| [6](https://doi.org/10.21203/rs.3.rs-10232223/v1) | Multi-Agent LLM Collaborative Reasoning and Task Planning | — | preprint | 0 |
| [7](https://doi.org/10.32388/yeymn9) | Review of: DRC-Coder (peer-review note) | 2025 | Qeios | 0 |
| [8](https://doi.org/10.20944/preprints202602.1841.v1) | An LLM-Agent Framework for Adaptive Task Decomposition and Continual Strategy Updating | — | preprint | 4 |

### OpenAlex (0 papers) — 504. DBLP (0 papers). OpenReview (0 papers).

---

## Query E — "provenance tracking AI-generated code linking prompts to code" (2022–2026)

### Crossref (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.2139/ssrn.5610993) | Auto-Generated AI Code Hallucinations: Detection, Impact, and Mitigation | — | SSRN | 1 |
| [2](https://doi.org/10.2139/ssrn.6810258) | AI-Generated and AI-Assisted Code Under EU Law: IP and Compliance | — | SSRN | 0 |
| [3](https://doi.org/10.51593/2023ca010) | Cybersecurity Risks of AI-Generated Code | 2024 | CSET | 2 |
| [4](https://doi.org/10.2139/ssrn.4979508) | Assessing Code Clone Detection of LLMs on Human and AI-Generated Code | — | SSRN | 0 |
| [5](https://doi.org/10.62441/nano-ntp.v20is11.9) | ChatGPT-Generated Code vs Kaggle Champion (off-topic) | 2024 | Nanotech. Perceptions | 0 |
| [6](https://doi.org/10.1109/issrew60843.2023.00060) | Poisoning Programs by Un-Repairing Code: Security Concerns of AI-generated Code | 2023 | ISSREW | 11 |
| [7](https://doi.org/10.3390/info15120819) | EX-CODE: A Robust and Explainable Model to Detect AI-Generated Code | 2024 | Information | 6 |
| [8](https://doi.org/10.5220/0013570300003964) | Enhancing AI-Generated Code Accuracy: Model-Based Reverse Engineering for Prompt Context Enrichment | 2025 | ICSOFT | 1 |

### Semantic Scholar (7 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://www.semanticscholar.org/paper/c43c11584b7f5b864656e7ab0f7e132d849df47b) | Teacher-Authored Prompts for Configuring Student-AI Dialogue (off-topic) | 2026 | arXiv | 1 |
| [2](https://www.semanticscholar.org/paper/75faf2aabd7f3dbc487ccadbf910631bc47fe24a) | **BonsAIDE: An Extended Vision for Human-AI Interaction in IDEs** | 2026 | TOSEM | 1 |
| [3](https://www.semanticscholar.org/paper/90c8760f993857089dec58a0d6e32ed4a600581a) | Policy-as-Prompt: Turning AI Governance Rules into Guardrails | 2025 | — | 9 |
| [4](https://www.semanticscholar.org/paper/e434a5c3ec8dd92ea944d07b5ae227492114655a) | Defending The AI-Powered Commerce Stack (off-topic) | 2025 | JICRCR | 0 |
| [5](https://www.semanticscholar.org/paper/327f2df80cccd89a10934eef495b06af949b0feb) | The LLMbda Calculus: AI Agents, Conversations, and Information Flow | 2026 | arXiv | 2 |
| [6](https://www.semanticscholar.org/paper/7d90c30d335e345b22786ed91e815a5fc950c5a9) | Social media distrust and turn to AI among Gen Z (off-topic) | 2026 | Frontiers in AI | 0 |
| [7](https://www.semanticscholar.org/paper/f6bc99088f51e29efb6af663a30ab26f74063309) | Weaponizing Words: Prompt Injection Attacks on LLM | 2025 | SIGCITE | 0 |

### arXiv (8 papers — keyword match drifted to AI-code security; kept for completeness)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](http://arxiv.org/abs/2401.10065v3) | Code Prompting Elicits Conditional Reasoning (dup of A) | 2024 | arXiv | 0 |
| [2](http://arxiv.org/abs/2506.11022v2) | Security Degradation in Iterative AI Code Generation (dup of A) | 2025 | arXiv | 0 |
| [3](http://arxiv.org/abs/2604.17587v1) | AIRA: AI-Induced Risk Audit for AI-Generated Code | 2026 | arXiv | 0 |
| [4](http://arxiv.org/abs/2605.28734v2) | Code as a Weapon: Prompt Bank for Malicious-Code Compliance | 2026 | arXiv | 0 |
| [5](http://arxiv.org/abs/2303.12869v1) | JaCoText: Pretrained Model for Java Code-Text Generation | 2023 | arXiv | 0 |
| [6](http://arxiv.org/abs/2412.09715v1) | Human vs. AI: Detection of Generated Images (off-topic) | 2024 | arXiv | 0 |
| [7](http://arxiv.org/abs/2409.04114v1) | Multi-Programming Language Ensemble for Code Generation | 2024 | arXiv | 0 |
| [8](http://arxiv.org/abs/2506.08790v1) | Do Generative AI Tools Ensure Green Code? | 2025 | arXiv | 0 |

### OpenAlex (0 papers) — 504. DBLP (0 papers). OpenReview (0 papers).

---

## Query F — "mining developer ChatGPT conversations linked to commits DevGPT" (2023–2026)

### Semantic Scholar (1 paper)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://www.semanticscholar.org/paper/def24fb1e977db69f4b1b866b807f9ab9bad5227) | **DevGPT: Studying Developer-ChatGPT Conversations** | 2023 | MSR | 68 |

### Crossref (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.1145/3643991.3648400) | DevGPT: Studying Developer-ChatGPT Conversations | 2024 | MSR | 44 |
| [2](https://doi.org/10.1145/3643991.3645075) | The role of library versions in Developer-ChatGPT conversations | 2024 | MSR | 0 |
| [3](https://doi.org/10.1145/3643991.3645078) | Chatting with AI: Deciphering Developer Conversations with ChatGPT | 2024 | MSR | 11 |
| [4](https://doi.org/10.1145/3643991.3645081) | How to refactor this code? Developer-ChatGPT refactoring conversations | 2024 | MSR | 27 |
| [5](https://doi.org/10.1145/3643991.3645082) | Analyzing Developer-ChatGPT Conversations for Software Refactoring | 2024 | MSR | 8 |
| [6](https://doi.org/10.5220/0014673100004018) | From Commits to Code Smells: Developer-Centric Visualization of Technical Debt | 2026 | ICEIS | 0 |
| [7](https://doi.org/10.1007/979-8-8688-0215-7_5) | Commits (book chapter, off-topic) | 2024 | Beginning Git and GitHub | 1 |
| [8](https://doi.org/10.1145/3643991.3645072) | **Analyzing Developer Use of ChatGPT Generated Code in Open Source GitHub Projects** | 2024 | MSR | 7 |

### arXiv (8 papers)

| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](http://arxiv.org/abs/2309.03914v2) | DevGPT: Studying Developer-ChatGPT Conversations | 2023 | arXiv | 0 |
| [2](http://arxiv.org/abs/2401.16340v1) | The role of library versions in Developer-ChatGPT conversations | 2024 | arXiv | 0 |
| [3](http://arxiv.org/abs/2402.16480v1) | **Unveiling ChatGPT's Usage in Open Source Projects: A Mining-based Study** | 2024 | arXiv | 0 |
| [4](http://arxiv.org/abs/2402.06013v1) | How to Refactor this Code? Developer-ChatGPT Refactoring Conversations | 2024 | arXiv | 0 |
| [5](http://arxiv.org/abs/2301.07597v1) | How Close is ChatGPT to Human Experts? (off-topic) | 2023 | arXiv | 0 |
| [6](http://arxiv.org/abs/2403.10468v1) | **An Empirical Study on Developers Shared Conversations with ChatGPT in GitHub PRs and Issues** | 2024 | arXiv | 0 |
| [7](http://arxiv.org/abs/2301.08745v4) | Is ChatGPT A Good Translator? (off-topic) | 2023 | arXiv | 0 |
| [8](http://arxiv.org/abs/2304.06488v1) | One Small Step for Generative AI: Survey on ChatGPT in AIGC Era (off-topic) | 2023 | arXiv | 0 |

### OpenAlex (0 papers) — 504. DBLP (0 papers). OpenReview (0 papers).

---

## Model Knowledge (10 papers, may include uncertain entries)

Deduplicated against all API results above. These are the "everyone cites these" papers the keyword APIs missed.

| # | Title | Year | Venue | Notes |
|---|-------|------|-------|-------|
| [1](https://scholar.google.com/scholar?q=The+Impact+of+Tangled+Code+Changes+Herzig+Zeller) | The Impact of Tangled Code Changes | 2013 | MSR | Foundational: showed tangled commits corrupt downstream mining — the original motivation for untangling |
| [2](https://scholar.google.com/scholar?q=Helping+Developers+Help+Themselves+Automatic+Decomposition+of+Code+Review+Changesets) | Helping Developers Help Themselves: Automatic Decomposition of Code Review Changesets (ClusterChanges) | 2015 | ICSE | Microsoft's deployed changeset partitioner using def-use relation graphs — direct ancestor of sgt-style clustering |
| [3](https://scholar.google.com/scholar?q=Untangling+Fine-Grained+Code+Changes+Dias+EpiceaUntangler) | Untangling Fine-Grained Code Changes (EpiceaUntangler) | 2015 | SANER | Untangles from *IDE-captured* fine-grained events rather than post-hoc diffs — the "capture at source" argument |
| [4](https://scholar.google.com/scholar?q=Generative+Agents+Interactive+Simulacra+of+Human+Behavior) | Generative Agents: Interactive Simulacra of Human Behavior | 2023 | UIST | Memory stream + reflection tree + recency×relevance×importance retrieval — the canonical two-layer memory design |
| [5](https://scholar.google.com/scholar?q=MemGPT+Towards+LLMs+as+Operating+Systems) | MemGPT: Towards LLMs as Operating Systems | 2023 | arXiv | Paged main/external memory; the case for explicit memory tiers |
| [6](https://scholar.google.com/scholar?q=Reflexion+Language+Agents+with+Verbal+Reinforcement+Learning) | Reflexion: Language Agents with Verbal Reinforcement Learning | 2023 | NeurIPS | Episodic self-feedback persisted across attempts — pattern for "unfulfilled intent" records |
| [7](https://scholar.google.com/scholar?q=Grounded+Copilot+How+Programmers+Interact+with+Code-Generating+Models) | Grounded Copilot: How Programmers Interact with Code-Generating Models | 2023 | OOPSLA | Acceleration vs exploration interaction modes — the same prompt text means different things in each; matters for intent distillation |
| [8](https://scholar.google.com/scholar?q=Meta-Manager+A+Tool+for+Collecting+and+Exploring+Meta+Information+about+Code) | Meta-Manager: A Tool for Collecting and Exploring Meta Information about Code | 2024 | CHI | Closest prior art to the whole idea: captures provenance of AI-generated code (incl. prompts) anchored to code ranges, queryable "why is this here" |
| [9](https://scholar.google.com/scholar?q=Voyager+An+Open-Ended+Embodied+Agent+with+Large+Language+Models) | Voyager: An Open-Ended Embodied Agent with Large Language Models | 2023 | arXiv | Skill library as growing, reusable memory — pattern for cross-session reuse of fulfilled intents |
| [10](https://scholar.google.com/scholar?q=Asking+and+Answering+Questions+during+a+Programming+Change+Task) | Asking and Answering Questions during a Programming Change Task | 2008 | TSE | Foundational taxonomy of the "why" questions developers actually ask during change tasks — the retrieval-side requirements |

---

# Summary of all searched results

## 1. Overview

Six queries (intent↔code alignment, commit untangling, agent memory, hierarchical decomposition, AI-code provenance, developer-ChatGPT conversation mining), 2018–2026, ~137 rows returned of which roughly 85 are distinct on-topic papers after removing duplicates and keyword-collision noise (knot theory, poetry corpora, graph drawing). The corpus covers exactly the four pillars an intent-ledger needs: how to decompose changes by intent, how to link conversations to code, how to structure long-term agent memory, and how to represent plans as trees.

## 2. Trends

- **Commit untangling pivoted from graphs to intent.** 2015–2022 work (ClusterChanges, EpiceaUntangler, UTANGO) clusters change atoms by def-use/context graphs; 2025–2026 work (Atomizer, the TOSEM collaborative-untangling model, "Detecting Multiple Semantic Concerns") explicitly recovers *developer intent* with LLMs and drives the partition with it. The field independently arrived at the thesis behind this design: structure alone under-determines the grouping; intent is the missing signal.
- **Conversation↔commit linking became a dataset problem in 2024.** MSR 2024 was dominated by DevGPT-derived studies. Their shared limitation: links exist only when developers voluntarily pasted ChatGPT share-links, and recovery is lossy. Post-hoc mining is the weak design; live capture at the tool boundary is the strong one.
- **Agent memory converged on two-layer designs in 2024–2026.** Raw episodic log + derived, consolidated semantic layer (A-MEM's Zettelkasten notes, hierarchical-memory papers, LiCoMemory, Structured Distillation). Retrieval is scored, not exact; consolidation is incremental, not a global rebuild.
- **SE-specific agent memory is brand new (2026).** Structurally Aligned Subtask-Level Memory argues memory units should align with code-structure subtasks — direct validation for keying intent memory to ops/features rather than free text.
- Venues: MSR for conversation mining, FSE/ASE/TOSEM for untangling, ACL/NeurIPS-orbit arXiv for memory, AAMAS/VLDB for planning.

## 3. Key themes

1. **Intent-driven change decomposition** — LLMs recover per-hunk intent then cluster by it (Atomizer B-3; TOSEM untangler B-6; UTANGO B-7 as the graph baseline).
2. **Conversation↔code linkage** — real developer-AI conversations mined against commits/PRs; messy, multi-turn, partially fulfilled (DevGPT F-1; Tufano mining study F-arXiv-3; Hao PR/issue study F-arXiv-6).
3. **Two-layer agent memory** — episodic stream + consolidated notes with typed links, scored retrieval (A-MEM C-OA-5; Hierarchical Memory C-OA-8; Generative Agents MK-4).
4. **Plan-as-tree with reuse** — hierarchical agent plans, checkpointed, validated, reused across tasks (ReAcTree D-2; TDAG D-3; SagaLLM D-5; Plan Reuse D-arXiv-5).
5. **Provenance capture at generation time** — anchor AI-generation metadata (incl. prompts) to code ranges in-editor (Meta-Manager MK-8; BonsAIDE E-S2-2; EpiceaUntangler MK-3).
6. **Fulfillment gap** — generated code diverges from what lands; intent completion is a spectrum, not a boolean (Grewal F-C-8; Security Degradation A-7; Reflexion MK-6).

## 4. Keywords frequency

| Keyword | Count |
|---------|-------|
| LLM / large language model | 41 |
| agent / multi-agent | 33 |
| memory / long-term memory | 16 |
| commit / code change / untangling | 15 |
| ChatGPT / developer conversation | 12 |

## 5. Most cited by accepted paper

| Rank | Title | Year | Citations |
|------|-------|------|-----------|
| 1 | DevGPT: Studying Developer-ChatGPT Conversations | 2023 | 68 |
| 2 | Commit Message Generation for Source Code Changes | 2019 | 68 |
| 3 | TDAG: Dynamic Task Decomposition and Agent Generation | 2024 | 61 |
| 4 | SagaLLM: Context Management, Validation, and Transaction Guarantees | 2025 | 59 |
| 5 | Evaluating Very Long-Term Conversational Memory of LLM Agents | 2024 | 51 |

## 6. Most cited by first author

| Rank | Author | Papers in set | Total citations |
|------|--------|---------------|-----------------|
| 1 | Tao Xiao | 1 | 68 |
| 2 | Shengbin Xu | 1 | 68 |
| 3 | Yaoxiang Wang | 1 | 61 |
| 4 | Edward Y. Chang | 1 | 59 |
| 5 | Adyasha Maharana | 1 | 51 |

## 7. Recommendations for reading

Ordered as a reading path, foundational → recent:

1. **DevGPT (MSR 2023/2024)** — ground truth on what real developer-AI conversations look like and why post-hoc prompt↔commit linking fails; motivates sgt's live-capture advantage.
2. **UTANGO (FSE 2022)** — the strongest pre-LLM change-clustering baseline (context-aware graph learning); establishes the graph substrate sgt already has.
3. **Atomizer (arXiv 2026)** — intent-driven commit untangling with multi-agent LLMs; the direct academic parallel to "cluster ops by user intent," and the paper to differentiate against (they *infer* intent from diffs; sgt can *record* it).
4. **A-MEM (arXiv 2025) + Generative Agents (UIST 2023)** — the two-layer memory architecture (episodic ledger + linked, consolidated notes; scored retrieval) that the intent store should copy rather than reinvent.
5. **Structurally Aligned Subtask-Level Memory for SE Agents (arXiv 2026)** — newest evidence that agent memory should be keyed to code-structure units, i.e. exactly ops/features, not free-floating text.
