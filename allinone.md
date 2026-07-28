# Literature scan: robust + incremental LLM/clustering pipeline for sgt

Three queries, mapped to the three levers the user named (algorithm / math / NLP-cache):

1. **Incremental dynamic community detection** — the "only re-cluster what changed" problem (maps to `tree._dirty_subdivide`).
2. **Temporal smoothness / evolutionary clustering** — reduce churn, stable communities over time (maps to Greene identity + checkpoints).
3. **Semantic caching for LLMs** — graded label-cache reuse instead of exact member-hash hit/miss (maps to `label.Labeler`).

Source caveats: OpenAlex unavailable (client type-hint bug on Python 3.9); DBLP 500/empty; query 3 largely returned KV-cache papers (wrong layer) — relevant semantic-cache hits flagged.

---

## Query 1 — Incremental / dynamic community detection (2018–2026)

### Semantic Scholar (10)
| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://www.semanticscholar.org/paper/511a5586780929b257aeab2b5d81d5c4ce115ab4) | Heuristic-based Dynamic Leiden Algorithm for Efficient Tracking of Communities on Evolving Graphs | 2024 | arXiv | 1 |
| [2](https://www.semanticscholar.org/paper/beec6b068654f9fe6f48f8527c53b05de225c721) | DF Louvain: Fast Incrementally Expanding Approach for Community Detection on Dynamic Graphs | 2024 | arXiv | 10 |
| [3](https://www.semanticscholar.org/paper/a61f1f51b1dcf390f1f39b30400d904fc641c87b) | A Starting Point for Dynamic Community Detection with Leiden Algorithm | 2024 | arXiv | 8 |
| [4](https://www.semanticscholar.org/paper/b53f5520415852331cf237570aec80abf5b151d0) | Shared-Memory Parallel Dynamic Louvain Algorithm | 2024 | IPDPSW | 1 |
| [5](https://www.semanticscholar.org/paper/e2991ef1860c797ce7c492faa54e10a9c7990faa) | Shared-Memory Parallel Algorithms for Community Detection in Dynamic Graphs | 2024 | IPDPSW | 5 |
| [6](https://www.semanticscholar.org/paper/17dd866d1f4922b6e1561a5309aa2bf620d9b98c) | Towards Dynamic Community Detection with Leiden Algorithm | 2025 | — | 2 |
| [7](https://www.semanticscholar.org/paper/555d7fb5aa51c10b2a8e3704bf51a6031a5248f5) | Efficient Tracking of Communities on Evolving Graphs with Leiden | 2026 | HPDC | 0 |
| [8](https://www.semanticscholar.org/paper/453bad4eb9b00260c94b702ce0046350d175d80f) | Incremental Similarity-Based Label Propagation for Dynamic Community Detection | 2026 | Concurrency & Computation | 0 |
| [9](https://www.semanticscholar.org/paper/724f131a6364d2370a8680955e749c25d2525755) | Maintaining Leiden Communities in Large Dynamic Graphs | 2026 | — | 0 |
| [10](https://www.semanticscholar.org/paper/1bd721d32dac11a1a2a7f29dac9755fbeb22ef10) | ECBR: Graph-Based Learning for Dynamic Community Detection | 2026 | MAKE | 0 |

### arXiv (10)
| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](http://arxiv.org/abs/2404.19634v4) | DF Louvain: Fast Incrementally Expanding Approach | 2024 | arXiv | 0 |
| [2](http://arxiv.org/abs/2410.15451v1) | Heuristic-based Dynamic Leiden Algorithm | 2024 | arXiv | 0 |
| [3](http://arxiv.org/abs/1810.08473v3) | From Louvain to Leiden: guaranteeing well-connected communities | 2018 | arXiv | — |
| [4](http://arxiv.org/abs/2601.08554v5) | Maintaining Leiden Communities in Large Dynamic Graphs | 2026 | arXiv | 0 |
| [5](http://arxiv.org/abs/2405.11658v4) | A Starting Point for Dynamic Community Detection with Leiden | 2024 | arXiv | 0 |
| [6](http://arxiv.org/abs/2509.03834v1) | From Leiden to Pleasure Island: CPM as a Hedonic Game | 2025 | arXiv | 0 |
| [7](http://arxiv.org/abs/2509.23411v1) | Hybrid Graph Embeddings and Louvain for Unsupervised Community Detection | 2025 | arXiv | 0 |
| [8](http://arxiv.org/abs/2601.12347v1) | RIPPLE++: Incremental Framework for GNN Inference on Evolving Graphs | 2026 | arXiv | 0 |
| [9](http://arxiv.org/abs/2110.06311v1) | Incremental Community Detection in Distributed Dynamic Graph | 2021 | arXiv | 0 |
| [10](http://arxiv.org/abs/2305.08977v2) | Autoencoder-based Anomaly Detection in Streaming Data | 2023 | arXiv | 0 |

### Crossref (10)
| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.47611/harp.308) | Community Detection in Dynamic Face-to-Face Interaction Networks | — | — | 0 |
| [2](https://doi.org/10.1504/ijie.2021.114496) | Incremental approach for hierarchical community mining in evolving social graphs | 2021 | IJIE | 1 |
| [3](https://doi.org/10.1109/sci68648.2025.11333864) | Towards Dynamic Community Detection with Leiden | 2025 | SCI | 1 |
| [4](https://doi.org/10.1109/itis64716.2024.10845656) | Comparative Analysis of Louvain, Leiden, Walktrap | 2024 | ITIS | 2 |
| [5](https://doi.org/10.1145/3810158.3810171) | Dynamic Construction of Medical AI Knowledge Graphs via Incremental Learning | 2026 | AIETDS | 0 |
| [6](https://doi.org/10.63913/jds.v2i1.1) | Dynamic Social Network Analysis of Metaverse Communities | 2026 | JDS | 0 |
| [7](https://doi.org/10.1093/comnet/cnaa027) | Evaluating community detection algorithms for progressively evolving graphs | 2021 | J. Complex Networks | 17 |
| [8](https://doi.org/10.3390/a15020064) | Accelerate Incremental TSP on Time Evolving Graphs with Partitioning | 2022 | Algorithms | 2 |
| [9](https://doi.org/10.1109/ipdpsw63119.2024.00207) | Shared-Memory Parallel Dynamic Louvain | 2024 | IPDPSW | 1 |
| [10](https://doi.org/10.5220/0014475900004061) | Incremental Federated Learning for Intrusion Detection | 2026 | ICISSP | 2 |

---

## Query 2 — Temporal smoothness / evolutionary clustering (2018–2026)

### Semantic Scholar (10)
| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://www.semanticscholar.org/paper/e72861f4ec3c22cb2ae9efd92fcf7669a94c32af) | Detecting and Tracking Community Structure in Temporal Networks: Low-Rank + Sparse Evolutionary Clustering | 2019 | IEEE TSIPN | 13 |
| [2](https://www.semanticscholar.org/paper/e9e1a9ec7ff7ed02327cde7dfdcdd7869d0f7efd) | Adaptive dynamic community detection via multi-objective evolutionary clustering | 2023 | IJICC | 9 |
| [3](https://www.semanticscholar.org/paper/5a74ac85878079b10f51ed6225fbffd9f2559470) | Improving temporal smoothness and snapshot quality (NOME) | 2023 | PeerJ CS | 4 |
| [4](https://www.semanticscholar.org/paper/11224bf3ff2eea04cf7b09303bd99f18b2c212e3) | Temporal Smoothness Framework: Evolutionary Transition Behavior | 2021 | ICTAI | 2 |
| [5](https://www.semanticscholar.org/paper/746e6aeff7df607cd018742732d503ceba1ba378) | Evolutionary Robust Clustering Over Time for Temporal Data | 2021 | IEEE T. Cybernetics | 8 |
| [6](https://www.semanticscholar.org/paper/e2f688d7fadbe5be276d43751fe59ac77adbbaa6) | Adaptive Evolutionary Clustering with Predictive Split–Merge | 2026 | ICSCAN | 0 |
| [7](https://www.semanticscholar.org/paper/6f2424fbb614d1c8913ccac49691aa01d72d769a) | Low-rank Estimation Based Evolutionary Clustering | 2019 | ICASSP | 2 |
| [8](https://www.semanticscholar.org/paper/a95590f4520d1d9c2105830b373d7d0adaaf85b8) | Temporal Community Detection and Analysis with Network Embeddings | 2025 | Mathematics | 12 |
| [9](https://www.semanticscholar.org/paper/31ce4884dc4c7abd577054677d9827bee527417d) | Graph Contrastive Learning for Tracking Dynamic Communities | 2024 | IEEE TETCI | 5 |
| [10](https://www.semanticscholar.org/paper/4882e470d1b61a78c2e04ff7cc00624de9db5b67) | Evolutionary Clustering of Moving Objects | 2022 | ICDE | 40 |

### Crossref (top relevant)
| # | Title | Date | Venue | Citations |
|---|-------|------|-------|-----------|
| [1](https://doi.org/10.1109/tsipn.2019.2942176) | Detecting and Tracking Community Structure: Low-Rank + Sparse Evolutionary Clustering | 2019 | IEEE TSIPN | 14 |
| [2](https://doi.org/10.1109/icassp.2019.8682987) | Low-rank Estimation Based Evolutionary Clustering | 2019 | ICASSP | 4 |
| [3](https://doi.org/10.1007/978-981-99-3814-8_6) | Evolutionary Clustering and Community Detection (handbook chapter) | 2024 | Springer | 0 |

*(arXiv results for this query drifted to "evolutionary computation"/genetic-algorithm papers — not relevant; omitted.)*

---

## Query 3 — Semantic caching for LLMs (2023–2026)

**Relevant (semantic response caching):**

| # | Title | Date | Venue | Citations | Src |
|---|-------|------|-------|-----------|-----|
| [1](http://arxiv.org/abs/2504.02268v1) | Advancing Semantic Caching for LLMs with Domain-Specific Embeddings and Synthetic Data | 2025 | arXiv | 0 | arXiv |
| [2](http://arxiv.org/abs/2606.19719v2) | Closing the Calibration Gap in Semantic Caching | 2026 | arXiv | 0 | arXiv |
| [3](http://arxiv.org/abs/2605.27494v1) | Grounded Cache Routing for RAG: When Is It Safe to Reuse an Answer? | 2026 | arXiv | 0 | arXiv |
| [4](http://arxiv.org/abs/2406.00025v1) | SCALM: Semantic Caching for Automated Chat Services with LLMs | 2024 | arXiv | 0 | arXiv |
| [5](https://doi.org/10.1109/icimtech63123.2024.10780864) | Knowledge Graph-Enhanced Semantic Cache for Low-Latency Inference | 2024 | ICIMTech | 4 | Crossref |

**Off-layer (KV-cache / inference memory — not applicable to label reuse):** dKV-Cache, WKVQuant, KVLink, ALISA, Corm, KV-Cache quantization papers, etc. Returned by the query but concern token-level attention caches inside a single forward pass, not caching *whole answers* across calls.

---

## Model Knowledge (foundational papers the APIs missed — verify)

| # | Title | Year | Venue | Notes |
|---|-------|------|-------|-------|
| [1](https://scholar.google.com/scholar?q=Evolutionary+Clustering+Chakrabarti+Kumar+Tomkins) | Evolutionary Clustering | 2006 | KDD | Origin of the *snapshot-cost + history-cost* objective — the formal basis for "smooth over time" |
| [2](https://scholar.google.com/scholar?q=Evolutionary+Spectral+Clustering+Temporal+Smoothness+Chi) | Evolutionary Spectral Clustering by Incorporating Temporal Smoothness | 2007 | KDD | Adds an explicit temporal-smoothness regularizer to the clustering objective |
| [3](https://scholar.google.com/scholar?q=Tracking+the+Evolution+of+Communities+Greene+Doyle+Cunningham) | Tracking the Evolution of Communities in Dynamic Social Networks | 2010 | ASONAM | The Greene member-overlap matching sgt **already uses** for feature ids |
| [4](https://scholar.google.com/scholar?q=Community+Discovery+in+Dynamic+Networks+Survey+Rossetti+Cazabet) | Community Discovery in Dynamic Networks: A Survey | 2018 | ACM CSUR | Taxonomy of instant-optimal / temporal-tradeoff / cross-time approaches |
| [5](https://scholar.google.com/scholar?q=From+Louvain+to+Leiden+Traag+Waltman) | From Louvain to Leiden: guaranteeing well-connected communities | 2019 | Sci. Reports | The Leiden paper sgt is built on |
| [6](https://scholar.google.com/scholar?q=GPTCache+semantic+cache+LLM+Bang) | GPTCache: An Open-Source Semantic Cache for LLM Applications | 2023 | EMNLP (demo) | The canonical embedding-similarity response cache |

---

## Summary & synthesis

### Overview
30+30+~15 relevant papers across incremental community detection, evolutionary/temporal clustering, and semantic LLM caching. The corpus shows three mature, separately-solved problems that together map cleanly onto sgt's three weak spots: the build-time dirty-subtree gate, feature-id churn, and the exact-match label cache.

### Trends
- **Dynamic Leiden is now an active line (2024–2026), dominated by Subhajit Sahu et al.** — the "Dynamic Frontier (DF)" family formalizes exactly the "only touch what changed" idea sgt approximates by hand.
- **Temporal smoothness has a 20-year formal basis** (Chakrabarti 2006 → Chi 2007 → Al-sharoa 2019): fold a stability term *into* the objective rather than fixing churn post-hoc.
- **Semantic caching matured fast post-2023** (GPTCache → SCALM → 2026 calibration/safety papers): the frontier is now *when is reuse safe*, not *how to embed*.

### Key themes
1. **Frontier-bounded incremental clustering** (DF Louvain/Leiden) — process only vertices near changed edges, propagate until stable. Convergence + quality guarantees.
2. **Objective-level temporal smoothness** — cost = snapshot_quality + α·change_from_last.
3. **Split–merge as first-class dynamic events** (Predictive Split-Merge, Greene tracking).
4. **Graded semantic cache** — embedding-similarity reuse with a calibrated safety threshold.

### Recommendations for sgt (reading path → concrete change)
See the inline chat message; each recommendation is tied to a specific function in `sgt/lens/tree.py`, `label.py`, or `intent/segment.py`.
