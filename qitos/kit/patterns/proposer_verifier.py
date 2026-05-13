"""Proposer-Verifier pattern: one agent proposes, another verifies.

Useful for code review, security auditing, and quality assurance.
The proposer explores/investigates and produces findings, then delegates
to the verifier who validates them.

Usage:
    from qitos.kit.patterns import build_proposer_verifier_system, ProposerVerifierConfig

    config = ProposerVerifierConfig(workspace_root="/path")
    proposer, registry = build_proposer_verifier_system(config)
    result = proposer.run(task="Audit the auth module for security issues")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ...core.agent_spec import AgentRegistry, AgentSpec, ContextStrategy
from ...core.tool_registry import ToolRegistry


@dataclass
class ProposerVerifierConfig:
    """Configuration for the proposer-verifier pattern."""

    proposer_name: str = "proposer"
    verifier_name: str = "verifier"
    proposer_description: str = "Explores and proposes findings"
    verifier_description: str = "Verifies and validates proposals"
    proposer_max_steps: int = 8
    verifier_max_steps: int = 6
    proposer_context_strategy: ContextStrategy = ContextStrategy.ISOLATED
    verifier_context_strategy: ContextStrategy = ContextStrategy.FULL
    workspace_root: str = "."
    llm: Any = None
    toolset_factory: Optional[Any] = None
    proposer_system_prompt: str = ""
    verifier_system_prompt: str = ""


def build_proposer_verifier_system(
    config: ProposerVerifierConfig,
) -> tuple[Any, AgentRegistry]:
    """Build a proposer-verifier multi-agent system.

    The proposer explores and generates findings via FanOutTool,
    then the verifier validates those findings.

    Returns:
        (proposer_agent, agent_registry) tuple.
    """
    from ..prompts import render_prompt
    from ..parser import ReActTextParser
    from ..tool import CodingToolSet
    from ...core.state import StateSchema
    from ...core.decision import Decision
    from ...core.action import Action
    from ...core.agent_module import AgentModule
    from ..planning import format_action

    @dataclass
    class AuditState(StateSchema):
        scratchpad: list[str] = field(default_factory=list)

    agent_registry = AgentRegistry()

    proposer_prompt = config.proposer_system_prompt or _DEFAULT_PROPOSER_PROMPT
    verifier_prompt = config.verifier_system_prompt or _DEFAULT_VERIFIER_PROMPT

    # --- Verifier agent ---

    class VerifierAgent(AgentModule[AuditState, dict[str, Any], Action]):
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

        def init_state(self, task: str, **kwargs: Any) -> AuditState:
            return AuditState(task=task, max_steps=int(kwargs.get("max_steps", config.verifier_max_steps)))

        def build_system_prompt(self, state):
            return render_prompt(verifier_prompt, {"tool_schema": self.tool_registry.get_tool_descriptions()})

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

    verifier_spec = AgentSpec(
        name=config.verifier_name,
        description=config.verifier_description,
        agent=VerifierAgent(),
        context_strategy=config.verifier_context_strategy,
        max_steps_override=config.verifier_max_steps,
    )
    agent_registry.register(verifier_spec)

    # --- Proposer agent ---

    class ProposerAgent(AgentModule[AuditState, dict[str, Any], Action]):
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
            # Register delegation tools for verifier
            for delegate_tool in agent_registry.get_delegate_tools():
                registry.register(delegate_tool)
            registry.register(agent_registry.get_fanout_tool())

            super().__init__(
                tool_registry=registry,
                llm=config.llm,
                model_parser=ReActTextParser(),
            )

        def init_state(self, task: str, **kwargs: Any) -> AuditState:
            return AuditState(task=task, max_steps=int(kwargs.get("max_steps", config.proposer_max_steps)))

        def build_system_prompt(self, state):
            return render_prompt(proposer_prompt, {"tool_schema": self.tool_registry.get_tool_descriptions()})

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

    return ProposerAgent(), agent_registry


_DEFAULT_PROPOSER_PROMPT = """\
You are a proposer agent. You investigate and generate findings about a codebase.

Strategy:
1. Use the fanout tool to dispatch workers to investigate different areas.
2. Collect and organize the findings.
3. Delegate to the verifier agent to validate your findings.

Rules:
- Use at most one tool call per response.
- Be thorough in your exploration.
- Present clear, specific findings for verification.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <organized findings>
"""

_DEFAULT_VERIFIER_PROMPT = """\
You are a verifier agent. You validate findings proposed by another agent.

For each finding:
1. Check if it is accurate by examining the relevant code.
2. Assess its severity and impact.
3. Confirm or refute the finding with evidence.

Rules:
- Use at most one tool call per response.
- Be skeptical — verify every claim independently.
- Provide clear verdicts: confirmed, refuted, or needs-more-info.

Output contract (strict):
Thought: <one concise reasoning sentence>
Action: <tool_name>(arg=value, ...)
or
Final Answer: <verified findings with verdicts>
"""
