"""Tests for Checkpoint / Resume."""

import json
import tempfile
from dataclasses import dataclass, field
from typing import Any

import pytest

from qitos import AgentModule, Decision, Action, Engine, StateSchema, ToolRegistry, tool
from qitos.checkpoint import CheckpointData, CheckpointManager
from qitos.engine import RuntimeBudget


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


# --- CheckpointData tests ---


class TestCheckpointData:
    def test_to_dict_from_dict(self):
        data = CheckpointData(
            run_id="test_run",
            step_id=3,
            state_dict={"task": "hello"},
            step_records=[{"step_id": 0}],
            runtime_events=[{"step_id": 0, "phase": "DECIDE"}],
            budget={"max_steps": 10},
            token_usage=150,
            task_text="hello",
            task_dict=None,
        )
        d = data.to_dict()
        restored = CheckpointData.from_dict(d)
        assert restored.run_id == "test_run"
        assert restored.step_id == 3
        assert restored.state_dict == {"task": "hello"}
        assert restored.token_usage == 150

    def test_from_dict_missing_optional(self):
        payload = {
            "run_id": "r1",
            "step_id": 0,
            "state_dict": {},
            "step_records": [],
            "runtime_events": [],
            "budget": {},
            "token_usage": 0,
            "task_text": "",
        }
        data = CheckpointData.from_dict(payload)
        assert data.task_dict is None
        assert data.schema_version == "v1"


# --- CheckpointManager tests ---


class TestCheckpointManager:
    def test_should_checkpoint(self):
        mgr = CheckpointManager("/tmp/test_cp", interval=2)
        assert not mgr.should_checkpoint(0)
        assert not mgr.should_checkpoint(1)
        assert mgr.should_checkpoint(2)
        assert not mgr.should_checkpoint(3)
        assert mgr.should_checkpoint(4)

    def test_should_checkpoint_default_interval(self):
        mgr = CheckpointManager("/tmp/test_cp")
        assert mgr.should_checkpoint(1)
        assert mgr.should_checkpoint(2)

    def test_save_and_load_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir, interval=1)
            data1 = CheckpointData(
                run_id="run1", step_id=1, state_dict={},
                step_records=[], runtime_events=[],
                budget={}, token_usage=0, task_text="t1",
            )
            data2 = CheckpointData(
                run_id="run1", step_id=2, state_dict={},
                step_records=[], runtime_events=[],
                budget={}, token_usage=0, task_text="t1",
            )
            mgr.save(data1)
            mgr.save(data2)

            latest = mgr.load_latest("run1")
            assert latest is not None
            assert latest.step_id == 2

    def test_load_latest_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            assert mgr.load_latest("nonexistent") is None

    def test_list_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir, interval=1)
            for i in range(1, 4):
                mgr.save(CheckpointData(
                    run_id="run1", step_id=i, state_dict={},
                    step_records=[], runtime_events=[],
                    budget={}, token_usage=0, task_text="t",
                ))
            checkpoints = mgr.list_checkpoints("run1")
            assert len(checkpoints) == 3
            assert [c.step_id for c in checkpoints] == [1, 2, 3]

    def test_cleanup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir, interval=1)
            for i in range(1, 5):
                mgr.save(CheckpointData(
                    run_id="run1", step_id=i, state_dict={},
                    step_records=[], runtime_events=[],
                    budget={}, token_usage=0, task_text="t",
                ))
            mgr.cleanup("run1", keep=2)
            remaining = mgr.list_checkpoints("run1")
            assert len(remaining) == 2
            assert remaining[0].step_id == 3
            assert remaining[1].step_id == 4

    def test_cleanup_noop_if_under_keep(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir, interval=1)
            mgr.save(CheckpointData(
                run_id="run1", step_id=1, state_dict={},
                step_records=[], runtime_events=[],
                budget={}, token_usage=0, task_text="t",
            ))
            mgr.cleanup("run1", keep=5)
            assert len(mgr.list_checkpoints("run1")) == 1


# --- Engine + Checkpoint integration ---


class TestEngineCheckpointIntegration:
    def test_engine_saves_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir, interval=1)
            agent = DemoAgent(answer="checkpointed")
            engine = Engine(
                agent=agent,
                budget=RuntimeBudget(max_steps=5),
                checkpoint_manager=mgr,
            )
            result = engine.run("test task")
            assert result.state.final_result == "checkpointed"
            # Should have saved checkpoints
            checkpoints = mgr.list_checkpoints(result.run_id)
            assert len(checkpoints) >= 1

    def test_engine_no_checkpoint_when_none(self):
        agent = DemoAgent()
        engine = Engine(agent=agent, budget=RuntimeBudget(max_steps=5))
        result = engine.run("test task")
        assert result.state.final_result == "42"
        # No error — backward compatible

    def test_checkpoint_interval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir, interval=2)
            agent = DemoAgent(answer="interval")
            engine = Engine(
                agent=agent,
                budget=RuntimeBudget(max_steps=5),
                checkpoint_manager=mgr,
            )
            result = engine.run("test task")
            checkpoints = mgr.list_checkpoints(result.run_id)
            # Only step 2 should be checkpointed (interval=2)
            for cp in checkpoints:
                assert cp.step_id % 2 == 0
