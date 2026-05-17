"""GracefulShutdownCritic — forces agents to wrap up near the iteration limit.

When the agent is within N steps of its max_steps limit, this critic
injects guidance telling the agent to call its barrier tool immediately.
This matches pentagi's "graceful termination zone" where the last 3
iterations produce a synthetic reflector message forcing a final result.
"""

from __future__ import annotations

from typing import Any

from qitos.core.decision import Decision
from qitos.engine.critic import Critic
from qitos.engine.critic_result import CriticResult


class GracefulShutdownCritic(Critic):
    """Detects when the agent is approaching the step limit and forces wrap-up.

    In the last N steps (default 3), injects an instruction patch telling
    the agent to immediately call its barrier tool (done, hack_result,
    code_result, maintenance_result, etc.) and produce a result.
    """

    def __init__(self, shutdown_zone_steps: int = 3):
        self._shutdown_zone = shutdown_zone_steps

    def evaluate(
        self, state: Any, decision: Decision[Any], results: list[Any]
    ) -> CriticResult:
        # If the agent already set final_result, let it finish
        final_result = getattr(state, 'final_result', None)
        if isinstance(final_result, str) and final_result:
            return CriticResult(action="continue", reason="final_result set", score=1.0)

        current_step = getattr(state, 'current_step', 0) or 0
        max_steps = getattr(state, 'max_steps', 0) or 0
        if max_steps <= 0:
            return CriticResult(action="continue", reason="", score=1.0)

        steps_remaining = max_steps - current_step
        if steps_remaining > self._shutdown_zone:
            return CriticResult(action="continue", reason="", score=1.0)

        # Agent is in the shutdown zone — force it to wrap up
        return CriticResult(
            action="retry",
            reason=f"Approaching step limit ({steps_remaining} steps remaining). "
            "Must produce a result immediately.",
            score=0.1,
            instruction_patch=(
                f"CRITICAL: You have only {steps_remaining} step(s) remaining before the iteration limit.\n"
                "You MUST immediately call your barrier/completion tool (e.g., done, hack_result, "
                "code_result, maintenance_result, search_result, memorist_result, report_result) "
                "to produce a final result. Do NOT attempt any more tool calls — just summarize "
                "what you have accomplished and submit it now."
            ),
        )


__all__ = ["GracefulShutdownCritic"]
