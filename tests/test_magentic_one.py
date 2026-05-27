"""Tests for Magentic-One method template."""

from __future__ import annotations

import pytest

from qitos.recipes.magentic_one import (
    MagenticOneOrchestrator,
    ProgressCritic,
    MagenticOneState,
)
from qitos.core.decision import Decision
from qitos.core.state import StateSchema
from qitos.engine.critic_result import CriticResult


class TestMagenticOneState:
    def test_default_values(self) -> None:
        state = MagenticOneState(task="test")
        assert state.fact_bank == []
        assert state.task_ledger == []
        assert state.completed_tasks == []
        assert state.stall_count == 0
        assert state.max_stalls == 3
        assert state.specialist_calls == 0
        assert state.current_subtask == ""


class TestProgressCritic:
    def test_stop_on_final_answer(self) -> None:
        critic = ProgressCritic(max_stalls=3)
        state = MagenticOneState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "FINAL ANSWER: The answer is 42"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "stop"
        assert result.score == pytest.approx(1.0)

    def test_continue_on_progress_new_facts(self) -> None:
        critic = ProgressCritic(max_stalls=3)
        state = MagenticOneState(task="test", fact_bank=["Fact 1"])
        decision = Decision(mode="act")
        results = [{"output": "Found some information"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "continue"
        assert result.score >= 0.5

    def test_continue_on_progress_new_completed_tasks(self) -> None:
        critic = ProgressCritic(max_stalls=3)
        # Set up critic with prior counts
        critic._prev_completed_count = 0
        critic._prev_fact_count = 0
        state = MagenticOneState(task="test", completed_tasks=["Step 1"])
        decision = Decision(mode="act")
        results = [{"output": "Completed step 1"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "continue"
        assert result.score >= 0.7

    def test_retry_on_no_progress(self) -> None:
        critic = ProgressCritic(max_stalls=3)
        state = MagenticOneState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "Just thinking..."}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "retry"
        assert result.instruction_patch is not None
        assert state.stall_count == 1

    def test_retry_on_error(self) -> None:
        critic = ProgressCritic(max_stalls=3)
        state = MagenticOneState(task="test")
        decision = Decision(mode="act")
        results = [{"error": "timeout"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "retry"
        # Error counts as extra stall
        assert state.stall_count == 2

    def test_stop_when_max_stalls_reached_no_completed(self) -> None:
        critic = ProgressCritic(max_stalls=2)
        state = MagenticOneState(task="test", stall_count=2)
        decision = Decision(mode="act")
        results = [{"output": "no progress"}]
        result = critic.evaluate(state, decision, results)
        assert result.action == "stop"
        assert result.score == pytest.approx(0.1)

    def test_stop_when_max_stalls_with_partial_results(self) -> None:
        critic = ProgressCritic(max_stalls=2)
        # Set prev counts so completed_tasks don't count as "new progress"
        critic._prev_completed_count = 1
        critic._prev_fact_count = 0
        state = MagenticOneState(
            task="test", stall_count=1, completed_tasks=["Step 1"]
        )
        decision = Decision(mode="act")
        results = [{"output": "no progress"}]
        result = critic.evaluate(state, decision, results)
        # stall_count goes from 1 → 2, which equals max_stalls=2
        assert result.action == "stop"
        assert result.score == pytest.approx(0.4)

    def test_non_magentic_one_state_continues(self) -> None:
        critic = ProgressCritic()
        state = StateSchema(task="test")
        decision = Decision(mode="act")
        result = critic.evaluate(state, decision, [])
        assert result.action == "continue"

    def test_replan_guidance_in_instruction_patch(self) -> None:
        critic = ProgressCritic(max_stalls=5)
        # Set prev counts to match current state so no "new progress" is detected
        critic._prev_completed_count = 1
        critic._prev_fact_count = 1
        state = MagenticOneState(
            task="test",
            fact_bank=["Fact A"],
            task_ledger=["Task 1", "Task 2"],
            completed_tasks=["Task 1"],
            stall_count=1,
        )
        decision = Decision(mode="act")
        results = [{"output": "stuck"}]
        result = critic.evaluate(state, decision, results)
        assert result.instruction_patch is not None
        assert "MAGENTIC-ONE" in result.instruction_patch
        assert "Fact A" in result.instruction_patch

    def test_stall_count_resets_on_progress(self) -> None:
        critic = ProgressCritic(max_stalls=3)
        state = MagenticOneState(task="test", stall_count=2, fact_bank=["Fact 1"])
        decision = Decision(mode="act")
        results = [{"output": "progress"}]
        critic.evaluate(state, decision, results)
        assert state.stall_count == 0

    def test_state_patch_on_retry(self) -> None:
        critic = ProgressCritic(max_stalls=3)
        state = MagenticOneState(task="test", stall_count=0)
        decision = Decision(mode="act")
        results = [{"output": "no progress"}]
        result = critic.evaluate(state, decision, results)
        assert result.state_patch is not None
        assert result.state_patch["stall_count"] == 1


class TestMagenticOneOrchestrator:
    def test_init_state(self) -> None:
        agent = MagenticOneOrchestrator()
        state = agent.init_state("Research topic X", max_steps=30)
        assert state.task == "Research topic X"
        assert state.max_steps == 30
        assert state.max_stalls == 3

    def test_init_state_custom_stalls(self) -> None:
        agent = MagenticOneOrchestrator()
        state = agent.init_state("test", max_steps=10, max_stalls=5)
        assert state.max_stalls == 5

    def test_build_system_prompt(self) -> None:
        agent = MagenticOneOrchestrator()
        state = MagenticOneState(task="test")
        prompt = agent.build_system_prompt(state)
        assert "Magentic-One" in prompt
        assert "Plan" in prompt
        assert "Delegate" in prompt

    def test_build_system_prompt_with_fact_bank(self) -> None:
        agent = MagenticOneOrchestrator()
        state = MagenticOneState(task="test", fact_bank=["Fact A", "Fact B"])
        prompt = agent.build_system_prompt(state)
        assert "Fact Bank" in prompt
        assert "Fact A" in prompt

    def test_build_system_prompt_with_task_ledger(self) -> None:
        agent = MagenticOneOrchestrator()
        state = MagenticOneState(
            task="test",
            task_ledger=["Step 1", "Step 2"],
            completed_tasks=["Step 1"],
        )
        prompt = agent.build_system_prompt(state)
        assert "Task Ledger" in prompt
        assert "Step 1" in prompt
        assert "[DONE]" in prompt

    def test_prepare(self) -> None:
        agent = MagenticOneOrchestrator()
        state = MagenticOneState(
            task="Research topic",
            stall_count=1,
            max_stalls=3,
            specialist_calls=2,
            fact_bank=["F1"],
            task_ledger=["T1", "T2"],
            completed_tasks=["T1"],
        )
        text = agent.prepare(state, {})
        assert "Research topic" in text
        assert "1/3" in text  # stall_count/max_stalls

    def test_reduce_extracts_final_answer(self) -> None:
        agent = MagenticOneOrchestrator()
        state = MagenticOneState(task="test")
        decision = Decision(mode="act")
        results = [{"output": "Done. FINAL ANSWER: The result is X"}]
        new_state = agent.reduce(state, {}, decision, results)
        assert new_state.final_result == "The result is X"

    def test_reduce_tracks_specialist_calls(self) -> None:
        agent = MagenticOneOrchestrator()
        state = MagenticOneState(task="test", specialist_calls=0)
        decision = Decision(mode="act", rationale="Delegate to coder specialist")
        new_state = agent.reduce(state, {}, decision, [])
        assert new_state.specialist_calls == 1
