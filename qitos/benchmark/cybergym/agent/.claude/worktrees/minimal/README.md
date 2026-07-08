# CyberGym Agent

> A specialized LLM agent for **automated vulnerability reproduction** (PoC generation) on the
> [CyberGym](https://github.com/sunblaze-ucb/cybergym) benchmark, built on the QitOS agent runtime
> and driven by `GLM-5.1`.

**[English](README_EN.md)** | **[中文](README_CN.md)**

---

## Design Philosophy

```
QitOS provides the benchmark/runtime shell.
The agent provides the attack policy.
The CyberGym server's submit feedback is the oracle.
```

The agent is **not** a general coding assistant. It is a narrow, feedback-first exploit-development
loop specialized for CyberGym level-1 input-PoC tasks. Three principles drive every design decision:

### 1. Feedback-First, Not Read-First

*Orient → form one concrete hypothesis → create a candidate early → submit → classify feedback →
mutate/replace/branch.* A candidate miss beats another vague source read. The verification server
is the only reliable oracle — heuristics and LLM reasoning are useful only insofar as they reduce
the number of submit-feedback rounds needed to reach a crash-differential pass.

### 2. Graph-Fused Sink Discovery

The agent's core challenge is **sink identification**: finding the exact function where untrusted
input causes a crash. The call graph is built once by an offline Tree-sitter interprocedural
analysis (`AnalysisService`), then **fused** into the agent's tool returns and observation blocks
at 10 integration points — never as a separate tool call. The LLM sees graph context
(callee depth, reachability, risk signals) embedded in the tools it already uses (READ, GREP,
FindSymbols, record_sink_candidate), making it act on graph information without needing to
explicitly query a graph database.

Key fusion mechanisms:
- **Callee leaf-depth annotation**: `⚠InsertRow(leaf)` vs `⚠decodeRow(3 callees)` — tells the LLM
  which callee is the deepest crash site
- **Auto-deepen with actionable next-read**: when the LLM records a mid-chain function as sink,
  the system auto-discovers a deeper leaf and injects `READ: READ(path="src/insert.c", offset=175, limit=40)`
- **Description de-anchoring**: persistent WARNING when a sink candidate was derived from the
  vulnerability description but the graph shows it's a caller, not the actual sink
- **GREP graph enrichment**: file-level `5 funcs, 3 reachable` annotations on search results

### 3. State-First Prompting

Behavior is driven by prompt-visible state labels (`candidate_ready`, `candidate_required`,
`post_submit_miss`, `orienting`) and action gating, not a rigid phase machine. The full internal
state is never dumped to the model — only compact summaries (task spec, repo profile, top evidence,
latest failure) survive context compaction by being regenerated from state each step.

---

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────┐
│                     QitOS Engine                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │              CyberGymAgent (agent.py)             │   │
│  │  ┌─────────────┐  ┌──────────────────────────┐   │   │
│  │  │ PhaseEngine │  │ PromptBuilder + Observations│  │   │
│  │  └─────────────┘  └──────────────────────────┘   │   │
│  │  ┌──────────────────────────────────────────────┐ │   │
│  │  │  Tool Surface (READ/GREP/FindSymbols/...)    │ │   │
│  │  │  ┌─────────────────────────────────────────┐ │ │   │
│  │  │  │  Static Analysis Fusion Layer            │ │ │   │
│  │  │  │  (callee depth, auto-deepen, de-anchor)  │ │ │   │
│  │  │  └─────────────────────────────────────────┘ │ │   │
│  │  └──────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │         AnalysisService (offline, SQLite)         │   │
│  │  Tree-sitter index → call graph → path queries    │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
   CyberGym Grading              Source Repo (repo-vul/)
   Server (oracle)
```

**Two-repo architecture**: `cybergym_agent` (this repo, the attack policy) and `qitos` (the runtime
engine). The agent is mounted at `qitos/benchmark/cybergym/agent/` and gitignored there — commit
agent changes here, framework changes in qitos.

---

## Optimization History

The agent has undergone 10 iterations of fusion-embedded optimization, targeting sink recall rate
(the percentage of tasks where the agent correctly identifies the crashing function):

| Phase | Iterations | Strategy | Recall |
|-------|-----------|----------|--------|
| V9 baseline | — | No graph context | ~39% |
| V10 (Iter 1-5) | Passive graph hints | Bootstrap enrichment, READ callee hints, auto-deepen, FindSymbols reachability, exploration gate | ~48% |
| V11 (Iter 6-10) | Active instructions | Callee leaf-depth, de-anchoring, next-read + TTL, GREP enrichment, candidate metadata | ~55-58% |

The V10→V11 shift was driven by a key insight: **passive hints don't drive action**. The LLM sees
`[GRAPH] Deeper sink detected` but doesn't READ the function. V11 converts suggestions into
actionable instructions with explicit READ paths, persistent warnings, and directive language.

---

## Quick Links

- **[Full English documentation](README_EN.md)** — setup, running, reading results, design details, tool surface, changelogs
- **[完整中文文档](README_CN.md)** — 安装接入、运行、结果判读、设计细节、工具面、变更记录
- **[ARCH.md](ARCH.md)** — detailed architecture, "tried but reverted" list, improvement seams
- **[CLAUDE.md](CLAUDE.md)** — developer instructions for AI assistants working on this repo
