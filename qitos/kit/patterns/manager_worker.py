"""Manager-Worker pattern: coordinator dispatches parallel tasks via FanOutTool.

Usage:
    from qitos.kit.patterns import build_manager_worker_system, ManagerWorkerConfig

    config = ManagerWorkerConfig(worker_name="explorer", workspace_root="/path")
    coordinator, registry = build_manager_worker_system(config)
    result = coordinator.run(task="Analyze the codebase structure")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ...core.agent_spec import AgentRegistry, AgentSpec, ContextStrategy
from ...core.tool_registry import ToolRegistry


@dataclass
class ManagerWorkerConfig:
    """Configuration for the manager-worker pattern."""

    worker_name: str = "worker"
    worker_description: str = "Executes a subtask and returns the result"
    worker_max_steps: int = 5
    worker_context_strategy: ContextStrategy = ContextStrategy.ISOLATED
    manager_max_steps: int = 12
    manager_system_prompt: str = ""
    worker_system_prompt: str = ""
    max_workers: int = 4
    workspace_root: str = "."
    llm: Any = None
    toolset_factory: Optional[Any] = None


def build_manager_worker_system(
    config: ManagerWorkerConfig,
) -> tuple[Any, AgentRegistry]:
    """Build a manager-worker multi-agent system.

    Returns:
        (coordinator_agent, agent_registry) tuple.
    """
    from ..prompts import REACT_SYSTEM_PROMPT, render_prompt
    from ..parser import ReActTextParser
    from ..tool import CodingToolSet

    agent_registry = AgentRegistry()

    # --- Worker agent ---

    worker_prompt = config.worker_system_prompt or _DEFAULT_WORKER_PROMPT

    class WorkerAgent(_build_react_agent_class()):
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

        def build_system_prompt(self, state):
            return render_prompt(worker_prompt, {"tool_schema": self.tool_registry.get_tool_descriptions()})

    worker_spec = AgentSpec(
        name=config.worker_name,
        description=config.worker_description,
        agent=WorkerAgent(),
        context_strategy=config.worker_context_strategy,
        max_steps_override=config.worker_max_steps,
    )
    agent_registry.register(worker_spec)

    # --- Manager (coordinator) agent ---

    manager_prompt = config.manager_system_prompt or _DEFAULT_MANAGER_PROMPT

    class ManagerAgent(_build_react_agent_class()):
        def __init__(self):
            from ...core.state import StateSchema

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
            # Register delegation and fanout tools
            for delegate_tool in agent_registry.get_delegate_tools():
                registry.register(delegate_tool)
            registry.register(agent_registry.get_fanout_tool(max_workers=config.max_workers))

            super().__init__(
                tool_registry=registry,
                llm=config.llm,
                model_parser=ReActTextParser(),
            )

        def build_system_prompt(self, state):
            return render_prompt(manager_prompt, {"tool_schema": self.tool_registry.get_tool_descriptions()})

    return ManagerAgent(), agent_registry


def _build_react_agent_class():
    """Build a base ReAct agent class with standard reduce/prepare."""
    from ...core.agent_module import AgentModule
    from ...core.state import StateSchema
    from ...core.decision import Decision
    from ...core.action import Action
    from ..planning import format_action

    @dataclass
    class _ReActState(StateSchema):
        scratchpad: list[str] = field(default_factory=list)

    class _ReActAgent(AgentModule[_ReActState, dict[str, Any], Action]):
        def init_state(self, task: str, **kwargs: Any) -> _ReActState:
            return _ReActState(task=task, max_steps=int(kwargs.get("max_steps", 10)))

        def prepare(self, state: _ReActState) -> str:
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
            state: _ReActState,
            observation: dict[str, Any],
            decision: Decision[Action],
        ) -> _ReActState:
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

    return _ReActAgent


_DEFAULT_MANAGER_PROMPT = """\
You are a coordinator agent. You use the fanout tool to dispatch multiple
worker agents in parallel to investigate different aspects of a task.

When you need to handle a complex task:
1. Break it into parallel subtasks.
2. Use the fanout tool to dispatch worker agents.
3. Review the aggregated results.
4. Synthesize a final answer.

Rules:
- Use at most one tool call per response.
- Give each worker a clear, specific subtask.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <final answer>
"""

_DEFAULT_WORKER_PROMPT = """\
You are a worker agent. You execute a specific subtask and report findings.

Rules:
- Use at most one tool call per response.
- Focus on the specific subtask assigned to you.
- Report findings concisely.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <summary of findings>
"""
