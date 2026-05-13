"""Tests for FanOutTool, ContextStrategy integration, and depth propagation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from qitos import (
    Action,
    AgentModule,
    AgentRegistry,
    AgentSpec,
    ContextStrategy,
    Decision,
    StateSchema,
    ToolRegistry,
)
from qitos.kit.tool.fanout import FanOutTool, MAX_DELEGATE_DEPTH
from qitos.engine.states import RuntimePhase


# ── Fixtures ─────────────────────────────────────────────────────────────


@dataclass
class DummyState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)


class DummyAgent(AgentModule[DummyState, dict[str, Any], Action]):
    def __init__(self, name: str = "agent", final_answer: str = "done"):
        registry = ToolRegistry()
        super().__init__(tool_registry=registry)
        self.name = name
        self._final_answer = final_answer

    def init_state(self, task: str, **kwargs: Any) -> DummyState:
        return DummyState(task=task, max_steps=3)

    def reduce(self, state, observation, decision):
        return state


def _make_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(
        AgentSpec(name="worker", description="A test worker", agent=DummyAgent(final_answer="explored"))
    )
    return registry


# ── FanOutTool creation tests ────────────────────────────────────────────


class TestFanOutToolCreation:
    def test_get_fanout_tool_from_registry(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()
        assert isinstance(tool, FanOutTool)
        assert tool.name == "fanout"

    def test_fanout_tool_custom_workers(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool(max_workers=8)
        assert tool._max_workers == 8

    def test_fanout_tool_registered_in_tool_registry(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()
        tool_reg = ToolRegistry()
        tool_reg.register(tool)
        assert tool_reg.resolve_name("fanout") == "fanout"

    def test_fanout_tool_spec_flags(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()
        assert tool.spec.concurrency_safe is True
        assert tool.spec.supports_background is True


# ── FanOutTool execution tests ───────────────────────────────────────────


class TestFanOutToolExecution:
    def test_empty_tasks_returns_error(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()
        result = tool.execute({"tasks": []})
        assert result["status"] == "error"
        assert "tasks" in result["message"]

    def test_depth_guard(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()
        result = tool.execute(
            {"tasks": [{"agent": "worker", "task": "do something"}]},
            runtime_context={"delegate_depth": MAX_DELEGATE_DEPTH},
        )
        assert result["status"] == "error"
        assert "Maximum delegate depth" in result["message"]

    def test_invalid_agent_name(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()
        result = tool.execute(
            {"tasks": [{"agent": "nonexistent", "task": "do something"}]},
        )
        assert result["status"] == "error"
        assert "nonexistent" in str(result["results"])

    def test_missing_task_field(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()
        result = tool.execute(
            {"tasks": [{"agent": "worker"}]},
        )
        # Invalid task spec should produce error in results
        assert "invalid_0" in result.get("results", {}) or result["status"] == "error"

    def test_parallel_execution_with_mock(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()

        mock_result = MagicMock()
        mock_result.state.final_result = "found 3 files"
        mock_result.state.stop_reason = "final"
        mock_result.step_count = 2

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_result
            result = tool.execute({
                "tasks": [
                    {"agent": "worker", "task": "explore /auth"},
                    {"agent": "worker", "task": "explore /api"},
                    {"agent": "worker", "task": "explore /db"},
                ]
            })

        assert result["status"] == "success"
        results = result["results"]
        assert len(results) == 3
        for key, r in results.items():
            assert r["status"] == "success"
            assert r["final_result"] == "found 3 files"

    def test_aggregation_summary(self):
        registry = _make_registry()
        tool = registry.get_fanout_tool()

        mock_result = MagicMock()
        mock_result.state.final_result = "found 3 files"
        mock_result.state.stop_reason = "final"
        mock_result.step_count = 2

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_result
            result = tool.execute({
                "tasks": [
                    {"agent": "worker", "task": "explore /auth"},
                    {"agent": "worker", "task": "explore /api"},
                ]
            })

        summary = result["summary"]
        assert "2 tasks" in summary
        assert "2 succeeded" in summary

    def test_sub_engine_receives_incremented_depth(self):
        """FanOutTool._build_sub_engine should pass depth + 1 to Engine."""
        registry = _make_registry()
        tool = registry.get_fanout_tool()
        spec = registry.resolve("worker")
        runtime_context = {"env": None, "trace_writer": None}
        sub_engine = tool._build_sub_engine(spec, runtime_context, depth=1, idx=0)
        assert sub_engine._delegate_depth == 2

    def test_per_task_timeout_returns_error(self):
        """A sub-agent whose task_deadline has passed should return timeout error."""
        import time
        registry = _make_registry()
        tool = registry.get_fanout_tool(per_task_timeout=0.001)

        spec = registry.resolve("worker")
        # Use a deadline that's already in the past
        result = tool._run_sub_agent(
            spec, "test task", {}, 0, 0,
            task_deadline=time.monotonic() - 1.0,  # already expired
        )
        assert result["status"] == "error"
        assert "timed out" in result["message"]

    def test_custom_per_task_timeout(self):
        """FanOutTool should accept custom per_task_timeout."""
        registry = _make_registry()
        tool = registry.get_fanout_tool(per_task_timeout=60.0)
        assert tool._per_task_timeout == 60.0


# ── ContextStrategy tests ────────────────────────────────────────────────


class TestContextStrategyIntegration:
    def test_isolated_no_parent_context(self):
        spec = AgentSpec(
            name="worker",
            description="test",
            agent=DummyAgent(),
            context_strategy=ContextStrategy.ISOLATED,
        )
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]

        state = DummyState(task="test", max_steps=5)
        state.scratchpad = ["Thought: analyzing", "Action: read_file", "Observation: found bug"]
        runtime_context = {"state": state}

        prepared = tool._prepare_task("explore auth module", runtime_context)
        assert prepared == "explore auth module"

    def test_full_includes_parent_context(self):
        spec = AgentSpec(
            name="worker",
            description="test",
            agent=DummyAgent(),
            context_strategy=ContextStrategy.FULL,
        )
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]

        state = DummyState(task="test", max_steps=5)
        state.scratchpad = ["Thought: analyzing", "Action: read_file", "Observation: found bug"]
        runtime_context = {"state": state}

        prepared = tool._prepare_task("explore auth module", runtime_context)
        assert "Parent agent context:" in prepared
        assert "explore auth module" in prepared

    def test_summary_includes_compressed_context(self):
        spec = AgentSpec(
            name="worker",
            description="test",
            agent=DummyAgent(),
            context_strategy=ContextStrategy.SUMMARY,
        )
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]

        state = DummyState(task="test", max_steps=5)
        state.scratchpad = ["Thought: analyzing", "Action: read_file", "Observation: found bug"]
        runtime_context = {"state": state}

        prepared = tool._prepare_task("explore auth module", runtime_context)
        assert "Parent agent summary:" in prepared
        assert "explore auth module" in prepared

    def test_no_scratchpad_passes_task_unchanged(self):
        spec = AgentSpec(
            name="worker",
            description="test",
            agent=DummyAgent(),
            context_strategy=ContextStrategy.FULL,
        )
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]

        state = DummyState(task="test", max_steps=5)
        # No scratchpad entries
        runtime_context = {"state": state}

        prepared = tool._prepare_task("explore auth module", runtime_context)
        assert prepared == "explore auth module"

    def test_fanout_context_strategy(self):
        spec = AgentSpec(
            name="worker",
            description="test",
            agent=DummyAgent(),
            context_strategy=ContextStrategy.FULL,
        )
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_fanout_tool()

        state = DummyState(task="test", max_steps=5)
        state.scratchpad = ["Thought: parent context"]
        runtime_context = {"state": state}

        prepared = tool._prepare_task(spec, "explore auth module", runtime_context)
        assert "Parent agent context:" in prepared


# ── RuntimePhase tests ────────────────────────────────────────────────────


class TestRuntimePhaseFanout:
    def test_fanout_phases_exist(self):
        assert RuntimePhase.FANOUT_START == "FANOUT_START"
        assert RuntimePhase.FANOUT_END == "FANOUT_END"
