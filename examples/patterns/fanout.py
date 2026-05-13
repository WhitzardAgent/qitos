"""Pattern: FanOutTool — coordinator agent dispatches parallel explorer sub-agents."""

from __future__ import annotations

import os
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
    StateSchema,
    ToolRegistry,
)
from qitos.kit import (
    CodingToolSet,
    REACT_SYSTEM_PROMPT,
    ReActTextParser,
    format_action,
    render_prompt,
)
from qitos.models import OpenAICompatibleModel

WORKSPACE = Path("./playground/fanout_pattern")
MODEL_NAME = os.getenv("QITOS_MODEL", "glm-5.1-w4a8")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://ekkmopeh8ecgccbjjb9johhhd5dcabcc.openapi-sj.sii.edu.cn/v1/")
MAX_STEPS = 12


# ── Explorer sub-agent ───────────────────────────────────────────────────

@dataclass
class ExplorerState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_dir: str = ""


EXPLORER_PROMPT = """\
You are a code explorer agent. You inspect directories and files to understand
their structure, purpose, and key components.

Rules:
- Use at most one tool call per response.
- Focus on understanding the structure and purpose of the code.
- Report your findings concisely.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <summary of findings>
"""


class ExplorerAgent(AgentModule[ExplorerState, dict[str, Any], Action]):
    """Explores a directory and reports findings."""

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

    def init_state(self, task: str, **kwargs: Any) -> ExplorerState:
        return ExplorerState(task=task, max_steps=int(kwargs.get("max_steps", 5)))

    def build_system_prompt(self, state: ExplorerState) -> str | None:
        return render_prompt(
            EXPLORER_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: ExplorerState) -> str:
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
        state: ExplorerState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> ExplorerState:
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
        state.scratchpad = state.scratchpad[-20:]
        return state


# ── Coordinator agent ────────────────────────────────────────────────────

@dataclass
class CoordinatorState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)


COORDINATOR_PROMPT = """\
You are a code analysis coordinator. You use the fanout tool to dispatch
multiple explorer agents in parallel to investigate different parts of a codebase.

When you need to understand a codebase structure:
1. Use the fanout tool to dispatch explorer agents to different directories.
2. Review the aggregated results.
3. Synthesize a final analysis.

Rules:
- Use at most one tool call per response.
- When dispatching, give each explorer a clear, specific directory to investigate.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <final analysis>
"""


class CoordinatorAgent(AgentModule[CoordinatorState, dict[str, Any], Action]):
    """Coordinator that dispatches parallel explorers."""

    def __init__(self, llm: Any, workspace_root: str, agent_registry: AgentRegistry):
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
        # Register delegation and fanout tools
        for delegate_tool in agent_registry.get_delegate_tools():
            registry.register(delegate_tool)
        registry.register(agent_registry.get_fanout_tool())

        super().__init__(
            tool_registry=registry, llm=llm, model_parser=ReActTextParser()
        )

    def init_state(self, task: str, **kwargs: Any) -> CoordinatorState:
        return CoordinatorState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def build_system_prompt(self, state: CoordinatorState) -> str | None:
        return render_prompt(
            COORDINATOR_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: CoordinatorState) -> str:
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
        state: CoordinatorState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> CoordinatorState:
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


# ── Main ─────────────────────────────────────────────────────────────────


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


def main() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    # Create a sample directory structure for exploration
    for subdir in ["auth", "api", "db"]:
        d = WORKSPACE / subdir
        d.mkdir(exist_ok=True)
        (d / "__init__.py").write_text(f"# {subdir} module\n", encoding="utf-8")
        (d / "models.py").write_text(
            f"class {subdir.title()}Model:\n    pass\n", encoding="utf-8"
        )
    (WORKSPACE / "main.py").write_text(
        "from auth import AuthModel\nfrom api import ApiModel\nfrom db import DbModel\n",
        encoding="utf-8",
    )

    llm = build_model()

    # Create explorer agent spec
    explorer_spec = AgentSpec(
        name="explorer",
        description="Explores a directory and reports its structure and key files",
        agent=ExplorerAgent(llm=llm, workspace_root=str(WORKSPACE)),
        context_strategy=ContextStrategy.ISOLATED,
        max_steps_override=5,
    )

    agent_registry = AgentRegistry()
    agent_registry.register(explorer_spec)

    coordinator = CoordinatorAgent(
        llm=llm,
        workspace_root=str(WORKSPACE),
        agent_registry=agent_registry,
    )

    result = coordinator.run(
        task="Analyze the structure of this codebase. Investigate the auth, api, and db modules in parallel.",
        workspace=str(WORKSPACE),
        max_steps=MAX_STEPS,
        return_state=True,
    )
    print("workspace:", WORKSPACE)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
