# QitOS

<img src="assets/logo.png" alt="QitOS Logo" width="75%">

[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-qitor.mintlify.app-0A66C2)](https://qitor.mintlify.app/)
[![PyPI](https://img.shields.io/pypi/v/qitos.svg)](https://pypi.org/project/qitos/)
[![Repo](https://img.shields.io/badge/github-Qitor%2Fqitos-black)](https://github.com/Qitor/qitos)

QitOS is the torch-flavor framework for agent researchers.

Prototype methods, run benchmarks, and inspect long-horizon trajectories on one `AgentModule + Engine` kernel with built-in `qita` observability.

QitOS core is the small framework. Product-grade applications and showcase agents live in `qitos-zoo`, including planned apps such as `qitos-coder` and `qitos-cyber-agent`.

[Quickstart](https://qitor.mintlify.app/quickstart) · [Tutorial Track](https://qitor.mintlify.app/tutorials) · [Benchmarks](https://qitor.mintlify.app/benchmarks/overview) · [CLI Reference](https://qitor.mintlify.app/reference/cli) · [Changelog](CHANGELOG.md) · [Chinese README](README.zh.md)

## What's New

- **Modern CyberGym tool turns**: authoritative per-step runtime state is now folded into the final real tool result instead of creating a trailing user turn, preserving native `assistant -> tool` chains for compatible providers.
- **qita trajectory workbench**: Run pages now open in a diagnosis-first view with a Focus Navigator, Agent Behavior Story, and right-side Inspector. Each step follows `Input -> Thought -> Action Calls -> Environment Observation`; every action is paired with its complete parameters, status, latency, and model-visible result, while canonical raw and unmatched evidence stays auditable in the Inspector. Failed calls expand by default, successful calls fold, and long content is wrapped and never available only as a truncated preview. CyberGym budget stops and `submit_poc` verification failures are promoted as review targets. Persistent light/dark themes cover board, run, replay, and compare pages.
- **Cleaner delegate tools**: `AgentSpec.tool_name` lets multi-agent systems expose task-oriented tool names, and `DelegateTool` now delivers structured `context` payloads into child agents.
- **CyberGym integration hardening**: v0.6 integration runs now preserve valid OpenAI-compatible tool schemas, redact persisted secrets across traces/results/render artifacts, and keep CyberGym PoC-generation shell commands out of the interactive review path while preserving the default coding-tool guard.
- **Lighter-weight CyberGym bootstrap guidance**: the CyberGym PoC agent now derives a compact task-spec summary, ranks likely parser/harness/sample paths more aggressively, tracks richer candidate provenance, and records a lightweight internal failure taxonomy without changing the single-agent runtime.

## What's New in v0.5.0

- **12 method templates**: ReAct, PlanAct, SWE-Agent, Voyager, Debate, Manager-Worker, Planner-Executor, Self-Refine, Reflexion, LATS, MoA, and Magentic-One — each with paper.md, config.yaml, and recipe implementations.
- **`qit new` CLI**: Scaffold a new agent project from built-in templates with `qit new --template <name>`.
- **Export APIs**: `EngineConfig`, `ToolPermissionSpec`, `CriticTrace`, and `HandoffTrace` for programmatic access to engine configuration and trace data.
- **Tracing integrations**: W&B (`WandbTraceProcessor`) and MLflow (`MlflowTraceProcessor`) for experiment tracking.
- **FamilyPreset extensibility**: `override()`, `recommended_*` advisory fields, and `MaxTokensCriteria` stop criterion.
- **qita cost panel**: Token usage and cost metrics in the run overview.

See [CHANGELOG.md](CHANGELOG.md) for the full list.

## Live Terminal of QitOS for Code Review

<p align="center">
  <img src="demo.gif" alt="QitOS long-running agent demo" width="92%">
</p>

## Who QitOS is For

- **Method researchers** who want to change prompts, parsers, critics, tools, and memory policies without rewriting the runtime.
- **Benchmark users** who want GAIA, Tau-Bench, and CyBench workflows on the same kernel they use for agent development.
- **Long-running agent debuggers** who care about trajectory review, replay, diff, and context-collapse diagnosis instead of app scaffolding alone.

## Run QitOS in 2 Minutes

The minimal agent in QitOS is a minimal **coding agent**. It configures a real model, works inside a workspace, edits code, runs a verification command, and leaves behind a qita-ready trace.

```bash
pip install "qitos[models]"
export OPENAI_API_KEY="sk-..."
qit --version
qit demo minimal
qita board --logdir runs
```

Optional but common for OpenAI-compatible providers:

```bash
export OPENAI_BASE_URL="https://api.siliconflow.cn/v1/"
export QITOS_MODEL="Qwen/Qwen3-8B"
```

`qit demo minimal` seeds a tiny buggy workspace, asks a model-backed coding agent to fix it, verifies the patch, and writes the trajectory to `./runs`.

Then go deeper:

- Want ReAct? See [`examples/patterns/react.py`](examples/patterns/react.py)
- Want a coding agent? See [`examples/real/coding_agent.py`](examples/real/coding_agent.py)
- Want benchmarks? Start with the [benchmark guides](https://qitor.mintlify.app/benchmarks/overview)
- Want method templates? See [Method Templates Guide](https://qitor.mintlify.app/guides/method-templates)

## Why QitOS

| If you want... | QitOS gives you... |
|---|---|
| reproducible agent research | a stable `AgentModule + Engine` kernel |
| method = Agent + Critic | 12 built-in method templates with paper mappings |
| observability | `qita` board, replay, export, and trace artifacts |
| benchmark workflows | GAIA, Tau-Bench, and CyBench adapters |
| less framework glue code | one canonical execution loop |

## Method Templates

QitOS ships 12 method templates — each is an Agent + Critic pair implementing a well-known agentic reasoning pattern:

| Template | Pattern | Paper |
|----------|---------|-------|
| ReAct | Reason + Act | Yao et al. 2023 |
| PlanAct | Plan then Execute | — |
| SWE-Agent | Software Engineering | Princeton 2024 |
| Voyager | Open-ended Exploration | Wang et al. 2023 |
| Debate | Multi-agent Debate | — |
| Manager-Worker | Orchestration with Delegation | — |
| Planner-Executor | Plan Decomposition | — |
| Self-Refine | Generate → Critique → Refine | Madaan et al. 2023 |
| Reflexion | Act → Reflect → Retry | Shinn et al. 2023 |
| LATS | Monte Carlo Tree Search | Zhou et al. 2023 |
| MoA | Parallel Proposals + Aggregation | Wang et al. 2024 |
| Magentic-One | Orchestrator + Specialists | Furtado et al. 2024 |

Use them directly:

```python
from qitos.recipes.reflexion import ReflexionAgent, ReflexionCritic

agent = ReflexionAgent(llm=my_llm)
result = agent.run(
    task="Debug the failing test",
    critics=[ReflexionCritic(max_reflections=3)],
    max_steps=15,
    return_state=True,
)
```

Or scaffold a new agent from any template:

```bash
pip install qitos[cookiecutter]
qit new --agent-name my_agent --agent-description "My custom agent"
qit list-templates
```

## Tooling Layout

QiTOS separates tool imports into three layers:

- `qitos.kit`: the simplest curated entrypoint for common toolsets
- `qitos.kit.toolset`: scenario-oriented presets and registry builders
- `qitos.kit.tool.<domain>`: advanced atomic capability imports

Default composition is list-first:

```python
from qitos import ToolRegistry
from qitos.kit.tool.file import ReadFile
from qitos.kit.toolset import coding_tools

registry = ToolRegistry().include_toolset(
    [
        ReadFile(workspace_root="."),
        coding_tools(workspace_root="."),
    ]
)
```

Security-sensitive tools are explicit opt-in imports and are not part of `qitos`, `qitos.kit`, `qit demo`, or the quickstart path.

## Documentation Map

- Start here: [Introduction](https://qitor.mintlify.app/introduction)
- First successful run: [Quickstart](https://qitor.mintlify.app/quickstart)
- Install options: [Installation](https://qitor.mintlify.app/installation)
- Build your own minimal coding agent: [First Agent](https://qitor.mintlify.app/guides/build-your-first-agent)
- Method templates: [Method Templates Guide](https://qitor.mintlify.app/guides/method-templates)
- Learn the runtime: [AgentModule](https://qitor.mintlify.app/concepts/agent-module) / [Engine](https://qitor.mintlify.app/concepts/engine)
- Inspect traces: [Observability](https://qitor.mintlify.app/guides/observability)
- Follow the course: [Tutorials](https://qitor.mintlify.app/tutorials)
- Run benchmarks: [Benchmarks Overview](https://qitor.mintlify.app/benchmarks/overview)
- Check commands: [CLI Reference](https://qitor.mintlify.app/reference/cli)
- Need API details: [API Reference](https://qitor.mintlify.app/reference/api)

## Preview

<table>
  <tr>
    <td align="center"><strong>QitOS CLI</strong></td>
    <td align="center"><strong>qita Board</strong></td>
    <td align="center"><strong>qita Trajectory View</strong></td>
  </tr>
  <tr>
    <td align="center">
      <a href="assets/qitos_cli_snapshot.png">
        <img src="assets/qitos_cli_snapshot.png" alt="QitOS CLI" width="100%" />
      </a>
    </td>
    <td align="center">
      <a href="assets/qita_board_snapshot.png">
        <img src="assets/qita_board_snapshot.png" alt="qita Board" width="100%" />
      </a>
    </td>
    <td align="center">
      <a href="assets/qita_traj_snapshot.png">
        <img src="assets/qita_traj_snapshot.png" alt="qita Trajectory View" width="100%" />
      </a>
    </td>
  </tr>
</table>

## Status

QitOS is currently **Beta**.

- Stable direction: `AgentModule + Engine`, trace/qita flow, canonical examples, benchmark adapters, and official reproducible-run contracts.
- Likely to evolve: higher-level convenience APIs, some `kit` modules, and experimental toolsets.
- If you are evaluating adoption, start from the kernel and examples, not assumptions about frozen surface area.
- For ongoing project evolution and upgrade notes, see [CHANGELOG.md](CHANGELOG.md).

## Installation and Versions

- Supported Python version: **3.10+**
- User install: `pip install "qitos[models]"`
- Version check: `qit --version`
- Minimal coding agent: `qit demo minimal`
- Optional provider config: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `QITOS_MODEL`
- Core-only install: `pip install qitos`
- Repo source install: `pip install -r requirements.txt`
- Full contributor install: `pip install -r requirements-dev.txt`
- Optional extras: `qitos[wandb]`, `qitos[mlflow]`, `qitos[cookiecutter]`, `qitos[all]`
- Installation guide: [Installation](https://qitor.mintlify.app/installation)

## Contributing

Contributions are welcome, especially around method templates, benchmark adapters, memory/history workflows, qita UX, and framework contracts. Product-grade agents should target `qitos-zoo`. Start with [CONTRIBUTING.md](CONTRIBUTING.md) for the PR process, [DEVELOPMENT.md](DEVELOPMENT.md) for the local workflow, [ARCHITECTURE.md](ARCHITECTURE.md) for system design, [SECURITY.md](SECURITY.md) for disclosure guidance, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.

## License

MIT. See [LICENSE](LICENSE).
