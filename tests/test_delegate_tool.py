"""Tests for AgentSpec, AgentRegistry, and DelegateTool."""

from __future__ import annotations

import json
import os
import tempfile
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
    HandoffContext,
    StateSchema,
    ToolRegistry,
)
from qitos.engine.action_executor import ActionExecutor
from qitos.engine.states import RuntimePhase
from qitos.kit.tool.delegate import DelegateTool, MAX_DELEGATE_DEPTH


# ── Fixtures ─────────────────────────────────────────────────────────────


@dataclass
class DummyState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)


class DummyAgent(AgentModule[DummyState, dict[str, Any], Action]):
    """Minimal agent that immediately returns a final answer."""

    def __init__(self, final_answer: str = "done"):
        registry = ToolRegistry()
        super().__init__(tool_registry=registry)
        self._final_answer = final_answer

    def init_state(self, task: str, **kwargs: Any) -> DummyState:
        return DummyState(task=task, max_steps=3)

    def reduce(
        self,
        state: DummyState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> DummyState:
        return state


def _make_spec(name: str = "worker", description: str = "A test agent") -> AgentSpec:
    return AgentSpec(
        name=name,
        description=description,
        agent=DummyAgent(final_answer=f"result from {name}"),
        context_strategy=ContextStrategy.ISOLATED,
        max_steps_override=3,
    )


# ── AgentSpec / AgentRegistry tests ──────────────────────────────────────


class TestAgentSpec:
    def test_creation_defaults(self):
        spec = _make_spec()
        assert spec.name == "worker"
        assert spec.context_strategy == ContextStrategy.ISOLATED
        assert spec.max_steps_override == 3
        assert spec.shared_env is True

    def test_creation_custom(self):
        spec = AgentSpec(
            name="coder",
            description="codes",
            agent=DummyAgent(),
            context_strategy=ContextStrategy.FULL,
            max_steps_override=20,
            shared_env=False,
        )
        assert spec.context_strategy == ContextStrategy.FULL
        assert spec.shared_env is False

    def test_tool_name_override(self):
        spec = AgentSpec(
            name="researcher",
            description="Researches a topic",
            agent=DummyAgent(),
            tool_name="research_topic",
        )
        assert spec.tool_name == "research_topic"


class TestAgentRegistry:
    def test_register_and_resolve(self):
        registry = AgentRegistry()
        spec = _make_spec()
        registry.register(spec)
        resolved = registry.resolve("worker")
        assert resolved is spec

    def test_resolve_missing_raises(self):
        registry = AgentRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.resolve("nonexistent")

    def test_duplicate_registration_raises(self):
        registry = AgentRegistry()
        registry.register(_make_spec("worker"))
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_make_spec("worker"))

    def test_list_available(self):
        registry = AgentRegistry()
        registry.register(_make_spec("a"))
        registry.register(_make_spec("b"))
        names = {s.name for s in registry.list_available()}
        assert names == {"a", "b"}

    def test_get_delegate_tools_returns_delegate_tools(self):
        registry = AgentRegistry()
        registry.register(_make_spec("researcher"))
        tools = registry.get_delegate_tools()
        assert len(tools) == 1
        assert isinstance(tools[0], DelegateTool)
        assert tools[0].name == "delegate_to_researcher"

    def test_get_delegate_tools_uses_public_tool_name_override(self):
        registry = AgentRegistry()
        registry.register(
            AgentSpec(
                name="researcher",
                description="Researches a topic",
                agent=DummyAgent(),
                tool_name="research_topic",
            )
        )
        tools = registry.get_delegate_tools()
        assert len(tools) == 1
        assert tools[0].name == "research_topic"


class TestHandoffContext:
    def test_defaults(self):
        ctx = HandoffContext()
        assert ctx.strategy == ContextStrategy.SUMMARY
        assert ctx.payload == {}
        assert ctx.shared_state_fields == []
        assert ctx.max_history_rounds is None


class TestContextStrategy:
    def test_values(self):
        assert ContextStrategy.FULL == "full"
        assert ContextStrategy.SUMMARY == "summary"
        assert ContextStrategy.ISOLATED == "isolated"


# ── DelegateTool tests ───────────────────────────────────────────────────


class TestDelegateTool:
    def test_name_format(self):
        spec = _make_spec("researcher")
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]
        assert tool.name == "delegate_to_researcher"

    def test_public_tool_name_override(self):
        spec = AgentSpec(
            name="researcher",
            description="Researches a topic",
            agent=DummyAgent(),
            tool_name="research_topic",
        )
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]
        assert tool.name == "research_topic"

    def test_spec_description_propagated(self):
        spec = AgentSpec(
            name="analyst",
            description="Analyzes data and produces reports.",
            agent=DummyAgent(),
        )
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]
        assert tool.agent_spec.description == "Analyzes data and produces reports."
        # Also check the tool's ToolSpec description was overridden
        assert tool.spec.description == "Analyzes data and produces reports."

    def test_register_in_tool_registry(self):
        registry = AgentRegistry()
        registry.register(_make_spec("worker"))
        tools = registry.get_delegate_tools()

        tool_reg = ToolRegistry()
        for t in tools:
            tool_reg.register(t)

        # The tool should be findable
        resolved = tool_reg.resolve_name("delegate_to_worker")
        assert resolved == "delegate_to_worker"

    def test_execute_no_task_returns_error(self):
        spec = _make_spec()
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]
        result = tool.execute({"task": ""})
        assert result["status"] == "error"
        assert "task is required" in result["message"]

    def test_execute_depth_guard(self):
        spec = _make_spec()
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]
        result = tool.execute(
            {"task": "do something"},
            runtime_context={"delegate_depth": MAX_DELEGATE_DEPTH},
        )
        assert result["status"] == "error"
        assert "Maximum delegate depth" in result["message"]


class TestDelegateToolExecution:
    """Integration-style tests that actually run a sub-engine."""

    def test_execute_returns_result(self):
        spec = _make_spec()
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]

        mock_result = MagicMock()
        mock_result.state.final_result = "research complete"
        mock_result.state.stop_reason = "final"
        mock_result.step_count = 2

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_result
            result = tool.execute({"task": "find the bug"})

        assert result["status"] == "success"
        assert result["agent"] == "worker"
        assert result["final_result"] == "research complete"
        assert result["steps"] == 2

    def test_execute_partial_result(self):
        spec = _make_spec()
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]

        mock_result = MagicMock()
        mock_result.state.final_result = "ran out of steps"
        mock_result.state.stop_reason = "max_steps"
        mock_result.step_count = 5

        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_result
            result = tool.execute({"task": "research"})

        assert result["status"] == "partial"
        assert result["stop_reason"] == "max_steps"

    def test_execute_passes_context_argument_to_sub_engine(self):
        spec = _make_spec()
        registry = AgentRegistry()
        registry.register(spec)
        tool = registry.get_delegate_tools()[0]

        mock_result = MagicMock()
        mock_result.state.final_result = "research complete"
        mock_result.state.stop_reason = "final"
        mock_result.step_count = 2

        context = {"repo_summary": "src/", "parser_paths": ["src/parser.c"]}
        with patch("qitos.engine.engine.Engine") as MockEngine:
            MockEngine.return_value.run.return_value = mock_result
            result = tool.execute({"task": "find the bug", "context": context})

        assert result["status"] == "success"
        MockEngine.return_value.run.assert_called_once_with(
            "find the bug",
            context=context,
        )


# ── RuntimePhase extension tests ─────────────────────────────────────────


class TestRuntimePhase:
    def test_delegate_phases_exist(self):
        assert RuntimePhase.DELEGATE_START == "DELEGATE_START"
        assert RuntimePhase.DELEGATE_END == "DELEGATE_END"


# ── ActionExecutor runtime_context tests ─────────────────────────────────


class TestActionExecutorContext:
    def test_runtime_context_has_delegation_keys(self):
        executor = ActionExecutor(tool_registry=ToolRegistry())
        ctx = executor._build_runtime_context("some_tool", env=None, state=None)
        assert "delegate_depth" in ctx
        assert "parent_run_id" in ctx
        assert "trace_writer" in ctx
        assert ctx["delegate_depth"] == 0
        assert ctx["parent_run_id"] == ""
        assert ctx["trace_writer"] is None

    def test_runtime_context_delegate_depth_propagated(self):
        executor = ActionExecutor(tool_registry=ToolRegistry(), delegate_depth=2)
        ctx = executor._build_runtime_context("some_tool", env=None, state=None)
        assert ctx["delegate_depth"] == 2

    def test_sub_engine_receives_incremented_depth(self):
        """DelegateTool._build_sub_engine should pass current_depth + 1 to Engine."""
        from qitos.engine.engine import Engine
        registry = AgentRegistry()
        registry.register(_make_spec("worker"))
        tool = registry.get_delegate_tools()[0]
        runtime_context = {"env": None, "trace_writer": None}
        sub_engine = tool._build_sub_engine(runtime_context, current_depth=1)
        assert sub_engine._delegate_depth == 2

    def test_runtime_context_trace_writer_passed_through(self):
        mock_tw = MagicMock()
        executor = ActionExecutor(tool_registry=ToolRegistry(), trace_writer=mock_tw)
        ctx = executor._build_runtime_context("some_tool", env=None, state=None)
        assert ctx["trace_writer"] is mock_tw
