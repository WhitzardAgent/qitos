"""Mixture-of-Agents (MoA) method template for QitOS.

Implements the MoA pattern (Wang et al. 2024):
multiple proposers generate independently → aggregator synthesizes
the best result from all proposals.

The MoACritic drives the loop: it checks whether proposals have
been collected and whether a synthesis has been produced. When
proposals are missing, it returns retry with an instruction patch
directing the agent to delegate to proposers. When a synthesis
exists, it returns continue/stop.

This recipe provides a single-agent orchestration interface that
tracks proposal state. For the multi-agent delegation pattern
with actual parallel proposer execution, see
``qitos.kit.patterns.moa.build_moa_system()``.

Usage::

    from qitos.recipes.moa import MoAOrchestrator, MoACritic

    agent = MoAOrchestrator(llm=my_llm)
    result = agent.run(
        task="Analyze the system design ...",
        critics=[MoACritic(proposer_count=3, max_rounds=1)],
        max_steps=15,
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
class MoARecipeState(StateSchema):
    """State for the MoA orchestrator.

    Tracks proposals collected from proposer agents and the
    final synthesis produced by the aggregator.
    """

    proposals: List[Dict[str, Any]] = field(default_factory=list)
    synthesis: str = ""
    round_count: int = 0
    max_rounds: int = 1
    proposer_count: int = 3


# ---------------------------------------------------------------------------
# Critic
# ---------------------------------------------------------------------------


class MoACritic(Critic):
    """Critic that drives the Mixture-of-Agents loop.

    The critic evaluates the current state of proposal collection
    and synthesis. It returns:

    - ``retry`` when proposals are missing (instructing the agent
      to collect proposals from proposers)
    - ``retry`` when proposals exist but no synthesis has been
      produced (instructing the agent to aggregate)
    - ``continue`` or ``stop`` when synthesis is complete

    Parameters
    ----------
    proposer_count:
        Expected number of proposals before aggregation.
    max_rounds:
        Maximum number of proposal-aggregation rounds.
    quality_threshold:
        Minimum score to accept the synthesis.
    """

    def __init__(
        self,
        proposer_count: int = 3,
        max_rounds: int = 1,
        quality_threshold: float = 0.6,
    ) -> None:
        self.proposer_count = proposer_count
        self.max_rounds = max_rounds
        self.quality_threshold = quality_threshold

    def evaluate(
        self,
        state: Any,
        decision: CoreDecision[Any],
        results: list[Any],
    ) -> CriticResult:
        moa_state = state if isinstance(state, MoARecipeState) else None
        if moa_state is None:
            return CriticResult(action="continue")

        has_proposals = len(moa_state.proposals) > 0
        has_synthesis = bool(moa_state.synthesis)
        enough_proposals = len(moa_state.proposals) >= self.proposer_count

        # Compute heuristic score based on state
        if has_synthesis:
            score = 0.8 if enough_proposals else 0.5
        elif has_proposals:
            score = 0.3 + 0.1 * len(moa_state.proposals)
        else:
            score = 0.1

        # Phase 1: Need to collect proposals
        if not enough_proposals and moa_state.round_count < self.max_rounds:
            needed = self.proposer_count - len(moa_state.proposals)
            return CriticResult(
                action="retry",
                reason=(
                    f"Only {len(moa_state.proposals)}/{self.proposer_count} "
                    f"proposals collected. Need {needed} more."
                ),
                score=score,
                instruction_patch=(
                    f"Collect proposals from {needed} more proposer(s). "
                    f"Each proposer should independently analyze the task "
                    f"and provide their unique perspective. "
                    f"So far {len(moa_state.proposals)} proposal(s) collected."
                ),
                state_patch={"round_count": moa_state.round_count},
            )

        # Phase 2: Have enough proposals but no synthesis
        if enough_proposals and not has_synthesis:
            return CriticResult(
                action="retry",
                reason=(
                    f"All {len(moa_state.proposals)} proposals collected. "
                    f"Now synthesize them into a unified answer."
                ),
                score=score,
                instruction_patch=(
                    "All proposals have been collected. "
                    "Synthesize the best insights from each proposal "
                    "into a coherent, comprehensive answer. "
                    "Address any contradictions and highlight agreements."
                ),
                state_patch={"round_count": moa_state.round_count},
            )

        # Phase 3: Synthesis exists
        if has_synthesis:
            if score >= self.quality_threshold:
                return CriticResult(
                    action="stop",
                    reason=f"Synthesis complete with score {score:.2f}.",
                    score=score,
                )
            # Low quality but no more rounds
            if moa_state.round_count >= self.max_rounds:
                return CriticResult(
                    action="stop",
                    reason="Max rounds reached. Returning best synthesis.",
                    score=score,
                )
            # Can do another round
            return CriticResult(
                action="retry",
                reason=f"Synthesis quality ({score:.2f}) below threshold.",
                score=score,
                instruction_patch=(
                    "The current synthesis may be incomplete. "
                    "Consider collecting additional proposals or "
                    "refining the synthesis with more detail."
                ),
                state_patch={"round_count": moa_state.round_count + 1},
            )

        # No proposals and round budget exhausted
        return CriticResult(
            action="stop",
            reason="No proposals collected and no rounds remaining.",
            score=0.1,
        )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


_MOA_SYSTEM_PROMPT = """\
You are a Mixture-of-Agents (MoA) orchestrator. Your workflow:

1. **Collect Proposals**: Gather independent analyses from multiple proposers. \
Each proposer provides a unique perspective on the task.
2. **Aggregate**: Synthesize the best insights from all proposals into a \
coherent, comprehensive answer.
3. **Output**: Produce the final synthesized answer.

When you have completed the synthesis, output:
FINAL ANSWER: <your synthesized answer>
"""


class MoAOrchestrator(AgentModule[MoARecipeState, Dict[str, Any], Any]):
    """Agent that implements the Mixture-of-Agents pattern.

    The orchestrator collects proposals from multiple independent
    proposers and synthesizes them into a unified answer. The
    MoACritic drives the loop by tracking proposal count and
    prompting for collection or aggregation as needed.
    """

    def __init__(self, llm: Any = None, **kwargs: Any) -> None:
        super().__init__(
            llm=llm,
            model_parser=ReActTextParser(),
            **kwargs,
        )

    def init_state(self, task: str, **kwargs: Any) -> MoARecipeState:
        return MoARecipeState(
            task=task,
            max_steps=int(kwargs.get("max_steps", 15)),
            max_rounds=int(kwargs.get("max_rounds", 1)),
            proposer_count=int(kwargs.get("proposer_count", 3)),
        )

    def build_system_prompt(self, state: MoARecipeState) -> str | None:
        parts = [_MOA_SYSTEM_PROMPT]
        if state.round_count > 0:
            parts.append(
                f"\nThis is round {state.round_count + 1} of "
                f"{state.max_rounds}."
            )
        return "\n".join(parts)

    def prepare(self, state: MoARecipeState, observation: Dict[str, Any]) -> str:
        lines = [f"Task: {state.task}"]
        lines.append(
            f"Proposals: {len(state.proposals)}/{state.proposer_count}"
        )
        if state.proposals:
            lines.append("\n## Proposals collected so far:")
            for i, p in enumerate(state.proposals, 1):
                proposer = p.get("proposer", f"proposer_{i}")
                content = p.get("content", p.get("proposal", str(p)))
                lines.append(f"### {proposer}")
                lines.append(str(content)[:500])
        if state.synthesis:
            lines.append(f"\n## Current synthesis:\n{state.synthesis}")
        return "\n".join(lines)

    def reduce(
        self,
        state: MoARecipeState,
        observation: Dict[str, Any],
        decision: Decision[Any],
        action_results: List[Any],
    ) -> MoARecipeState:
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
            state.synthesis = answer
            state.final_result = answer
        elif result_text and not state.synthesis:
            # Store as proposal if not already a synthesis
            state.proposals.append(
                {"proposer": f"auto_{len(state.proposals) + 1}", "content": result_text}
            )

        return state


__all__ = ["MoAOrchestrator", "MoACritic", "MoARecipeState"]
