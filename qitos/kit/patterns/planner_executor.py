"""Planner-Executor pattern: planner creates a plan, executor carries it out.

This pattern uses Decision.handoff() for linear control transfer:
planner → executor (workflow-style, no return).

Usage:
    from qitos.kit.patterns import build_planner_executor_system, PlannerExecutorConfig

    config = PlannerExecutorConfig(workspace_root="/path")
    engine = build_planner_executor_system(config)
    result = engine.run(task="Fix the bug in auth.py")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ...core.agent_spec import AgentRegistry, AgentSpec, ContextStrategy, HandoffContext
from ...core.tool_registry import ToolRegistry
from ...engine.engine import Engine


@dataclass
class PlannerExecutorConfig:
    """Configuration for the planner-executor pattern."""

    planner_max_steps: int = 3
    executor_max_steps: int = 10
    planner_system_prompt: str = ""
    executor_system_prompt: str = ""
    workspace_root: str = "."
    llm: Any = None
    toolset_factory: Optional[Any] = None
    shared_state_fields: list[str] = field(default_factory=list)


def build_planner_executor_system(
    config: PlannerExecutorConfig,
) -> Engine:
    """Build a planner-executor multi-agent system.

    The planner analyzes the task and creates a plan, then hands off
    to the executor who carries it out.

    Returns:
        An Engine configured with the planner as the initial agent.
    """
    from ..prompts import REACT_SYSTEM_PROMPT, render_prompt
    from ..parser import ReActTextParser
    from ..tool import CodingToolSet
    from ...core.state import StateSchema
    from ...core.decision import Decision
    from ...core.action import Action
    from ...core.agent_module import AgentModule
    from ..planning import format_action

    @dataclass
    class PlanState(StateSchema):
        scratchpad: list[str] = field(default_factory=list)
        plan: str = ""

    planner_prompt = config.planner_system_prompt or _DEFAULT_PLANNER_PROMPT
    executor_prompt = config.executor_system_prompt or _DEFAULT_EXECUTOR_PROMPT

    # --- Planner agent ---

    class PlannerAgent(AgentModule[PlanState, dict[str, Any], Action]):
        def __init__(self):
            registry = ToolRegistry()
            # Planner gets read-only tools for analysis
            if config.toolset_factory:
                registry.include(config.toolset_factory(workspace_root=config.workspace_root))
            else:
                registry.include(
                    CodingToolSet(
                        workspace_root=config.workspace_root,
                        include_notebook=False,
                        enable_lsp=False,
                        enable_tasks=False,
                        enable_web=False,
                        expose_modern_names=False,
                    )
                )
            super().__init__(
                tool_registry=registry,
                llm=config.llm,
                model_parser=ReActTextParser(),
            )

        def init_state(self, task: str, **kwargs: Any) -> PlanState:
            return PlanState(task=task, max_steps=int(kwargs.get("max_steps", config.planner_max_steps)))

        def build_system_prompt(self, state):
            return render_prompt(planner_prompt, {"tool_schema": self.tool_registry.get_tool_descriptions()})

        def prepare(self, state):
            lines = [f"Task: {state.task}", f"Step: {state.current_step}/{state.max_steps}"]
            if state.scratchpad:
                lines.append("Recent trajectory:")
                lines.extend(state.scratchpad[-8:])
            return "\n".join(lines)

        def reduce(self, state, observation, decision):
            action_results = observation.get("action_results", []) if isinstance(observation, dict) else []
            if decision.rationale:
                state.scratchpad.append(f"Thought: {decision.rationale}")
            if decision.actions:
                state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
            if action_results:
                state.scratchpad.append(f"Observation: {action_results[0]}")
            state.scratchpad = state.scratchpad[-20:]
            return state

    # --- Executor agent ---

    class ExecutorAgent(AgentModule[PlanState, dict[str, Any], Action]):
        def __init__(self):
            registry = ToolRegistry()
            if config.toolset_factory:
                registry.include(config.toolset_factory(workspace_root=config.workspace_root))
            else:
                registry.include(
                    CodingToolSet(
                        workspace_root=config.workspace_root,
                        include_notebook=False,
                        enable_lsp=False,
                        enable_tasks=False,
                        enable_web=False,
                        expose_modern_names=False,
                    )
                )
            super().__init__(
                tool_registry=registry,
                llm=config.llm,
                model_parser=ReActTextParser(),
            )

        def init_state(self, task: str, **kwargs: Any) -> PlanState:
            return PlanState(task=task, max_steps=int(kwargs.get("max_steps", config.executor_max_steps)))

        def build_system_prompt(self, state):
            return render_prompt(executor_prompt, {"tool_schema": self.tool_registry.get_tool_descriptions()})

        def prepare(self, state):
            lines = [f"Task: {state.task}", f"Step: {state.current_step}/{state.max_steps}"]
            if state.plan:
                lines.append(f"Plan:\n{state.plan}")
            if state.scratchpad:
                lines.append("Recent trajectory:")
                lines.extend(state.scratchpad[-8:])
            return "\n".join(lines)

        def reduce(self, state, observation, decision):
            action_results = observation.get("action_results", []) if isinstance(observation, dict) else []
            if decision.rationale:
                state.scratchpad.append(f"Thought: {decision.rationale}")
            if decision.actions:
                state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
            if action_results:
                state.scratchpad.append(f"Observation: {action_results[0]}")
            state.scratchpad = state.scratchpad[-20:]
            return state

    # Build registry
    handoff_ctx = None
    if config.shared_state_fields:
        handoff_ctx = HandoffContext(shared_state_fields=config.shared_state_fields)

    registry = AgentRegistry()
    registry.register(AgentSpec(
        name="planner",
        description="Analyzes task and creates a plan",
        agent=PlannerAgent(),
        context_strategy=ContextStrategy.SUMMARY,
        max_steps_override=config.planner_max_steps,
    ))
    registry.register(AgentSpec(
        name="executor",
        description="Executes the plan step by step",
        agent=ExecutorAgent(),
        context_strategy=ContextStrategy.FULL,
        max_steps_override=config.executor_max_steps,
        handoff_context=handoff_ctx,
    ))

    return Engine(
        agent=PlannerAgent(),
        agent_registry=registry,
    )


_DEFAULT_PLANNER_PROMPT = """\
You are a planning agent. Analyze the task and create a step-by-step plan.

Once you have a clear plan, hand off to the executor agent to carry it out.

Rules:
- Use at most one tool call per response.
- Gather just enough information to create a plan.
- When ready, use handoff to transfer to the executor.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <plan for the executor>
"""

_DEFAULT_EXECUTOR_PROMPT = """\
You are an execution agent. You receive a plan and carry it out step by step.

Rules:
- Use at most one tool call per response.
- Follow the plan precisely.
- Adapt if something doesn't work as expected.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <result of execution>
"""
