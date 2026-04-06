# QitOS

![QitOS Logo](assets/logo.png)

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-qitor.github.io/qitos-0A66C2)](https://qitor.github.io/qitos/)
[![PyPI](https://img.shields.io/pypi/v/qitos.svg)](https://pypi.org/project/qitos/)
[![Repo](https://img.shields.io/badge/github-Qitor%2Fqitos-black)](https://github.com/Qitor/qitos)

**QitOS is a research-first framework for building serious LLM agents.**  
It gives you one clean execution kernel, composable modules, and benchmark-ready workflows so you can move from idea to reproducible results without rewriting your stack.

- 中文 README: [README.zh.md](README.zh.md)
- Documentation: [https://qitor.github.io/qitos/](https://qitor.github.io/qitos/)

## Preview

<table>
  <tr>
    <td align="center"><strong>QiTOS CLI</strong></td>
    <td align="center"><strong>qita Board</strong></td>
    <td align="center"><strong>qita Trajectory View</strong></td>
  </tr>
  <tr>
    <td align="center">
      <a href="assets/qitos_cli_snapshot.png">
        <img src="assets/qitos_cli_snapshot.png" alt="QiTOS CLI" width="100%" />
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

## Why Teams Choose QitOS

- **Research-first by design**: built for rapid iteration on ReAct, Plan-Act, ToT, Reflexion, and custom scaffolds.
- **One canonical kernel**: `AgentModule + Engine` with a stable lifecycle that is easy to reason about and extend.
- **Modular architecture**: use only what you need from `core`, `engine`, `kit`, `benchmark`, and `evaluate`.
- **Ecosystem compatibility**: works naturally with OpenAI-compatible model APIs, host environments, and tool registries.
- **Benchmark-native workflow**: unified adapters for GAIA, Tau-Bench, and CyBench.
- **Production-grade observability**: traces, hooks, replay, and export via `qita`.

## Core Advantage

```text
Task -> Engine.run(...)
     -> prepare -> decide -> act -> reduce -> check_stop -> ...
     -> hooks + trace + replay + metrics
```

One architecture for research, evaluation, and real deployment.

## Install

```bash
pip install qitos
```

Development:

```bash
pip install -e .
pip install -e ".[models,yaml,benchmarks]"
```

Validate the supported framework suite from the repo root:

```bash
python -m pytest -q
```

## Quick Start

Run a minimal end-to-end flow:

```bash
python examples/quickstart/minimal_agent.py
```

Run a pattern-based agent:

```bash
export OPENAI_BASE_URL="https://api.siliconflow.cn/v1/"
export OPENAI_API_KEY="<your_api_key>"
python examples/patterns/react.py
```

Inspect trajectories:

```bash
qita board --logdir runs
```

Primary examples are now intentionally self-contained:

- top-level constants for task, workspace, and model defaults
- `from qitos.kit import ...` for common practical components
- direct `agent.run(...)`
- terminal UI and trace enabled by default

## AgentModule + Engine Mindset

QitOS keeps responsibilities explicit:

- `AgentModule`: your strategy layer. Define state, prompts, decision policy, and reduction logic.
- `Engine`: your execution layer. Orchestrate lifecycle, tool execution, stop checks, tracing, and hooks.

This separation gives you a clean place to innovate on agent intelligence without rebuilding runtime infrastructure.

## Minimal SWE Agent (Requirement to PR)

```python
from dataclasses import dataclass, field
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema, ToolRegistry
from qitos.kit import EditorToolSet, MarkdownFileMemory, ReActTextParser, RunCommand

SWE_REACT_SYSTEM_PROMPT = """
You are a senior software engineer agent that delivers requirement-complete, PR-ready patches.

Follow ReAct format on every step:
Thought: concise reasoning about the next best move.
Action: exactly one tool call with concrete arguments.

Output contract (MUST follow exactly):
Thought: <your reasoning>
Action: <tool_name>(arg1="...", arg2="...")

Rules:
- Always inspect code before editing.
- Make small, verifiable changes.
- Run checks/tests after edits.
- If a check fails, diagnose and fix, then re-run.
- Keep actions grounded in observed outputs.
- Prefer deterministic edits over speculative rewrites.
""".strip()


@dataclass
class SWEState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = "buggy_module.py"
    test_command: str = 'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'


class MinimalSWEAgent(AgentModule[SWEState, dict[str, Any], Action]):
    def __init__(self, llm: Any, workspace_root: str):
        reg = ToolRegistry()
        reg.include(EditorToolSet(workspace_root=workspace_root))
        reg.register(RunCommand(cwd=workspace_root))
        super().__init__(
            tool_registry=reg,
            llm=llm,
            model_parser=ReActTextParser(),
            memory=MarkdownFileMemory(path=f"{workspace_root}/memory.md"),
        )

    def init_state(self, task: str, **kwargs: Any) -> SWEState:
        return SWEState(task=task, max_steps=int(kwargs.get("max_steps", 12)))

    def build_system_prompt(self, state: SWEState) -> str | None:
        return SWE_REACT_SYSTEM_PROMPT

    def prepare(self, state: SWEState) -> str:
        return (
            f"Task: {state.task}\n"
            f"Target file: {state.target_file}\n"
            f"Test command: {state.test_command}\n"
            f"Step: {state.current_step}/{state.max_steps}"
        )

    def decide(self, state: SWEState, observation: dict[str, Any]):
        return None  # Engine model path: prepare -> llm -> parser

    def reduce(self, state: SWEState, observation: dict[str, Any], decision: Decision[Action]) -> SWEState:
        results = observation.get("action_results", [])
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {decision.actions[0]}")
        if results:
            state.scratchpad.append(f"Observation: {results[0]}")
            if isinstance(results[0], dict) and int(results[0].get("returncode", 1)) == 0:
                state.final_result = "Requirement implemented and verification passed."
        state.scratchpad = state.scratchpad[-40:]
        return state


# llm = ...
# agent = MinimalSWEAgent(llm=llm, workspace_root="./playground")
# result = agent.run(
#     task="Implement the requirement and make checks pass.",
#     workspace="./playground",
#     max_steps=12,
#     return_state=True,
# )
# print(result.state.final_result)
# print(result.state.stop_reason)
```

`agent.run(...)` is the blessed path. By default it gives you:

- terminal render
- trace artifacts in `runs/`
- workspace-local render event logs when `workspace=...` is provided

## Prompt-Parser Contract (Critical)

Your prompt format and parser must match. This is a hard contract, not a style preference.

- `ReActTextParser` expects `Thought:` + `Action:` style plain text output.
- If you switch to XML output, use an XML parser and enforce XML tags in the system prompt.
- If you switch to JSON output, use a JSON parser and enforce strict JSON schema in the prompt.
- Do not change output format without changing parser.

Quick mapping:

- ReAct text prompt -> `ReActTextParser`
- XML prompt (`<think>...</think><action>...</action>`) -> `XML parser`
- JSON prompt (`{"thought": "...", "action": {...}}`) -> `JSON parser`

## What You Can Build

Agent patterns:
- `examples/patterns/react.py`
- `examples/patterns/planact.py`
- `examples/patterns/tot.py`
- `examples/patterns/reflexion.py`

Real scenarios:
- `examples/real/coding_agent.py`
- `examples/real/swe_agent.py`
- `examples/real/computer_use_agent.py`
- `examples/real/epub_reader_agent.py`

## Benchmark and Evaluation

QitOS standardizes the path:

`dataset row -> adapter -> Task -> Engine -> evaluate -> metric report`

Built-in adapters:
- `qitos.benchmark.gaia`
- `qitos.benchmark.tau_bench`
- `qitos.benchmark.cybench`

Evaluation stack:
- `qitos.evaluate` for per-task outcome judgment
- `qitos.metric` for benchmark-level reporting
- `qitos.kit` with rule-based / DSL-based / model-based evaluators and common metrics

## Observability with qita

- `qita board`: run overview and summary
- `qita view`: structured trajectory inspection
- `qita replay`: execution playback
- `qita export`: JSON / HTML artifact export

## Project Structure

- `qitos/core/`: interfaces and contracts
- `qitos/engine/`: execution kernel
- `qitos/kit/`: reusable modules (tools, parsers, planning, memory, eval)
- `qitos/benchmark/`: benchmark adapters
- `qitos/qita/`: trajectory tooling

## Docs

- Main docs: [https://qitor.github.io/qitos/](https://qitor.github.io/qitos/)
- API reference: [https://qitor.github.io/qitos/reference/api_generated/](https://qitor.github.io/qitos/reference/api_generated/)
- Chinese docs: [https://qitor.github.io/qitos/zh/](https://qitor.github.io/qitos/zh/)

## License

MIT. See [LICENSE](LICENSE).
