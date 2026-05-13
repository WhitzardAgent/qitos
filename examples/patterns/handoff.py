"""Pattern: Decision.handoff() — triage agent hands off control to a specialist."""

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
    Engine,
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

TASK = "Find and fix the bug in buggy_module.py so that add(20, 22) returns 42."
WORKSPACE = Path("./playground/handoff_pattern")
MODEL_NAME = os.getenv("QITOS_MODEL", "glm-5.1-w4a8")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://ekkmopeh8ecgccbjjb9johhhd5dcabcc.openapi-sj.sii.edu.cn/v1/")
MAX_STEPS = 10


# ── Shared state ─────────────────────────────────────────────────────────


@dataclass
class SharedState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = "buggy_module.py"
    test_command: str = (
        'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'
    )
    current_agent: str = "triage"


# ── Triage agent: inspects and hands off ─────────────────────────────────


class TriageAgent(AgentModule[SharedState, dict[str, Any], Action]):
    """First-responder that inspects the task and hands off to a specialist."""

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

    def init_state(self, task: str, **kwargs: Any) -> SharedState:
        return SharedState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def decide(self, state: SharedState, observation: dict[str, Any]) -> Decision[Action] | None:
        """After the first inspection step, hand off to the coder."""
        # On the very first step, let the LLM inspect the file.
        # On the second step (after inspection), hand off.
        if state.current_step >= 1:
            return Decision.handoff(
                target="coder",
                rationale="Initial inspection complete. Handing off to coder to apply the fix.",
            )
        return None  # let the LLM decide the first step

    def build_system_prompt(self, state: SharedState) -> str | None:
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: SharedState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"Your job: Inspect the file to understand the bug. Then hand off to the coder agent.",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: SharedState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> SharedState:
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


class CoderAgent(AgentModule[SharedState, dict[str, Any], Action]):
    """Specialist that receives the task after triage and applies the fix."""

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

    def init_state(self, task: str, **kwargs: Any) -> SharedState:
        # Not called during handoff — state already exists
        return SharedState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def build_system_prompt(self, state: SharedState) -> str | None:
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: SharedState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"Verification command: {state.test_command}",
            f"You are the coder agent. Apply the fix and verify.",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Previous context (from triage agent):")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: SharedState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> SharedState:
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

    triage = TriageAgent(llm=llm, workspace_root=str(WORKSPACE))
    coder = CoderAgent(llm=llm, workspace_root=str(WORKSPACE))

    agent_registry = AgentRegistry()
    agent_registry.register(
        AgentSpec(
            name="triage",
            description="Triage agent that inspects files",
            agent=triage,
        )
    )
    agent_registry.register(
        AgentSpec(
            name="coder",
            description="Coder agent that applies fixes and verifies",
            agent=coder,
        )
    )

    engine = Engine(
        agent=triage,
        agent_registry=agent_registry,
        budget=None,
    )
    result = engine.run(TASK, workspace=str(WORKSPACE), max_steps=MAX_STEPS)

    print("workspace:", WORKSPACE)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
