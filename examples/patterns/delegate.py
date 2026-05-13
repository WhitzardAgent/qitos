"""Pattern: DelegateTool — a coding agent delegates research to a sub-agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, AgentRegistry, AgentSpec, ContextStrategy, Decision, StateSchema, ToolRegistry
from qitos.kit import (
    CodingToolSet,
    REACT_SYSTEM_PROMPT,
    ReActTextParser,
    format_action,
    render_prompt,
)
from qitos.models import OpenAICompatibleModel

TASK = (
    "Find out what functions are defined in buggy_module.py, "
    "then fix add(a, b) so it returns a + b, then run verification."
)
WORKSPACE = Path("./playground/delegate_pattern")
MODEL_NAME = os.getenv("QITOS_MODEL", "glm-5.1-w4a8")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://ekkmopeh8ecgccbjjb9johhhd5dcabcc.openapi-sj.sii.edu.cn/v1/")
MAX_STEPS = 10


# ── Sub-agent: simple researcher ─────────────────────────────────────────

@dataclass
class ResearcherState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = "buggy_module.py"


class ResearcherAgent(AgentModule[ResearcherState, dict[str, Any], Action]):
    """Read-only agent that inspects files and reports findings."""

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

    def init_state(self, task: str, **kwargs: Any) -> ResearcherState:
        return ResearcherState(
            task=task, max_steps=int(kwargs.get("max_steps", 5))
        )

    def build_system_prompt(self, state: ResearcherState) -> str | None:
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: ResearcherState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: ResearcherState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> ResearcherState:
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


# ── Parent agent: coder with delegation ──────────────────────────────────

@dataclass
class CoderState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = "buggy_module.py"
    test_command: str = (
        'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'
    )


DELEGATE_SYSTEM_PROMPT = """\
You are a coding agent that can delegate research tasks to a sub-agent.

Rules:
- Use at most one tool call per response.
- If you need to inspect or understand code first, delegate to the researcher.
- After getting research results, proceed with coding fixes yourself.
- Never invent tool names or arguments.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <final answer only>
"""


class CoderAgent(AgentModule[CoderState, dict[str, Any], Action]):
    """Coding agent that can delegate research to a sub-agent."""

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
        # Inject delegation tools
        for delegate_tool in agent_registry.get_delegate_tools():
            registry.register(delegate_tool)

        super().__init__(
            tool_registry=registry, llm=llm, model_parser=ReActTextParser()
        )

    def init_state(self, task: str, **kwargs: Any) -> CoderState:
        return CoderState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def build_system_prompt(self, state: CoderState) -> str | None:
        return render_prompt(
            DELEGATE_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: CoderState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"Verification command: {state.test_command}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: CoderState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> CoderState:
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
                state.final_result = "Patch applied and verification passed."
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
    target = WORKSPACE / "buggy_module.py"
    if not target.exists():
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

    llm = build_model()

    # Build the sub-agent
    researcher = ResearcherAgent(llm=llm, workspace_root=str(WORKSPACE))

    # Register it in an AgentRegistry
    agent_registry = AgentRegistry()
    agent_registry.register(
        AgentSpec(
            name="researcher",
            description=(
                "Delegate a research subtask to find information about code, "
                "file contents, or project structure. Use this when you need to "
                "inspect or understand code before making changes."
            ),
            agent=researcher,
            context_strategy=ContextStrategy.ISOLATED,
            max_steps_override=5,
        )
    )

    # Build the parent agent with delegation tools
    coder = CoderAgent(
        llm=llm,
        workspace_root=str(WORKSPACE),
        agent_registry=agent_registry,
    )

    result = coder.run(
        task=TASK,
        workspace=str(WORKSPACE),
        max_steps=MAX_STEPS,
        return_state=True,
    )
    print("workspace:", WORKSPACE)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
