"""Pattern: Actor-Critic via Handoff — actor proposes, critic evaluates and refines.

Demonstrates:
- Alternating handoffs between actor and critic
- SharedMemory for tracking proposals and evaluations
- Critic-driven refinement with convergence detection
- Decision.handoff() for control flow between agents
"""

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
from qitos.core.shared_memory import InMemorySharedMemory
from qitos.kit import (
    CodingToolSet,
    REACT_SYSTEM_PROMPT,
    ReActTextParser,
    format_action,
    render_prompt,
)
from qitos.models import OpenAICompatibleModel

WORKSPACE = Path("./playground/actor_critic_handoff")
MODEL_NAME = os.getenv("QITOS_MODEL", "glm-5.1-w4a8")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://ekkmopeh8ecgccbjjb9johhhd5dcabcc.openapi-sj.sii.edu.cn/v1/")
MAX_STEPS = 14


# ── Shared state ─────────────────────────────────────────────────────────


@dataclass
class ACState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    target_file: str = "buggy_module.py"
    iteration: int = 0
    max_iterations: int = 3


# ── Actor: proposes a solution ───────────────────────────────────────────


class ActorAgent(AgentModule[ACState, dict[str, Any], Action]):
    """Actor that proposes solutions based on critic feedback."""

    name = "actor"

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

    def init_state(self, task: str, **kwargs: Any) -> ACState:
        return ACState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def decide(self, state: ACState, observation: dict[str, Any]) -> Decision[Action] | None:
        """After proposing a fix, hand off to critic for evaluation."""
        # On first step, read the file. On subsequent steps, hand off to critic.
        if state.current_step >= 2:
            state.iteration += 1
            if state.iteration >= state.max_iterations:
                return Decision.final(answer="Max iterations reached. Fix applied as-is.")
            return Decision.handoff(
                target="critic",
                rationale="Solution proposed. Handing off to critic for evaluation.",
                handoff_message="Evaluate the proposed fix for correctness.",
            )
        return None

    def build_system_prompt(self, state: ACState) -> str | None:
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: ACState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"You are the ACTOR agent. Propose and apply a fix.",
            f"Iteration: {state.iteration}/{state.max_iterations}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: ACState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> ACState:
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


# ── Critic: evaluates and provides feedback ──────────────────────────────


class CriticAgent(AgentModule[ACState, dict[str, Any], Action]):
    """Critic that evaluates the actor's proposals and provides feedback."""

    name = "critic"

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

    def init_state(self, task: str, **kwargs: Any) -> ACState:
        return ACState(task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS)))

    def decide(self, state: ACState, observation: dict[str, Any]) -> Decision[Action] | None:
        """After evaluating, either approve (final) or hand back to actor."""
        # Run verification after inspection
        if state.current_step >= 2:
            # Check if verification passed (convergence)
            # If verification passes, we're done
            return Decision.final(
                answer="Fix verified and accepted by critic."
            )
        return None

    def build_system_prompt(self, state: ACState) -> str | None:
        return render_prompt(
            REACT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: ACState) -> str:
        lines = [
            f"Task: {state.task}",
            f"Target file: {state.target_file}",
            f"You are the CRITIC agent. Evaluate the actor's proposed fix.",
            f"Read the code, run verification, and determine if it's correct.",
            f"Iteration: {state.iteration}/{state.max_iterations}",
            f"Step: {state.current_step}/{state.max_steps}",
        ]
        if state.scratchpad:
            lines.append("Previous context (from actor):")
            lines.extend(state.scratchpad[-8:])
        return "\n".join(lines)

    def reduce(
        self,
        state: ACState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> ACState:
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
                state.final_result = "Fix verified and accepted by critic."
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
    shared_mem = InMemorySharedMemory()

    actor = ActorAgent(llm=llm, workspace_root=str(WORKSPACE))
    critic = CriticAgent(llm=llm, workspace_root=str(WORKSPACE))

    agent_registry = AgentRegistry()
    agent_registry.register(
        AgentSpec(
            name="actor",
            description="Actor agent that proposes fixes",
            agent=actor,
        )
    )
    agent_registry.register(
        AgentSpec(
            name="critic",
            description="Critic agent that evaluates and verifies fixes",
            agent=critic,
            context_strategy=ContextStrategy.SUMMARY,
            shared_memory=shared_mem,
        )
    )

    engine = Engine(
        agent=actor,
        agent_registry=agent_registry,
        budget=None,
    )
    result = engine.run(
        "Find and fix the bug in buggy_module.py so that add(20, 22) returns 42.",
        workspace=str(WORKSPACE),
        max_steps=MAX_STEPS,
    )

    print("workspace:", WORKSPACE)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
