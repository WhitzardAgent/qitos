"""Tests for Experiment Runner and Sweep."""

import json
import tempfile
from dataclasses import dataclass, field
from typing import Any

import pytest

from qitos import AgentModule, Decision, Action, Engine, StateSchema, ToolRegistry, tool
from qitos.checkpoint import CheckpointManager
from qitos.engine.engine import RuntimeBudget
from qitos.experiment import ExperimentRunner, ExperimentResult, SweepSpec, sweep_product


# --- Fixtures ---


@dataclass
class DemoState(StateSchema):
    logs: list[str] = field(default_factory=list)


class DemoAgent(AgentModule[DemoState, dict[str, Any], Action]):
    def __init__(self, answer: str = "42"):
        registry = ToolRegistry()

        @tool(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        registry.register(add)
        self._answer = answer
        super().__init__(tool_registry=registry)

    def init_state(self, task: str, **kwargs: Any) -> DemoState:
        return DemoState(task=task, max_steps=3)

    def decide(self, state: DemoState, observation: dict[str, Any]) -> Decision[Action]:
        if state.current_step == 0:
            return Decision.act(
                actions=[Action(name="add", args={"a": 1, "b": 2})],
                rationale="use tool",
            )
        return Decision.final(self._answer)

    def reduce(
        self,
        state: DemoState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> DemoState:
        return state


# --- SweepSpec tests ---


class TestSweepSpec:
    def test_empty_sweep(self):
        sweep = SweepSpec()
        assert sweep.is_empty()
        result = sweep_product(sweep)
        assert result == [{}]

    def test_single_param(self):
        sweep = SweepSpec(params={"temperature": [0.0, 0.5, 1.0]})
        result = sweep_product(sweep)
        assert len(result) == 3
        assert result[0] == {"temperature": 0.0}
        assert result[2] == {"temperature": 1.0}

    def test_multi_param_cartesian(self):
        sweep = SweepSpec(
            params={
                "model.temperature": [0.0, 0.5],
                "max_steps": [5, 10],
            }
        )
        result = sweep_product(sweep)
        assert len(result) == 4
        # Verify all combinations exist
        combos = [
            (r["model.temperature"], r["max_steps"]) for r in result
        ]
        assert (0.0, 5) in combos
        assert (0.0, 10) in combos
        assert (0.5, 5) in combos
        assert (0.5, 10) in combos


# --- ExperimentResult tests ---


class TestExperimentResult:
    def test_to_dict(self):
        result = ExperimentResult(
            experiment_name="test_exp",
            total_tasks=2,
            completed_tasks=2,
            failed_tasks=0,
            skipped_tasks=0,
            summary={"success_rate": 1.0},
        )
        d = result.to_dict()
        assert d["experiment_name"] == "test_exp"
        assert d["total_tasks"] == 2
        assert d["summary"]["success_rate"] == 1.0


# --- ExperimentRunner tests ---


class TestExperimentRunner:
    def test_basic_two_tasks(self):
        agent = DemoAgent(answer="hello")
        tasks = [
            {"task": "task 1", "id": "t1"},
            {"task": "task 2", "id": "t2"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner(
                agent=agent,
                output_dir=tmpdir,
            )
            result = runner.run(tasks=tasks)
            assert result.total_tasks == 2
            assert result.completed_tasks == 2
            assert result.failed_tasks == 0
            assert len(result.results) == 2

    def test_with_sweep(self):
        agent = DemoAgent(answer="swept")
        sweep = SweepSpec(params={"max_steps": [3, 5]})
        tasks = [{"task": "compute", "id": "t1"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner(
                agent=agent,
                sweep=sweep,
                output_dir=tmpdir,
            )
            result = runner.run(tasks=tasks)
            # 1 task x 2 sweep values = 2 total runs
            assert result.total_tasks == 2
            assert result.completed_tasks == 2

    def test_resume_skips_completed(self):
        agent = DemoAgent(answer="resumed")
        tasks = [
            {"task": "task 1", "id": "t1"},
            {"task": "task 2", "id": "t2"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            # First run
            runner = ExperimentRunner(
                agent=agent,
                output_dir=tmpdir,
            )
            result1 = runner.run(tasks=tasks)
            assert result1.completed_tasks == 2
            assert result1.skipped_tasks == 0

            # Second run with resume should skip all
            runner2 = ExperimentRunner(
                agent=agent,
                output_dir=tmpdir,
                resume=True,
            )
            result2 = runner2.run(tasks=tasks)
            assert result2.skipped_tasks == 2
            assert result2.completed_tasks == 2  # includes loaded results

    def test_no_agent_raises(self):
        tasks = [{"task": "hello", "id": "t1"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner(output_dir=tmpdir)
            with pytest.raises(ValueError, match="requires an `agent`"):
                runner.run(tasks=tasks)

    def test_no_tasks_returns_empty(self):
        agent = DemoAgent()
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner(agent=agent, output_dir=tmpdir)
            result = runner.run(tasks=[])
            assert result.total_tasks == 0
            assert result.completed_tasks == 0

    def test_results_persisted(self):
        agent = DemoAgent(answer="persisted")
        tasks = [{"task": "task 1", "id": "t1"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner(
                agent=agent,
                output_dir=tmpdir,
            )
            runner.run(tasks=tasks)
            # Check file was written
            results_file = tmpdir + "/results.json"
            with open(results_file) as f:
                data = json.load(f)
            assert data["experiment_name"] == "unnamed"
            assert len(data["results"]) == 1

    def test_with_checkpoint_config(self):
        agent = DemoAgent(answer="checkpointed")
        tasks = [{"task": "task 1", "id": "t1"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            cp_dir = tmpdir + "/checkpoints"
            runner = ExperimentRunner(
                agent=agent,
                output_dir=tmpdir,
                checkpoint_config={"dir": cp_dir, "interval": 1},
            )
            result = runner.run(tasks=tasks)
            assert result.completed_tasks == 1

    def test_summary_statistics(self):
        agent = DemoAgent(answer="ok")
        tasks = [{"task": "task 1", "id": "t1"}]
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = ExperimentRunner(
                agent=agent,
                output_dir=tmpdir,
            )
            result = runner.run(tasks=tasks)
            assert "success_rate" in result.summary
            assert "total_runs" in result.summary
            assert result.summary["total_runs"] == 1
