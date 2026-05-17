"""StuckDetectionCritic — detects when an agent is stuck in a loop."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from qitos.core.decision import Decision
from qitos.engine.critic import Critic
from qitos.engine.critic_result import CriticResult


class StuckDetectionCritic(Critic):
    """Detects when an agent is stuck in a loop or making no progress.

    Monitors for:
    - Consecutive identical actions (same tool + same args)
    - No new findings after N steps
    - Repeating error patterns

    When stuck is detected, provides guidance to pivot strategy.
    """

    def __init__(
        self,
        max_identical_actions: int = 3,
        max_steps_without_progress: int = 5,
    ):
        self._max_identical = max_identical_actions
        self._max_no_progress = max_steps_without_progress
        self._action_history: List[str] = []
        self._last_finding_step: int = 0
        self._last_state_size: int = 0
        self._current_step: int = 0

    def evaluate(
        self, state: Any, decision: Decision[Any], results: list[Any]
    ) -> CriticResult:
        # If the agent already set final_result (called barrier tool), don't interfere
        final_result = getattr(state, 'final_result', None)
        if isinstance(final_result, str) and final_result:
            return CriticResult(action="continue", reason="final_result set", score=1.0)

        self._current_step = getattr(state, 'current_step', 0) or 0

        # Track actions
        action_sig = self._action_signature(decision)
        if action_sig:
            self._action_history.append(action_sig)

        # Check for consecutive identical actions
        if self._detect_identical_loop():
            return CriticResult(
                action="retry",
                reason=f"Detected {self._max_identical}+ consecutive identical actions. "
                "The current approach is not making progress.",
                score=0.2,
                instruction_patch=(
                    "DETECTED: You are repeating the same action without success.\n"
                    "PIVOT STRATEGY:\n"
                    "1. Stop the current approach — it is not working\n"
                    "2. Try a completely different technique or tool\n"
                    "3. If a tool is failing, try an alternative tool\n"
                    "4. Consider delegating to a specialist for help\n"
                    "5. Use the 'done' tool if the subtask cannot be completed\n"
                ),
                state_patch={"_stuck_detected": True},
            )

        # Check for no progress based on findings in state
        if self._detect_no_progress(state):
            return CriticResult(
                action="retry",
                reason=f"No new findings in {self._max_no_progress} steps. "
                "Consider changing strategy.",
                score=0.3,
                instruction_patch=(
                    "PROGRESS WARNING: No new findings in recent steps.\n"
                    "SUGGESTED ACTIONS:\n"
                    "1. Try a different angle or technique\n"
                    "2. Search for information about the target\n"
                    "3. Ask the adviser for strategic guidance\n"
                    "4. Move on to the next subtask if current one is blocked\n"
                ),
            )

        return CriticResult(
            action="continue",
            reason="Agent is making progress.",
            score=1.0,
        )

    def _action_signature(self, decision: Decision[Any]) -> str:
        """Create a signature for the current action to detect loops."""
        if not decision.actions:
            return ""
        parts = []
        for action in (decision.actions or []):
            if isinstance(action, dict):
                tool_name = action.get("tool", action.get("name", ""))
                args_str = str(sorted(action.get("args", action.get("arguments", {})).items()))
                parts.append(f"{tool_name}:{args_str}")
            else:
                parts.append(str(action))
        return "|".join(parts)

    def _detect_identical_loop(self) -> bool:
        """Check if the last N actions are identical."""
        if len(self._action_history) < self._max_identical:
            return False
        recent = self._action_history[-self._max_identical:]
        return len(set(recent)) == 1 and recent[0] != ""

    def _detect_no_progress(self, state: Any) -> bool:
        """Check if there's been no progress for too many steps.

        Tracks progress by monitoring the size of findings/scratchpad lists
        on the agent state. If they haven't grown in N steps, the agent
        may be stuck.
        """
        if self._current_step < self._max_no_progress:
            return False
        # Check for findings or scratchpad growth
        findings = getattr(state, 'findings', None)
        scratchpad = getattr(state, 'scratchpad', None)
        current_size = 0
        if isinstance(findings, list):
            current_size += len(findings)
        if isinstance(scratchpad, list):
            current_size += len(scratchpad)
        if current_size > self._last_state_size:
            self._last_finding_step = self._current_step
            self._last_state_size = current_size
        if self._current_step - self._last_finding_step > self._max_no_progress:
            return True
        return False
