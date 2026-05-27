"""Tests for MoA method template."""

from __future__ import annotations

import pytest

from qitos.recipes.moa import MoAOrchestrator, MoACritic, MoARecipeState
from qitos.core.decision import Decision
from qitos.core.state import StateSchema
from qitos.engine.critic_result import CriticResult


class TestMoARecipeState:
    def test_default_values(self) -> None:
        state = MoARecipeState(task="test")
        assert state.proposals == []
        assert state.synthesis == ""
        assert state.round_count == 0
        assert state.max_rounds == 1
        assert state.proposer_count == 3


class TestMoACritic:
    def test_retry_when_no_proposals(self) -> None:
        critic = MoACritic(proposer_count=3)
        state = MoARecipeState(task="test", proposals=[])
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "retry"
        assert result.instruction_patch is not None
        assert "proposer" in result.instruction_patch.lower()

    def test_retry_when_not_enough_proposals(self) -> None:
        critic = MoACritic(proposer_count=3)
        state = MoARecipeState(
            task="test", proposals=[{"proposer": "a", "content": "proposal a"}]
        )
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "retry"
        assert "2 more" in result.reason

    def test_retry_when_enough_proposals_no_synthesis(self) -> None:
        critic = MoACritic(proposer_count=3)
        state = MoARecipeState(
            task="test",
            proposals=[
                {"proposer": "a", "content": "p1"},
                {"proposer": "b", "content": "p2"},
                {"proposer": "c", "content": "p3"},
            ],
            synthesis="",
        )
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "retry"
        assert "Synthesize" in result.instruction_patch or "synthesize" in result.instruction_patch

    def test_stop_when_synthesis_complete(self) -> None:
        critic = MoACritic(proposer_count=3, quality_threshold=0.6)
        state = MoARecipeState(
            task="test",
            proposals=[
                {"proposer": "a", "content": "p1"},
                {"proposer": "b", "content": "p2"},
                {"proposer": "c", "content": "p3"},
            ],
            synthesis="The synthesized answer combining all proposals.",
        )
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "stop"
        assert result.score >= 0.6

    def test_continue_with_partial_proposals_and_synthesis(self) -> None:
        critic = MoACritic(proposer_count=3, quality_threshold=0.6)
        state = MoARecipeState(
            task="test",
            proposals=[{"proposer": "a", "content": "p1"}],
            synthesis="Partial synthesis.",
        )
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        # Not enough proposals but has synthesis — should stop or retry
        assert result.action in ("stop", "retry")

    def test_non_moa_state_continues(self) -> None:
        critic = MoACritic()
        state = StateSchema(task="test")
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "continue"

    def test_multiple_rounds(self) -> None:
        critic = MoACritic(proposer_count=3, max_rounds=2, quality_threshold=0.9)
        state = MoARecipeState(
            task="test",
            proposals=[
                {"proposer": "a", "content": "p1"},
                {"proposer": "b", "content": "p2"},
                {"proposer": "c", "content": "p3"},
            ],
            synthesis="Low quality synthesis.",
            round_count=0,
        )
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        # Score < threshold, rounds remaining → retry
        assert result.action == "retry"
        assert result.state_patch["round_count"] == 1

    def test_stop_when_max_rounds_reached(self) -> None:
        critic = MoACritic(proposer_count=3, max_rounds=1, quality_threshold=0.9)
        state = MoARecipeState(
            task="test",
            proposals=[
                {"proposer": "a", "content": "p1"},
                {"proposer": "b", "content": "p2"},
                {"proposer": "c", "content": "p3"},
            ],
            synthesis="A synthesis but maybe not great.",
            round_count=1,
        )
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "stop"

    def test_stop_when_no_proposals_no_rounds(self) -> None:
        critic = MoACritic(proposer_count=3, max_rounds=0)
        state = MoARecipeState(task="test", proposals=[], round_count=0)
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "stop"
        assert result.score == pytest.approx(0.1)


class TestMoAOrchestrator:
    def test_init_state(self) -> None:
        agent = MoAOrchestrator()
        state = agent.init_state("Analyze design", max_steps=15)
        assert state.task == "Analyze design"
        assert state.max_steps == 15
        assert state.proposer_count == 3
        assert state.max_rounds == 1

    def test_init_state_custom(self) -> None:
        agent = MoAOrchestrator()
        state = agent.init_state("test", max_steps=10, proposer_count=5, max_rounds=2)
        assert state.proposer_count == 5
        assert state.max_rounds == 2

    def test_build_system_prompt(self) -> None:
        agent = MoAOrchestrator()
        state = MoARecipeState(task="test")
        prompt = agent.build_system_prompt(state)
        assert "MoA" in prompt or "Mixture-of-Agents" in prompt
        assert "Collect" in prompt
        assert "Aggregate" in prompt

    def test_build_system_prompt_with_round(self) -> None:
        agent = MoAOrchestrator()
        state = MoARecipeState(task="test", round_count=1, max_rounds=2)
        prompt = agent.build_system_prompt(state)
        assert "round 2" in prompt.lower()

    def test_prepare(self) -> None:
        agent = MoAOrchestrator()
        state = MoARecipeState(
            task="Analyze design",
            proposals=[{"proposer": "a", "content": "Great analysis"}],
            proposer_count=3,
        )
        text = agent.prepare(state, {})
        assert "Analyze design" in text
        assert "1/3" in text

    def test_reduce_extracts_final_answer(self) -> None:
        agent = MoAOrchestrator()
        state = MoARecipeState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "After analysis. FINAL ANSWER: The best approach is X"}]
        new_state = agent.reduce(state, {}, decision, results)
        assert new_state.synthesis == "The best approach is X"
        assert new_state.final_result == "The best approach is X"

    def test_reduce_stores_proposal(self) -> None:
        agent = MoAOrchestrator()
        state = MoARecipeState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "A proposal from proposer 1"}]
        new_state = agent.reduce(state, {}, decision, results)
        assert len(new_state.proposals) == 1
        assert "proposal from proposer 1" in new_state.proposals[0]["content"]
