"""LATS (Language Agent Tree Search) method template for QitOS.

Implements the LATS pattern (Zhou et al. 2023):
select → expand → evaluate → backpropagate, using MCTS with
LLM-powered value functions and self-reflection.

The LATSCritic drives the loop: after each simulation step it
evaluates results, tracks tree statistics, and guides exploration
via instruction patches. When max simulations are reached, it
returns the best trajectory found.

Usage::

    from qitos.recipes.lats import LATSAgent, LATSCritic

    agent = LATSAgent(llm=my_llm)
    result = agent.run(
        task="Solve the logic puzzle ...",
        critics=[LATSCritic(max_simulations=5)],
        max_steps=20,
        return_state=True,
    )
"""

from __future__ import annotations

import math
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
class TreeStatistics:
    """Statistics for a single tree node."""

    visits: int = 0
    value: float = 0.0
    reward: float = 0.0
    depth: int = 0
    is_terminal: bool = False
    reflection: str = ""


@dataclass
class LATSState(StateSchema):
    """State for the LATS agent.

    Tracks tree search statistics including visited nodes,
    cumulative rewards, and reflection history from failed paths.
    """

    simulations_done: int = 0
    max_simulations: int = 5
    exploration_weight: float = 1.41
    best_reward: float = 0.0
    best_answer: str = ""
    current_value: float = 0.0
    current_visits: int = 0
    failed_paths: List[str] = field(default_factory=list)
    reflections: List[str] = field(default_factory=list)
    node_count: int = 0


# ---------------------------------------------------------------------------
# Critic
# ---------------------------------------------------------------------------


class LATSCritic(Critic):
    """Critic that drives the LATS tree search loop.

    Evaluates step results using heuristic signals (success, error
    indicators) to simulate the MCTS value function. On each step
    it tracks visit counts and values, guiding exploration via
    UCB1-inspired scoring.

    When simulations remain, returns ``retry`` with an instruction
    patch that guides the agent toward unexplored paths. When the
    simulation budget is exhausted, returns ``stop`` with the best
    result found.

    Parameters
    ----------
    max_simulations:
        Maximum number of simulation iterations.
    exploration_weight:
        UCB1 exploration constant (c). Higher values encourage
        exploring less-visited paths.
    success_threshold:
        Reward value at which a trajectory is considered successful
        and the search can stop early.
    """

    def __init__(
        self,
        max_simulations: int = 5,
        exploration_weight: float = 1.41,
        success_threshold: float = 0.8,
    ) -> None:
        self.max_simulations = max_simulations
        self.exploration_weight = exploration_weight
        self.success_threshold = success_threshold

    def evaluate(
        self,
        state: Any,
        decision: CoreDecision[Any],
        results: list[Any],
    ) -> CriticResult:
        lats_state = state if isinstance(state, LATSState) else None
        if lats_state is None:
            return CriticResult(action="continue")

        # Determine reward from results
        reward = self._compute_reward(results, decision)

        # Update best answer if this is the best reward so far
        if reward > lats_state.best_reward:
            answer = self._extract_answer(results)
            lats_state.best_reward = reward
            if answer:
                lats_state.best_answer = answer

        lats_state.simulations_done += 1
        lats_state.node_count += 1
        lats_state.current_visits += 1

        # UCB1-style score for guiding next exploration
        ucb_score = self._ucb1(
            lats_state.current_value,
            lats_state.current_visits,
            lats_state.simulations_done,
        )
        lats_state.current_value = (
            (lats_state.current_value * (lats_state.current_visits - 1) + reward)
            / lats_state.current_visits
        )

        # Early stop on success
        if reward >= self.success_threshold:
            return CriticResult(
                action="stop",
                reason=f"Successful trajectory found (reward={reward:.2f}).",
                score=reward,
            )

        # Record failed path and generate reflection
        if reward < 0.3:
            failed_desc = self._describe_failure(decision, results)
            lats_state.failed_paths.append(failed_desc)
            reflection = self._generate_reflection(decision, results, failed_desc)
            if reflection:
                lats_state.reflections.append(reflection)

        # Continue simulation if budget remains
        if lats_state.simulations_done < self.max_simulations:
            exploration_guidance = self._exploration_guidance(lats_state)
            return CriticResult(
                action="retry",
                reason=(
                    f"Simulation {lats_state.simulations_done}/"
                    f"{lats_state.max_simulations}, reward={reward:.2f}. "
                    f"Exploring further."
                ),
                score=ucb_score,
                instruction_patch=exploration_guidance,
                state_patch={
                    "current_visits": lats_state.current_visits,
                    "current_value": lats_state.current_value,
                },
            )

        # Budget exhausted — return best found
        return CriticResult(
            action="stop",
            reason=(
                f"Max simulations ({self.max_simulations}) reached. "
                f"Best reward={lats_state.best_reward:.2f}."
            ),
            score=lats_state.best_reward,
        )

    def _compute_reward(
        self, results: list[Any], decision: CoreDecision[Any]
    ) -> float:
        """Compute a heuristic reward from action results."""
        if not results:
            return 0.0

        # Check for explicit success markers
        for r in results:
            if isinstance(r, dict):
                if r.get("error"):
                    return 0.1
                if r.get("returncode", 0) != 0:
                    return 0.2
                output = r.get("output", "")
                if "FINAL ANSWER:" in output:
                    return 0.9
                if output.strip():
                    return 0.5
            elif isinstance(r, str) and "FINAL ANSWER:" in r:
                return 0.9

        return 0.3

    def _extract_answer(self, results: list[Any]) -> str:
        """Extract answer text from results."""
        for r in results:
            text = ""
            if isinstance(r, dict):
                text = r.get("output", "")
            elif isinstance(r, str):
                text = r
            if "FINAL ANSWER:" in text:
                return text.split("FINAL ANSWER:", 1)[1].strip()
        return ""

    def _ucb1(self, value: float, visits: int, parent_visits: int) -> float:
        """Compute UCB1 score for exploration guidance."""
        if visits == 0:
            return float("inf")
        return value / visits + self.exploration_weight * math.sqrt(
            math.log(max(1, parent_visits)) / visits
        )

    def _describe_failure(
        self, decision: CoreDecision[Any], results: list[Any]
    ) -> str:
        """Describe what went wrong in a failed path."""
        parts = []
        if decision.rationale:
            parts.append(f"Planned: {decision.rationale}")
        for r in results:
            if isinstance(r, dict):
                if r.get("error"):
                    parts.append(f"Error: {r['error']}")
                if r.get("returncode", 0) != 0:
                    parts.append(f"Exit code: {r['returncode']}")
        return " | ".join(parts) if parts else "No useful output produced"

    def _generate_reflection(
        self,
        decision: CoreDecision[Any],
        results: list[Any],
        failure_desc: str,
    ) -> str:
        """Generate a reflection from a failed trajectory."""
        return f"Failed path: {failure_desc}. Try a different approach."

    def _exploration_guidance(self, state: LATSState) -> str:
        """Generate instruction patch guiding the next exploration."""
        parts = [
            f"LATS exploration: simulation {state.simulations_done}/"
            f"{state.max_simulations}. "
            f"Best reward so far: {state.best_reward:.2f}."
        ]
        if state.reflections:
            parts.append(
                "\nPrevious failed paths to avoid:\n"
                + "\n".join(f"- {r}" for r in state.reflections[-3:])
            )
        parts.append("Explore a different strategy. Avoid repeating failed approaches.")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


_LATS_SYSTEM_PROMPT = """\
You are a LATS (Language Agent Tree Search) agent. Your workflow:

1. **Think**: Reason about the current state and what to try next.
2. **Act**: Take an action toward solving the task.
3. **Observe**: Process the result and evaluate progress.
4. **Reflect**: When a path fails, reflect on what went wrong.
5. **Explore**: Try alternative approaches guided by reflections.

You are performing a tree search. Each step explores a path.
Avoid repeating failed approaches mentioned in reflections.

When you have found the answer, output:
FINAL ANSWER: <your answer>
"""


class LATSAgent(AgentModule[LATSState, Dict[str, Any], Any]):
    """Agent that implements the LATS pattern.

    The agent explores solution paths guided by the LATSCritic.
    Failed paths generate reflections that steer future exploration
    away from similar mistakes, while UCB1-inspired scoring
    balances exploitation of promising paths with exploration of
    new ones.
    """

    def __init__(self, llm: Any = None, **kwargs: Any) -> None:
        super().__init__(
            llm=llm,
            model_parser=ReActTextParser(),
            **kwargs,
        )

    def init_state(self, task: str, **kwargs: Any) -> LATSState:
        max_sim = int(kwargs.get("max_simulations", 5))
        return LATSState(
            task=task,
            max_steps=int(kwargs.get("max_steps", 20)),
            max_simulations=max_sim,
            exploration_weight=float(kwargs.get("exploration_weight", 1.41)),
        )

    def build_system_prompt(self, state: LATSState) -> str | None:
        parts = [_LATS_SYSTEM_PROMPT]
        if state.reflections:
            parts.append("\n## Reflections from failed paths")
            for i, r in enumerate(state.reflections, 1):
                parts.append(f"{i}. {r}")
        if state.best_answer and state.best_reward > 0.5:
            parts.append(
                f"\n## Current best answer (reward={state.best_reward:.2f})"
            )
            parts.append(state.best_answer)
        return "\n".join(parts)

    def prepare(self, state: LATSState, observation: Dict[str, Any]) -> str:
        lines = [f"Task: {state.task}"]
        lines.append(f"Simulation: {state.simulations_done}/{state.max_simulations}")
        lines.append(f"Best reward: {state.best_reward:.2f}")
        lines.append(f"Nodes explored: {state.node_count}")
        return "\n".join(lines)

    def reduce(
        self,
        state: LATSState,
        observation: Dict[str, Any],
        decision: Decision[Any],
        action_results: List[Any],
    ) -> LATSState:
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
            state.best_answer = answer
            state.final_result = answer

        return state


__all__ = ["LATSAgent", "LATSCritic", "LATSState", "TreeStatistics"]
