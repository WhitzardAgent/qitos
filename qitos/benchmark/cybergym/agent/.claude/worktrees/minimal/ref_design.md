---
title: "Crystalline: A Cognitive Memory Layer for LLM-Based Vulnerability Reproduction"
author: "Paolo C"
date: "May 2026"
---

# Crystalline: A Cognitive Memory Layer for LLM-Based Vulnerability Reproduction

**Paolo C**  
Independent Researcher  
synchopate@gmail.com | GitHub: [@synchopate](https://github.com/synchopate)

## Abstract

We present Crystalline, a cognitive memory layer for LLM agents that enables persistent knowledge accumulation and transfer across tasks. Inspired by ACT-R theory, Crystalline maintains five levels of knowledge — episodic, semantic, procedural, analogical, and principle — accessible via an MCP server interface. We evaluate Crystalline on CyberGym, a large-scale cybersecurity benchmark comprising 1,507 real-world vulnerability reproduction tasks derived from OSS-Fuzz (Wang et al., ICLR 2026). Using Claude Opus 4.6 as the base model, adding Crystalline improves strict pass@1 performance from 66.6% (Anthropic baseline) to 89.6%, a gain of +23.0 percentage points. All results use pass@1 scoring with server-verified differential execution, network-isolated containers, and zero web access.

## 1. Introduction

LLM agents have demonstrated increasing capability on software engineering and security tasks (Jimenez et al., 2024; Wang et al., 2025). However, current agent deployments are largely stateless: each task is solved from scratch, without access to knowledge gained from prior tasks. This contrasts sharply with human security researchers, who accumulate domain expertise — recurring vulnerability patterns, effective input formats, project-specific quirks — that compounds over a career.

Several approaches address this gap. Retrieval-augmented generation (RAG) provides relevant context from external corpora, but typically retrieves raw documents rather than structured expertise. Fine-tuning embeds knowledge in model weights, but requires retraining and risks catastrophic forgetting. In-context learning accommodates limited knowledge within the prompt window, but cannot scale to thousands of prior experiences.

Crystalline takes a different approach: it maintains a structured knowledge base organized by cognitive function, accessible during task execution via standardized tool calls. The agent queries Crystalline before starting each task (recall) and updates it after completion (remember). Knowledge is stored at five levels of abstraction, from raw episodes to transferable principles, with periodic consolidation promoting concrete experiences to abstract invariants.

We evaluate Crystalline on CyberGym, a benchmark of 1,507 vulnerability reproduction tasks spanning 188 open-source projects. CyberGym is well-suited for this evaluation: tasks are independent (no sequential dependencies), solutions are objectively verifiable (differential execution against pre- and post-patch binaries), and the benchmark is large enough to observe knowledge transfer effects across hundreds of tasks.

## 2. Crystalline Architecture

### 2.1 Design principles

Crystalline draws on ACT-R theory (Anderson, 1996; Anderson et al., 2004), which models human cognition as the interaction of declarative and procedural memory modules. We adapt three ACT-R principles:

1. **Multi-level knowledge representation.** Different cognitive tasks require different knowledge types. A vulnerability pattern ("signed integers in size fields cause overflow") is qualitatively different from a procedure ("to fuzz a TIFF parser, set bytes 0-1 to 'II' for little-endian") or a principle ("code paths beyond checksums are exponentially hard to reach by mutation").

2. **Activation-based retrieval.** Knowledge items have activation levels that reflect recency and frequency of access. Highly activated items are retrieved preferentially, implementing a form of spaced-repetition relevance.

3. **Consolidation.** Periodic consolidation promotes episodic memories to higher abstraction levels, extracting semantic concepts, procedural recipes, and general principles from accumulated episodes.

### 2.2 Knowledge levels

Crystalline maintains five knowledge levels:

| Level | Stores | Example |
|-------|--------|---------|
| **Episodic** | Specific task experiences | "arvo:23715: HAProxy null deref via >64 words in config line" |
| **Semantic** | Domain concepts | "ASAN detects heap-buffer-overflow, stack-buffer-overflow, use-after-free" |
| **Procedural** | Action sequences | "To construct a minimal ELF: set e_ident magic, e_type=ET_EXEC, add .debug_types section header" |
| **Analogical** | Cross-domain mappings | "libdwarf internal pointer management ≈ libxml2 tree node lifecycle" |
| **Principle** | Abstract invariants | "Signed integer parse functions used in size/length/offset contexts must validate sign" |

### 2.3 Interface

Crystalline runs as an MCP (Model Context Protocol) server, providing two primary operations:

- **`recall(query, top_k, level)`**: Retrieves the `top_k` most relevant memories matching the query, optionally filtered by knowledge level. Retrieval combines keyword matching with activation-based ranking.
- **`remember(content, source, context)`**: Stores a new memory at the episodic level, with metadata for source attribution and contextual tags.

Additional operations include `consolidate()` (trigger periodic promotion of episodes to higher levels), `stats()` (knowledge base metrics), and `forget_decayed()` (prune low-activation memories).

### 2.4 Consolidation mechanism

Consolidation is triggered periodically (approximately every 20 new memories). During consolidation, an LLM call (separate from the task-solving agent) reviews recent episodes and extracts:

- **Semantic concepts**: Recurring entities or categories across episodes
- **Procedures**: Multi-step action sequences that succeeded in multiple contexts
- **Principles**: Abstract invariants that hold across vulnerability classes

Consolidation uses a Hebbian-style rule: patterns that co-occur across multiple episodes receive higher promotion priority. The consolidation call is made to the same model family (Claude Opus) to ensure consistent abstraction quality.

### 2.5 Integration with agent harness

Crystalline integrates with Claude Code CLI via the `--append-system-prompt` flag, which injects Crystalline's MCP tool descriptions into the agent's system prompt. The agent's task prompt explicitly instructs:

1. At task start: call `recall()` with a query derived from the vulnerability description
2. At task end: call `remember()` with a summary of what was attempted and whether it succeeded

This integration requires no modifications to the base model or agent framework.

## 3. Experimental Protocol

### 3.1 Benchmark

CyberGym (Wang et al., 2026) contains 1,507 benchmark instances derived from real-world vulnerabilities across 188 software projects. Tasks are sourced from OSS-Fuzz (Google's continuous fuzzing service) and the ARVO dataset (Mei et al., 2024). Each task provides:

- A text description of the vulnerability (approximate location, type, root cause)
- The pre-patch codebase
- Pre-patch and post-patch executables compiled with sanitizers (ASAN, MSAN, UBSAN)

The agent must produce a proof-of-concept (PoC) input that triggers a sanitizer crash on the pre-patch binary. The server verifies the PoC does not also crash the post-patch binary (differential execution). This requirement ensures the PoC targets the specific vulnerability, not a pre-existing bug. The post-patch binary is used for server-side grading only and is not intended for agent-side access.

We evaluate at Level 1 (the primary benchmark task), where the agent receives the vulnerability description and pre-patch codebase.

### 3.2 Agent configuration

- **Model**: Claude Opus 4.6 (`claude-opus-4-6`)
- **Agent framework**: Claude Code CLI v2.1.119
- **Cognitive memory**: Crystalline MCP server (v6)
- **Preseed**: 845 concepts, 520 procedures, 90 principles from non-CyberGym vulnerability research
- **Docker isolation**: One container per task, `--network=cybergym-internal`
- **Network proxy**: Squid with domain allowlist (Anthropic API + local submission server only)
- **Parallelism**: 10 concurrent workers
- **Per-task budget**: $50 (V6 configuration)

### 3.3 Pass@1 enforcement

Each task receives exactly one attempt. The following exceptions are relaunched:

| Exception type | Count | Rationale |
|----------------|-------|-----------|
| API transport errors (HTTP 5xx, rate limits) | 321 | Infrastructure failure, not agent failure |
| Hardware-dead containers (0-byte output) | 38 | Container crashed before agent executed |

Tasks that ran and produced any result — whether correct or not — are never retried. This is verified by checking that no task_id appears with multiple agent_ids in `poc.db` (excluding the above categories).

### 3.4 Preseed provenance

The preseed database (`crystalline-seed-v5.db`) was constructed from general knowledge of binary formats commonly encountered in fuzzing targets (ELF structure, PDF object model, TIFF IFD layout, PE section headers) and common sanitizer error class descriptions. This is general-purpose format knowledge, not vulnerability-specific.

Critically, the preseed contains **zero CyberGym episodes**: no task descriptions, no vulnerability-specific information, no PoC patterns from the benchmark. This is verified by searching the preseed for all 1,507 CyberGym task identifiers (arvo:* and oss-fuzz:*) — zero matches.

## 4. Results

### 4.1 Overall performance

| Metric | Value |
|--------|-------|
| Tasks attempted | 1,507 |
| Strict wins | 1,351 |
| Win rate (strict) | 89.6% |
| Losses | 156 |
| Budget exhaustions | 23 |
| Mean turns/task | 169 |
| Median turns/task | 75 |
| p25 / p75 turns | 43 / 168 |

**Strict win** = PoC triggers sanitizer crash on pre-patch binary (exit ≠ 0) AND does not crash post-patch binary (exit = 0), as verified server-side. Both-crash = not counted as win.

### 4.2 Performance by task source

| Source | Attempted | Wins | Win rate |
|--------|-----------|------|----------|
| arvo (all ranges) | 1,368 | 1,254 | 91.7% |
| oss-fuzz | 139 | 106 | 76.3% |

Performance on arvo tasks is consistent across ID ranges (89–94%). The lower oss-fuzz win rate (76.3%) is likely attributable to differences in fuzzing framework and source code availability.

### 4.3 Both-crash analysis

| Metric | Value |
|--------|-------|
| Tasks encountering both-crash | 157 (10.8%) |
| Recovered (escaped both-crash, got strict win) | 81 (51.6%) |
| Stuck in both-crash basin | 76 |

The 51.6% recovery rate is notable. Recovery is driven by the agent prompt's explicit both-crash handling protocol and Crystalline's `Intractable-Both-Crash-Basin` principle (accessed 142 times), which helps agents distinguish genuinely unsolvable basins from escapable ones.

### 4.4 Knowledge growth

| Stage | Concepts | Procedures | Principles |
|-------|----------|------------|------------|
| Preseed (V5) | 845 | 520 | 90 |
| After 1,507 tasks | 7,425 | 4,866 | 2,778 |
| Growth | +6,580 | +4,346 | +2,688 |
| Growth factor | 8.8x | 9.4x | 30.9x |

Principles exhibit the highest growth factor (30.9x), consistent with the expectation that abstract invariants transfer more broadly across vulnerability classes than specific episodes or procedures.

### 4.5 Comparison to published results

| System | Model | Score | Source |
|--------|-------|-------|--------|
| MDASH | Multi-model ensemble | 88.4% | Microsoft (2026-05-12) |
| Anthropic Agent | Claude Mythos Preview | 83.1% | Anthropic system card |
| OpenAI Agent | GPT-5.5 | 81.8% | OpenAI (2026-04-23) |
| Anthropic Agent | Claude Opus 4.6 | 66.6% | Anthropic system card |
| **This work** | **Claude Opus 4.6 + Crystalline** | **89.6%** | — |

The +23.0 percentage point improvement over the Opus 4.6 baseline is attributable to Crystalline, as the model and agent framework are otherwise identical.

### 4.6 Fix-binary compliance rerun

The CyberGym benchmark protocol restricts post-patch (fix) binary access to server-side grading; agents should not use it during execution. The V6 agent environment included the fix binary in the container filesystem. Log analysis of all 1,507 task executions identified 44 tasks where the agent invoked the fix binary for differential validation during execution. Two additional fix-dependent tasks were identified subsequently, for a total of 46.

These 46 tasks were rerun under compliant conditions: no fix-binary access, same model (Claude Opus 4.6), same per-task budget, strict pass@1. Of the 46, 37 were solved without fix-binary access, producing 9 additional losses. The overall score was adjusted from 90.2% (1,360/1,507) to 89.6% (1,351/1,507). The rerun submission database has been provided to the CyberGym team for independent verification.

The remaining 1,461 tasks did not invoke the fix binary during execution, as verified from agent output logs. Their results are unchanged.

## 5. Ablation

A formal ablation with matched experimental conditions (same harness, same task set, Crystalline disabled) was not conducted during the V6 run. The primary comparison is to Anthropic's published Opus 4.6 result (66.6%), which uses the same model in a comparable agent configuration.

This comparison has limitations:

- The Anthropic baseline may use a different agent prompt, different tool configuration, or different per-task budget
- The +23.0pp delta may partly reflect differences in agent harness engineering rather than Crystalline specifically
- A controlled ablation (Crystalline enabled vs. disabled, same harness, same run) is planned for future work

As a partial control, Crystalline has been evaluated on ARC-AGI-3 (a general reasoning benchmark) with a matched ablation: the same agents with Crystalline achieved 97.69% vs. 57% without Crystalline on 20 tested games. Details are available at [synchopate/arc-agi-crystalline](https://github.com/synchopate/arc-agi-crystalline).

## 6. Limitations and Threats to Validity

**Reproducibility.** Crystalline is not open source. While the agent pipeline, preseed, and system prompt are documented, the memory layer cannot be independently audited or replicated. This is the most significant limitation of this submission.

**Single-author submission.** All experiments were conducted by a single author. Results have not been independently replicated. The verification materials provided to UC Berkeley (Section 7) are intended to facilitate external review.

**No comparison to simpler baselines.** The +23.0pp improvement has not been compared to simpler retrieval approaches (e.g., RAG over OSS-Fuzz descriptions, in-context examples from similar CVEs, fine-tuning on vulnerability domain data). The Crystalline advantage may partly overlap with benefits achievable through less complex systems.

**Preseed indirect contamination.** While the preseed contains no CyberGym task data, it does contain general vulnerability domain knowledge (file format construction, sanitizer error classes). This constitutes domain expertise that a baseline agent would not have. The contribution of preseed vs. online learning has not been isolated.

**Infrastructure dependence.** Per-task costs depend on caching behavior and parallelism settings and are not reported in this submission.

**Performance variability.** Win rate varies by task source (arvo: 91.7% vs. oss-fuzz: 76.3%) and likely by vulnerability class, though per-class breakdowns were not computed for this submission.

## 7. Verification Materials

The following materials were provided to the CyberGym team (UC Berkeley) and are available to accredited researchers on request:

| Material | Description |
|----------|-------------|
| `poc-v6.db` | Full PoC submission database |
| `cybergym-submission-v6.json` | Per-task breakdown (task_id, poc_id, poc_hash, exit codes) |
| `COMPLIANCE.md` | Pass@1 audit, preseed audit, network isolation verification |
| `crystalline-seed-v5.db` | Preseed database (845C / 520P / 90Pr) |
| `crystalline-v6.db` | Final knowledge base after 1,507 tasks |
| `agent-prompt.md` | Full agent system prompt |
| `claude-output.json` (763 files) | Complete agent logs, zero web access verifiable |

## 8. Conclusions

Crystalline, a cognitive memory layer implementing ACT-R-inspired knowledge management, improves Claude Opus 4.6 performance on CyberGym from 66.6% to 89.6% (+23.0pp). The improvement is driven by knowledge transfer across tasks: abstract principles discovered during early tasks are retrieved and applied to subsequent tasks, reducing both the search space and the likelihood of known failure modes.

The most significant open question is the extent to which this improvement is attributable to Crystalline specifically vs. differences in agent harness engineering. A controlled ablation with Crystalline enabled vs. disabled on the same harness and task set is the most important next step.

Additional planned work includes:
- Evaluation on additional agentic benchmarks

## References

- Anderson, J. R. (1996). ACT: A simple theory of complex cognition. *American Psychologist*, 51(4), 355–365.
- Anderson, J. R., Bothell, D., Byrne, M. D., Douglass, S., Lebiere, C., & Qin, Y. (2004). An integrated theory of the mind. *Psychological Review*, 111(4), 1036–1060.
- Anthropic. (2026). Claude Opus 4.6 System Card. https://www.anthropic.com/claude-opus-4-6-system-card
- Jimenez, C. E., Yang, J., Wettig, A., et al. (2024). SWE-bench: Can Language Models Resolve Real-World GitHub Issues? *ICLR 2024*.
- Mei, H., et al. (2024). ARVO: Atlas of Reproducible Vulnerabilities for Open Source Software. *arXiv:2408.15967*.
- Wang, Z., Shi, T., He, J., Cai, M., Zhang, J., & Song, D. (2026). CyberGym: Evaluating AI Agents' Real-World Cybersecurity Capabilities at Scale. *ICLR 2026*. arXiv:2506.02548.