"""ReflectorCritic — enforces tool-call-only communication.

Replicates pentagi's Reflector pattern: if an agent produces free text
without a tool call, this critic generates guidance via an LLM call
(using reflector prompt templates) and retries.

In pentagi, the Reflector is a full LLM call that produces contextual
advice, injected as a human message into the agent's chain. Our
implementation uses CriticResult.instruction_patch to achieve the same
effect through the Engine's retry mechanism.
"""

from __future__ import annotations

import datetime
from typing import Any, List, Optional

from qitos.core.decision import Decision
from qitos.engine.critic import Critic
from qitos.engine.critic_result import CriticResult

from ..prompts.reflector_prompt import REFLECTOR_SYSTEM_PROMPT, REFLECTOR_QUESTION_PROMPT


class ReflectorCritic(Critic):
    """Enforces tool-call-only communication with optional LLM-based guidance.

    When an agent produces free text instead of a tool call:
    1. If an LLM is provided, makes a separate LLM call using the reflector
       prompt templates to generate contextual guidance in the user's voice.
    2. If no LLM, falls back to rule-based text guidance.
    3. Max 3 retries before forcing stop.

    Parameters
    ----------
    llm : Any | None
        Optional LLM instance for generating reflector guidance.
        If None, uses rule-based fallback.
    max_retries : int
        Maximum number of reflector retries before stopping.
    barrier_tools : list[str] | None
        Names of barrier tools available to the agent.
        Used in the reflector prompt to guide the agent.
    execution_context : str | None
        Current execution context to include in reflector prompt.
    """

    def __init__(
        self,
        llm: Any = None,
        max_retries: int = 3,
        barrier_tools: Optional[List[str]] = None,
        execution_context: Optional[str] = None,
    ):
        self._llm = llm
        self._retry_count = 0
        self._max_retries = max_retries
        self._barrier_tools = barrier_tools or ["done"]
        self._execution_context = execution_context or ""

    def _generate_llm_guidance(self, agent_message: str) -> Optional[str]:
        """Generate reflector guidance via LLM call."""
        if self._llm is None:
            return None

        try:
            # Build reflector system prompt
            barrier_tools_xml = "\n".join(
                f"  <tool><name>{name}</name></tool>"
                for name in self._barrier_tools
            )
            barrier_tool_names_xml = "\n".join(
                f"    <tool>{name}</tool>"
                for name in self._barrier_tools
            )
            current_time = datetime.datetime.now().isoformat()
            request_section = ""  # Could include original task here

            system_prompt = REFLECTOR_SYSTEM_PROMPT.format(
                barrier_tools=barrier_tools_xml,
                execution_context=self._execution_context,
                request_section=request_section,
            )

            user_prompt = REFLECTOR_QUESTION_PROMPT.format(
                barrier_tool_names=barrier_tool_names_xml,
                message=agent_message,
            )

            # Call LLM
            if hasattr(self._llm, 'invoke'):
                response = self._llm.invoke(
                    system_prompt + "\n\n" + user_prompt
                )
                return str(response)
            elif hasattr(self._llm, 'predict'):
                response = self._llm.predict(
                    user_prompt,
                    system_prompt=system_prompt,
                )
                return str(response)
            elif hasattr(self._llm, 'generate'):
                response = self._llm.generate(
                    [system_prompt, user_prompt],
                )
                return str(response)
            elif callable(self._llm):
                response = self._llm(user_prompt)
                return str(response)
        except Exception:
            pass

        return None

    def _generate_rule_based_guidance(self) -> str:
        """Generate rule-based guidance as fallback."""
        return (
            "IMPORTANT: You MUST use a tool call. Free text responses are NOT allowed. "
            "Available options:\n"
            "- Use 'done' tool with a summary when your subtask is complete\n"
            "- Use 'ask_user' tool if you need clarification\n"
            "- Delegate to a specialist agent using the appropriate delegation tool\n"
            "- Use terminal, file, or search tools to take action\n"
            "NEVER respond with plain text — always use a tool call."
        )

    def evaluate(
        self, state: Any, decision: Decision[Any], results: list[Any]
    ) -> CriticResult:
        # If the agent already set final_result, let it finish
        final_result = getattr(state, 'final_result', None)
        if isinstance(final_result, str) and final_result:
            return CriticResult(action="continue", reason="final_result set", score=1.0)

        # Check if the decision has any actions (tool calls)
        has_actions = bool(decision.actions) if decision.actions else False

        if has_actions:
            self._retry_count = 0
            return CriticResult(
                action="continue",
                reason="Tool call detected — communication protocol satisfied.",
                score=1.0,
            )

        # No tool calls = free text response, which violates the protocol
        self._retry_count += 1

        if self._retry_count > self._max_retries:
            return CriticResult(
                action="stop",
                reason=f"Agent failed to use tool calls after {self._max_retries} attempts. "
                "Stopping to prevent infinite loop.",
                score=0.0,
            )

        # Extract the agent's free-text message
        agent_message = ""
        if hasattr(decision, 'thought') and decision.thought:
            agent_message = decision.thought
        elif results:
            agent_message = str(results[-1])[:500]

        # Try LLM-based guidance first
        instruction = self._generate_llm_guidance(agent_message)

        # Fall back to rule-based guidance
        if not instruction:
            instruction = self._generate_rule_based_guidance()

        return CriticResult(
            action="retry",
            reason="Free text responses are not allowed in this system. "
            "You MUST use a tool call to communicate.",
            score=0.0,
            instruction_patch=instruction,
        )
