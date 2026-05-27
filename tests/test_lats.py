"""Tests for LATS method template."""

from __future__ import annotations

import pytest

from qitos.recipes.lats import LATSAgent, LATSCritic, LATSState, TreeStatistics
from qitos.core.decision import Decision
from qitos.core.state import StateSchema
from qitos.engine.critic_result import CriticResult


class TestLATSState:
    def test_default_values(self) -> None:
        state = LATSState(task="test")
        assert state.simulations_done == 0
        assert state.max_simulations == 5
        assert state.exploration_weight == pytest.approx(1.41)
        assert state.best_reward == 0.0
        assert state.best_answer == ""
        assert state.current_value == 0.0
        assert state.current_visits == 0
        assert state.failed_paths == []
        assert state.reflections == []
        assert state.node_count == 0


class TestTreeStatistics:
    def test_default_values(self) -> None:
        stats = TreeStatistics()
        assert stats.visits == 0
        assert stats.value == 0.0
        assert stats.reward == 0.0
        assert stats.is_terminal is False


class TestLATSCritic:
    def test_retry_on_empty_results(self) -> None:
        critic = LATSCritic(max_simulations=5)
        state = LATSState(task="test")
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "retry"
        assert result.state_patch is not None
        assert result.instruction_patch is not None

    def test_retry_on_error(self) -> None:
        critic = LATSCritic(max_simulations=3)
        state = LATSState(task="test")
        decision = Decision(mode="act")
        results = [{"error": "command not found"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "retry"
        assert result.score is not None
        assert state.simulations_done == 1
        assert len(state.reflections) == 1  # reward < 0.3 triggers reflection

    def test_retry_on_nonzero_returncode(self) -> None:
        critic = LATSCritic(max_simulations=3)
        state = LATSState(task="test")
        decision = Decision(mode="act")
        results = [{"returncode": 1, "output": "error"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "retry"

    def test_stop_on_final_answer(self) -> None:
        critic = LATSCritic(max_simulations=5)
        state = LATSState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "FINAL ANSWER: 42"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "stop"
        assert result.score >= 0.8
        assert state.best_answer == "42"

    def test_stop_on_success_threshold(self) -> None:
        critic = LATSCritic(max_simulations=5, success_threshold=0.8)
        state = LATSState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "FINAL ANSWER: solved"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "stop"

    def test_stop_when_max_simulations_reached(self) -> None:
        critic = LATSCritic(max_simulations=2)
        state = LATSState(task="test", simulations_done=1)
        decision = Decision(mode="act")
        results = [{"output": "partial result"}]
        # First evaluation increments simulations_done to 2
        result = critic.evaluate(state, decision, results)
        assert result.action == "stop"
        assert result.score == pytest.approx(0.5)  # best_reward after update

    def test_non_lats_state_continues(self) -> None:
        critic = LATSCritic()
        state = StateSchema(task="test")
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "continue"

    def test_reflection_generated_on_failure(self) -> None:
        critic = LATSCritic(max_simulations=5)
        state = LATSState(task="test")
        decision = Decision(mode="act", rationale="run the tests")
        results = [{"error": "ImportError: no module"}]
        result = critic.evaluate(state, decision, results)
        assert len(state.reflections) == 1
        assert "ImportError" in state.reflections[0]

    def test_failed_paths_recorded(self) -> None:
        critic = LATSCritic(max_simulations=5)
        state = LATSState(task="test")
        decision = Decision(mode="act", rationale="try something")
        results = [{"error": "failed"}]
        critic.evaluate(state, decision, results)
        assert len(state.failed_paths) == 1

    def test_exploration_guidance_in_instruction_patch(self) -> None:
        critic = LATSCritic(max_simulations=5)
        state = LATSState(task="test", reflections=["Avoid X"])
        decision = Decision(mode="act")
        results = [{"output": "partial"}]
        result = critic.evaluate(state, decision, results)
        assert result.instruction_patch is not None
        assert "LATS exploration" in result.instruction_patch
        assert "Avoid X" in result.instruction_patch

    def test_ucb1_score_in_retry(self) -> None:
        critic = LATSCritic(max_simulations=5, exploration_weight=1.41)
        state = LATSState(task="test", current_value=0.5, current_visits=2)
        decision = Decision(mode="act")
        results = [{"output": "some output"}]
        result = critic.evaluate(state, decision, results)
        # Score should be UCB1 computed value
        assert result.score is not None
        assert result.score > 0

    def test_best_reward_updated(self) -> None:
        critic = LATSCritic(max_simulations=5)
        state = LATSState(task="test", best_reward=0.3)
        decision = Decision(mode="act")
        results = [{"output": "FINAL ANSWER: better"}]
        critic.evaluate(state, decision, results)
        assert state.best_reward >= 0.8
        assert state.best_answer == "better"

    def test_multiple_simulations_increment(self) -> None:
        critic = LATSCritic(max_simulations=3)
        state = LATSState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "partial"}]
        critic.evaluate(state, decision, results)
        assert state.simulations_done == 1
        critic.evaluate(state, decision, results)
        assert state.simulations_done == 2
        result = critic.evaluate(state, decision, results)
        assert state.simulations_done == 3
        assert result.action == "stop"  # max_simulations reached


class TestLATSAgent:
    def test_init_state(self) -> None:
        agent = LATSAgent()
        state = agent.init_state("Solve puzzle", max_steps=20)
        assert state.task == "Solve puzzle"
        assert state.max_steps == 20
        assert state.max_simulations == 5

    def test_init_state_custom_simulations(self) -> None:
        agent = LATSAgent()
        state = agent.init_state("test", max_steps=10, max_simulations=10)
        assert state.max_simulations == 10

    def test_build_system_prompt(self) -> None:
        agent = LATSAgent()
        state = LATSState(task="test")
        prompt = agent.build_system_prompt(state)
        assert "LATS" in prompt
        assert "Think" in prompt
        assert "Explore" in prompt

    def test_build_system_prompt_with_reflections(self) -> None:
        agent = LATSAgent()
        state = LATSState(
            task="test",
            reflections=["Avoid approach X", "Try approach Y"],
        )
        prompt = agent.build_system_prompt(state)
        assert "Reflections" in prompt
        assert "Avoid approach X" in prompt

    def test_build_system_prompt_with_best_answer(self) -> None:
        agent = LATSAgent()
        state = LATSState(task="test", best_answer="42", best_reward=0.9)
        prompt = agent.build_system_prompt(state)
        assert "Current best answer" in prompt
        assert "42" in prompt

    def test_prepare(self) -> None:
        agent = LATSAgent()
        state = LATSState(task="Solve puzzle", simulations_done=2, max_simulations=5)
        text = agent.prepare(state, {})
        assert "Solve puzzle" in text
        assert "2/5" in text

    def test_reduce_extracts_final_answer(self) -> None:
        agent = LATSAgent()
        state = LATSState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "After analysis. FINAL ANSWER: 42"}]
        new_state = agent.reduce(state, {}, decision, results)
        assert new_state.best_answer == "42"
        assert new_state.final_result == "42"

    def test_reduce_stores_text_on_no_final_answer(self) -> None:
        agent = LATSAgent()
        state = LATSState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "Working on it"}]
        new_state = agent.reduce(state, {}, decision, results)
        assert new_state.best_answer == ""
