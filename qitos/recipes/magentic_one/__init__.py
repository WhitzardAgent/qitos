"""Magentic-One method template for QitOS.

Implements the Magentic-One pattern (Furtado et al. 2024):
a generalist Orchestrator maintains a Task Ledger and Fact Bank,
delegates subtasks to specialist agents, tracks progress via a
Progress Ledger, and re-plans when stuck.

The ProgressCritic drives the loop: it evaluates whether the
orchestrator is making progress, detects stalls, and triggers
re-planning when the agent is not advancing. This maps the
Magentic-One dual-loop architecture onto QitOS's Agent+Critic
pattern.

Usage::

    from qitos.recipes.magentic_one import (
        MagenticOneOrchestrator,
        ProgressCritic,
        MagenticOneState,
    )

    agent = MagenticOneOrchestrator(llm=my_llm)
    result = agent.run(
        task="Research and summarize the latest findings on ...",
        critics=[ProgressCritic(max_stalls=3)],
        max_steps=30,
        return_state=True,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos import AgentModule, Decision, StateSchema
from qitos.core.decision import Decision as CoreDecision
from qitos.engine.critic import Critic
from qitos.engine.critic_result import CriticResult
from qitos.kit.parser import ReActTextParser


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class MagenticOneState(StateSchema):
    """State for the Magentic-One orchestrator.

    Implements the dual-ledger architecture:
    - **Fact Bank**: accumulated facts and educated guesses
    - **Task Ledger**: the current plan of subtasks
    - **Completed Tasks**: subtasks that have been finished
    - **Stall Counter**: tracks consecutive steps without progress
    """

    fact_bank: List[str] = field(default_factory=list)
    task_ledger: List[str] = field(default_factory=list)
    completed_tasks: List[str] = field(default_factory=list)
    stall_count: int = 0
    max_stalls: int = 3
    specialist_calls: int = 0
    current_subtask: str = ""


# ---------------------------------------------------------------------------
# Critic
# ---------------------------------------------------------------------------


class ProgressCritic(Critic):
    """Critic that implements Magentic-One's progress evaluation.

    The critic acts as the Progress Ledger: after each step it
    evaluates whether the orchestrator is advancing toward the
    goal. It tracks stall count and triggers re-planning when
    the agent is stuck.

    Progress is detected by checking if new tasks have been
    completed or new facts have been gathered since the last
    evaluation. When no progress is detected for ``max_stalls``
    consecutive steps, the critic forces a stop or requests
    re-planning.

    Parameters
    ----------
    max_stalls:
        Maximum consecutive no-progress steps before triggering
        re-planning or stopping.
    progress_threshold:
        Minimum score to consider a step as making progress.
    """

    def __init__(
        self,
        max_stalls: int = 3,
        progress_threshold: float = 0.5,
    ) -> None:
        self.max_stalls = max_stalls
        self.progress_threshold = progress_threshold
        self._prev_completed_count = 0
        self._prev_fact_count = 0

    def evaluate(
        self,
        state: Any,
        decision: CoreDecision[Any],
        results: list[Any],
    ) -> CriticResult:
        mo_state = state if isinstance(state, MagenticOneState) else None
        if mo_state is None:
            return CriticResult(action="continue")

        # Check for FINAL ANSWER — task is complete
        has_final = False
        for r in results:
            text = ""
            if isinstance(r, dict):
                text = r.get("output", "")
            elif isinstance(r, str):
                text = r
            if "FINAL ANSWER:" in text:
                has_final = True
                break

        if has_final:
            return CriticResult(
                action="stop",
                reason="Task completed. Final answer provided.",
                score=1.0,
            )

        # Detect progress
        has_error = any(
            isinstance(r, dict) and (r.get("error") or r.get("returncode", 0) != 0)
            for r in results
        )

        new_facts = len(mo_state.fact_bank) > self._prev_fact_count
        new_completed = len(mo_state.completed_tasks) > self._prev_completed_count
        self._prev_fact_count = len(mo_state.fact_bank)
        self._prev_completed_count = len(mo_state.completed_tasks)

        is_progress = new_facts or new_completed

        # Also check for empty results (no progress indicator)
        has_output = any(
            isinstance(r, dict) and r.get("output", "").strip()
            for r in results
        )

        if is_progress:
            mo_state.stall_count = 0
            score = 0.7 if new_completed else 0.5
            return CriticResult(
                action="continue",
                reason="Progress detected. Continuing.",
                score=score,
            )

        # No progress detected
        mo_state.stall_count += 1

        if has_error:
            mo_state.stall_count += 1  # Errors count extra toward stall

        if mo_state.stall_count >= self.max_stalls:
            # Check if we have any completed work
            if mo_state.completed_tasks:
                return CriticResult(
                    action="stop",
                    reason=(
                        f"Stalled for {mo_state.stall_count} steps. "
                        f"Completed {len(mo_state.completed_tasks)} subtasks. "
                        f"Stopping with partial results."
                    ),
                    score=0.4,
                )
            return CriticResult(
                action="stop",
                reason=f"Stalled for {mo_state.stall_count} steps with no progress. Stopping.",
                score=0.1,
            )

        # Stall but budget remains — suggest re-planning
        remaining_stalls = self.max_stalls - mo_state.stall_count
        return CriticResult(
            action="retry",
            reason=(
                f"No progress detected (stall {mo_state.stall_count}/"
                f"{self.max_stalls}). "
                f"{remaining_stalls} stalls remaining before forced stop."
            ),
            score=0.3,
            instruction_patch=self._replan_guidance(mo_state),
            state_patch={"stall_count": mo_state.stall_count},
        )

    def _replan_guidance(self, state: MagenticOneState) -> str:
        """Generate re-planning guidance when the agent is stuck."""
        parts = [
            "MAGENTIC-ONE PROGRESS ALERT: No progress detected. "
            "Consider an alternative approach."
        ]
        if state.fact_bank:
            parts.append(
                f"\nKnown facts ({len(state.fact_bank)}): "
                + "; ".join(state.fact_bank[-5:])
            )
        if state.completed_tasks:
            parts.append(
                f"\nCompleted tasks ({len(state.completed_tasks)}): "
                + "; ".join(state.completed_tasks[-3:])
            )
        if state.task_ledger:
            remaining = [
                t for t in state.task_ledger if t not in state.completed_tasks
            ]
            if remaining:
                parts.append(
                    f"\nRemaining tasks: " + "; ".join(remaining[:5])
                )
        parts.append(
            "\nRe-plan: update the task ledger with a new strategy. "
            "Try a different specialist or approach."
        )
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


_MAGENTIC_ONE_SYSTEM_PROMPT = """\
You are a Magentic-One orchestrator. Your workflow:

1. **Plan**: Create a task ledger — a list of subtasks to solve the problem.
2. **Gather Facts**: Collect facts and educated guesses into a fact bank.
3. **Delegate**: Assign subtasks to specialists or work on them yourself.
4. **Track Progress**: After each subtask, update completed tasks and facts.
5. **Re-plan**: If you're stuck, revise the task ledger with a new strategy.

Maintain the task ledger and fact bank as you work. When all subtasks are \
complete, output:
FINAL ANSWER: <your answer>
"""


class MagenticOneOrchestrator(AgentModule[MagenticOneState, Dict[str, Any], Any]):
    """Agent that implements the Magentic-One pattern.

    The orchestrator maintains a fact bank and task ledger,
    delegates subtasks, tracks progress, and re-plans when
    stuck. The ProgressCritic evaluates progress and triggers
    re-planning via instruction patches.
    """

    def __init__(self, llm: Any = None, **kwargs: Any) -> None:
        super().__init__(
            llm=llm,
            model_parser=ReActTextParser(),
            **kwargs,
        )

    def init_state(self, task: str, **kwargs: Any) -> MagenticOneState:
        return MagenticOneState(
            task=task,
            max_steps=int(kwargs.get("max_steps", 30)),
            max_stalls=int(kwargs.get("max_stalls", 3)),
        )

    def build_system_prompt(self, state: MagenticOneState) -> str | None:
        parts = [_MAGENTIC_ONE_SYSTEM_PROMPT]
        if state.fact_bank:
            parts.append("\n## Fact Bank")
            for i, fact in enumerate(state.fact_bank, 1):
                parts.append(f"{i}. {fact}")
        if state.task_ledger:
            parts.append("\n## Task Ledger")
            for i, task in enumerate(state.task_ledger, 1):
                marker = " [DONE]" if task in state.completed_tasks else ""
                parts.append(f"{i}. {task}{marker}")
        return "\n".join(parts)

    def prepare(self, state: MagenticOneState, observation: Dict[str, Any]) -> str:
        lines = [f"Task: {state.task}"]
        lines.append(f"Stall count: {state.stall_count}/{state.max_stalls}")
        lines.append(f"Specialist calls: {state.specialist_calls}")
        lines.append(f"Facts gathered: {len(state.fact_bank)}")
        lines.append(f"Tasks completed: {len(state.completed_tasks)}/{len(state.task_ledger)}")
        if state.current_subtask:
            lines.append(f"Current subtask: {state.current_subtask}")
        return "\n".join(lines)

    def reduce(
        self,
        state: MagenticOneState,
        observation: Dict[str, Any],
        decision: Decision[Any],
        action_results: List[Any],
    ) -> MagenticOneState:
        # Extract text from action results
        result_text = ""
        if action_results:
            for r in action_results:
                if isinstance(r, dict):
                    result_text += r.get("output", r.get("text", str(r)))
                elif isinstance(r, str):
                    result_text += r

        # Check for FINAL ANSWER
        if "FINAL ANSWER:" in result_text:
            answer = result_text.split("FINAL ANSWER:", 1)[1].strip()
            state.final_result = answer

        # Track specialist calls
        if decision.rationale and "delegate" in decision.rationale.lower():
            state.specialist_calls += 1

        return state


__all__ = ["MagenticOneOrchestrator", "ProgressCritic", "MagenticOneState"]
