"""Pattern: Benchmark-Style Multi-Agent Comparison — single-agent vs handoff.

Demonstrates:
- Running the same task under two configurations
- Comparing token usage, steps, and outcome
- Using Engine.run() with step budgets for benchmark-style evaluation
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import (
    Action,
    AgentModule,
    AgentRegistry,
    AgentSpec,
    ContextStrategy,
    Decision,
    Engine,
    StateSchema,
    ToolRegistry,
)
from qitos.core.shared_memory import InMemorySharedMemory
from qitos.kit import (
    CodingToolSet,
    REACT_SYSTEM_PROMPT,
    ReActTextParser,
    format_action,
    render_prompt,
)
from qitos.models import OpenAICompatibleModel

MODEL_NAME = os.getenv("QITOS_MODEL", "glm-5.1-w4a8")
MODEL_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://ekkmopeh8ecgccbjjb9johhhd5dcabcc.openapi-sj.sii.edu.cn/v1/",
)
MAX_STEPS = 10

# Benchmark tasks: each has a buggy file and a verification command
BENCHMARK_TASKS = [
    {
        "name": "add_bug",
        "file_content": "def add(a, b):\n    return a - b\n",
        "task": "Fix the bug in target.py so that add(20, 22) returns 42.",
        "verify_cmd": "python3 -c 'from target import add; assert add(20, 22) == 42'",
    },
    {
        "name": "multiply_bug",
        "file_content": "def multiply(a, b):\n    return a + b\n",
        "task": "Fix the bug in target.py so that multiply(6, 7) returns 42.",
        "verify_cmd": "python3 -c 'from target import multiply; assert multiply(6, 7) == 42'",
    },
    {
        "name": "is_even_bug",
        "file_content": "def is_even(n):\n    return n % 2 == 1\n",
        "task": "Fix the bug in target.py so that is_even(4) returns True.",
        "verify_cmd": "python3 -c 'from target import is_even; assert is_even(4) == True'",
    },
]


# ── Shared state ─────────────────────────────────────────────────────────


@dataclass
class BenchState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)


# ── Single agent: does everything itself ─────────────────────────────────


class SingleAgent(AgentModule[BenchState, dict[str, Any], Action]):
    """A single agent that reads, fixes, and verifies."""

    name = "single"

    def __init__(self, llm: Any, workspace_root: str):
        registry = ToolRegistry()
        registry.include(
            CodingToolSet(
                workspace_root=workspace_root,
                include_notebook=False,
                enable_lsp=False,
                enable_tasks=False,
                enable_web=False,
                expose_modern_names=False,
            )
        )
        super().__init__(
            tool_registry=registry, llm=llm, model_parser=ReActTextParser()
        )

    def init_state(self, task: str, **kwargs: Any) -> BenchState:
        return BenchState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def build_system_prompt(self, state: BenchState) -> str | None:
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: BenchState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: BenchState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> BenchState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if action_results:
            first = action_results[0]
            state.scratchpad.append(f"Observation: {first}")
            if isinstance(first, dict) and int(first.get("returncode", 1)) == 0:
                state.final_result = "Fix applied and verified."
        state.scratchpad = state.scratchpad[-30:]
        return state


# ── Triage agent: reads and hands off ────────────────────────────────────


class TriageAgent(AgentModule[BenchState, dict[str, Any], Action]):
    """Triage agent that inspects and hands off to the coder."""

    name = "triage"

    def __init__(self, llm: Any, workspace_root: str):
        registry = ToolRegistry()
        registry.include(
            CodingToolSet(
                workspace_root=workspace_root,
                include_notebook=False,
                enable_lsp=False,
                enable_tasks=False,
                enable_web=False,
                expose_modern_names=False,
            )
        )
        super().__init__(
            tool_registry=registry, llm=llm, model_parser=ReActTextParser()
        )

    def init_state(self, task: str, **kwargs: Any) -> BenchState:
        return BenchState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def decide(
        self, state: BenchState, observation: dict[str, Any]
    ) -> Decision[Action] | None:
        if state.current_step >= 1:
            return Decision.handoff(
                target="coder",
                rationale="Inspection complete. Delegating to coder.",
                handoff_message="Fix the bug in target.py and verify.",
            )
        return None

    def build_system_prompt(self, state: BenchState) -> str | None:
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: BenchState) -> str:
        lines = [
            f"Task: {state.task}",
            f"You are the triage agent. Read the code, then hand off to coder.",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: BenchState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> BenchState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if action_results:
            first = action_results[0]
            state.scratchpad.append(f"Observation: {first}")
        state.scratchpad = state.scratchpad[-30:]
        return state


# ── Coder agent: applies the fix ─────────────────────────────────────────


class CoderAgent(AgentModule[BenchState, dict[str, Any], Action]):
    """Coder specialist that applies fixes and verifies."""

    name = "coder"

    def __init__(self, llm: Any, workspace_root: str):
        registry = ToolRegistry()
        registry.include(
            CodingToolSet(
                workspace_root=workspace_root,
                include_notebook=False,
                enable_lsp=False,
                enable_tasks=False,
                enable_web=False,
                expose_modern_names=False,
            )
        )
        super().__init__(
            tool_registry=registry, llm=llm, model_parser=ReActTextParser()
        )

    def init_state(self, task: str, **kwargs: Any) -> BenchState:
        return BenchState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def build_system_prompt(self, state: BenchState) -> str | None:
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: BenchState) -> str:
        lines = [
            f"Task: {state.task}",
            f"You are the coder agent. Apply the fix and verify.",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Previous context (from triage):")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: BenchState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> BenchState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if action_results:
            first = action_results[0]
            state.scratchpad.append(f"Observation: {first}")
            if isinstance(first, dict) and int(first.get("returncode", 1)) == 0:
                state.final_result = "Fix applied and verified."
        state.scratchpad = state.scratchpad[-30:]
        return state


# ── Benchmark runner ─────────────────────────────────────────────────────


def build_model() -> OpenAICompatibleModel:
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "Set OPENAI_API_KEY or QITOS_API_KEY before running this example."
        )
    return OpenAICompatibleModel(
        model=MODEL_NAME,
        api_key=api_key,
        base_url=MODEL_BASE_URL,
        temperature=0.2,
        max_tokens=2048,
    )


def run_single_agent(llm: OpenAICompatibleModel, task: dict, workspace: Path) -> dict:
    """Run a single-agent configuration on one benchmark task."""
    target = workspace / "target.py"
    target.write_text(task["file_content"], encoding="utf-8")

    agent = SingleAgent(llm=llm, workspace_root=str(workspace))
    engine = Engine(agent=agent, agent_registry=AgentRegistry(), budget=None)
    result = engine.run(
        task["task"],
        workspace=str(workspace),
        max_steps=MAX_STEPS,
    )

    return {
        "config": "single-agent",
        "task": task["name"],
        "final_result": result.state.final_result,
        "stop_reason": result.state.stop_reason,
        "total_steps": result.state.current_step,
        "token_usage": result.state.token_usage if hasattr(result.state, "token_usage") else None,
    }


def run_multi_agent(llm: OpenAICompatibleModel, task: dict, workspace: Path) -> dict:
    """Run a multi-agent (triage → coder) configuration on one benchmark task."""
    target = workspace / "target.py"
    target.write_text(task["file_content"], encoding="utf-8")

    shared_mem = InMemorySharedMemory()

    triage = TriageAgent(llm=llm, workspace_root=str(workspace))
    coder = CoderAgent(llm=llm, workspace_root=str(workspace))

    agent_registry = AgentRegistry()
    agent_registry.register(
        AgentSpec(
            name="triage",
            description="Triage agent that inspects and delegates",
            agent=triage,
        )
    )
    agent_registry.register(
        AgentSpec(
            name="coder",
            description="Coder agent that applies fixes and verifies",
            agent=coder,
            context_strategy=ContextStrategy.SUMMARY,
            shared_memory=shared_mem,
        )
    )

    engine = Engine(
        agent=triage,
        agent_registry=agent_registry,
        budget=None,
    )
    result = engine.run(
        task["task"],
        workspace=str(workspace),
        max_steps=MAX_STEPS,
    )

    return {
        "config": "multi-agent (triage→coder)",
        "task": task["name"],
        "final_result": result.state.final_result,
        "stop_reason": result.state.stop_reason,
        "total_steps": result.state.current_step,
        "token_usage": result.state.token_usage if hasattr(result.state, "token_usage") else None,
    }


def print_comparison_table(results: list[dict]) -> None:
    """Print a comparison table of benchmark results."""
    print("\n" + "=" * 80)
    print("Benchmark Comparison: Single-Agent vs Multi-Agent (Triage→Coder)")
    print("=" * 80)
    print(f"{'Config':<28} {'Task':<16} {'Steps':<8} {'Stop Reason':<16} {'Result'}")
    print("-" * 80)
    for r in results:
        result_preview = (r.get("final_result") or "—")[:30]
        print(
            f"{r['config']:<28} {r['task']:<16} {r['total_steps']:<8} "
            f"{(r['stop_reason'] or '—'):<16} {result_preview}"
        )
    print("=" * 80)

    # Summary
    single_results = [r for r in results if r["config"] == "single-agent"]
    multi_results = [r for r in results if "multi-agent" in r["config"]]
    single_steps = sum(r["total_steps"] for r in single_results)
    multi_steps = sum(r["total_steps"] for r in multi_results)
    print(f"\nTotal steps — single-agent: {single_steps}, multi-agent: {multi_steps}")


def main() -> None:
    llm = build_model()
    results: list[dict] = []

    for task in BENCHMARK_TASKS:
        # Single-agent run
        with tempfile.TemporaryDirectory(prefix=f"bench_single_{task['name']}_") as ws:
            results.append(run_single_agent(llm, task, Path(ws)))

        # Multi-agent run
        with tempfile.TemporaryDirectory(prefix=f"bench_multi_{task['name']}_") as ws:
            results.append(run_multi_agent(llm, task, Path(ws)))

    print_comparison_table(results)


if __name__ == "__main__":
    main()
