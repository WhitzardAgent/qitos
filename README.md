# QitOS

<img src="assets/logo.png" alt="QitOS Logo" width="75%">

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-qitor.github.io/qitos-0A66C2)](https://qitor.github.io/qitos/)
[![PyPI](https://img.shields.io/pypi/v/qitos.svg)](https://pypi.org/project/qitos/)
[![Repo](https://img.shields.io/badge/github-Qitor%2Fqitos-black)](https://github.com/Qitor/qitos)

Research-first agent framework for building reproducible LLM agents.

QitOS gives you a clean `AgentModule + Engine` kernel, benchmark-ready workflows, and built-in run observability with `qita`.

[Get Started](https://qitor.github.io/qitos/start-here/) · [10-Minute Tutorial](https://qitor.github.io/qitos/getting-started/build_agent_in_10_minutes/) · [Examples](https://qitor.github.io/qitos/tutorials/examples/) · [Changelog](CHANGELOG.md) · [Chinese README](README.zh.md)

## Live Terminal of QitOS for Code Review

<p align="center">
  <img src="demo.gif" alt="QitOS long-running agent demo" width="92%">
</p>

## Who QitOS Is For

- **Researchers**: prototype ReAct, PlanAct, ToT, Reflexion, and new agent methods with reproducible runs.
- **Agent builders**: build tool-using agents on a stable execution loop instead of framework glue code.
- **Evaluators**: run GAIA, Tau-Bench, and CyBench style workflows with the same kernel you use in product agents.

## Run In 2 Minutes

From the repository root:

```bash
pip install -r requirements.txt
export OPENAI_API_KEY="<your_api_key>"
python examples/quickstart/minimal_agent.py
qita board --logdir runs
```

Then go deeper:

- Want ReAct? See [`examples/patterns/react.py`](examples/patterns/react.py)
- Want a coding agent? See [`examples/real/coding_agent.py`](examples/real/coding_agent.py)
- Want benchmarks? Start with the [benchmark guides](https://qitor.github.io/qitos/builder/benchmark_gaia/)

## Why QitOS

| If you want... | QitOS gives you... |
|---|---|
| reproducible agent research | a stable `AgentModule + Engine` kernel |
| observability | `qita` board, replay, export, and trace artifacts |
| benchmark workflows | GAIA, Tau-Bench, and CyBench adapters |
| less framework glue code | one canonical execution loop |

## Minimal Agent Shape

```python
from dataclasses import dataclass
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema


@dataclass
class DemoState(StateSchema):
    pass


class DemoAgent(AgentModule[DemoState, dict[str, Any], Action]):
    def init_state(self, task: str, **kwargs: Any) -> DemoState:
        return DemoState(task=task, max_steps=6)

    def build_system_prompt(self, state: DemoState) -> str | None:
        return "Solve the task step by step."

    def prepare(self, state: DemoState) -> str:
        return f"Task: {state.task}\nStep: {state.current_step}/{state.max_steps}"

    def decide(self, state: DemoState, observation: dict[str, Any]):
        return None

    def reduce(self, state: DemoState, observation: dict[str, Any], decision: Decision[Action]) -> DemoState:
        return state
```

For a full coding-agent walkthrough and the SWE-style example, see:

- [Build an Agent in 10 Minutes](https://qitor.github.io/qitos/getting-started/build_agent_in_10_minutes/)
- [Coding Agent Walkthrough](https://qitor.github.io/qitos/tutorials/examples/real_coding/)
- [SWE Agent Walkthrough](https://qitor.github.io/qitos/tutorials/examples/real_swe/)

## Example Gallery

### Core Patterns

- **ReAct**: text protocol + one-action-per-step baseline.
- **PlanAct**: explicit plan first, then execute step by step.
- **Tree-of-Thought**: branch and select before acting.
- **Reflexion**: actor-critic loop with grounded retry behavior.

### Real Agents

- **Coding agent**: practical coding loop with editor, shell, and memory.
- **SWE agent**: richer planning-oriented software engineering flow.
- **Computer-use agent**: web research and computer-use style interaction.
- **EPUB reader**: document-grounded reasoning with branching.

### Evaluation

- **GAIA**: benchmark runner on the QitOS kernel.
- **Tau-Bench**: standardized benchmark adapter path.
- **CyBench**: CTF-like evaluation with guided metrics.

Canonical examples live in:

- [`examples/quickstart/`](examples/quickstart/)
- [`examples/patterns/`](examples/patterns/)
- [`examples/real/`](examples/real/)
- [`examples/benchmarks/`](examples/benchmarks/)

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

## Documentation Map

- New here: [Start Here](https://qitor.github.io/qitos/start-here/)
- First successful run: [Getting Started](https://qitor.github.io/qitos/getting-started/)
- Writing your first agent: [Build an Agent in 10 Minutes](https://qitor.github.io/qitos/getting-started/build_agent_in_10_minutes/)
- Understanding the runtime: [Kernel](https://qitor.github.io/qitos/research/kernel/)
- Framework contracts: [Contracts & Guarantees](https://qitor.github.io/qitos/reference/contracts/)
- Typical scenarios: [Use Cases](https://qitor.github.io/qitos/use-cases/)
- Need examples: [Example Walkthroughs](https://qitor.github.io/qitos/tutorials/examples/)
- Need benchmarks: [GAIA](https://qitor.github.io/qitos/builder/benchmark_gaia/) / [Tau-Bench](https://qitor.github.io/qitos/builder/benchmark_tau/)
- Need API details: [API Reference](https://qitor.github.io/qitos/reference/api_generated/)

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

QitOS is currently **Alpha**.

- Stable direction: `AgentModule + Engine`, trace/qita flow, canonical examples, benchmark adapters.
- Likely to evolve: higher-level convenience APIs, some `kit` modules, and experimental toolsets.
- If you are evaluating adoption, start from the kernel and examples, not assumptions about frozen surface area.
- For ongoing project evolution and upgrade notes, see [CHANGELOG.md](CHANGELOG.md).

## Installation And Versions

- Supported Python version: **3.9+**
- User install: `pip install qitos`
- Repo quickstart: `pip install -r requirements.txt`
- Full contributor install: `pip install -r requirements-dev.txt`
- Installation guide: [Installation](https://qitor.github.io/qitos/getting-started/installation/)

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) for the PR process, [DEVELOPMENT.md](DEVELOPMENT.md) for the local workflow, [ARCHITECTURE.md](ARCHITECTURE.md) for system design, [SECURITY.md](SECURITY.md) for disclosure guidance, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community expectations.

## License

MIT. See [LICENSE](LICENSE).
